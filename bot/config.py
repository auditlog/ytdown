"""
Configuration module for YouTube Downloader Telegram Bot.

Handles loading and validation of configuration from various sources:
- Environment variables (highest priority)
- .env file
- api_key.md file
- Default values (lowest priority)
"""

import os
import re
import json
import shutil
import logging
from datetime import datetime

# Configuration file path
CONFIG_FILE_PATH = "api_key.md"

# Default configuration values
DEFAULT_CONFIG = {
    "TELEGRAM_BOT_TOKEN": "",
    "GROQ_API_KEY": "",
    "PIN_CODE": "12345678",
    "CLAUDE_API_KEY": ""
}

# Download directory
DOWNLOAD_PATH = "./downloads"

# Create download directory if it doesn't exist
os.makedirs(DOWNLOAD_PATH, exist_ok=True)

# Path to authorized users file
AUTHORIZED_USERS_FILE = "authorized_users.json"


def load_config():
    """
    Loads configuration from api_key.md or environment variables.
    Priority: environment variables > .env file > api_key.md > default values

    Returns:
        dict: Configuration dictionary
    """
    config = DEFAULT_CONFIG.copy()

    # Optional .env support
    try:
        from dotenv import load_dotenv
        load_dotenv()
        logging.info("Loaded .env file (if exists)")
    except ImportError:
        pass

    # Try to load from file
    try:
        if os.path.exists(CONFIG_FILE_PATH):
            with open(CONFIG_FILE_PATH, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and '=' in line:
                        key, value = line.split('=', 1)
                        config[key] = value
            logging.info("Loaded configuration from file")
        else:
            logging.warning(f"Configuration file {CONFIG_FILE_PATH} does not exist.")
    except Exception as e:
        logging.error(f"Error loading configuration from file: {e}")

    # Override with environment variables
    env_vars = {
        "TELEGRAM_BOT_TOKEN": os.environ.get("TELEGRAM_BOT_TOKEN"),
        "GROQ_API_KEY": os.environ.get("GROQ_API_KEY"),
        "CLAUDE_API_KEY": os.environ.get("CLAUDE_API_KEY"),
        "PIN_CODE": os.environ.get("PIN_CODE")
    }

    for key, value in env_vars.items():
        if value:
            config[key] = value
            logging.info(f"Using environment variable for {key}")

    # Check for required keys
    if not config.get("TELEGRAM_BOT_TOKEN"):
        logging.error("ERROR: Missing TELEGRAM_BOT_TOKEN! Set in api_key.md or as environment variable.")

    # Validate configuration
    validate_config(config)

    return config


def validate_config(config):
    """
    Validates configuration and displays warnings.

    Args:
        config: Configuration dictionary to validate
    """
    # Check PIN format
    pin = config.get("PIN_CODE", "")
    if not pin:
        logging.error("ERROR: Missing PIN_CODE in configuration!")
    elif not pin.isdigit() or len(pin) != 8:
        logging.error(f"ERROR: PIN_CODE must be an 8-digit code! Received: {pin}")
    elif pin == "12345678":
        logging.warning("WARNING: Using default PIN! Change it for security.")

    # Check Telegram token
    telegram_token = config.get("TELEGRAM_BOT_TOKEN", "")
    if telegram_token:
        if not re.match(r'^\d{8,10}:[A-Za-z0-9_-]{35}$', telegram_token):
            logging.warning("WARNING: TELEGRAM_BOT_TOKEN format may be invalid!")

    # Check Groq key
    groq_key = config.get("GROQ_API_KEY", "")
    if groq_key and len(groq_key) < 20:
        logging.warning("WARNING: GROQ_API_KEY seems too short!")

    # Check Claude key
    claude_key = config.get("CLAUDE_API_KEY", "")
    if claude_key and not claude_key.startswith("sk-"):
        logging.warning("WARNING: CLAUDE_API_KEY should start with 'sk-'!")

    # Check config file permissions (Unix only)
    if os.path.exists(CONFIG_FILE_PATH) and hasattr(os, 'stat'):
        try:
            file_stats = os.stat(CONFIG_FILE_PATH)
            file_mode = oct(file_stats.st_mode)[-3:]
            if file_mode != '600':
                logging.warning(f"WARNING: File {CONFIG_FILE_PATH} has permissions {file_mode}. "
                              f"Recommended: 600 (owner read/write only).")
                logging.warning(f"Run: chmod 600 {CONFIG_FILE_PATH}")
        except:
            pass


def load_authorized_users():
    """
    Loads list of authorized users from JSON file.

    Returns:
        set: Set of user_id for authorized users
    """
    try:
        if os.path.exists(AUTHORIZED_USERS_FILE):
            with open(AUTHORIZED_USERS_FILE, 'r') as f:
                data = json.load(f)
                return set(int(user_id) for user_id in data.get('authorized_users', []))
        else:
            logging.info(f"File {AUTHORIZED_USERS_FILE} does not exist. Creating new.")
            return set()
    except (json.JSONDecodeError, ValueError, IOError) as e:
        logging.warning(f"Error loading {AUTHORIZED_USERS_FILE}: {e}")
        logging.warning("Using empty authorized users list.")
        return set()


def save_authorized_users(authorized_users_set):
    """
    Saves list of authorized users to JSON file.

    Args:
        authorized_users_set: Set of authorized user IDs
    """
    try:
        data = {
            'authorized_users': [str(user_id) for user_id in authorized_users_set],
            'last_updated': datetime.now().isoformat(),
            'version': '1.0'
        }

        # Write to temp file then move (atomic write)
        temp_file = AUTHORIZED_USERS_FILE + '.tmp'
        with open(temp_file, 'w') as f:
            json.dump(data, f, indent=2)

        shutil.move(temp_file, AUTHORIZED_USERS_FILE)

        # Set secure permissions (Unix only)
        if hasattr(os, 'chmod'):
            os.chmod(AUTHORIZED_USERS_FILE, 0o600)

        logging.debug(f"Saved {len(authorized_users_set)} authorized users to {AUTHORIZED_USERS_FILE}")

    except (IOError, OSError) as e:
        logging.error(f"Error saving {AUTHORIZED_USERS_FILE}: {e}")


# Path to download history file
DOWNLOAD_HISTORY_FILE = "download_history.json"

# Maximum number of history entries to keep
MAX_HISTORY_ENTRIES = 500


def load_download_history():
    """
    Loads download history from JSON file.

    Returns:
        list: List of download records
    """
    try:
        if os.path.exists(DOWNLOAD_HISTORY_FILE):
            with open(DOWNLOAD_HISTORY_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('downloads', [])
        else:
            return []
    except (json.JSONDecodeError, ValueError, IOError) as e:
        logging.warning(f"Error loading {DOWNLOAD_HISTORY_FILE}: {e}")
        return []


def save_download_history(history):
    """
    Saves download history to JSON file.

    Args:
        history: List of download records
    """
    try:
        # Keep only the last MAX_HISTORY_ENTRIES
        if len(history) > MAX_HISTORY_ENTRIES:
            history = history[-MAX_HISTORY_ENTRIES:]

        data = {
            'downloads': history,
            'last_updated': datetime.now().isoformat(),
            'version': '1.0'
        }

        temp_file = DOWNLOAD_HISTORY_FILE + '.tmp'
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        shutil.move(temp_file, DOWNLOAD_HISTORY_FILE)
        logging.debug(f"Saved {len(history)} download records to {DOWNLOAD_HISTORY_FILE}")

    except (IOError, OSError) as e:
        logging.error(f"Error saving {DOWNLOAD_HISTORY_FILE}: {e}")


def add_download_record(user_id, title, url, format_type, file_size_mb=None, time_range=None):
    """
    Adds a download record to history.

    Args:
        user_id: Telegram user ID
        title: Video/audio title
        url: YouTube URL
        format_type: Download format (e.g., "video_best", "audio_mp3")
        file_size_mb: File size in MB (optional)
        time_range: Time range dict (optional)
    """
    history = load_download_history()

    record = {
        'timestamp': datetime.now().isoformat(),
        'user_id': user_id,
        'title': title,
        'url': url,
        'format': format_type,
    }

    if file_size_mb:
        record['file_size_mb'] = round(file_size_mb, 2)

    if time_range:
        record['time_range'] = f"{time_range.get('start', '0:00')}-{time_range.get('end', 'end')}"

    history.append(record)
    save_download_history(history)


def get_download_stats(user_id=None):
    """
    Gets download statistics.

    Args:
        user_id: Optional user ID to filter stats

    Returns:
        dict: Statistics dictionary
    """
    history = load_download_history()

    if user_id:
        history = [h for h in history if h.get('user_id') == user_id]

    total_downloads = len(history)
    total_size = sum(h.get('file_size_mb', 0) for h in history)

    # Count by format
    format_counts = {}
    for h in history:
        fmt = h.get('format', 'unknown')
        format_counts[fmt] = format_counts.get(fmt, 0) + 1

    return {
        'total_downloads': total_downloads,
        'total_size_mb': round(total_size, 2),
        'format_counts': format_counts,
        'recent': history[-10:][::-1] if history else []  # Last 10, newest first
    }


# Initialize configuration on module load
CONFIG = load_config()
BOT_TOKEN = CONFIG["TELEGRAM_BOT_TOKEN"]
PIN_CODE = CONFIG["PIN_CODE"]

# Load authorized users from JSON file
authorized_users = load_authorized_users()
