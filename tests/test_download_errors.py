"""
Unit tests for mapping yt-dlp failures onto user-facing messages.

The error strings below are verbatim yt-dlp output captured from real failures,
so the classifier is tested against what it actually has to parse.
"""

from bot.download_errors import build_media_error_message, classify_download_error


AGE_RESTRICTED = (
    "ERROR: [youtube] fFl7hEVSE1Q: Sign in to confirm your age. "
    "This video may be inappropriate for some users. "
    "Use --cookies-from-browser or --cookies for the authentication."
)
COOKIES_ROTATED = (
    "The provided YouTube account cookies are no longer valid. "
    "They have likely been rotated in the browser as a security measure."
)
BOT_CHECK = "ERROR: [youtube] abc: Sign in to confirm you're not a bot."
PRIVATE_VIDEO = "ERROR: [youtube] abc: Private video. Sign in if you've been granted access to this video"
REMOVED_VIDEO = "ERROR: [youtube] abc: Video unavailable. This video has been removed by the uploader"
GEO_BLOCKED = "ERROR: [youtube] abc: The uploader has not made this video available in your country"
NETWORK_FAILURE = "ERROR: Unable to download webpage: <urlopen error [Errno -3] Temporary failure in name resolution>"


def test_classifies_age_restriction():
    reason = classify_download_error(AGE_RESTRICTED)

    assert reason is not None
    assert "wiek" in reason.lower()


def test_classifies_rotated_cookies_as_expired_session():
    reason = classify_download_error(COOKIES_ROTATED)

    assert reason is not None
    assert "cookies" in reason.lower()


def test_classifies_bot_check_separately_from_age_restriction():
    bot_reason = classify_download_error(BOT_CHECK)

    assert bot_reason is not None
    assert bot_reason != classify_download_error(AGE_RESTRICTED)


def test_classifies_private_video():
    reason = classify_download_error(PRIVATE_VIDEO)

    assert reason is not None
    assert "prywatn" in reason.lower()


def test_classifies_removed_video():
    reason = classify_download_error(REMOVED_VIDEO)

    assert reason is not None
    assert "usuni" in reason.lower() or "niedostęp" in reason.lower()


def test_classifies_geo_blocking():
    reason = classify_download_error(GEO_BLOCKED)

    assert reason is not None
    assert "kraj" in reason.lower()


def test_classifies_network_failure():
    reason = classify_download_error(NETWORK_FAILURE)

    assert reason is not None
    assert "połącz" in reason.lower()


def test_returns_none_for_unrecognised_error():
    assert classify_download_error("ERROR: something entirely new happened") is None


def test_returns_none_for_empty_error():
    assert classify_download_error("") is None


def test_message_explains_age_restriction_to_the_user():
    message = build_media_error_message("filmie", AGE_RESTRICTED)

    assert "filmie" in message
    assert "wiek" in message.lower()


def test_message_falls_back_to_generic_text_for_unknown_errors():
    message = build_media_error_message("filmie", "ERROR: something entirely new happened")

    assert message == "Wystąpił błąd podczas pobierania informacji o filmie."


def test_message_falls_back_to_generic_text_when_error_is_missing():
    message = build_media_error_message("utworze", None)

    assert message == "Wystąpił błąd podczas pobierania informacji o utworze."
