"""yt-dlp extractors for TOK FM episodes (audycje.tokfm.pl).

Loaded through yt-dlp's plugin system: the bot runs main.py from the repo root,
so the root is on sys.path and yt-dlp discovers the ``yt_dlp_plugins`` namespace
package there. Neither ``yt_dlp_plugins/`` nor ``yt_dlp_plugins/extractor/`` may
contain an ``__init__.py``.

See also: bot/download_errors.py (maps SESSION_ERROR and NON_EPISODE_ERROR to
Polish messages), docs/superpowers/specs/2026-11-18-tokfm-podcast-design.md.
"""

import json
import re

from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.networking import HEADRequest
from yt_dlp.utils import ExtractorError, extract_attributes, int_or_none, url_or_none

__all__ = ['TokFMIE', 'TokFMNonEpisodeIE']

# Matched (lowercased) by bot/download_errors.py. The word "cookies" also makes
# bot/handlers/download_callbacks.py show the TOK FM cookies hint when the
# session expires between the format menu and the download.
SESSION_ERROR = 'TOK FM premium session missing or expired; refresh TOK FM cookies'
NON_EPISODE_ERROR = 'TOK FM: only episode links are supported (audycje.tokfm.pl/podcast/...)'

_BASE_URL = 'https://audycje.tokfm.pl'
# TOK FM serves constant-bitrate 64 kb/s MP3, i.e. 8000 bytes per second, so the
# file size gives the exact duration (the page only shows rounded minutes).
_MP3_BYTES_PER_SECOND = 8000


def _quoted_names(value):
    """Parse TOK FM's "'Name One', 'Name Two'" attribute format into a list."""

    names = re.findall(r"'([^']+)'", value or '')
    return names or None


class TokFMIE(InfoExtractor):
    IE_NAME = 'tokfm'
    _VALID_URL = r'https?://audycje\.tokfm\.pl/podcast/(?P<id>\d+)'

    def _real_extract(self, url):
        video_id = self._match_id(url)
        webpage = self._download_webpage(url, video_id)
        player = self._find_player_attrs(webpage, video_id)

        # Fail closed: only an episode the page explicitly marks as free skips the
        # session check, so a markup change can never turn premium into "free".
        is_free = (
            player.get('data-podcast_free') == 'true'
            and player.get('data-only_for_premium') != 'true'
        )
        if not is_free:
            self._check_premium_access(video_id)

        audio_url = self._fetch_audio_url(video_id)
        filesize = self._fetch_audio_size(audio_url, video_id)

        audio_format = {
            'format_id': 'mp3',
            'url': audio_url,
            'ext': 'mp3',
            'acodec': 'mp3',
            'vcodec': 'none',
            'abr': 64,
        }
        if filesize:
            audio_format['filesize'] = filesize

        return {
            'id': video_id,
            'title': player['data-podcast-title'],
            'creators': _quoted_names(player.get('data-leader')),
            'cast': _quoted_names(player.get('data-guests')),
            'thumbnail': self._og_search_thumbnail(webpage, default=None),
            'duration': round(filesize / _MP3_BYTES_PER_SECOND) if filesize else None,
            'formats': [audio_format],
        }

    def _find_player_attrs(self, webpage, video_id):
        # The page may also hold play buttons of other (recommended) episodes, so the
        # player element is matched by this episode's id, not taken as the first one.
        pattern = r'<[a-zA-Z][^>]*\bdata-id="%s"[^>]*>' % re.escape(video_id)
        for match in re.finditer(pattern, webpage):
            attrs = extract_attributes(match.group(0))
            if attrs.get('data-podcast-title'):
                return attrs
        raise ExtractorError('TOK FM episode page has no player data', video_id=video_id)

    def _check_premium_access(self, video_id):
        # /gets currently returns premium audio without checking the session. The
        # paywall is therefore enforced here, before /gets is ever asked: without a
        # live premium session the extractor refuses (anti-goal in the design spec).
        # /premium/blokada answers 204 when the session may play the episode and 200
        # with a paywall page when it may not; anything but 204 is treated as no access.
        urlh = self._request_webpage(
            f'{_BASE_URL}/premium/blokada?podcast={video_id}', video_id,
            note='Checking TOK FM premium access',
            expected_status=lambda _: True)
        if urlh.status != 204:
            raise ExtractorError(
                f'{SESSION_ERROR} (access check returned HTTP {urlh.status})',
                expected=True, video_id=video_id)

    def _fetch_audio_url(self, video_id):
        # Mirrors the web player's request; the returned URL is signed and short-lived,
        # which is fine because every download re-runs the extractor.
        response = self._download_json(
            f'{_BASE_URL}/gets', video_id,
            note='Fetching TOK FM audio URL',
            data=json.dumps({'pid': int(video_id), 'st': 'tokfm'}).encode(),
            headers={'X-Tuba': 'tokfm', 'Content-Type': 'text/plain;charset=UTF-8'})
        audio_url = url_or_none(response.get('url')) if isinstance(response, dict) else None
        if not audio_url:
            raise ExtractorError('TOK FM did not return an audio URL', video_id=video_id)
        return audio_url

    def _fetch_audio_size(self, audio_url, video_id):
        # Without a size the bot would estimate ~1.5 GB for a 42-minute episode from
        # its video default bitrate, so an unknown size is left unknown, not guessed.
        urlh = self._request_webpage(
            HEADRequest(audio_url), video_id,
            note='Checking TOK FM audio size',
            errnote='Unable to check TOK FM audio size', fatal=False)
        if not urlh:
            return None
        return int_or_none(urlh.headers.get('Content-Length'))


class TokFMNonEpisodeIE(InfoExtractor):
    """Rejects every other audycje.tokfm.pl page (series, player, home) with a clear error.

    Without it such links fall through to yt-dlp's generic extractor and end in an
    unhelpful "Unsupported URL".
    """

    IE_NAME = 'tokfm:non-episode'
    _VALID_URL = r'https?://audycje\.tokfm\.pl(?=[/?#]|$)(?!/podcast/\d)'

    def _real_extract(self, url):
        raise ExtractorError(NON_EPISODE_ERROR, expected=True)
