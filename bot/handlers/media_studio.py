"""Telegram entry points for source-grounded media workflows."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot.config import DOWNLOAD_PATH
from bot.handlers.common_ui import send_long_message
from bot.jobs import JobDescriptor, busy_chats, is_chat_busy, job_registry
from bot.media.analysis import polish_captions, select_fragments
from bot.media.ingest import import_source
from bot.media.radar import add_watch, check_watches, radar_config, update_watch
from bot.media.render import assemble_audio, render_short, source_map
from bot.media.store import MediaError, media_store
from bot.media.visual import analyze_gallery, analyze_video_frames
from bot.runtime import get_authorized_user_ids_for, record_download_for
from bot.security_pin import is_user_blocked
from bot.security_policy import extract_url_from_text
from bot.security_throttling import RATE_LIMIT_MESSAGE, check_rate_limit
from bot.session_context import (
    get_session_context_value,
    get_session_value,
    set_session_context_value,
)
from bot.session_store import user_urls

HELP = (
    "🎛 Studio mediów\n\n"
    "1. Wyślij link lub plik, potem /zapisz (audio) albo /zapisz video. "
    "Możesz też podać link: /zapisz https://…\n"
    "2. /materialy — wybierz do 3 nagrań do montażu, oznacz przerobione lub usuń.\n"
    "3. /montaz 20 temat — MP3 do 20 minut, z oryginalnych wypowiedzi i mapą źródeł.\n"
    "4. /klipy temat — do 3 pionowych filmów po 30–90 sekund, z napisami. "
    "Najpierw zapisz materiał jako video.\n"
    "5. /analizuj polecenie — zdjęcia z ostatniej galerii albo 12 próbek obrazu "
    "z wybranego zapisanego filmu. /analizuj ID polecenie wybiera konkretny materiał.\n"
    "6. /radar — obserwowanie nowych materiałów na wybrany temat.\n\n"
    "Zapis i analizy mogą korzystać z płatnego API OpenRouter. "
    "Napisy źródłowe mają pierwszeństwo przed transkrypcją AI. "
    "Zapisane materiały są dostępne przez 7 dni, do 16 nagrań na czat, "
    "do 2 godzin i 512 MB każde. /stop zatrzymuje trwającą pracę."
)


def source_tag(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def _allowed(context, user_id):
    return user_id in get_authorized_user_ids_for(context) and not is_user_blocked(user_id)


async def _auth(update, context):
    if _allowed(context, update.effective_user.id):
        return True
    await context.bot.send_message(
        chat_id=update.effective_chat.id, text="Wymagane uwierzytelnienie — wyślij kod PIN."
    )
    return False


def _args(update):
    message = update.effective_message
    return (message.text or "").partition(" ")[2].strip()


def _get(context, owner, field, default=None):
    return get_session_context_value(context, owner, field, legacy_key=field, default=default)


def _selected(context, owner):
    sources = media_store.list(owner)
    chosen = _get(context, owner, "media_selection")
    selected = (
        [source for source in sources if source["id"] in chosen]
        if chosen is not None
        else [source for source in sources if source["kind"] != "gallery"][:3]
    )
    if not selected or len(selected) > 3:
        raise MediaError("Wybierz od 1 do 3 nagrań przez /materialy.")
    return selected


async def _job(update, context, label, operation):
    owner = update.effective_chat.id
    descriptor = JobDescriptor("", owner, "media", label, datetime.now())
    cancellation = job_registry.register(owner, descriptor)
    progress = None
    try:
        progress = await context.bot.send_message(
            chat_id=owner,
            text=label,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "Zatrzymaj", callback_data=f"stop_{cancellation.job_id}"
                        )
                    ],
                ]
            ),
        )

        async def status(text):
            if cancellation.event.is_set():
                raise MediaError("Operacja została zatrzymana.")
            if not _allowed(context, update.effective_user.id):
                cancellation.event.set()
                raise MediaError("Sesja wygasła. Zaloguj się ponownie.")
            try:
                await progress.edit_text(
                    text,
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "Zatrzymaj", callback_data=f"stop_{cancellation.job_id}"
                                )
                            ],
                        ]
                    ),
                )
            except Exception:
                logging.debug("Media progress could not be delivered")

        data = context.application.bot_data
        slots = data.setdefault("media_work_slots", asyncio.Semaphore(2))
        async with slots:
            if not _allowed(context, update.effective_user.id):
                raise MediaError("Sesja wygasła. Zaloguj się ponownie.")
            await operation(cancellation, status)
        await progress.edit_text(
            "Gotowe." if not cancellation.event.is_set() else "Operacja zatrzymana."
        )
    except MediaError as exc:
        await context.bot.send_message(chat_id=owner, text=str(exc))
    except asyncio.CancelledError:
        cancellation.event.set()
        raise
    except Exception as exc:
        logging.exception("Media workflow failed: %s", type(exc).__name__)
        await context.bot.send_message(
            chat_id=owner, text="Nie udało się ukończyć operacji. Spróbuj ponownie."
        )
    finally:
        if progress is not None:
            try:
                await progress.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
        job_registry.unregister(cancellation.job_id)
        busy_chats.discard(owner)


async def _launch(update, context, label, operation, *, guarded=False):
    owner = update.effective_chat.id
    if not guarded:
        if not await _auth(update, context):
            return
        if is_chat_busy(owner):
            await context.bot.send_message(
                chat_id=owner, text="Trwa już inna operacja. Poczekaj albo użyj /stop."
            )
            return
        if not check_rate_limit(update.effective_user.id):
            await context.bot.send_message(chat_id=owner, text=RATE_LIMIT_MESSAGE)
            return
        busy_chats.add(owner)
        # PTB tracks these tasks during shutdown; commands remain responsive.
        job = _job(update, context, label, operation)
        try:
            context.application.create_task(job, update=update)
        except BaseException:
            job.close()
            busy_chats.discard(owner)
            raise
    else:
        await _job(update, context, label, operation)


async def studio_command(update, context):
    if await _auth(update, context):
        await context.bot.send_message(chat_id=update.effective_chat.id, text=HELP)


async def materials_command(update, context):
    if not await _auth(update, context):
        return
    owner = update.effective_chat.id
    sources = await asyncio.to_thread(media_store.list, owner)
    chosen = _get(context, owner, "media_selection")
    if chosen is None:
        chosen = [source["id"] for source in sources if source["kind"] != "gallery"][:3]
    lines, keyboard = ["📚 Materiały (7 dni)\n"], []
    for source in sources:
        selected = source["id"] in chosen
        lines.append(
            f"{'✅' if selected else '▫️'} {source['title'][:100]} ({source['kind']})\nID: {source['id']}"
        )
        keyboard.append(
            [
                InlineKeyboardButton(
                    "Odznacz" if selected else "Wybierz",
                    callback_data=f"media_toggle_{source['id']}",
                ),
                InlineKeyboardButton(
                    "Przerobione ✓" if source.get("reviewed") else "Oznacz przerobione",
                    callback_data=f"media_review_{source['id']}",
                ),
                InlineKeyboardButton("Usuń", callback_data=f"media_delete_{source['id']}"),
            ]
        )
    if not sources:
        lines.append("Wyślij link lub nagranie, a następnie /zapisz. /studio pokazuje instrukcje.")
    else:
        lines.append("\nWybierz do 3 nagrań. Potem /montaz 20 temat albo /klipy temat.")
    await context.bot.send_message(
        chat_id=owner,
        text="\n".join(lines),
        reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None,
    )


def _capture_input(update, context, *, video=False, explicit_url=""):
    owner = update.effective_chat.id
    url = explicit_url or get_session_value(context, owner, "current_url", user_urls)
    local = _get(context, owner, "video_file_path" if video else "audio_file_path")
    if video and not explicit_url and not local and _get(context, owner, "audio_file_path"):
        raise MediaError("Przesłany materiał zawiera tylko audio. Wyślij plik lub link do wideo.")
    # An explicit URL always takes precedence over a previously uploaded file.
    if not explicit_url and local:
        path = Path(local)
        base = (Path(DOWNLOAD_PATH) / str(owner)).resolve()
        if path.is_symlink() or not path.resolve().is_relative_to(base):
            raise MediaError("Plik nie należy do tego czatu.")
        return {
            "local_path": path,
            "title": _get(context, owner, "audio_file_title", "Nagranie"),
            "video": video,
        }
    if not url:
        raise MediaError("Najpierw wyślij link lub plik. Możesz też użyć /zapisz URL.")
    return {"url": url, "video": video}


async def _save(update, context, params, *, guarded=False):
    owner = update.effective_chat.id

    async def run(cancellation, status):
        source = await import_source(
            media_store, owner, **params, cancellation=cancellation, status=status
        )
        await status("Materiał zapisany. Przygotowywanie potwierdzenia…")
        set_session_context_value(
            context, owner, "media_selection", [source["id"]], legacy_key="media_selection"
        )
        await context.bot.send_message(
            chat_id=owner,
            text=f"Zapisano: {source['title']}\nFragmentów: {len(source['segments'])}.\n"
            "Teraz /montaz 20 temat, /klipy temat lub /materialy.",
        )

    await _launch(
        update, context, "Zapisywanie materiału ze znacznikami czasu…", run, guarded=guarded
    )


async def save_command(update, context):
    if not await _auth(update, context):
        return
    args = _args(update)
    video = args.split(" ", 1)[0].lower() == "video"
    try:
        if re.search(r"https?://", args) and not extract_url_from_text(args):
            raise MediaError("Podaj link z obsługiwanej platformy.")
        params = _capture_input(
            update, context, video=video, explicit_url=extract_url_from_text(args) or ""
        )
        await _save(update, context, params)
    except MediaError as exc:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=str(exc))


async def _send_document(context, owner, path, caption=None):
    with open(path, "rb") as file_obj:
        await context.bot.send_document(
            chat_id=owner, document=file_obj, caption=caption, read_timeout=120, write_timeout=120
        )


async def _edit(update, context, *, shorts=False):
    if not await _auth(update, context):
        return
    owner = update.effective_chat.id
    args = _args(update)
    try:
        if shorts:
            budget, query = 270, args
        else:
            amount, separator, query = args.partition(" ")
            if not separator or not amount.isdigit() or not 1 <= int(amount) <= 30:
                raise MediaError(
                    "Użyj /montaz MINUTY temat, np. /montaz 20 ocena agentów AI. Limit: 1–30 minut."
                )
            budget = int(amount) * 60
        sources = _selected(context, owner)
        if shorts and any(source["kind"] != "video" for source in sources):
            raise MediaError("Do klipów wybierz nagrania zapisane przez /zapisz video.")
        if len(query.strip()) < 3:
            raise MediaError("Opisz, jakie fragmenty mam wybrać.")

        async def run(cancellation, status):
            await status("Wybieranie kompletnych wypowiedzi pasujących do tematu…")
            picks = await asyncio.to_thread(select_fragments, sources, query, budget, shorts=shorts)
            await status("Renderowanie klipów…" if shorts else "Montaż oryginalnych wypowiedzi…")
            with TemporaryDirectory(prefix="render_", dir=media_store.chat_dir(owner)) as temporary:
                workspace = Path(temporary)
                if shorts:
                    for index, pick in enumerate(picks, 1):
                        await status(f"Przygotowywanie polskich napisów {index}/{len(picks)}…")
                        captions = await asyncio.to_thread(polish_captions, pick.segments)
                        await status(f"Renderowanie klipu {index}/{len(picks)}…")
                        path = await render_short(
                            media_store,
                            owner,
                            pick,
                            workspace / f"clip_{index}.mp4",
                            cancellation=cancellation,
                            captions=captions,
                        )
                        await status(f"Wysyłanie klipu {index}/{len(picks)}…")
                        with open(path, "rb") as file_obj:
                            await context.bot.send_video(
                                chat_id=owner,
                                video=file_obj,
                                caption=pick.reason[:900],
                                supports_streaming=True,
                                read_timeout=120,
                                write_timeout=120,
                            )
                else:
                    path = await assemble_audio(
                        media_store,
                        owner,
                        picks,
                        workspace / "montaz.mp3",
                        cancellation=cancellation,
                    )
                    await status("Wysyłanie montażu i mapy źródeł…")
                    with open(path, "rb") as file_obj:
                        await context.bot.send_audio(
                            chat_id=owner,
                            audio=file_obj,
                            title=query[:80],
                            caption="Montaż oryginalnych wypowiedzi. Mapa źródeł w załączniku.",
                            read_timeout=120,
                            write_timeout=120,
                        )
                mapping = workspace / "mapa_zrodel.md"
                mapping.write_text(source_map(sources, picks, separate=shorts), encoding="utf-8")
                await _send_document(context, owner, mapping)
                record_download_for(
                    context,
                    owner,
                    query,
                    sources[0]["url"],
                    "media_clips" if shorts else "media_montage",
                    0,
                )

        await _launch(
            update,
            context,
            "Przygotowywanie klipów…" if shorts else "Przygotowywanie montażu…",
            run,
        )
    except MediaError as exc:
        await context.bot.send_message(chat_id=owner, text=str(exc))


async def montage_command(update, context):
    await _edit(update, context)


async def clips_command(update, context):
    await _edit(update, context, shorts=True)


async def _analyze(update, context, *, guarded=False):
    owner = update.effective_chat.id
    query = _args(update) if not update.callback_query else ""
    explicit_source = None
    candidate, _, request = query.partition(" ")
    if re.fullmatch(r"[a-f0-9]{16}", candidate):
        explicit_source = media_store.get(owner, candidate)
        query = request.strip()
    query = (
        query
        or "Odczytaj tekst i objaśnij treść, wykresy i porównania. Wskaż źródła każdego wniosku."
    )
    if len(query) > 1000:
        raise MediaError("Polecenie analizy może mieć do 1000 znaków.")
    gallery = _get(context, owner, "tiktok_gallery") or get_session_context_value(
        context, owner, "instagram_carousel", legacy_key="ig_carousel"
    )
    url = get_session_value(context, owner, "current_url", user_urls) or ""
    source = explicit_source
    if source is not None:
        gallery = None
        if source["kind"] == "gallery":
            path = media_store.directory(owner, source["id"]) / "analysis.md"
            if not path.is_file():
                raise MediaError("Analiza galerii wygasła. Wyślij link ponownie.")
            await _send_document(context, owner, path, "Zapisana analiza galerii.")
            return
        if source["kind"] != "video":
            raise MediaError("Analiza obrazu wymaga galerii lub materiału video.")
    elif not gallery:
        source = next((item for item in _selected(context, owner) if item["kind"] == "video"), None)
        if source is None:
            raise MediaError("Wyślij link do galerii lub wybierz nagranie zapisane jako video.")

    async def run(cancellation, status):
        await status("Odczytywanie obrazu i przygotowywanie analizy…")
        if gallery:
            _, output = await analyze_gallery(
                media_store, owner, gallery=gallery, url=url, query=query, cancellation=cancellation
            )
            await status("Wysyłanie analizy zdjęć…")
            await _send_document(context, owner, output, "Analiza z odnośnikami do stron galerii.")
        else:
            with TemporaryDirectory(prefix="frames_", dir=media_store.chat_dir(owner)) as temporary:
                output = await analyze_video_frames(
                    media_store, owner, source, Path(temporary), query, cancellation=cancellation
                )
                await status("Wysyłanie analizy próbek obrazu…")
                await _send_document(
                    context, owner, output, "Analiza 12 próbek obrazu ze znacznikami czasu."
                )

    await _launch(update, context, "Analiza obrazu…", run, guarded=guarded)


async def analyze_command(update, context):
    if not await _auth(update, context):
        return
    try:
        await _analyze(update, context)
    except MediaError as exc:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=str(exc))


async def radar_command(update, context):
    if not await _auth(update, context):
        return
    owner = update.effective_chat.id
    args = _args(update)
    action, _, rest = args.partition(" ")
    if not args or action == "status":
        config = radar_config(media_store, owner)
        lines = [
            "📡 Radar tematów",
            "",
            "/radar dodaj URL | temat",
            "/radar sprawdz",
            "/radar pauza ID, /radar wznow ID, /radar usun ID",
            "",
            "/radar ai ID on — zezwól na transkrypcję, off — wyłącz",
            "",
            "Obserwuję nowe pozycje z kanałów/playlist YouTube i publicznych RSS. "
            "Domyślnie analizuję dostępne napisy. Opcjonalna transkrypcja AI: maksymalnie "
            "1 nagranie do godziny na 24 godziny na czat. "
            "Limit: 5 źródeł i 6 analiz AI na 24 godziny. Sprawdzanie co 4 godziny.",
        ]
        for watch in config["watches"]:
            lines += [
                f"\n{watch['id']} — {'aktywny' if watch['enabled'] else 'pauza'}",
                watch["topic"],
                watch["url"],
                "Transkrypcja AI: " + ("włączona" if watch.get("transcribe") else "wyłączona"),
                watch.get("last_error") or "",
            ]
        if not config["watches"]:
            lines.append("\nNie obserwuję jeszcze żadnego źródła.")
        await send_long_message(context.bot, owner, "\n".join(lines), parse_mode=None)
        return

    async def run(cancellation, status):
        if action == "dodaj":
            url, separator, topic = rest.partition("|")
            if not separator:
                raise MediaError("Użyj /radar dodaj URL | temat.")
            identity = await asyncio.to_thread(
                add_watch, media_store, owner, update.effective_user.id, url.strip(), topic.strip()
            )
            await context.bot.send_message(
                chat_id=owner,
                text=f"Dodano obserwację {identity}. "
                "Będę analizować nowe pozycje. Dotychczasowe odcinki są oznaczone jako znane.",
            )
        elif action == "ai":
            identity, _, choice = rest.strip().partition(" ")
            if choice not in {"on", "off"}:
                raise MediaError("Użyj /radar ai ID on albo /radar ai ID off.")
            update_watch(media_store, owner, identity, transcribe=choice == "on")
            await context.bot.send_message(
                chat_id=owner,
                text=(
                    "Włączono transkrypcję AI. Limit: 1 nagranie do godziny na 24 godziny "
                    "na czat, rozliczane przez OpenRouter."
                )
                if choice == "on"
                else "Wyłączono transkrypcję AI. Radar będzie korzystał z dostępnych napisów.",
            )
        elif action in {"pauza", "wznow", "usun"}:
            update_watch(
                media_store, owner, rest.strip(), enabled=action == "wznow", delete=action == "usun"
            )
            await context.bot.send_message(chat_id=owner, text="Zapisano ustawienia radaru.")
        elif action == "sprawdz":
            await status("Sprawdzanie nowych pozycji i ich napisów…")
            await check_watches(
                media_store,
                owner,
                allowed_user=lambda user: _allowed(context, user),
                notify=lambda text: context.bot.send_message(chat_id=owner, text=text),
                cancellation=cancellation,
            )
            await context.bot.send_message(
                chat_id=owner,
                text="Sprawdzono radar. Szczegóły i ograniczenia źródeł: /radar status.",
            )
        else:
            raise MediaError("Nieznana opcja radaru. Użyj /radar.")

    await _launch(update, context, "Aktualizowanie radaru…", run)


async def handle_media_callback(update, context, data):
    if not await _auth(update, context):
        return
    owner = update.effective_chat.id
    try:
        if data.startswith("media_import_"):
            match = re.fullmatch(r"media_import_(audio|video)_([a-f0-9]{12})", data)
            url = get_session_value(context, owner, "current_url", user_urls)
            if not match or not url or source_tag(url) != match[2]:
                raise MediaError("Przycisk dotyczy starszego materiału. Wyślij link ponownie.")
            await _save(update, context, {"url": url, "video": match[1] == "video"}, guarded=True)
        elif data.startswith("media_upload_"):
            match = re.fullmatch(r"media_upload_(audio|video)_([a-f0-9]{12})", data)
            if not match:
                raise MediaError("Nieprawidłowy przycisk materiału.")
            params = _capture_input(update, context, video=match[1] == "video")
            if not params.get("local_path") or source_tag(str(params["local_path"])) != match[2]:
                raise MediaError("Przesłany plik wygasł. Wyślij go ponownie.")
            await _save(update, context, params, guarded=True)
        elif data.startswith("media_analyze_"):
            url = get_session_value(context, owner, "current_url", user_urls)
            if not url or data != "media_analyze_" + source_tag(url):
                raise MediaError("Galeria wygasła. Wyślij link ponownie.")
            await _analyze(update, context, guarded=True)
        elif data.startswith(("media_toggle_", "media_review_", "media_delete_")):
            _, action, identity = data.split("_", 2)
            source = media_store.get(owner, identity)
            if action == "delete":
                media_store.delete(owner, identity)
            elif action == "review":
                source["reviewed"] = not source.get("reviewed", False)
                media_store.atomic_json(
                    media_store.directory(owner, identity) / "source.json", source
                )
            else:
                chosen = _get(context, owner, "media_selection")
                if chosen is None:
                    chosen = [
                        item["id"] for item in media_store.list(owner) if item["kind"] != "gallery"
                    ][:3]
                chosen = list(chosen)
                if identity in chosen:
                    chosen.remove(identity)
                else:
                    if source["kind"] == "gallery" or len(chosen) >= 3:
                        raise MediaError(
                            "Do montażu wybierz maksymalnie 3 nagrania audio lub wideo."
                        )
                    chosen.append(identity)
                set_session_context_value(
                    context, owner, "media_selection", chosen, legacy_key="media_selection"
                )
            await materials_command(update, context)
    except MediaError as exc:
        await context.bot.send_message(chat_id=owner, text=str(exc))


async def run_radar_tick(context):
    """Only explicitly configured watches can send notifications to their own chat."""
    if not media_store.root.exists():
        return
    for directory in media_store.root.iterdir():
        if not re.fullmatch(r"-?\d+", directory.name) or directory.is_symlink():
            continue
        owner = int(directory.name)
        if is_chat_busy(owner):
            continue
        try:
            config = radar_config(media_store, owner)
            if not any(
                watch["enabled"] and _allowed(context, watch["requester"])
                for watch in config["watches"]
            ):
                await asyncio.to_thread(media_store.purge, owner)
                continue
        except Exception as exc:
            logging.warning("Media maintenance skipped a chat: %s", type(exc).__name__)
            continue
        busy_chats.add(owner)
        cancellation = job_registry.register(
            owner, JobDescriptor("", owner, "media", "Radar tematów", datetime.now())
        )
        try:
            await asyncio.to_thread(media_store.purge, owner)
            slots = context.application.bot_data.setdefault(
                "media_work_slots", asyncio.Semaphore(2)
            )
            async with slots:
                await check_watches(
                    media_store,
                    owner,
                    allowed_user=lambda user: _allowed(context, user),
                    notify=lambda text, chat=owner: context.bot.send_message(
                        chat_id=chat, text=text
                    ),
                    cancellation=cancellation,
                )
        except Exception as exc:
            logging.warning("Radar maintenance failed: %s", type(exc).__name__)
        finally:
            job_registry.unregister(cancellation.job_id)
            busy_chats.discard(owner)
