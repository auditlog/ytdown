"""Stage-1 UX text checks: unified rate-limit message and no bare expiry text."""

from pathlib import Path

from bot.security_limits import RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW
from bot.security_throttling import RATE_LIMIT_MESSAGE

BOT_DIR = Path(__file__).resolve().parent.parent / "bot"


def test_rate_limit_message_text():
    assert RATE_LIMIT_MESSAGE == (
        f"Przekroczono limit żądań ({RATE_LIMIT_REQUESTS} na {RATE_LIMIT_WINDOW} s). "
        "Poczekaj chwilę i spróbuj ponownie."
    )


def test_no_anglicism_requests_left_in_bot_package():
    offenders = [
        str(path) for path in BOT_DIR.rglob("*.py") if "requestów" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_expired_archive_sessions_share_one_text():
    from bot.services.archive_service import ARCHIVE_EXPIRED_TEXT

    assert ARCHIVE_EXPIRED_TEXT.endswith("Pobierz plik ponownie.")
    for name in ("handlers/download_callbacks.py", "services/archive_service.py"):
        source = (BOT_DIR / name).read_text(encoding="utf-8")
        assert '"Sesja wygasła."' not in source
        assert source.count("Paczki wygasły") <= 1  # only the constant definition
