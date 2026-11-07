"""Shared limits and token heuristics for transcription and summarization."""

from __future__ import annotations

# Claude Haiku 4.5 limits (summaries and custom analyses, served through OpenRouter)
CLAUDE_MAX_OUTPUT_TOKENS = 64_000
CLAUDE_CONTEXT_WINDOW_TOKENS = 200_000

# Conservative thresholds (leave room for prompt overhead)
SUMMARY_MAX_INPUT_TOKENS = 175_000
CUSTOM_PROMPT_MAX_CHARS = 4_000
CUSTOM_ANALYSIS_MAX_OUTPUT_TOKENS = 16_384

# Speaker naming (Claude Sonnet 5.5 through OpenRouter). The reply is a small
# JSON map, but the budget also covers the model's reasoning tokens.
SPEAKER_HINTS_MAX_CHARS = 1_000
SPEAKER_MAX_INPUT_TOKENS = SUMMARY_MAX_INPUT_TOKENS
SPEAKER_MAX_OUTPUT_TOKENS = 8_000

# Approximate speech duration threshold (minutes) for user-facing warnings
SUMMARY_DURATION_LIMIT_MIN = 840

# Retry settings for chat (Claude) calls
CLAUDE_API_MAX_RETRIES = 3
CLAUDE_API_RETRY_BASE_DELAY = 10

# Retry settings for transcription calls. Retry-After can ask for minutes once a
# quota is used up; waiting longer than the cap would stall the whole
# transcription, so such a part fails and is marked instead.
TRANSCRIPTION_API_MAX_RETRIES = 3
TRANSCRIPTION_API_RETRY_BASE_DELAY = 5
RETRY_AFTER_MAX_SEC = 60
RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504, 529})

# OpenRouter rejects multipart uploads above 25 MB. Parts are cut to
# MAX_MP3_PART_SIZE_MB (bot/security_limits.py), which stays below it.
TRANSCRIPTION_UPLOAD_MAX_MB = 25


def estimate_token_count(text: str) -> int:
    """Estimate token count using a rough 4 chars/token heuristic."""

    if not text:
        return 0
    return len(text) // 4


def is_text_too_long_for_summary(text: str) -> bool:
    """Return True if text exceeds the summary context-window limit."""

    return estimate_token_count(text) > SUMMARY_MAX_INPUT_TOKENS


def is_custom_analysis_input_too_long(transcript_text: str, prompt: str) -> bool:
    """Return True when transcript plus custom instruction exceed the safe limit."""

    return (
        estimate_token_count(transcript_text) + estimate_token_count(prompt)
        > SUMMARY_MAX_INPUT_TOKENS
    )
