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
from bot.handlers.common_ui import progress_stop_markup
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
from bot.services.spotify_service import (
    download_resolved_audio,
    resolve_track_info_detailed,
)
from bot.session_store import ArchivedDeliveryState


@dataclass(frozen=True)
class SpotifyArchiveFailure:
    """One failed track with enough context for logs and a user report."""

    track_index: int
    label: str
    stage: str
    reason: str


@dataclass(frozen=True)
class SpotifyArchiveResult:
    """Outcome used by the selection handler to retain only failed tracks."""

    downloaded_count: int
    failed_indices: tuple[int, ...]
    archive_count: int
    volume_count: int
    cancelled: bool = False
    failures: tuple[SpotifyArchiveFailure, ...] = ()


def _track_label(track: dict[str, Any]) -> str:
    artist = str(track.get("artist") or "").strip()
    title = str(track.get("title") or "Utwór").strip()
    return f"{artist} — {title}" if artist else title


def _archive_slug(title: str) -> str:
    value = sanitize_filename(transliterate_to_ascii(title)).replace(" ", "_")
    return value[:60] or "spotify"


def _compact_exception(exc: Exception, *, limit: int = 240) -> str:
    """Return a one-line, bounded exception description safe for a TXT report."""

    detail = " ".join(str(exc).split())
    if detail:
        return f"{type(exc).__name__}: {detail[:limit]}"
    return type(exc).__name__


def _write_failure_report(
    workspace: Path,
    *,
    collection_title: str,
    selected_count: int,
    downloaded_count: int,
    failures: list[SpotifyArchiveFailure],
) -> Path:
    """Write a complete UTF-8 failure report retained with the archive workspace."""

    report_path = workspace / "spotify_nieudane_utwory.txt"
    lines = [
        "Spotify — raport nieudanych utworów",
        f"Kolekcja: {collection_title}",
        f"Wybrano: {selected_count}",
        f"Pobrano: {downloaded_count}",
        f"Nieudane: {len(failures)}",
        "",
    ]
    for number, failure in enumerate(failures, start=1):
        lines.extend(
            [
                f"{number}. [pozycja {failure.track_index + 1}] {failure.label}",
                f"   Etap: {failure.stage}",
                f"   Powód: {failure.reason}",
                "",
            ]
        )
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def _report_message_chunks(text: str, *, limit: int = 3500) -> list[str]:
    """Split a report on line boundaries for the Telegram text fallback."""

    chunks: list[str] = []
    current: list[str] = []
    current_size = 0
    for line in text.splitlines():
        line = line[:limit]
        extra = len(line) + (1 if current else 0)
        if current and current_size + extra > limit:
            chunks.append("\n".join(current))
            current = []
            current_size = 0
        current.append(line)
        current_size += len(line) + (1 if len(current) > 1 else 0)
    if current:
        chunks.append("\n".join(current))
    return chunks


