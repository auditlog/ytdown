"""
Unit tests for JavaScript runtime detection used by yt-dlp.

YouTube's "n challenge" cannot be solved without a JS runtime, and yt-dlp only
enables deno by default. On hosts that ship node instead, every extraction
degrades to "Only images are available for download", so the runtime actually
present has to be detected and passed through.
"""

from bot.config import detect_js_runtimes


def _which_for(*available):
    """Build a shutil.which stand-in that only knows about the given binaries."""
    return lambda name: f"/usr/bin/{name}" if name in available else None


def test_prefers_deno_when_available():
    assert detect_js_runtimes(which=_which_for("deno", "node")) == {"deno": {}}


def test_falls_back_to_node_when_deno_missing():
    assert detect_js_runtimes(which=_which_for("node")) == {"node": {}}


def test_falls_back_to_bun_when_only_bun_present():
    assert detect_js_runtimes(which=_which_for("bun")) == {"bun": {}}


def test_keeps_yt_dlp_default_when_no_runtime_found():
    # Returning an empty dict would disable runtimes entirely, which is worse
    # than leaving yt-dlp on its own default and letting it warn.
    assert detect_js_runtimes(which=_which_for()) == {"deno": {}}
