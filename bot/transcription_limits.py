"""Shared limits and token heuristics for transcription and summarization."""

from __future__ import annotations

# Claude Haiku 4.5 limits
CLAUDE_MAX_OUTPUT_TOKENS = 64_000
CLAUDE_CONTEXT_WINDOW_TOKENS = 200_000

# Conservative thresholds (leave room for prompt overhead)
POST_PROCESS_MAX_INPUT_TOKENS = 50_000
SUMMARY_MAX_INPUT_TOKENS = 175_000

# Approximate speech duration thresholds (minutes) for user-facing warnings
CORRECTION_DURATION_LIMIT_MIN = 270
SUMMARY_DURATION_LIMIT_MIN = 840

# Retry settings for Claude API calls
CLAUDE_API_MAX_RETRIES = 3
CLAUDE_API_RETRY_BASE_DELAY = 10


def estimate_token_count(text: str) -> int:
    """Estimate token count using a rough 4 chars/token heuristic."""

    if not text:
        return 0
    return len(text) // 4


def is_text_too_long_for_correction(text: str) -> bool:
    """Return True if text exceeds the post-processing input limit."""

    return estimate_token_count(text) > POST_PROCESS_MAX_INPUT_TOKENS


def is_text_too_long_for_summary(text: str) -> bool:
    """Return True if text exceeds the summary context-window limit."""

    return estimate_token_count(text) > SUMMARY_MAX_INPUT_TOKENS
