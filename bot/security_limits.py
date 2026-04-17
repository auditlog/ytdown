"""Shared security-related limits and size thresholds."""

from __future__ import annotations

# Maximum failed attempts before blocking
MAX_ATTEMPTS = 3

# Block time in seconds (15 minutes)
BLOCK_TIME = 15 * 60

# Rate limiting - max requests per user
RATE_LIMIT_REQUESTS = 10
RATE_LIMIT_WINDOW = 60

# Maximum file size for download (in MB)
MAX_FILE_SIZE_MB = 1000

# Telegram Bot API upload limit (in MB) — files larger than this
# require MTProto (pyrogram) to send
TELEGRAM_UPLOAD_LIMIT_MB = 50

# Maximum MP3 part size for transcription (in MB)
MAX_MP3_PART_SIZE_MB = 20

# Timeout for ffmpeg operations (in seconds)
FFMPEG_TIMEOUT = 180

# Maximum number of playlist items to download (default / expanded)
MAX_PLAYLIST_ITEMS = 10
MAX_PLAYLIST_ITEMS_EXPANDED = 50
