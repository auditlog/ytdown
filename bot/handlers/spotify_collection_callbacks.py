"""Telegram callbacks for selecting Spotify album/playlist tracks."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.handlers.common_ui import (
    build_spotify_archive_batch_view,
    build_spotify_collection_view,
    safe_edit_message,
    progress_stop_markup,
)
from bot.handlers.spotify_callbacks import download_spotify_resolved
from bot.jobs import JobDescriptor, job_registry
from bot.services.spotify_archive_service import execute_spotify_collection_archive_flow
from bot.services.spotify_service import resolve_track_info
from bot.session_context import get_session_context_value, set_session_context_value

_executor = ThreadPoolExecutor(max_workers=2)


def _get_raw_collection(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Return the session object itself (not a copy) for identity checks."""

    return get_session_context_value(
        context,
        chat_id,
        "spotify_collection",
        legacy_key="spotify_collection",
    )


def _get_collection(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> dict | None:
    collection = _get_raw_collection(context, chat_id)
    return dict(collection) if isinstance(collection, dict) else None


def _store_collection(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    collection: dict,
) -> None:
    set_session_context_value(
        context,
        chat_id,
        "spotify_collection",
        collection,
        legacy_key="spotify_collection",
    )


def _store_collection_if_current(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    collection: dict,
    expected,
) -> bool:
    """Store a job's collection only if the session still holds ``expected``.

    Long jobs run while the user may send a newer album/playlist link; identity
    (``is``) keeps the finishing job from overwriting that newer collection.
    See also: bot/session_context.py ``clear_session_context_value_if``.
    """

    if _get_raw_collection(context, chat_id) is not expected:
        return False
    _store_collection(context, chat_id, collection)
    return True


async def _render_collection(update: Update, collection: dict, *, prefix: str = "") -> None:
    text, keyboard = build_spotify_collection_view(collection)
    if prefix:
        text = f"{prefix}\n\n{text}"
    await safe_edit_message(
        update.callback_query,
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def _render_archive_batch_choice(
    update: Update,
    collection: dict,
    *,
    audio_format: str,
) -> None:
    if not collection.get("selected"):
        await _render_collection(
            update,
            collection,
            prefix="Najpierw zaznacz co najmniej jeden utwór.",
        )
        return
    text, keyboard = build_spotify_archive_batch_view(
        collection,
        audio_format=audio_format,
    )
    await safe_edit_message(
        update.callback_query,
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def _download_selected(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    collection: dict,
    *,
    audio_format: str,
    session_collection,
) -> None:
    chat_id = update.effective_chat.id
    tracks = collection.get("tracks") or []
    selected = sorted(
        index
        for index in {int(value) for value in collection.get("selected") or []}
        if 0 <= index < len(tracks)
    )
    if not selected:
        await _render_collection(update, collection, prefix="Najpierw zaznacz co najmniej jeden utwór.")
        return

    descriptor = JobDescriptor(
        job_id="",
        chat_id=chat_id,
        kind="playlist_legacy",
        label=f"Spotify → YT Music ({len(selected)} utworów)",
        started_at=datetime.now(),
    )
    cancellation = job_registry.register(chat_id, descriptor)
    succeeded = 0
    failed: list[int] = []
    cancelled = False

    try:
        for position, track_index in enumerate(selected, start=1):
            if cancellation.event.is_set():
                cancelled = True
                failed.extend(selected[position - 1 :])
                break

            track = tracks[track_index]
            label = f"{track.get('artist', '')} — {track.get('title', '?')}".strip(" —")
            job_registry.update_label(
                cancellation.job_id,
                f"Spotify → YT Music [{position}/{len(selected)}] {label[:50]}",
            )
            await safe_edit_message(
                update.callback_query,
                f"[{position}/{len(selected)}] Dopasowywanie w YT Music:\n{label}",
                reply_markup=progress_stop_markup(cancellation),
            )

            resolved = await resolve_track_info(track, executor=_executor)
            if not resolved:
                failed.append(track_index)
                continue

            success = await download_spotify_resolved(
                update,
                context,
                resolved,
                audio_format=audio_format,
                transcribe=False,
            )
            if success:
                succeeded += 1
            else:
                failed.append(track_index)
    finally:
        job_registry.unregister(cancellation.job_id)

    collection["selected"] = failed
    summary = (
        f"{'Zatrzymano. ' if cancelled else ''}Pobrano: {succeeded}/{len(selected)}."
    )
    if not _store_collection_if_current(context, chat_id, collection, session_collection):
        # A newer collection replaced this one during the job: report the
        # result without redrawing the old track list over the newer menu.
        if failed:
            summary += f" Niepowodzenia: {len(failed)}."
        await safe_edit_message(update.callback_query, summary)
        return
    if failed:
        summary += f" Niepowodzenia: {len(failed)} — pozostawiłem je zaznaczone do ponowienia."
    await _render_collection(update, collection, prefix=summary)


async def handle_spotify_collection_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    data: str,
) -> None:
    """Update Spotify selection state or download the selected tracks."""

    chat_id = update.effective_chat.id
    # Captured before copying: long jobs store their result only while the
    # session still holds this exact object.
    session_collection = _get_raw_collection(context, chat_id)
    collection = _get_collection(context, chat_id)
    if not collection:
        await update.callback_query.edit_message_text(
            "Sesja wyboru Spotify wygasła. Wyślij link do albumu lub playlisty ponownie."
        )
        return

    tracks = collection.get("tracks") or []
    selected = {int(value) for value in collection.get("selected") or []}

    if data.startswith("spc_t_"):
        try:
            index = int(data.removeprefix("spc_t_"))
        except ValueError:
            index = -1
        if not 0 <= index < len(tracks):
            await update.callback_query.edit_message_text("Nieprawidłowy wybór utworu.")
            return
        if index in selected:
            selected.remove(index)
        else:
            selected.add(index)
        collection["selected"] = sorted(selected)
    elif data.startswith("spc_p_"):
        try:
            collection["page"] = max(0, int(data.removeprefix("spc_p_")))
        except ValueError:
            collection["page"] = 0
    elif data == "spc_all":
        collection["selected"] = list(range(len(tracks)))
    elif data == "spc_clear":
        collection["selected"] = []
    elif data in {"spc_pack_mp3", "spc_pack_m4a"}:
        await _render_archive_batch_choice(
            update,
            collection,
            audio_format=data.rsplit("_", 1)[-1],
        )
        return
    elif data == "spc_pack_back":
        await _render_collection(update, collection)
        return
    elif data.startswith("spc_pack_"):
        parts = data.split("_")
        if len(parts) != 4 or parts[2] not in {"mp3", "m4a"}:
            await update.callback_query.edit_message_text("Nieprawidłowy wybór paczki Spotify.")
            return
        try:
            files_per_archive = None if parts[3] == "all" else int(parts[3])
        except ValueError:
            await update.callback_query.edit_message_text("Nieprawidłowy wybór paczki Spotify.")
            return
        result = await execute_spotify_collection_archive_flow(
            update,
            context,
            chat_id=chat_id,
            collection=collection,
            selected_indices=sorted(selected),
            audio_format=parts[2],
            files_per_archive=files_per_archive,
            executor=_executor,
        )
        collection["selected"] = list(result.failed_indices)
        _store_collection_if_current(context, chat_id, collection, session_collection)
        return
    elif data in {"spc_dl_mp3", "spc_dl_m4a"}:
        await _download_selected(
            update,
            context,
            collection,
            audio_format=data.rsplit("_", 1)[-1],
            session_collection=session_collection,
        )
        return
    else:
        await update.callback_query.edit_message_text("Nieobsługiwana akcja Spotify.")
        return

    _store_collection(context, chat_id, collection)
    await _render_collection(update, collection)
