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

# 7z archive volume size depending on transport.
# MTProto bot upload caps single message at ~2 GB; we leave 100 MB margin
# for 7z header overhead and per-message metadata.
MTPROTO_VOLUME_SIZE_MB = 1900

# Bot API upload caps at 50 MB; 49 MB volume keeps slack for the wrapper.
BOTAPI_VOLUME_SIZE_MB = 49

# Per-item size cap for playlist archive mode. Playlist 7z mode allows
# items larger than MAX_FILE_SIZE_MB because the file never has to fit a
# single Telegram message — it will be split into volumes.
MAX_ARCHIVE_ITEM_SIZE_MB = 10240

# How long workspaces (pl_*/big_*) and pending archive jobs survive
# after success, so the user can resend a single failed volume without
# having to re-download the whole playlist.
PLAYLIST_ARCHIVE_RETENTION_MIN = 60

# Cleanup of stale (zombie) entries in JobRegistry. Defends /stop list
# against operations that never unregistered due to bugs or crashes.
JOB_DEAD_AGE_HOURS = 6

# Grace between SIGTERM and SIGKILL when terminating a 7z subprocess
# attached to a JobCancellation. Long enough for 7z to finish writing
# its current 1 MiB block, short enough to not block /stop UX.
JOB_TERMINATE_GRACE_SEC = 1.0
