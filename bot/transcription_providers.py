"""Provider adapters for Groq transcription and Claude summarization."""

from __future__ import annotations

import logging
import os
import time

import requests

from bot.config import get_runtime_value
from bot.transcription_limits import (
    CLAUDE_API_MAX_RETRIES,
    CLAUDE_API_RETRY_BASE_DELAY,
    CLAUDE_MAX_OUTPUT_TOKENS,
    POST_PROCESS_MAX_INPUT_TOKENS,
    SUMMARY_MAX_INPUT_TOKENS,
    estimate_token_count,
)


def get_api_key(*, config_getter=get_runtime_value):
    """Return Groq API key from runtime configuration."""

    return config_getter("GROQ_API_KEY", "")


def get_claude_api_key(*, config_getter=get_runtime_value):
    """Return Claude API key from runtime configuration."""

    return config_getter("CLAUDE_API_KEY", "")


def transcribe_audio(file_path, api_key, language=None, prompt=None, *, requests_module=requests):
    """Transcribe audio file with Groq Whisper."""

    if not os.path.exists(file_path):
        logging.error("File does not exist: %s", file_path)
        return ""

    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
    logging.info("Transcribing file: %s (%.2f MB)", file_path, file_size_mb)
    if file_size_mb > 25:
        logging.error("File too large for Groq API: %.2f MB (max 25 MB)", file_size_mb)
        return ""

    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        with open(file_path, "rb") as audio_file:
            filename = os.path.basename(file_path)
            if not filename.lower().endswith('.mp3'):
                filename = filename.rsplit('.', 1)[0] + '.mp3'

            files = {"file": (filename, audio_file.read(), "audio/mpeg")}
            data = {"model": "whisper-large-v3-turbo", "response_format": "text"}
            if language:
                data["language"] = language
            if prompt:
                data["prompt"] = prompt

            response = requests_module.post(url, headers=headers, files=files, data=data, timeout=300)
            if response.status_code == 200:
                result = response.text.strip()
                if result:
                    logging.debug("Transcription received: %s characters", len(result))
                    return result
                logging.warning("API returned empty transcription")
                return ""

            logging.error("Groq API error: %s", response.status_code)
            logging.error("Response: %s", response.text[:500])
            return ""
    except Exception as e:
        logging.error("Error during transcription: %s", e)
        return ""


def _extract_claude_text(result: dict) -> str:
    """Extract text blocks from an Anthropic messages response."""

    content = ""
    if "content" in result:
        for item in result["content"]:
            if item.get("type") == "text":
                content += item.get("text", "")
    return content.strip()


