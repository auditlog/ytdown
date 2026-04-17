"""URL policy, platform detection, and file-size estimation helpers."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

# Matches http(s) URLs; trailing punctuation is trimmed after the match.
_URL_PATTERN = re.compile(r'https?://[^\s<>"\'`]+')
_URL_TRAILING_PUNCT = '.,;:!?)]}>"\''

_DOMAIN_TO_PLATFORM = {
    'youtube.com': 'youtube',
    'youtu.be': 'youtube',
    'm.youtube.com': 'youtube',
    'music.youtube.com': 'youtube',
    'vimeo.com': 'vimeo',
    'player.vimeo.com': 'vimeo',
    'tiktok.com': 'tiktok',
    'm.tiktok.com': 'tiktok',
    'vm.tiktok.com': 'tiktok',
    'instagram.com': 'instagram',
    'linkedin.com': 'linkedin',
    'castbox.fm': 'castbox',
    'open.spotify.com': 'spotify',
}

ALLOWED_DOMAINS = sorted(
    set(_DOMAIN_TO_PLATFORM.keys())
    | {f'www.{domain}' for domain in _DOMAIN_TO_PLATFORM if domain.count('.') == 1}
)


def _normalize_domain(url: str) -> str | None:
    """Extract and normalize domain from URL. Return None on error."""

    try:
        if not url or not url.startswith('https://'):
            return None
        parsed = urlparse(url)
        return parsed.netloc.lower()
    except Exception:
        return None


def normalize_url(url: str, _depth: int = 0) -> str:
    """Resolve supported redirect URLs to their canonical form."""

    if _depth > 5:
        return url
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()

        if domain == 'd.castbox.fm':
            link_param = parse_qs(parsed.query).get('link', [None])[0]
            if link_param and 'castbox.fm' in link_param:
                return normalize_url(link_param, _depth + 1)

        if (domain in ('castbox.fm', 'www.castbox.fm')
                and '/episode/' not in parsed.path
                and parsed.path not in ('', '/')):
            from urllib.request import Request, urlopen

            try:
                req = Request(url, method='HEAD', headers={'User-Agent': 'Mozilla/5.0'})
                with urlopen(req, timeout=5) as resp:
                    if resp.url != url:
                        return normalize_url(resp.url, _depth + 1)
            except Exception:
                pass
    except Exception:
        pass
    return url


def get_media_label(platform: str | None) -> str:
    """Return Polish locative noun for media type."""

    if platform in ('castbox', 'spotify'):
        return 'odcinku'
    return 'filmie'


def validate_url(url) -> bool:
    """Validate URL against all supported platforms."""

    domain = _normalize_domain(url)
    if domain is None:
        return False
    if domain in ALLOWED_DOMAINS:
        return True
    if domain.startswith('www.'):
        return domain[4:] in ALLOWED_DOMAINS
    return False


def _offline_redirect_target(url: str) -> str | None:
    """Resolve supported redirect URLs without network I/O.

    Mirrors the offline branch of normalize_url so extract_url_from_text can
    recognize Castbox share links (d.castbox.fm?link=...) as supported. Network
    resolution (HEAD on castbox.fm/ch/...) stays in normalize_url, which runs
    in an executor off the event loop.
    """

    try:
        parsed = urlparse(url)
        if parsed.netloc.lower() == 'd.castbox.fm':
            link_param = parse_qs(parsed.query).get('link', [None])[0]
            if link_param and 'castbox.fm' in link_param:
                return link_param
    except Exception:
        pass
    return None


def extract_url_from_text(text) -> str | None:
    """Return the first supported URL found in free-form text, or None.

    Handles messages where the user prefixes the link with descriptive text
    (e.g. "please download this: https://youtu.be/abc"). Trailing punctuation
    like '.', ',', ')' is stripped so copy-pasted URLs still validate.
    Redirect links resolvable without network I/O (e.g. d.castbox.fm share
    URLs) are resolved up-front so the returned candidate passes validate_url.
    """

    if not isinstance(text, str) or not text:
        return None
    for match in _URL_PATTERN.finditer(text):
        candidate = match.group(0).rstrip(_URL_TRAILING_PUNCT)
        resolved = _offline_redirect_target(candidate) or candidate
        if validate_url(resolved):
            return resolved
    return None


def detect_platform(url) -> str | None:
    """Detect source platform name from URL."""

    domain = _normalize_domain(url)
    if domain is None:
        return None

    bare = domain[4:] if domain.startswith('www.') else domain
    return _DOMAIN_TO_PLATFORM.get(bare) or _DOMAIN_TO_PLATFORM.get(domain)


def estimate_file_size(info):
    """Estimate media size in MB from yt-dlp info when possible."""

    try:
        formats = info.get('formats', [])
        for fmt in formats:
            if fmt.get('filesize'):
                return fmt['filesize'] / (1024 * 1024)

        duration = info.get('duration', 0)
        if duration:
            bitrate_mbps = 5
            return duration * bitrate_mbps * 0.125
        return None
    except Exception:
        return None
