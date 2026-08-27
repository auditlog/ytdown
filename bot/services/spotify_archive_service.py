"""Archive selected Spotify collection tracks into grouped 7z packages."""

from __future__ import annotations

import logging
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot.archive import (
    compute_archive_basename,
    is_7z_available,
    pack_to_volumes,
    transliterate_to_ascii,
    volume_size_for,
)
from bot.downloader_validation import sanitize_filename
from bot.jobs import JobDescriptor, job_registry
from bot.mtproto import mtproto_unavailability_reason
from bot.security_limits import (
    MAX_ARCHIVE_ITEM_SIZE_MB,
    PLAYLIST_ARCHIVE_RETENTION_MIN,
    SPOTIFY_ARCHIVE_VOLUME_SIZE_MB,
)
from bot.services.archive_service import (
    prepare_playlist_workspace,
    register_archived_delivery,
    save_partial_archive_after_cancel,
    send_volumes,
)
from bot.services.spotify_service import download_resolved_audio, resolve_track_info
from bot.session_store import ArchivedDeliveryState


@dataclass(frozen=True)
class SpotifyArchiveResult:
    """Outcome used by the selection handler to retain only failed tracks."""

    downloaded_count: int
    failed_indices: tuple[int, ...]
    archive_count: int
    volume_count: int
    cancelled: bool = False


def _track_label(track: dict[str, Any]) -> str:
    artist = str(track.get("artist") or "").strip()
    title = str(track.get("title") or "Utwór").strip()
    return f"{artist} — {title}" if artist else title


def _archive_slug(title: str) -> str:
    value = sanitize_filename(transliterate_to_ascii(title)).replace(" ", "_")
    return value[:60] or "spotify"


def _target_track_path(
    workspace: Path,
    source: Path,
    *,
    playlist_index: int,
    track: dict[str, Any],
) -> Path:
    """Build a stable, ordered and collision-safe archive member name."""

    stem = sanitize_filename(f"{playlist_index + 1:03d} - {_track_label(track)}")
    suffix = source.suffix or ".bin"
    candidate = workspace / f"{stem}{suffix}"
    duplicate = 2
    while candidate.exists():
        candidate = workspace / f"{stem} ({duplicate}){suffix}"
        duplicate += 1
    return candidate


async def _safe_status_edit(update, text: str, *, reply_markup=None) -> None:
    try:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    except Exception as exc:
        logging.debug("Spotify archive status edit failed: %s", exc)


