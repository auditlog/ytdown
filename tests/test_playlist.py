"""
Unit tests for playlist support.
"""

import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock

from bot.downloader import (
    is_playlist_url,
    is_pure_playlist_url,
    strip_playlist_params,
    get_playlist_info,
)
from bot import telegram_commands as tc
from bot import telegram_callbacks as tcb
from bot.security import user_urls, user_playlist_data, MAX_PLAYLIST_ITEMS, MAX_PLAYLIST_ITEMS_EXPANDED
from bot.telegram_commands import _build_playlist_message


def _async(coro):
    return asyncio.run(coro)


def _set_authorized_users(monkeypatch, users):
    monkeypatch.setattr(tc, "get_authorized_user_ids_for", lambda *_args, **_kwargs: users)


def _make_update(text="", user_id=123456, chat_id=123456):
    update = Mock()
    update.effective_user.id = user_id
    update.effective_chat.id = chat_id
    update.message = Mock()
    update.message.text = text
    update.message.reply_text = AsyncMock(return_value=Mock(edit_text=AsyncMock()))
    update.message.delete = AsyncMock()
    return update


def _make_context():
    context = Mock()
    context.user_data = {}
    context.bot = Mock()
    context.bot.send_message = AsyncMock()
    context.bot.send_audio = AsyncMock()
    context.bot.send_video = AsyncMock()
    return context


def _make_callback_update(data, user_id=123456, chat_id=123456):
    update = Mock()
    update.effective_user.id = user_id
    update.effective_chat.id = chat_id
    update.callback_query = Mock()
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.callback_query.message = Mock()
    update.message = None
    return update


# --- is_playlist_url ---

class TestIsPlaylistUrl:
    def test_pure_playlist_url(self):
        assert is_playlist_url("https://www.youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf") is True

    def test_video_with_playlist(self):
        assert is_playlist_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf") is True

    def test_single_video(self):
        assert is_playlist_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is False

    def test_youtu_be_with_playlist(self):
        assert is_playlist_url("https://youtu.be/dQw4w9WgXcQ?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf") is True

    def test_youtu_be_without_playlist(self):
        assert is_playlist_url("https://youtu.be/dQw4w9WgXcQ") is False

    def test_vimeo_url(self):
        assert is_playlist_url("https://vimeo.com/123456") is False

    def test_tiktok_url(self):
        assert is_playlist_url("https://www.tiktok.com/@user/video/123") is False

    def test_empty_string(self):
        assert is_playlist_url("") is False

    def test_invalid_url(self):
        assert is_playlist_url("not a url") is False

    def test_music_youtube_playlist(self):
        assert is_playlist_url("https://music.youtube.com/playlist?list=PLtest") is True


class TestIsPurePlaylistUrl:
    def test_pure_playlist(self):
        assert is_pure_playlist_url("https://www.youtube.com/playlist?list=PLtest") is True

    def test_video_with_playlist(self):
        assert is_pure_playlist_url("https://www.youtube.com/watch?v=abc&list=PLtest") is False

    def test_single_video(self):
        assert is_pure_playlist_url("https://www.youtube.com/watch?v=abc") is False


class TestStripPlaylistParams:
    def test_strip_list_and_index(self):
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLtest&index=3"
        result = strip_playlist_params(url)
        assert "list=" not in result
        assert "index=" not in result
        assert "v=dQw4w9WgXcQ" in result

    def test_preserves_video_param(self):
        url = "https://www.youtube.com/watch?v=abc123&list=PLtest"
        result = strip_playlist_params(url)
        assert "v=abc123" in result
        assert "list=" not in result

    def test_no_playlist_params(self):
        url = "https://www.youtube.com/watch?v=abc123"
        result = strip_playlist_params(url)
        assert "v=abc123" in result


# --- get_playlist_info ---

