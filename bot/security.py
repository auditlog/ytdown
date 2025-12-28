"""
Security module for YouTube Downloader Telegram Bot.

Handles rate limiting, URL validation, user management, and file size estimation.
"""

import time
import logging
from collections import defaultdict
from urllib.parse import urlparse

from bot.config import authorized_users, save_authorized_users

# Maximum failed attempts before blocking
MAX_ATTEMPTS = 3

# Block time in seconds (15 minutes)
BLOCK_TIME = 15 * 60

# Rate limiting - max requests per user
RATE_LIMIT_REQUESTS = 10  # number of requests
RATE_LIMIT_WINDOW = 60    # time window in seconds

# Maximum file size for download (in MB)
MAX_FILE_SIZE_MB = 1000  # 1GB limit

# Maximum MP3 part size for transcription (in MB)
# Groq API has 25MB limit, use 20MB for safety margin
MAX_MP3_PART_SIZE_MB = 20

# Allowed domains
ALLOWED_DOMAINS = [
    'youtube.com',
    'www.youtube.com',
    'youtu.be',
    'm.youtube.com',
    'music.youtube.com'
]

# State variables
failed_attempts = defaultdict(int)
block_until = defaultdict(float)
user_requests = defaultdict(list)

# Dictionary to store URLs (key: chat_id, value: url)
# Needed because callback_data has 64 byte limit
user_urls = {}

# Dictionary to store time ranges (key: chat_id, value: {"start": "0:30", "end": "5:45"})
user_time_ranges = {}


def check_rate_limit(user_id):
    """
    Checks if user hasn't exceeded request limit.

    Args:
        user_id: Telegram user ID

    Returns:
        bool: True if can continue, False if limit exceeded
    """
    current_time = time.time()

    # Remove old requests outside time window
    user_requests[user_id] = [
        req_time for req_time in user_requests[user_id]
        if current_time - req_time < RATE_LIMIT_WINDOW
    ]

    # Check if limit exceeded
    if len(user_requests[user_id]) >= RATE_LIMIT_REQUESTS:
        return False

    # Add new request
    user_requests[user_id].append(current_time)
    return True


def validate_youtube_url(url):
    """
    Validates YouTube URL.

    Args:
        url: URL to validate

    Returns:
        bool: True if URL is valid, False otherwise
    """
    try:
        # Only HTTPS is allowed (secure connection)
        if not url.startswith('https://'):
            return False

        parsed = urlparse(url)
        domain = parsed.netloc.lower()

        # Remove 'www.' if exists
        if domain.startswith('www.'):
            domain = domain[4:]

        # Check if domain is in allowed list
        return domain in ALLOWED_DOMAINS
    except:
        return False


def manage_authorized_user(user_id, action='add'):
    """
    Manages authorized users.

    Args:
        user_id (int): User ID
        action (str): 'add' or 'remove'

    Returns:
        bool: True if operation succeeded
    """
    try:
        if action == 'add':
            if user_id not in authorized_users:
                authorized_users.add(user_id)
                save_authorized_users(authorized_users)
                logging.info(f"Added user {user_id} to authorized")
                return True
            else:
                logging.info(f"User {user_id} is already authorized")
                return True

        elif action == 'remove':
            if user_id in authorized_users:
                authorized_users.discard(user_id)
                save_authorized_users(authorized_users)
                logging.info(f"Removed user {user_id} from authorized")
                return True
            else:
                logging.info(f"User {user_id} was not authorized")
                return True
        else:
            logging.error(f"Unknown action: {action}")
            return False

    except Exception as e:
        logging.error(f"Error managing user {user_id}: {e}")
        return False


def estimate_file_size(info):
    """
    Estimates file size based on yt-dlp info.

    Args:
        info: Video info dictionary from yt-dlp

    Returns:
        float or None: Size in MB or None if cannot estimate
    """
    try:
        # Try to find format with size
        formats = info.get('formats', [])
        for fmt in formats:
            if fmt.get('filesize'):
                return fmt['filesize'] / (1024 * 1024)

        # If no exact size, try to estimate
        duration = info.get('duration', 0)
        if duration:
            # Assume average bitrate for different qualities
            bitrate_mbps = 5  # 5 Mbps for average quality video
            estimated_mb = (duration * bitrate_mbps * 0.125)
            return estimated_mb

        return None
    except:
        return None
