"""Telegram gallery menus, authorization, album limits and session ownership."""

from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from bot import telegram_callbacks as callbacks
from bot import telegram_commands as commands
from bot.handlers import inbound_media
from bot.handlers import media_extras_callbacks as extras
from bot.session_context import get_session_context_value, set_session_context_value
from bot.tiktok import TikTokError
from tests.telegram_commands_support import _async, _attach_runtime, _make_context, _make_update

POST_ID = "7706467733575060769"
URL = f"https://www.tiktok.com/@example/photo/{POST_ID}"
CALLBACK = f"dl_tiktok_photos_{POST_ID}"


def _gallery(count=1):
    return {
        "id": POST_ID,
        "url": URL,
        "title": "Photo post",
        "photos": [{"url": f"https://cdn.test/{i}"} for i in range(count)],
    }


def test_short_photo_link_displays_gallery_menu_and_stores_canonical_url(monkeypatch):
    update, context = _make_update(), _make_context()
    runtime = _attach_runtime(context)
    gallery = _gallery(3)
    monkeypatch.setattr(inbound_media, "resolve_tiktok_url", lambda url: URL)
    monkeypatch.setattr(inbound_media, "get_tiktok_gallery", lambda url: gallery)
    video_fetch = Mock(side_effect=AssertionError("Photos must not use video extraction"))
    monkeypatch.setattr(commands, "get_video_info_with_error", video_fetch)
    _async(commands.process_youtube_link(update, context, "https://vm.tiktok.com/share/"))
    assert runtime.session_store.get_field(update.effective_chat.id, "tiktok_gallery") is gallery
    assert runtime.session_store.get_field(update.effective_chat.id, "current_url") == URL
    menu = update.message.reply_text.return_value.edit_text.await_args
    assert menu.kwargs["reply_markup"].inline_keyboard[0][0].callback_data == CALLBACK
    assert "3" in menu.args[0]


def test_gallery_metadata_error_is_explained_without_video_fallback(monkeypatch):
    update, context = _make_update(), _make_context()
    monkeypatch.setattr(inbound_media, "resolve_tiktok_url", lambda url: URL)
    monkeypatch.setattr(
        inbound_media, "get_tiktok_gallery", Mock(side_effect=TikTokError("Post niedostępny."))
    )
    _async(commands.process_youtube_link(update, context, URL))
    update.message.reply_text.return_value.edit_text.assert_awaited_once_with("Post niedostępny.")
    assert "tiktok_gallery" not in context.user_data


def _callback_context(monkeypatch, tmp_path, count=1):
    context = _make_context()
    runtime = _attach_runtime(context, authorized_users={7})
    update = _make_update(user_id=7, chat_id=7)
    update.callback_query = Mock(data=CALLBACK, answer=AsyncMock(), edit_message_text=AsyncMock())
    context.bot.send_message = AsyncMock()
    context.bot.send_photo = AsyncMock()
    context.bot.send_media_group = AsyncMock()
    gallery = _gallery(count)
    runtime.session_store.update_session(
        7, current_url=URL, platform="tiktok", tiktok_gallery=gallery
    )
    monkeypatch.setattr(extras, "DOWNLOAD_PATH", str(tmp_path))
    monkeypatch.setattr(extras, "record_download_for", Mock())
    monkeypatch.setattr(callbacks, "check_rate_limit", lambda user_id: True)

    def download(url, output):
        path = Path(output + ".jpg")
        path.write_bytes(url.rsplit("/", 1)[-1].encode())
        return str(path)

    monkeypatch.setattr(extras, "download_tiktok_photo", download)
    return update, context, runtime, gallery


@pytest.mark.parametrize("count", [1, 10, 11, 21])
def test_callback_sends_photos_in_order_with_valid_album_sizes(monkeypatch, tmp_path, count):
    update, context, runtime, _ = _callback_context(monkeypatch, tmp_path, count)
    delivered = []

    async def photo(**kwargs):
        delivered.append(kwargs["photo"].read())

    async def album(**kwargs):
        assert 2 <= len(kwargs["media"]) <= 10
        delivered.extend(item.media.input_file_content for item in kwargs["media"])

    context.bot.send_photo.side_effect = photo
    context.bot.send_media_group.side_effect = album
    _async(callbacks.handle_callback(update, context))
    assert delivered == [str(i).encode() for i in range(count)]
    assert runtime.session_store.get_field(7, "tiktok_gallery") is None
    assert list((tmp_path / "7").iterdir()) == []
    extras.record_download_for.assert_called_once()


