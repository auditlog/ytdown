"""
Unit tests for Spotify podcast support.
"""

import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock

from bot.spotify import (
    parse_spotify_episode_url,
    _extract_title_from_url,
    search_itunes_episode,
    search_youtube_episode,
    resolve_spotify_episode,
    get_spotify_episode_info,
)
from bot import security
from bot import telegram_commands as tc


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
    return context


# --- URL parsing ---

class TestParseSpotifyEpisodeUrl:
    def test_valid_episode_url(self):
        url = "https://open.spotify.com/episode/4rOoJ6Egrf8K2IrywzwOMk"
        assert parse_spotify_episode_url(url) == "4rOoJ6Egrf8K2IrywzwOMk"

    def test_episode_url_with_query_params(self):
        url = "https://open.spotify.com/episode/4rOoJ6Egrf8K2IrywzwOMk?si=abc123"
        assert parse_spotify_episode_url(url) == "4rOoJ6Egrf8K2IrywzwOMk"

    def test_show_url_rejected(self):
        url = "https://open.spotify.com/show/abc123"
        assert parse_spotify_episode_url(url) is None

    def test_track_url_rejected(self):
        url = "https://open.spotify.com/track/abc123"
        assert parse_spotify_episode_url(url) is None

    def test_playlist_url_rejected(self):
        url = "https://open.spotify.com/playlist/abc123"
        assert parse_spotify_episode_url(url) is None

    def test_invalid_domain(self):
        url = "https://spotify.com/episode/abc123"
        assert parse_spotify_episode_url(url) is None

    def test_empty_string(self):
        assert parse_spotify_episode_url("") is None

    def test_not_a_url(self):
        assert parse_spotify_episode_url("not a url") is None


class TestExtractTitleFromUrl:
    def test_returns_none_for_base62_id(self):
        # Spotify episode URLs use Base62 IDs, not human-readable slugs
        url = "https://open.spotify.com/episode/4rOoJ6Egrf8K2IrywzwOMk"
        assert _extract_title_from_url(url) is None

    def test_returns_none_for_invalid(self):
        assert _extract_title_from_url("https://open.spotify.com/show/abc") is None


# --- URL validation and platform detection ---

class TestSpotifyUrlValidation:
    def test_validate_url_spotify_episode(self):
        assert security.validate_url("https://open.spotify.com/episode/abc123") is True

    def test_detect_platform_spotify(self):
        assert security.detect_platform("https://open.spotify.com/episode/abc123") == "spotify"

    def test_detect_platform_spotify_www(self):
        # www.open.spotify.com is a subdomain, may not be in allowed domains
        # but open.spotify.com should work
        assert security.detect_platform("https://open.spotify.com/episode/abc") == "spotify"


# --- iTunes search ---

class TestSearchItunesEpisode:
    def test_returns_best_match(self, monkeypatch):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'results': [
                {
                    'trackName': 'My Podcast Episode',
                    'collectionName': 'My Show',
                    'trackTimeMillis': 3600000,
                    'episodeUrl': 'https://cdn.example.com/ep1.mp3',
                },
                {
                    'trackName': 'Unrelated Episode',
                    'collectionName': 'Other Show',
                    'trackTimeMillis': 1800000,
                    'episodeUrl': 'https://cdn.example.com/ep2.mp3',
                },
            ]
        }
        monkeypatch.setattr("bot.spotify.requests.get", lambda *a, **kw: mock_response)

        result = search_itunes_episode("My Podcast Episode", "My Show", 3600)

        assert result is not None
        assert result['audio_url'] == 'https://cdn.example.com/ep1.mp3'
        assert result['title'] == 'My Podcast Episode'

    def test_returns_none_for_no_results(self, monkeypatch):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'results': []}
        monkeypatch.setattr("bot.spotify.requests.get", lambda *a, **kw: mock_response)

        assert search_itunes_episode("Nonexistent Episode") is None

    def test_returns_none_on_api_error(self, monkeypatch):
        mock_response = Mock()
        mock_response.status_code = 500
        monkeypatch.setattr("bot.spotify.requests.get", lambda *a, **kw: mock_response)

        assert search_itunes_episode("Test") is None

    def test_skips_entries_without_audio_url(self, monkeypatch):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'results': [
                {
                    'trackName': 'No Audio',
                    'collectionName': 'Show',
                    'trackTimeMillis': 1000,
                    'episodeUrl': '',
                },
            ]
        }
        monkeypatch.setattr("bot.spotify.requests.get", lambda *a, **kw: mock_response)

        assert search_itunes_episode("No Audio", "Show") is None


# --- YouTube search ---

