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


def _labels(markup):
    return [btn.text for row in markup.inline_keyboard for btn in row]


def _playlist(shown, total):
    return {
        "title": "X",
        "playlist_count": total,
        "entries": [{"title": f"s{i}", "duration": 60} for i in range(shown)],
    }


def test_truncated_playlist_buttons_state_the_number_and_offer_more_first():
    msg, kb = ps.build_playlist_message(_playlist(10, 120))

    labels = _labels(kb)
    assert "Pobierz 10 — Audio MP3" in labels
    assert not any("wszystkie" in label for label in labels)
    # "Show more" is the first row, above the format buttons.
    assert kb.inline_keyboard[0][0].callback_data == "pl_more"
    assert kb.inline_keyboard[0][0].text == "Pokaż więcej (do 50)"
    assert "Pobiorę pozycje widoczne na liście (10 z 120)." in msg
    assert "„Pokaż więcej” rozszerza listę do 50." in msg


def test_truncated_expanded_playlist_has_no_more_button_or_hint():
    msg, kb = ps.build_playlist_message(_playlist(50, 120))

    assert "Pobierz 50 — Audio MP3" in _labels(kb)
    assert not any(b.callback_data == "pl_more" for r in kb.inline_keyboard for b in r)
    assert "(50 z 120)" in msg
    assert "rozszerza" not in msg


def test_full_playlist_keeps_download_all_labels():
    msg, kb = ps.build_playlist_message(_playlist(5, 5))

    assert "Pobierz wszystkie — Audio MP3" in _labels(kb)
    assert "Pobiorę pozycje" not in msg
    assert not any(b.callback_data == "pl_more" for r in kb.inline_keyboard for b in r)


def test_legacy_playlist_item_download_is_bounded_by_size_and_free_space(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from bot.security_limits import MAX_FILE_SIZE_MB
    from bot.services import playlist_service

    captured = {}
    monkeypatch.setattr(
        playlist_service, "build_playlist_item_download_plan", lambda **_kwargs: SimpleNamespace(url="u")
    )
    # An unknown size estimate used to skip every limit for the item.
    monkeypatch.setattr(playlist_service, "estimate_download_size", lambda _plan: None)

    async def fake_execute_download(_plan, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(file_path="item.mp3", file_size_mb=1.0)

    monkeypatch.setattr(playlist_service, "execute_download", fake_execute_download)

    asyncio.run(playlist_service.download_playlist_item(
        chat_id=1, url="u", title="t", media_type="audio", format_choice="mp3", executor=None,
    ))

    # max_file_bytes turns on DownloadBudget: byte cap plus the free-disk guard.
    assert captured["max_file_bytes"] == MAX_FILE_SIZE_MB * 1024**2