def test_callback_after_logout_cannot_download(monkeypatch, tmp_path):
    update, context, runtime, _ = _callback_context(monkeypatch, tmp_path)
    runtime.authorized_users_set.clear()
    download = Mock()
    monkeypatch.setattr(extras, "download_tiktok_photo", download)
    _async(callbacks.handle_callback(update, context))
    download.assert_not_called()
    context.bot.send_message.assert_awaited_once()


def test_old_post_button_cannot_download_new_gallery(monkeypatch, tmp_path):
    update, context, _, _ = _callback_context(monkeypatch, tmp_path)
    update.callback_query.data = "dl_tiktok_photos_123"
    download = Mock()
    monkeypatch.setattr(extras, "download_tiktok_photo", download)
    _async(callbacks.handle_callback(update, context))
    download.assert_not_called()
    assert "wygasła" in update.callback_query.edit_message_text.await_args.args[0]


def test_send_failure_cleans_files_and_keeps_gallery_for_retry(monkeypatch, tmp_path):
    update, context, runtime, gallery = _callback_context(monkeypatch, tmp_path)
    context.bot.send_photo.side_effect = RuntimeError("Telegram unavailable")
    _async(callbacks.handle_callback(update, context))
    assert list((tmp_path / "7").iterdir()) == []
    assert runtime.session_store.get_field(7, "tiktok_gallery") is gallery
    extras.record_download_for.assert_not_called()


def test_partial_download_is_reported_and_does_not_erase_new_session(monkeypatch, tmp_path):
    update, context, runtime, _ = _callback_context(monkeypatch, tmp_path, 2)
    original_download = extras.download_tiktok_photo
    newer = _gallery(4)

    def download(url, output):
        if url.endswith("/1"):
            return None
        runtime.session_store.update_session(7, current_url="https://new", tiktok_gallery=newer)
        return original_download(url, output)

    monkeypatch.setattr(extras, "download_tiktok_photo", download)
    _async(callbacks.handle_callback(update, context))
    assert "1 z 2" in update.callback_query.edit_message_text.await_args.args[0]
    assert runtime.session_store.get_field(7, "tiktok_gallery") is newer
    assert list((tmp_path / "7").iterdir()) == []


def test_new_video_clears_previous_gallery(monkeypatch):
    context, update = _make_context(), _make_update()
    context.user_data["tiktok_gallery"] = _gallery()
    monkeypatch.setattr(
        commands, "get_video_info_with_error", lambda url: ({"title": "Video"}, None)
    )
    _async(
        commands.process_youtube_link(update, context, "https://www.tiktok.com/@example/video/123")
    )
    assert "tiktok_gallery" not in context.user_data


def test_session_with_only_gallery_is_retained_and_cleared_on_logout():
    from bot.session_context import clear_transient_flow_state

    context = _make_context()
    runtime = _attach_runtime(context)
    gallery = _gallery()
    set_session_context_value(context, 7, "tiktok_gallery", gallery, legacy_key="tiktok_gallery")
    assert (
        get_session_context_value(context, 7, "tiktok_gallery", legacy_key="tiktok_gallery")
        is gallery
    )
    clear_transient_flow_state(context, 7, user_urls={}, user_time_ranges={}, user_playlist_data={})
    assert runtime.session_store.get_field(7, "tiktok_gallery") is None


def test_instagram_still_uses_shared_photo_delivery(monkeypatch, tmp_path):
    update, context, runtime, gallery = _callback_context(monkeypatch, tmp_path, 11)
    runtime.session_store.update_session(7, instagram_carousel=gallery)
    download = Mock(wraps=extras.download_tiktok_photo)
    monkeypatch.setattr(extras, "download_photo", download)
    _async(
        extras._download_and_send_ig_photos(
            update,
            context,
            gallery["photos"],
            gallery["title"],
            str(tmp_path),
            url=URL,
            carousel=gallery,
        )
    )
    assert download.call_count == 11
    context.bot.send_media_group.assert_awaited_once()
    context.bot.send_photo.assert_awaited_once()
    assert runtime.session_store.get_field(7, "instagram_carousel") is None
    assert not list(tmp_path.rglob("photo_*.jpg"))