class TestGetPlaylistInfo:
    def test_returns_structured_data(self, monkeypatch):
        fake_info = {
            '_type': 'playlist',
            'title': 'Test Playlist',
            'playlist_count': 25,
            'entries': [
                {'id': 'abc123', 'title': 'Video 1', 'duration': 120},
                {'id': 'def456', 'title': 'Video 2', 'duration': 300},
            ],
        }

        class FakeYDL:
            def __init__(self, opts):
                self.opts = opts
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass
            def extract_info(self, url, download=False):
                return fake_info

        monkeypatch.setattr("bot.downloader_playlist.yt_dlp.YoutubeDL", FakeYDL)

        result = get_playlist_info("https://www.youtube.com/playlist?list=PLtest", max_items=10)

        assert result is not None
        assert result['title'] == 'Test Playlist'
        assert result['playlist_count'] == 25
        assert len(result['entries']) == 2
        assert result['entries'][0]['id'] == 'abc123'
        assert result['entries'][0]['url'] == 'https://www.youtube.com/watch?v=abc123'

    def test_returns_none_for_single_video(self, monkeypatch):
        fake_info = {'_type': 'video', 'title': 'Single', 'id': 'abc'}

        class FakeYDL:
            def __init__(self, opts):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass
            def extract_info(self, url, download=False):
                return fake_info

        monkeypatch.setattr("bot.downloader_playlist.yt_dlp.YoutubeDL", FakeYDL)

        result = get_playlist_info("https://www.youtube.com/watch?v=abc")
        assert result is None

    def test_returns_none_on_error(self, monkeypatch):
        class FakeYDL:
            def __init__(self, opts):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass
            def extract_info(self, url, download=False):
                raise Exception("Network error")

        monkeypatch.setattr("bot.downloader_playlist.yt_dlp.YoutubeDL", FakeYDL)

        result = get_playlist_info("https://www.youtube.com/playlist?list=PLtest")
        assert result is None

    def test_skips_none_entries(self, monkeypatch):
        fake_info = {
            '_type': 'playlist',
            'title': 'Test',
            'entries': [
                {'id': 'abc', 'title': 'Good', 'duration': 60},
                None,
                {'id': 'def', 'title': 'Also Good', 'duration': 120},
            ],
        }

        class FakeYDL:
            def __init__(self, opts):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass
            def extract_info(self, url, download=False):
                return fake_info

        monkeypatch.setattr("bot.downloader_playlist.yt_dlp.YoutubeDL", FakeYDL)

        result = get_playlist_info("https://www.youtube.com/playlist?list=PLtest")
        assert len(result['entries']) == 2


# --- process_youtube_link playlist detection ---

class TestPlaylistDetection:
    def test_pure_playlist_url_calls_process_playlist(self, monkeypatch):
        update = _make_update(user_id=111, chat_id=111)
        context = _make_context()

        _set_authorized_users(monkeypatch, {111})

        called = {}

        async def fake_process_playlist(u, c, url):
            called['url'] = url

        monkeypatch.setattr(tc, "process_playlist_link", fake_process_playlist)

        _async(tc.process_youtube_link(
            update, context,
            "https://www.youtube.com/playlist?list=PLtest"
        ))

        assert 'url' in called
        assert "PLtest" in called['url']

    def test_video_with_playlist_shows_choice(self, monkeypatch):
        update = _make_update(user_id=111, chat_id=111)
        context = _make_context()

        _set_authorized_users(monkeypatch, {111})

        _async(tc.process_youtube_link(
            update, context,
            "https://www.youtube.com/watch?v=abc&list=PLtest"
        ))

        # Should show choice buttons, not start processing
        call_args = update.message.reply_text.await_args
        assert "pl_single" in str(call_args) or "pl_full" in str(call_args)

    def test_single_video_proceeds_normally(self, monkeypatch):
        update = _make_update(user_id=111, chat_id=111)
        context = _make_context()

        _set_authorized_users(monkeypatch, {111})
        monkeypatch.setattr(tc, "get_video_info", lambda url: {
            'title': 'Test', 'duration': 60, 'formats': []
        })

        _async(tc.process_youtube_link(
            update, context,
            "https://www.youtube.com/watch?v=abc"
        ))

        # Should show format selection (reply_text called with keyboard)
        assert update.message.reply_text.await_count >= 1


# --- Playlist callback handling ---

class TestPlaylistCallbacks:
    def test_pl_cancel_clears_session(self, monkeypatch):
        update = _make_callback_update("pl_cancel", chat_id=111)
        context = _make_context()
        user_playlist_data[111] = {'title': 'test', 'entries': []}

        monkeypatch.setattr(tcb, "check_rate_limit", lambda *_: True)

        _async(tcb.handle_callback(update, context))

        assert 111 not in user_playlist_data
        update.callback_query.edit_message_text.assert_awaited_with(
            "Pobieranie playlisty anulowane."
        )

    def test_pl_more_refetches_with_expanded_limit(self, monkeypatch):
        update = _make_callback_update("pl_more", chat_id=333)
        context = _make_context()
        user_urls[333] = "https://youtube.com/playlist?list=PLtest"
        user_playlist_data[333] = {'title': 'Old', 'entries': [{'url': 'x', 'title': 'x', 'duration': 60, 'id': '1'}], 'playlist_count': 25}

        expanded_playlist = {
            'title': 'Expanded',
            'playlist_count': 25,
            'entries': [
                {'url': f'https://youtube.com/watch?v={i}', 'title': f'Song {i}', 'duration': 120, 'id': str(i)}
                for i in range(25)
            ],
        }

        monkeypatch.setattr(tcb, "check_rate_limit", lambda *_: True)
        monkeypatch.setattr(
            tcb,
            "load_playlist",
            lambda url, max_items: expanded_playlist if max_items == MAX_PLAYLIST_ITEMS_EXPANDED else None,
        )

        _async(tcb.handle_callback(update, context))

        # Verify session updated with expanded data
        assert len(user_playlist_data[333]['entries']) == 25
        # Verify message was updated
        call_text = update.callback_query.edit_message_text.await_args.args[0]
        assert "Song 24" in call_text

    def test_pl_dl_expired_session(self, monkeypatch):
        update = _make_callback_update("pl_dl_audio_mp3", chat_id=222)
        context = _make_context()
        # No playlist data stored

        monkeypatch.setattr(tcb, "check_rate_limit", lambda *_: True)

        _async(tcb.handle_callback(update, context))

        update.callback_query.edit_message_text.assert_awaited_with(
            "Sesja playlisty wygasła. Wyślij link ponownie."
        )


