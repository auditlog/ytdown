"""Translate yt-dlp failures into actionable, user-facing Polish messages.

yt-dlp reports every failure as free-form English text, and the handlers used to
collapse all of them into one generic sentence. That hid the difference between
"this video needs an age-verified account" and "the network is down", which left
users with no idea whether to retry, fix cookies, or give up.

Patterns are matched in order, so more specific cases must come first.
See also: bot/downloader_metadata.py (produces the raw text this module parses).
"""

from __future__ import annotations

# Each entry maps yt-dlp substrings (lowercased) to the explanation shown to the user.
# NOTE: "sign in to confirm ..." covers two distinct causes (age gate vs bot check),
# so both variants are matched explicitly rather than on the shared prefix.
_ERROR_PATTERNS: list[tuple[tuple[str, ...], str]] = [
    (
        ("sign in to confirm your age", "age-restricted", "inappropriate for some users"),
        "Materiał jest objęty ograniczeniem wiekowym. YouTube wymaga zalogowanego "
        "konta z potwierdzonym wiekiem — potrzebny jest aktualny plik cookies.",
    ),
    (
        ("sign in to confirm you're not a bot", "confirm you are not a bot"),
        "YouTube zażądał potwierdzenia, że nie jesteś botem. Trzeba odświeżyć plik cookies.",
    ),
    (
        ("cookies are no longer valid", "have likely been rotated"),
        "Sesja YouTube wygasła — zapisane cookies zostały unieważnione. "
        "Trzeba wyeksportować je ponownie.",
    ),
    (
        ("private video",),
        "Materiał jest prywatny — konto nie ma do niego dostępu.",
    ),
    (
        ("has been removed", "video unavailable", "no longer available"),
        "Materiał został usunięty lub jest niedostępny.",
    ),
    (
        ("available in your country", "geo restricted", "geo-restricted", "blocked in your country"),
        "Materiał jest zablokowany w Twoim kraju.",
    ),
    (
        ("unable to download webpage", "name resolution", "timed out", "connection refused",
         "temporary failure"),
        "Brak połączenia z serwerem. Spróbuj ponownie za chwilę.",
    ),
]

GENERIC_ERROR_TEMPLATE = "Wystąpił błąd podczas pobierania informacji o {media_name}."


def classify_download_error(error_text: str | None) -> str | None:
    """Return a user-facing explanation for a yt-dlp error, or None if unrecognised."""

    if not error_text:
        return None

    haystack = error_text.lower()
    for needles, explanation in _ERROR_PATTERNS:
        if any(needle in haystack for needle in needles):
            return explanation
    return None


def build_media_error_message(media_name: str, error_text: str | None) -> str:
    """Build the message shown when media info could not be fetched.

    Falls back to the original generic wording for unknown failures so that an
    unrecognised error never degrades into a misleading explanation.
    """

    explanation = classify_download_error(error_text)
    if explanation is None:
        return GENERIC_ERROR_TEMPLATE.format(media_name=media_name)
    return f"Nie udało się pobrać informacji o {media_name}.\n\n{explanation}"
