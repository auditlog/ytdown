"""End-to-end orchestration for 7z archive flows (playlist + single-file).

Boundaries:
- ``bot.archive`` — pure 7z wrapper, no Telegram/session knowledge.
- ``archive_service`` (this module) — knows about sessions, downloads,
  Telegram bot client, and gluing them together.
- ``bot.handlers.*`` — translate inline keyboard callbacks into calls
  on this service, never call ``bot.archive`` directly.
"""

from __future__ import annotations

import logging
import secrets
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from bot.archive import (
    compute_archive_basename,
    is_7z_available,
    pack_to_volumes,
    transliterate_to_ascii,
    volume_size_for,
)
from bot.config import DOWNLOAD_PATH
from bot.downloader_validation import sanitize_filename
from bot.mtproto import mtproto_unavailability_reason, send_document_mtproto
from bot.security_limits import MAX_ARCHIVE_ITEM_SIZE_MB, PLAYLIST_ARCHIVE_RETENTION_MIN, TELEGRAM_UPLOAD_LIMIT_MB
from bot.services.download_service import (
    ensure_size_within_limit,
    estimate_download_size,
    execute_download,
    prepare_download_plan,
)
from bot.session_store import (
    ArchiveJobState,
    ArchivedDeliveryState,
    archived_deliveries,
    pending_archive_jobs,
)
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


_SLUG_MAX_LEN = 60


def _build_slug(title: str) -> str:
    """Translit-then-sanitize playlist/file title for use in filesystem path."""

    transliterated = transliterate_to_ascii(title)
    sanitized = sanitize_filename(transliterated)
    cleaned = sanitized.replace(" ", "_")
    return cleaned[:_SLUG_MAX_LEN] or "untitled"


def prepare_playlist_workspace(
    chat_id: int,
    playlist_title: str,
    *,
    prefix: str = "pl",
) -> Path:
    """Create ``downloads/<chat_id>/<prefix>_<slug>_<ts>/`` and return it."""

    slug = _build_slug(playlist_title)
    basename = compute_archive_basename(slug, datetime.now())
    chat_dir = Path(DOWNLOAD_PATH) / str(chat_id)
    chat_dir.mkdir(parents=True, exist_ok=True)
    workspace = chat_dir / f"{prefix}_{basename}"
    workspace.mkdir(parents=True, exist_ok=True)
    logging.info("Archive workspace ready: %s", workspace)
    return workspace


def register_pending_archive_job(chat_id: int, state: ArchiveJobState) -> str:
    """Store a pending archive job and return the lookup token (8 hex chars)."""

    token = secrets.token_hex(4)
    bucket = pending_archive_jobs.get(chat_id) or {}
    bucket[token] = state
    pending_archive_jobs[chat_id] = bucket
    return token


def register_archived_delivery(chat_id: int, state: ArchivedDeliveryState) -> str:
    """Store delivery metadata for retry/purge actions and return its token."""

    token = secrets.token_hex(4)
    bucket = archived_deliveries.get(chat_id) or {}
    bucket[token] = state
    archived_deliveries[chat_id] = bucket
    return token


async def _noop_status(_text: str) -> None:
    """No-op async status callback used internally by _download_one_into_workspace."""
    return None


