# AGENTS.md

This file provides guidance to Codex and compatible coding agents when working in this repository.

## Project Overview

This project is a modular Python Telegram bot for:
- downloading YouTube video/audio,
- transcribing audio with Groq Whisper,
- generating summaries with Claude.

The app runs in two modes:
- Telegram bot (`python main.py`)
- CLI (`python main.py --cli ...`)

## Core Architecture

### Entry point
- `main.py`

### Main package
- `bot/config.py` - config loading, authorized users, download history
- `bot/security.py` - rate limit, URL whitelist, PIN lockout, shared runtime state
- `bot/cleanup.py` - cleanup and disk monitoring
- `bot/downloader.py` - yt-dlp wrappers
- `bot/transcription.py` - audio split/transcribe/post-process/summarize
- `bot/telegram_commands.py` - `/start`, `/help`, `/status`, `/history`, `/cleanup`, `/users`
- `bot/telegram_callbacks.py` - inline callback router and download/transcription flows
- `bot/cli.py` - CLI argument parsing and interactive curses flow

### Storage model
- File-based persistence (JSON), no database.
- `authorized_users.json` for access persistence.
- `download_history.json` for download stats/history.
- `downloads/<chat_id>/` for user output files.

## Development Commands

### Setup
```bash
# Interactive config writer
python setup_config.py
```

### Install dependencies
```bash
# Poetry (recommended)
poetry install

# or pip
pip install -r requirements.txt
```

### Run
```bash
python main.py
# or
poetry run python main.py
```

### Tests
```bash
python -m pytest tests/
```

Note: async tests require `pytest-asyncio` in the active environment.

## Configuration Requirements

Required keys (env vars or `api_key.md`):
- `TELEGRAM_BOT_TOKEN`
- `GROQ_API_KEY`
- `CLAUDE_API_KEY`
- `PIN_CODE` (8 digits)

Load priority:
1. Environment variables
2. `.env`
3. `api_key.md`
4. Defaults

## Language Policy

Keep this split:
- User-facing bot messages: Polish
- Code, comments, docs, commits: English

When editing user messages, maintain Polish UX consistency.

## Engineering Guidelines

1. Keep module boundaries clear; avoid moving business logic into `main.py`.
2. Prefer extending existing workflows in `telegram_commands.py` / `telegram_callbacks.py` over duplicating logic.
3. Reuse shared runtime maps from `bot/security.py` (`user_urls`, `user_time_ranges`) consistently.
4. Preserve graceful error handling:
   - user-safe messages in Telegram,
   - technical detail in logs.
5. Be mindful of Telegram limits and large-file behavior when changing media/transcription flows.

## Security Expectations

- Do not commit secrets (`api_key.md` must stay ignored).
- Keep URL validation strict (HTTPS + allowed YouTube domains).
- Do not bypass rate limiting/PIN checks in command handlers.
- Maintain cleanup behavior for disk safety.

## Dependency Notes

- Python target: 3.12+ (`pyproject.toml`)
- System dependency: `ffmpeg`
- Main libs: `yt-dlp`, `python-telegram-bot`, `mutagen`, `requests`, `python-dotenv`

## Deployment Notes

- Suitable for local machine, VPS, or Raspberry Pi.
- Uses polling mode by default.
- For service mode, run `python /path/to/ytdown/main.py` and ensure config/env is present.