class TestProcessPlaylistLink:
    def test_shows_playlist_menu(self, monkeypatch):
        update = _make_update(user_id=111, chat_id=111)
        context = _make_context()

        progress_msg = Mock()
        progress_msg.edit_text = AsyncMock()
        update.message.reply_text = AsyncMock(return_value=progress_msg)

        fake_playlist = {
            'title': 'My Playlist',
            'playlist_count': 3,
            'entries': [
                {'url': 'https://youtube.com/watch?v=a', 'title': 'Song A', 'duration': 180, 'id': 'a'},
                {'url': 'https://youtube.com/watch?v=b', 'title': 'Song B', 'duration': 240, 'id': 'b'},
                {'url': 'https://youtube.com/watch?v=c', 'title': 'Song C', 'duration': 300, 'id': 'c'},
            ],
        }

        monkeypatch.setattr(tc, "load_playlist", lambda url, max_items: fake_playlist)

        _async(tc.process_playlist_link(update, context, "https://youtube.com/playlist?list=PLtest"))

        # Verify playlist data stored
        assert 111 in user_playlist_data
        assert user_playlist_data[111]['title'] == 'My Playlist'

        # Verify edit_text was called with playlist content
        call_args = progress_msg.edit_text.await_args
        msg_text = call_args.args[0]
        assert "My Playlist" in msg_text
        assert "Song A" in msg_text
        assert "pl_dl_audio_mp3" in str(call_args)

    def test_empty_playlist(self, monkeypatch):
        update = _make_update(user_id=111, chat_id=111)
        context = _make_context()

        progress_msg = Mock()
        progress_msg.edit_text = AsyncMock()
        update.message.reply_text = AsyncMock(return_value=progress_msg)

        monkeypatch.setattr(tc, "load_playlist", lambda url, max_items: {
            'title': 'Empty', 'playlist_count': 0, 'entries': [],
        })

        _async(tc.process_playlist_link(update, context, "https://youtube.com/playlist?list=PLtest"))

        progress_msg.edit_text.assert_awaited_with("Playlista jest pusta.")

    def test_shows_more_button_when_truncated(self, monkeypatch):
        update = _make_update(user_id=111, chat_id=111)
        context = _make_context()

        progress_msg = Mock()
        progress_msg.edit_text = AsyncMock()
        update.message.reply_text = AsyncMock(return_value=progress_msg)

        # Playlist with more items than displayed
        fake_playlist = {
            'title': 'Big Playlist',
            'playlist_count': 25,
            'entries': [
                {'url': f'https://youtube.com/watch?v={i}', 'title': f'Song {i}', 'duration': 180, 'id': str(i)}
                for i in range(10)
            ],
        }

        monkeypatch.setattr(tc, "load_playlist", lambda url, max_items: fake_playlist)

        _async(tc.process_playlist_link(update, context, "https://youtube.com/playlist?list=PLtest"))

        call_args = progress_msg.edit_text.await_args
        # Should contain "Pokaż więcej" button
        assert "pl_more" in str(call_args)

    def test_no_more_button_when_all_shown(self, monkeypatch):
        update = _make_update(user_id=111, chat_id=111)
        context = _make_context()

        progress_msg = Mock()
        progress_msg.edit_text = AsyncMock()
        update.message.reply_text = AsyncMock(return_value=progress_msg)

        # Playlist where all items are shown
        fake_playlist = {
            'title': 'Small Playlist',
            'playlist_count': 3,
            'entries': [
                {'url': f'https://youtube.com/watch?v={i}', 'title': f'Song {i}', 'duration': 120, 'id': str(i)}
                for i in range(3)
            ],
        }

        monkeypatch.setattr(tc, "load_playlist", lambda url, max_items: fake_playlist)

        _async(tc.process_playlist_link(update, context, "https://youtube.com/playlist?list=PLtest"))

        call_args = progress_msg.edit_text.await_args
        # Should NOT contain "Pokaż więcej" button
        assert "pl_more" not in str(call_args)

    def test_playlist_info_error(self, monkeypatch):
        update = _make_update(user_id=111, chat_id=111)
        context = _make_context()

        progress_msg = Mock()
        progress_msg.edit_text = AsyncMock()
        update.message.reply_text = AsyncMock(return_value=progress_msg)

        monkeypatch.setattr(tc, "load_playlist", lambda url, max_items: None)

        _async(tc.process_playlist_link(update, context, "https://youtube.com/playlist?list=PLtest"))

        progress_msg.edit_text.assert_awaited_with(
            "Nie udało się pobrać informacji o playliście."
        )