async def _download_one_into_workspace(
    entry: dict,
    workspace: Path,
    *,
    media_type: str,
    format_choice: str,
    executor: ThreadPoolExecutor,
) -> tuple[Path | None, float | None]:
    """Download one playlist item into ``workspace``. Returns (path, size_mb).

    Returns (None, size_mb) when the estimated size exceeds
    MAX_ARCHIVE_ITEM_SIZE_MB; the caller should record this as a failure
    with a descriptive title.
    Raises on metadata fetch failure or yt-dlp errors.
    """

    plan = prepare_download_plan(
        url=entry["url"],
        media_type=media_type,
        format_choice=format_choice,
        chat_download_path=str(workspace),
    )
    if plan is None:
        raise RuntimeError(f"could not fetch metadata for {entry.get('title')}")

    try:
        estimated = estimate_download_size(plan)
    except Exception:
        estimated = None

    if estimated is not None and not ensure_size_within_limit(
        estimated, max_size_mb=MAX_ARCHIVE_ITEM_SIZE_MB
    ):
        return None, estimated

    # chat_id=0 with an isolated local progress_state={} dict so per-chat
    # progress reporting (which writes to that dict by chat_id) does not
    # leak across concurrent archive flows. Do not pass session_store's
    # global download_progress here.
    result = await execute_download(
        plan,
        chat_id=0,
        executor=executor,
        progress_hook_factory=lambda _cid: (lambda _data: None),
        progress_state={},
        status_callback=_noop_status,
        format_bytes=lambda v: str(v),
        format_eta=lambda v: str(v),
    )
    return Path(result.file_path), result.file_size_mb


async def download_playlist_into(
    workspace: Path,
    entries: list[dict],
    *,
    media_type: str,
    format_choice: str,
    executor: ThreadPoolExecutor,
    status_cb: Callable[[str], Awaitable[None]],
) -> tuple[list[Path], list[str]]:
    """Download every entry into workspace, keeping the files (no os.remove).

    Returns (downloaded_paths, failed_titles). Items exceeding the
    MAX_ARCHIVE_ITEM_SIZE_MB cap are reported on failed_titles with a
    ``(za duzy: X MB)`` suffix. Network failures are similarly recorded
    with the original title.
    """

    downloaded: list[Path] = []
    failed: list[str] = []
    total = len(entries)

    for idx, entry in enumerate(entries, 1):
        title = entry.get("title", f"item_{idx}")
        await status_cb(f"[{idx}/{total}] Pobieranie: {title}...")
        try:
            path, size = await _download_one_into_workspace(
                entry,
                workspace,
                media_type=media_type,
                format_choice=format_choice,
                executor=executor,
            )
        except Exception as exc:
            logging.error("Archive download failed for %s: %s", title, exc)
            failed.append(title)
            continue

        if path is None:
            mb_str = f"{size:.0f} MB" if size is not None else "?"
            failed.append(f"{title} (za duzy: {mb_str})")
            continue

        downloaded.append(path)

    return downloaded, failed


async def send_volumes(
    bot,
    chat_id: int,
    volumes: list[Path],
    caption_prefix: str,
    use_mtproto: bool,
    *,
    start_index: int = 0,
    status_cb: Callable[[str], Awaitable[None]] | None = None,
) -> None:
    """Send 7z volumes [start_index:] to ``chat_id`` as documents.

    Volumes ≤ TELEGRAM_UPLOAD_LIMIT_MB go via Bot API (``bot.send_document``);
    larger ones go via MTProto. Caption per volume is
    ``"<caption_prefix> [j/M]"``. The displayed file name is the volume's
    original name (``<basename>.7z.001`` etc).

    ``use_mtproto`` is informational for higher layers — the per-volume
    transport decision is based solely on volume size vs TELEGRAM_UPLOAD_LIMIT_MB.

    Raises:
        RuntimeError: when a volume needs MTProto but it is unavailable, or
            when MTProto sending returns False.
    """

    total = len(volumes)
    for idx in range(start_index, total):
        volume = volumes[idx]
        size_mb = volume.stat().st_size / (1024 * 1024)
        caption = f"{caption_prefix} [{idx + 1}/{total}]"
        if status_cb is not None:
            await status_cb(f"Wysyłanie [{idx + 1}/{total}] ({size_mb:.0f} MB)...")

        if size_mb <= TELEGRAM_UPLOAD_LIMIT_MB:
            with open(volume, "rb") as handle:
                await bot.send_document(
                    chat_id=chat_id,
                    document=handle,
                    filename=volume.name,
                    caption=caption,
                    read_timeout=120,
                    write_timeout=120,
                )
        else:
            reason = mtproto_unavailability_reason()
            if reason is not None:
                raise RuntimeError(
                    f"Wolumen {volume.name} przekracza Bot API ({size_mb:.0f} MB), "
                    f"a MTProto jest niedostępny: {reason}"
                )
            ok = await send_document_mtproto(
                chat_id=chat_id,
                file_path=str(volume),
                caption=caption,
                file_name=volume.name,
            )
            if not ok:
                raise RuntimeError(f"Wysyłka {volume.name} przez MTProto nie powiodła się.")

        logging.info("Sent volume %d/%d: %s (%.1f MB)", idx + 1, total, volume.name, size_mb)