class TestSearchYoutubeEpisode:
    def test_returns_best_match(self, monkeypatch):
        fake_results = {
            'entries': [
                {
                    'id': 'abc123',
                    'title': 'My Podcast Episode - Full',
                    'channel': 'My Show Channel',
                    'duration': 3600,
                    'url': 'https://www.youtube.com/watch?v=abc123',
                },
            ]
        }

        class FakeYDL:
            def __init__(self, opts):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass
            def extract_info(self, query, download=False):
                return fake_results

        monkeypatch.setattr("bot.spotify.yt_dlp.YoutubeDL", FakeYDL)

        result = search_youtube_episode("My Podcast Episode", "My Show Channel", 3600)

        assert result is not None
        assert 'abc123' in result['url']

    def test_returns_none_on_no_results(self, monkeypatch):
        class FakeYDL:
            def __init__(self, opts):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass
            def extract_info(self, query, download=False):
                return {'entries': []}

        monkeypatch.setattr("bot.spotify.yt_dlp.YoutubeDL", FakeYDL)

        assert search_youtube_episode("Nonexistent") is None


# --- resolve_spotify_episode ---

class TestResolveSpotifyEpisode:
    def test_returns_none_for_invalid_url(self):
        assert resolve_spotify_episode("https://open.spotify.com/show/abc") is None

    def test_itunes_takes_priority(self, monkeypatch):
        monkeypatch.setattr("bot.spotify.get_spotify_episode_info", lambda eid: {
            'title': 'Test Episode',
            'show_name': 'Test Show',
            'duration_ms': 3600000,
            'description': '',
            'release_date': '',
            'language': '',
        })
        monkeypatch.setattr("bot.spotify.search_itunes_episode", lambda t, s, d: {
            'audio_url': 'https://cdn.example.com/ep.mp3',
            'title': 'Test Episode',
            'show_name': 'Test Show',
            'duration': 3600,
            'score': 0.9,
        })
        # YouTube should NOT be called
        monkeypatch.setattr("bot.spotify.search_youtube_episode", lambda *a, **kw: None)

        result = resolve_spotify_episode("https://open.spotify.com/episode/abc123")

        assert result is not None
        assert result['source'] == 'itunes'
        assert result['audio_url'] == 'https://cdn.example.com/ep.mp3'

    def test_youtube_fallback(self, monkeypatch):
        monkeypatch.setattr("bot.spotify.get_spotify_episode_info", lambda eid: {
            'title': 'Fallback Episode',
            'show_name': 'Some Show',
            'duration_ms': 1800000,
            'description': '', 'release_date': '', 'language': '',
        })
        monkeypatch.setattr("bot.spotify.search_itunes_episode", lambda *a, **kw: None)
        monkeypatch.setattr("bot.spotify.search_youtube_episode", lambda t, s, d: {
            'url': 'https://www.youtube.com/watch?v=xyz',
            'title': 'Fallback Video',
            'channel': 'Channel',
            'duration': 1800,
            'score': 0.5,
        })

        result = resolve_spotify_episode("https://open.spotify.com/episode/abc123")

        assert result is not None
        assert result['source'] == 'youtube'
        assert 'xyz' in result['youtube_url']

    def test_returns_no_credentials_when_no_api_keys(self, monkeypatch):
        monkeypatch.setattr("bot.spotify.get_spotify_episode_info", lambda eid: None)

        result = resolve_spotify_episode("https://open.spotify.com/episode/abc123")
        assert result is not None
        assert result['source'] == 'no_credentials'
        assert result['episode_id'] == 'abc123'

    def test_returns_none_when_not_found_with_credentials(self, monkeypatch):
        monkeypatch.setattr("bot.spotify.get_spotify_episode_info", lambda eid: {
            'title': 'Test', 'show_name': 'Show', 'duration_ms': 60000,
            'description': '', 'release_date': '', 'language': '',
        })
        monkeypatch.setattr("bot.spotify.search_itunes_episode", lambda *a, **kw: None)
        monkeypatch.setattr("bot.spotify.search_youtube_episode", lambda *a, **kw: None)

        result = resolve_spotify_episode("https://open.spotify.com/episode/abc123")
        assert result is None


# --- Telegram integration ---

class TestSpotifyTelegramFlow:
    def test_non_episode_url_rejected(self, monkeypatch):
        update = _make_update(user_id=111, chat_id=111)
        context = _make_context()

        monkeypatch.setattr(tc, "detect_platform", lambda *_: "spotify")
        _set_authorized_users(monkeypatch, {111})

        _async(tc.process_youtube_link(
            update, context,
            "https://open.spotify.com/show/abc123"
        ))

        call_text = update.message.reply_text.await_args.args[0]
        assert "tylko linki do odcinków" in call_text

    def test_keyboard_is_audio_only_for_spotify(self):
        keyboard = tc._build_main_keyboard('spotify')
        button_texts = [btn.text for row in keyboard for btn in row]

        assert "Audio (MP3)" in button_texts
        assert "Transkrypcja audio" in button_texts
        assert "Najlepsza jakość video" not in button_texts
        assert "Audio (FLAC)" not in button_texts
        assert "✂️ Zakres czasowy" not in button_texts
        assert "Lista formatów" not in button_texts
