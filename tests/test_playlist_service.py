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
