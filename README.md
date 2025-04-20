# YouTube Downloader Telegram

A bot for downloading YouTube videos with transcription and content summarization capabilities.

## Features

- Downloading YouTube videos in various video formats
- Extracting audio tracks in MP3, M4A, FLAC formats
- Automatic audio transcription using Groq API (Whisper)
- Generating summaries of transcriptions using Claude API
- PIN code access protection
- Console interface and Telegram bot

## Requirements

- Python 3.7+
- yt-dlp
- mutagen
- python-telegram-bot
- ffmpeg (installed on your system)

## Installation

```bash
pip install yt-dlp mutagen python-telegram-bot requests
```

Make sure you have ffmpeg installed on your system.

## Configuration

Create an `api_key.md` file in the main directory with the following content:

TELEGRAM_BOT_TOKEN=your_bot_token
GROQ_API_KEY=your_groq_api_key
CLAUDE_API_KEY=your_claude_api_key
PIN_CODE=12345678 (unless you change it)
