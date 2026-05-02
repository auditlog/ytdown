"""Tests for the playlist service layer."""

from bot.services import playlist_service as ps


def test_build_playlist_message_shows_load_more_button():
    playlist = {
        'title': 'My Playlist',
        'playlist_count': 25,
        'entries': [
            {'url': f'https://youtube.com/watch?v={i}', 'title': f'Song {i}', 'duration': 180}
            for i in range(10)
        ],
    }

    message, markup = ps.build_playlist_message(playlist)

    assert "My Playlist" in message
    assert "Filmów: 10 (z 25)" in message
    assert any(button.callback_data == "pl_more" for row in markup.inline_keyboard for button in row)


def test_build_single_video_url_strips_playlist_params():
    url = "https://www.youtube.com/watch?v=abc123&list=PLtest&index=3"

    result = ps.build_single_video_url(url)

    assert "v=abc123" in result
    assert "list=" not in result
    assert "index=" not in result


def test_parse_playlist_download_choice_audio():
    choice = ps.parse_playlist_download_choice("pl_dl_audio_mp3")

    assert choice.media_type == "audio"
    assert choice.format_choice == "mp3"


def test_parse_playlist_download_choice_video():
    choice = ps.parse_playlist_download_choice("pl_dl_video_720p")

    assert choice.media_type == "video"
    assert choice.format_choice == "720p"


def test_parse_playlist_download_choice_recognizes_zip_prefix():
    from bot.services.playlist_service import parse_playlist_download_choice

    choice = parse_playlist_download_choice("pl_zip_dl_audio_mp3")

    assert choice.media_type == "audio"
    assert choice.format_choice == "mp3"
    assert choice.as_archive is True


def test_parse_playlist_download_choice_legacy_prefix_unchanged():
    from bot.services.playlist_service import parse_playlist_download_choice

    choice = parse_playlist_download_choice("pl_dl_audio_mp3")

    assert choice.media_type == "audio"
    assert choice.format_choice == "mp3"
    assert choice.as_archive is False


def test_build_playlist_message_includes_zip_buttons_when_archive_available():
    from bot.services.playlist_service import build_playlist_message

    msg, kb = build_playlist_message(
        {"title": "X", "entries": [{"title": "a", "duration": 60}], "playlist_count": 1},
        archive_available=True,
    )

    callback_data = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "pl_dl_audio_mp3" in callback_data
    assert "pl_zip_dl_audio_mp3" in callback_data
    assert "pl_zip_dl_video_720p" in callback_data


def test_build_playlist_message_hides_zip_buttons_when_archive_unavailable():
    from bot.services.playlist_service import build_playlist_message

    msg, kb = build_playlist_message(
        {"title": "X", "entries": [{"title": "a", "duration": 60}], "playlist_count": 1},
        archive_available=False,
    )

    callback_data = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "pl_dl_audio_mp3" in callback_data
    assert not any(cd.startswith("pl_zip_dl_") for cd in callback_data)