async def _safe_status_edit(update, text: str) -> None:
    """Edit the inline-keyboard message body, ignoring 'message not modified' errors."""

    try:
        await update.callback_query.edit_message_text(text)
    except Exception as exc:
        logging.debug("status edit failed (non-fatal): %s", exc)


async def execute_playlist_archive_flow(
    update,
    context,
    *,
    chat_id: int,
    playlist: dict[str, Any],
    media_type: str,
    format_choice: str,
    executor: ThreadPoolExecutor,
) -> None:
    """End-to-end: workspace → download all → pack to 7z → send volumes."""

    if not is_7z_available():
        await _safe_status_edit(
            update,
            "Funkcja 7z niedostępna — administrator nie zainstalował p7zip-full.",
        )
        return

    use_mtproto = mtproto_unavailability_reason() is None
    volume_size_mb = volume_size_for(use_mtproto)

    title = playlist.get("title", "Playlista")
    entries = playlist.get("entries") or []
    total = len(entries)

    workspace = prepare_playlist_workspace(chat_id, title, prefix="pl")
    lock_path = workspace / ".lock"
    lock_path.touch()

    async def status(text: str) -> None:
        await _safe_status_edit(update, text)

    await status(f"Playlista → 7z ({media_type} {format_choice})\n[0/{total}] Pobieranie...")

    try:
        downloaded, failed = await download_playlist_into(
            workspace,
            entries,
            media_type=media_type,
            format_choice=format_choice,
            executor=executor,
            status_cb=status,
        )

        if not downloaded:
            shutil.rmtree(workspace, ignore_errors=True)
            await status("Nie udało się pobrać żadnego elementu.")
            return

        await status(f"Pakowanie do 7z (vol_size={volume_size_mb} MB)...")
        slug = _build_slug(title)
        dest_basename = workspace / compute_archive_basename(
            f"{slug}_{media_type}_{format_choice}", datetime.now()
        )
        volumes = await pack_to_volumes(downloaded, dest_basename, volume_size_mb)

        caption_prefix = f"{title} ({media_type} {format_choice})"
        await status(f"Pakowanie OK: {len(volumes)} paczek. Wysyłanie...")
        await send_volumes(
            context.bot,
            chat_id=chat_id,
            volumes=volumes,
            caption_prefix=caption_prefix,
            use_mtproto=use_mtproto,
            status_cb=status,
        )

        delivery = ArchivedDeliveryState(
            workspace=workspace,
            volumes=volumes,
            caption_prefix=caption_prefix,
            use_mtproto=use_mtproto,
            created_at=datetime.now(),
        )
        token = register_archived_delivery(chat_id, delivery)

        summary_lines = [
            "Playlista zakończona.",
            f"Pobrano: {len(downloaded)}/{total}",
            f"Spakowano: {len(downloaded)} plików → {len(volumes)} paczek 7z",
            f"Wysłano: {len(volumes)}/{len(volumes)}",
            f"Folder zostanie usunięty po {PLAYLIST_ARCHIVE_RETENTION_MIN} min.",
        ]
        if failed:
            summary_lines.append("")
            summary_lines.append("Nieudane elementy:")
            for title_ in failed[:5]:
                summary_lines.append(f"  - {title_[:60]}")

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "Wyślij wszystkie paczki ponownie",
                callback_data=f"arc_resend_{token}_0",
            )],
            [InlineKeyboardButton(
                "Usuń teraz",
                callback_data=f"arc_purge_{token}",
            )],
        ])
        try:
            await update.callback_query.edit_message_text(
                "\n".join(summary_lines),
                reply_markup=keyboard,
            )
        except Exception as exc:
            logging.debug("summary edit failed: %s", exc)
    except Exception as exc:
        logging.error("Playlist archive flow failed: %s", exc)
        await status(f"Pakowanie/wysyłka nie powiodły się: {exc}")
        # Workspace stays so user can retry; cleanup will remove it after retention.
    finally:
        try:
            lock_path.unlink()
        except OSError:
            pass