async def _send_failure_report(bot, *, chat_id: int, report_path: Path) -> str | None:
    """Send the report as TXT, falling back to complete chunked messages."""

    try:
        with report_path.open("rb") as handle:
            await bot.send_document(
                chat_id=chat_id,
                document=handle,
                filename=report_path.name,
                caption="Pełna lista nieudanych utworów Spotify wraz z przyczynami.",
                read_timeout=60,
                write_timeout=60,
            )
        return "plik TXT"
    except Exception as exc:
        logging.warning("Could not send Spotify failure report as a document: %s", exc)

    try:
        report_text = report_path.read_text(encoding="utf-8")
        for chunk in _report_message_chunks(report_text):
            await bot.send_message(chat_id=chat_id, text=chunk)
        return "osobne wiadomości"
    except Exception as exc:
        logging.error("Could not send Spotify failure report fallback: %s", exc)
        return None


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
    failures: list[SpotifyArchiveFailure] = []
    packed_volumes: list[Path] = []
    archive_count = 0
    cancelled = False

    def record_failure(
        track_index: int,
        label: str,
        *,
        stage: str,
        reason: str,
    ) -> None:
        failed_indices.append(track_index)
        failure = SpotifyArchiveFailure(track_index, label, stage, reason)
        failures.append(failure)
        logging.warning(
            "Spotify archive item failed: index=%d label=%r stage=%s reason=%s",
            track_index,
            label,
            stage,
            reason,
        )

    try:
        total = len(selected)
        for position, track_index in enumerate(selected, start=1):
            if cancellation.event.is_set():
                cancelled = True
                remaining = selected[position - 1 :]
                for index in remaining:
                    record_failure(
                        index,
                        _track_label(tracks[index]),
                        stage="anulowanie",
                        reason="Operacja została zatrzymana przed rozpoczęciem utworu.",
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
                reply_markup=progress_stop_markup(cancellation),
            )

            temp_dir = workspace / f".spotify_{track_index:04d}"
            temp_dir.mkdir(parents=True, exist_ok=True)
            try:
                try:
                    resolution = await resolve_track_info_detailed(track, executor=executor)
                except Exception as exc:
                    record_failure(
                        track_index,
                        label,
                        stage="dopasowanie w YouTube",
                        reason=_compact_exception(exc),
                    )
                    continue
                if not resolution.resolved:
                    record_failure(
                        track_index,
                        label,
                        stage="dopasowanie w YouTube",
                        reason=(
                            resolution.failure_detail
                            or "Nie znaleziono wiarygodnego dopasowania."
                        ),
                    )
                    continue
                try:
                    downloaded_path = await download_resolved_audio(
                        resolved=resolution.resolved,
                        audio_format=audio_format,
                        output_dir=str(temp_dir),
                        executor=executor,
                    )
                except Exception as exc:
                    record_failure(
                        track_index,
                        label,
                        stage="pobieranie audio",
                        reason=_compact_exception(exc),
                    )
                    continue
                if not downloaded_path:
                    record_failure(
                        track_index,
                        label,
                        stage="pobieranie audio",
                        reason="Pobieranie zakończyło się bez pliku wynikowego.",
                    )
                    continue
                source = Path(downloaded_path)
                size_mb = source.stat().st_size / (1024 * 1024)
                if size_mb > MAX_ARCHIVE_ITEM_SIZE_MB:
                    record_failure(
                        track_index,
                        label,
                        stage="kontrola rozmiaru",
                        reason=(
                            f"Plik ma {size_mb:.0f} MB i przekracza limit "
                            f"{MAX_ARCHIVE_ITEM_SIZE_MB:.0f} MB."
                        ),
                    )
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
                record_failure(
                    track_index,
                    label,
                    stage="przygotowanie pliku do archiwum",
                    reason=_compact_exception(exc),
                )
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
                len(downloaded),
                tuple(sorted(set(failed_indices))),
                0,
                0,
                True,
                tuple(failures),
            )

        if not downloaded:
            failure_report_delivery = None
            if failures:
                report_path = _write_failure_report(
                    workspace,
                    collection_title=title,
                    selected_count=len(selected),
                    downloaded_count=0,
                    failures=failures,
                )
                failure_report_delivery = await _send_failure_report(
                    context.bot,
                    chat_id=chat_id,
                    report_path=report_path,
                )
            shutil.rmtree(workspace, ignore_errors=True)
            report_note = (
                f" Pełny raport wysłano jako {failure_report_delivery}."
                if failure_report_delivery
                else " Nie udało się wysłać pełnego raportu."
            )
            await _safe_status_edit(
                update,
                f"Nie udało się pobrać żadnego utworu.{report_note}",
            )
            return SpotifyArchiveResult(
                0,
                tuple(sorted(set(failed_indices or selected))),
                0,
                0,
                failures=tuple(failures),
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
                reply_markup=progress_stop_markup(cancellation),
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
                len(downloaded),
                tuple(sorted(set(failed_indices))),
                0,
                0,
                True,
                tuple(failures),
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
            reply_markup=progress_stop_markup(cancellation),
        )
        await send_volumes(
            context.bot,
            chat_id=chat_id,
            volumes=packed_volumes,
            caption_prefix=caption_prefix,
            use_mtproto=use_mtproto,
            status_cb=lambda text: _safe_status_edit(
                update, text, reply_markup=progress_stop_markup(cancellation),
            ),
            cancellation=cancellation,
        )

        failure_report_delivery = None
        if failures:
            report_path = _write_failure_report(
                workspace,
                collection_title=title,
                selected_count=len(selected),
                downloaded_count=len(downloaded),
                failures=failures,
            )
            failure_report_delivery = await _send_failure_report(
                context.bot,
                chat_id=chat_id,
                report_path=report_path,
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
            f"Logiczne archiwa 7z: {archive_count}",
            f"Wolumeny wysłane do Telegrama: {len(packed_volumes)}",
            f"Rozmiar grupy: {batch_label}",
            "Każde archiwum zaczyna numerację wolumenów od .7z.001.",
            f"Folder zostanie usunięty po {PLAYLIST_ARCHIVE_RETENTION_MIN} min.",
        ]
        if failures:
            summary.extend(["", f"Nieudane utwory: {len(failures)}"])
            summary.extend(f"- {failure.label[:70]}" for failure in failures[:5])
            if len(failures) > 5:
                summary.append(f"- … oraz {len(failures) - 5} kolejnych")
            if failure_report_delivery:
                summary.append(
                    f"Pełna lista z przyczynami została wysłana jako {failure_report_delivery}."
                )
            else:
                summary.append("Nie udało się wysłać pełnego raportu; szczegóły są w logach.")

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
            tuple(failures),
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
                len(downloaded),
                tuple(sorted(set(failed_indices))),
                0,
                0,
                True,
                tuple(failures),
            )
        logging.error("Spotify archive flow failed: %s", exc)
        await _safe_status_edit(update, "Pakowanie lub wysyłka archiwum nie powiodły się.")
        return SpotifyArchiveResult(
            len(downloaded), tuple(selected), 0, 0, failures=tuple(failures)
        )
    except Exception:
        logging.exception("Unexpected Spotify archive flow failure")
        await _safe_status_edit(update, "Pakowanie lub wysyłka archiwum nie powiodły się.")
        return SpotifyArchiveResult(
            len(downloaded), tuple(selected), 0, 0, failures=tuple(failures)
        )
    finally:
        lock_path.unlink(missing_ok=True)
        job_registry.unregister(cancellation.job_id)