async def execute_spotify_collection_archive_flow(
    update,
    context,
    *,
    chat_id: int,
    collection: dict[str, Any],
    selected_indices: list[int],
    audio_format: str,
    files_per_archive: int | None,
    executor: ThreadPoolExecutor,
) -> SpotifyArchiveResult:
    """Resolve, download, group, pack and send selected Spotify tracks.

    ``files_per_archive`` is 50, 100, or ``None`` for one logical archive.
    Every logical archive is additionally split into transport-safe volumes;
    on the normal MTProto deployment those volumes are capped at 1000 MiB.
    """

    selected = sorted(set(selected_indices))
    empty_result = SpotifyArchiveResult(0, tuple(selected), 0, 0)
    if not selected:
        await _safe_status_edit(update, "Najpierw zaznacz co najmniej jeden utwór.")
        return empty_result
    if files_per_archive is not None and files_per_archive not in {50, 100}:
        await _safe_status_edit(update, "Nieprawidłowy rozmiar paczki Spotify.")
        return empty_result
    if not is_7z_available():
        await _safe_status_edit(
            update,
            "Funkcja 7z jest niedostępna — administrator nie zainstalował p7zip-full.",
        )
        return empty_result

    tracks = collection.get("tracks") or []
    selected = [index for index in selected if 0 <= index < len(tracks)]
    if not selected:
        await _safe_status_edit(update, "Wybrane utwory nie są już dostępne w tej sesji.")
        return empty_result

    title = str(collection.get("title") or "Spotify")
    use_mtproto = mtproto_unavailability_reason() is None
    volume_size_mb = min(
        SPOTIFY_ARCHIVE_VOLUME_SIZE_MB,
        volume_size_for(use_mtproto),
    )
    batch_label = "całość" if files_per_archive is None else str(files_per_archive)
    descriptor = JobDescriptor(
        job_id="",
        chat_id=chat_id,
        kind="playlist_zip",
        label=f"Spotify 7z ({audio_format}, paczki {batch_label}) — start",
        started_at=datetime.now(),
    )
    cancellation = job_registry.register(chat_id, descriptor)
    workspace = prepare_playlist_workspace(chat_id, title, prefix="pl")
    lock_path = workspace / ".lock"
    lock_path.touch()

    downloaded: list[Path] = []
    failed_indices: list[int] = []
    failed_labels: list[str] = []
    packed_volumes: list[Path] = []
    archive_count = 0
    cancelled = False

    try:
        total = len(selected)
        for position, track_index in enumerate(selected, start=1):
            if cancellation.event.is_set():
                cancelled = True
                remaining = selected[position - 1 :]
                failed_indices.extend(remaining)
                failed_labels.extend(
                    f"{_track_label(tracks[index])} (anulowano)" for index in remaining
                )
                break

            track = tracks[track_index]
            label = _track_label(track)
            job_registry.update_label(
                cancellation.job_id,
                f"Spotify 7z [{position}/{total}] {label[:50]}",
            )
            await _safe_status_edit(
                update,
                f"Spotify → 7z ({audio_format.upper()})\n"
                f"[{position}/{total}] Dopasowywanie i pobieranie:\n{label}",
            )

            temp_dir = workspace / f".spotify_{track_index:04d}"
            temp_dir.mkdir(parents=True, exist_ok=True)
            try:
                resolved = await resolve_track_info(track, executor=executor)
                if not resolved:
                    failed_indices.append(track_index)
                    failed_labels.append(label)
                    continue
                downloaded_path = await download_resolved_audio(
                    resolved=resolved,
                    audio_format=audio_format,
                    output_dir=str(temp_dir),
                    executor=executor,
                )
                if not downloaded_path:
                    failed_indices.append(track_index)
                    failed_labels.append(label)
                    continue
                source = Path(downloaded_path)
                size_mb = source.stat().st_size / (1024 * 1024)
                if size_mb > MAX_ARCHIVE_ITEM_SIZE_MB:
                    failed_indices.append(track_index)
                    failed_labels.append(f"{label} (za duży: {size_mb:.0f} MB)")
                    continue
                target = _target_track_path(
                    workspace,
                    source,
                    playlist_index=track_index,
                    track=track,
                )
                shutil.move(str(source), target)
                downloaded.append(target)
            except Exception as exc:
                logging.error("Spotify archive download failed for %s: %s", label, exc)
                failed_indices.append(track_index)
                failed_labels.append(label)
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        if cancelled:
            await save_partial_archive_after_cancel(
                update,
                chat_id,
                workspace,
                downloaded,
                title,
                "audio",
                audio_format,
                use_mtproto,
                len(selected),
                files_per_archive=files_per_archive,
                volume_size_mb=volume_size_mb,
            )
            return SpotifyArchiveResult(
                len(downloaded), tuple(sorted(set(failed_indices))), 0, 0, True
            )

        if not downloaded:
            shutil.rmtree(workspace, ignore_errors=True)
            await _safe_status_edit(update, "Nie udało się pobrać żadnego utworu.")
            return SpotifyArchiveResult(
                0, tuple(sorted(set(failed_indices or selected))), 0, 0
            )

        batch_size = files_per_archive or len(downloaded)
        groups = [
            downloaded[index:index + batch_size]
            for index in range(0, len(downloaded), batch_size)
        ]
        archive_count = len(groups)
        base_name = compute_archive_basename(
            f"{_archive_slug(title)}_{audio_format}", datetime.now()
        )

        for group_index, group in enumerate(groups, start=1):
            if cancellation.event.is_set():
                cancelled = True
                break
            start = (group_index - 1) * batch_size + 1
            end = start + len(group) - 1
            group_name = base_name
            if len(groups) > 1:
                group_name = f"{base_name}_{start:03d}-{end:03d}"
            await _safe_status_edit(
                update,
                f"Pakowanie archiwum {group_index}/{archive_count} "
                f"({len(group)} utworów, wolumeny do {volume_size_mb} MB)...",
            )
            packed_volumes.extend(
                await pack_to_volumes(
                    group,
                    workspace / group_name,
                    volume_size_mb,
                    cancellation=cancellation,
                )
            )

        if cancellation.event.is_set() or cancelled:
            for volume in packed_volumes:
                volume.unlink(missing_ok=True)
            await save_partial_archive_after_cancel(
                update,
                chat_id,
                workspace,
                downloaded,
                title,
                "audio",
                audio_format,
                use_mtproto,
                len(selected),
                files_per_archive=files_per_archive,
                volume_size_mb=volume_size_mb,
            )
            return SpotifyArchiveResult(
                len(downloaded), tuple(sorted(set(failed_indices))), 0, 0, True
            )

        caption_prefix = f"{title} (Spotify {audio_format.upper()}, paczki {batch_label})"
        job_registry.update_label(
            cancellation.job_id,
            f"Spotify 7z — wysyłka [0/{len(packed_volumes)}]",
        )
        await _safe_status_edit(
            update,
            f"Pakowanie zakończone: {archive_count} archiwów, "
            f"{len(packed_volumes)} plików do wysłania.",
        )
        await send_volumes(
            context.bot,
            chat_id=chat_id,
            volumes=packed_volumes,
            caption_prefix=caption_prefix,
            use_mtproto=use_mtproto,
            status_cb=lambda text: _safe_status_edit(update, text),
            cancellation=cancellation,
        )

        delivery = ArchivedDeliveryState(
            workspace=workspace,
            volumes=packed_volumes,
            caption_prefix=caption_prefix,
            use_mtproto=use_mtproto,
            created_at=datetime.now(),
        )
        token = register_archived_delivery(chat_id, delivery)
        cancelled = cancellation.event.is_set()
        summary = [
            "⏹ Wysyłka zatrzymana." if cancelled else "Spotify: archiwa gotowe.",
            f"Pobrano: {len(downloaded)}/{len(selected)} utworów",
            f"Archiwa 7z: {archive_count}",
            f"Pliki wysłane do Telegrama: {len(packed_volumes)}",
            f"Rozmiar grupy: {batch_label}",
            f"Folder zostanie usunięty po {PLAYLIST_ARCHIVE_RETENTION_MIN} min.",
        ]
        if failed_labels:
            summary.extend(["", f"Nieudane utwory: {len(failed_labels)}"])
            summary.extend(f"- {label[:70]}" for label in failed_labels[:5])

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "Wyślij wszystkie paczki ponownie",
                callback_data=f"arc_resend_{token}_0",
            )],
            [InlineKeyboardButton("Usuń teraz", callback_data=f"arc_purge_{token}")],
        ])
        await _safe_status_edit(update, "\n".join(summary), reply_markup=keyboard)
        return SpotifyArchiveResult(
            len(downloaded),
            tuple(sorted(set(failed_indices))),
            archive_count,
            len(packed_volumes),
            cancelled,
        )
    except RuntimeError as exc:
        if str(exc) == "cancelled":
            for volume in packed_volumes:
                volume.unlink(missing_ok=True)
            await save_partial_archive_after_cancel(
                update,
                chat_id,
                workspace,
                downloaded,
                title,
                "audio",
                audio_format,
                use_mtproto,
                len(selected),
                files_per_archive=files_per_archive,
                volume_size_mb=volume_size_mb,
            )
            return SpotifyArchiveResult(
                len(downloaded), tuple(sorted(set(failed_indices))), 0, 0, True
            )
        logging.error("Spotify archive flow failed: %s", exc)
        await _safe_status_edit(update, "Pakowanie lub wysyłka archiwum nie powiodły się.")
        return SpotifyArchiveResult(
            len(downloaded), tuple(selected), 0, 0
        )
    except Exception:
        logging.exception("Unexpected Spotify archive flow failure")
        await _safe_status_edit(update, "Pakowanie lub wysyłka archiwum nie powiodły się.")
        return SpotifyArchiveResult(
            len(downloaded), tuple(selected), 0, 0
        )
    finally:
        lock_path.unlink(missing_ok=True)
        job_registry.unregister(cancellation.job_id)