async def execute_single_file_archive_flow(
    update,
    context,
    *,
    chat_id: int,
    token: str,
) -> None:
    """End-to-end fallback for an oversized single file pre-registered as token."""

    if not is_7z_available():
        await _safe_status_edit(
            update,
            "Funkcja 7z niedostępna — administrator nie zainstalował p7zip-full.",
        )
        return

    bucket = pending_archive_jobs.get(chat_id) or {}
    state = bucket.get(token)
    if state is None:
        await _safe_status_edit(update, "Sesja wygasła. Wyślij plik ponownie.")
        return

    use_mtproto = mtproto_unavailability_reason() is None
    volume_size_mb = volume_size_for(use_mtproto)

    workspace = prepare_playlist_workspace(chat_id, state.title, prefix="big")
    lock_path = workspace / ".lock"
    lock_path.touch()

    src = Path(state.file_path)
    moved_path = workspace / src.name
    try:
        shutil.move(str(src), moved_path)
    except OSError as exc:
        await _safe_status_edit(update, f"Nie można przenieść pliku do workspace: {exc}")
        try:
            lock_path.unlink()
        except OSError:
            pass
        return

    async def status(text: str) -> None:
        await _safe_status_edit(update, text)

    await status(f"Pakowanie do 7z (vol_size={volume_size_mb} MB)...")
    try:
        slug = _build_slug(state.title)
        dest_basename = workspace / compute_archive_basename(slug, datetime.now())
        volumes = await pack_to_volumes([moved_path], dest_basename, volume_size_mb)

        caption_prefix = state.title
        await status(f"Pakowanie OK: {len(volumes)} paczek. Wysyłanie...")
        await send_volumes(
            context.bot,
            chat_id=chat_id,
            volumes=volumes,
            caption_prefix=caption_prefix,
            use_mtproto=use_mtproto,
            status_cb=status,
        )

        delivery = ArchivedDeliveryState(
            workspace=workspace,
            volumes=volumes,
            caption_prefix=caption_prefix,
            use_mtproto=use_mtproto,
            created_at=datetime.now(),
        )
        delivery_token = register_archived_delivery(chat_id, delivery)

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "Wyślij wszystkie paczki ponownie",
                callback_data=f"arc_resend_{delivery_token}_0",
            )],
            [InlineKeyboardButton(
                "Usuń teraz",
                callback_data=f"arc_purge_{delivery_token}",
            )],
        ])
        try:
            await update.callback_query.edit_message_text(
                f"Plik wysłany w {len(volumes)} paczkach. Folder zostanie usunięty po {PLAYLIST_ARCHIVE_RETENTION_MIN} min.",
                reply_markup=keyboard,
            )
        except Exception as exc:
            logging.debug("summary edit failed: %s", exc)
    except Exception as exc:
        logging.error("Single-file archive flow failed: %s", exc)
        await status(f"Pakowanie/wysyłka nie powiodły się: {exc}")
    finally:
        # Always consume the pending job so the token cannot be re-used.
        bucket.pop(token, None)
        if not bucket:
            pending_archive_jobs.pop(chat_id, None)
        else:
            pending_archive_jobs[chat_id] = bucket
        try:
            lock_path.unlink()
        except OSError:
            pass