def post_process_transcript(
    text,
    *,
    api_key=None,
    requests_module=requests,
    sleep_fn=time.sleep,
):
    """Clean raw Whisper transcription using Claude."""

    if not api_key:
        return None

    input_tokens = estimate_token_count(text)
    if input_tokens > POST_PROCESS_MAX_INPUT_TOKENS:
        logging.info(
            "Skipping post-processing: text too long (%s est. tokens, limit %s). Roughly %s characters.",
            f"{input_tokens:,}",
            f"{POST_PROCESS_MAX_INPUT_TOKENS:,}",
            f"{len(text):,}",
        )
        return None

    dynamic_max_tokens = min(input_tokens + 2000, CLAUDE_MAX_OUTPUT_TOKENS)
    dynamic_timeout = max(300, dynamic_max_tokens // 100)
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    data = {
        "model": "claude-haiku-4-5",
        "max_tokens": dynamic_max_tokens,
        "messages": [{
            "role": "user",
            "content": (
                "You are a transcription proofreader. Fix the following audio transcript:\n"
                "- Fix typos and spelling errors\n"
                "- Complete truncated or incomplete words\n"
                "- Fix punctuation\n"
                "- Do NOT change the meaning or structure\n"
                "- Do NOT add new content or commentary\n"
                "- Do NOT add any headers, labels or prefixes\n"
                "- Preserve the original language of the text\n"
                "- Return ONLY the corrected text, nothing else\n\n"
                f"{text}"
            ),
        }],
    }

    for attempt in range(1, CLAUDE_API_MAX_RETRIES + 1):
        try:
            response = requests_module.post(url, headers=headers, json=data, timeout=dynamic_timeout)
            if response.status_code == 200:
                corrected = _extract_claude_text(response.json())
                if corrected:
                    return corrected
                logging.warning("Post-processing returned empty result, using original text")
                return None
            if response.status_code in (429, 500, 502, 503, 529):
                logging.warning(
                    "Claude API post-processing attempt %s/%s failed with status %s",
                    attempt, CLAUDE_API_MAX_RETRIES, response.status_code,
                )
            else:
                logging.error("Claude API error during post-processing: %s", response.status_code)
                logging.error("Response: %s", response.text[:500])
                return None
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            logging.warning(
                "Claude API post-processing attempt %s/%s failed: %s",
                attempt, CLAUDE_API_MAX_RETRIES, e,
            )
        except Exception as e:
            logging.error("Error during transcript post-processing: %s", e)
            return None

        if attempt < CLAUDE_API_MAX_RETRIES:
            delay = CLAUDE_API_RETRY_BASE_DELAY * (2 ** (attempt - 1))
            logging.info("Retrying post-processing in %ss...", delay)
            sleep_fn(delay)

    logging.error("Post-processing failed after %s attempts", CLAUDE_API_MAX_RETRIES)
    return None


def generate_summary(
    transcript_text,
    summary_type,
    *,
    api_key=None,
    requests_module=requests,
    sleep_fn=time.sleep,
):
    """Generate a summary of transcription text with Claude."""

    if not api_key:
        logging.error("Cannot read Claude API key from api_key.md.")
        return None

    input_tokens = estimate_token_count(transcript_text)
    if input_tokens > SUMMARY_MAX_INPUT_TOKENS:
        logging.warning(
            "Text too long for summary (%s est. tokens, limit %s). Roughly %s characters.",
            f"{input_tokens:,}",
            f"{SUMMARY_MAX_INPUT_TOKENS:,}",
            f"{len(transcript_text):,}",
        )
        return None

    prompts = {
        1: "Napisz krótkie podsumowanie następującego tekstu:",
        2: "Napisz szczegółowe i rozbudowane podsumowanie następującego tekstu:",
        3: "Przygotuj podsumowanie w formie punktów (bullet points) następującego tekstu:",
        4: "Przygotuj podział zadań na osoby na podstawie następującego tekstu:",
    }
    summary_max_tokens = {1: 4096, 2: 16384, 3: 8192, 4: 8192}
    dynamic_max_tokens = summary_max_tokens.get(summary_type, 8192)
    dynamic_timeout = max(240, input_tokens // 300)

    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    data = {
        "model": "claude-haiku-4-5",
        "max_tokens": dynamic_max_tokens,
        "messages": [{
            "role": "user",
            "content": f"{prompts.get(summary_type, prompts[1])}\n\n{transcript_text}",
        }],
    }

    for attempt in range(1, CLAUDE_API_MAX_RETRIES + 1):
        try:
            response = requests_module.post(url, headers=headers, json=data, timeout=dynamic_timeout)
            if response.status_code == 200:
                return _extract_claude_text(response.json())
            if response.status_code in (429, 500, 502, 503, 529):
                logging.warning(
                    "Claude API summary attempt %s/%s failed with status %s",
                    attempt, CLAUDE_API_MAX_RETRIES, response.status_code,
                )
            else:
                logging.error("Claude API error: %s", response.status_code)
                logging.error("Response: %s", response.text[:500])
                return None
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            logging.warning(
                "Claude API summary attempt %s/%s failed: %s",
                attempt, CLAUDE_API_MAX_RETRIES, e,
            )
        except Exception as e:
            logging.error("Error generating summary: %s", e)
            return None

        if attempt < CLAUDE_API_MAX_RETRIES:
            delay = CLAUDE_API_RETRY_BASE_DELAY * (2 ** (attempt - 1))
            logging.info("Retrying summary in %ss...", delay)
            sleep_fn(delay)

    logging.error("Summary generation failed after %s attempts", CLAUDE_API_MAX_RETRIES)
    return None
