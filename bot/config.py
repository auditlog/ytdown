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
import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from collections.abc import Mapping
from bot.repositories import (
    AuthorizedUsersRepository,
    DownloadHistoryRepository,
    DownloadRecord,
)

# Configuration file path
CONFIG_FILE_PATH = "api_key.md"

# Default configuration values
DEFAULT_CONFIG = {
    "TELEGRAM_BOT_TOKEN": "",
    "GROQ_API_KEY": "",
    "PIN_CODE": "12345678",
    "CLAUDE_API_KEY": "",
    "ADMIN_CHAT_ID": "",
    "SPOTIFY_CLIENT_ID": "",
    "SPOTIFY_CLIENT_SECRET": "",
    "TELEGRAM_API_ID": "",
    "TELEGRAM_API_HASH": "",
}

# Download directory (absolute path based on project root)
DOWNLOAD_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "downloads")

# Path to authorized users file
AUTHORIZED_USERS_FILE = "authorized_users.json"


@dataclass
class RuntimeServices:
    """Runtime persistence services initialized during bootstrap."""

    authorized_users_repository: AuthorizedUsersRepository
    download_history_repository: DownloadHistoryRepository

def _read_config_file(file_path: str) -> dict:
    """Reads key/value pairs from api_key.md-like config file."""

    data: dict[str, str] = {}
    with open(file_path, "r", encoding="utf-8") as file:
        for line_no, line in enumerate(file, start=1):
            raw_line = line.strip()
            if not raw_line or raw_line.startswith("#"):
                continue
            if "=" not in raw_line:
                logging.warning(
                    "Invalid config line in %s:%s: %s",
                    file_path,
                    line_no,
                    raw_line,
                )
                continue

            key, value = raw_line.split("=", 1)
            data[key.strip()] = value.strip()
    return data


def load_config(
    config_file_path: str = CONFIG_FILE_PATH,
    *,
    env: Mapping[str, str | None] | None = None,
    load_env_file: bool = True,
    ensure_downloads_dir: bool = False,
) -> dict:
    """
    Loads configuration from api_key.md or environment variables.
    Priority: environment variables > .env file > config file > defaults.

    Returns:
        dict: Configuration dictionary
    """
    config = DEFAULT_CONFIG.copy()

    # Optional .env support
    if load_env_file:
        try:
            from dotenv import load_dotenv
            load_dotenv()
            logging.info("Loaded .env file (if exists)")
        except ImportError:
            logging.debug("python-dotenv not installed; skipping .env loading")

    environment = os.environ if env is None else env

    # Try to load from file
    try:
        if os.path.exists(config_file_path):
            file_values = _read_config_file(config_file_path)
            config.update(file_values)
            logging.info("Loaded configuration from file")
        else:
            logging.warning(
                "Configuration file %s does not exist.", config_file_path
            )
    except Exception as e:
        logging.error("Error loading configuration from file: %s", e)

    # Override with environment variables
    overrides = {
        "TELEGRAM_BOT_TOKEN": environment.get("TELEGRAM_BOT_TOKEN"),
        "GROQ_API_KEY": environment.get("GROQ_API_KEY"),
        "CLAUDE_API_KEY": environment.get("CLAUDE_API_KEY"),
        "PIN_CODE": environment.get("PIN_CODE"),
        "ADMIN_CHAT_ID": environment.get("ADMIN_CHAT_ID"),
        "TELEGRAM_API_ID": environment.get("TELEGRAM_API_ID"),
        "TELEGRAM_API_HASH": environment.get("TELEGRAM_API_HASH"),
    }

    for key, value in overrides.items():
        if value:
            config[key] = value
            logging.info(f"Using environment variable for {key}")

    # Check for required keys
    if not config.get("TELEGRAM_BOT_TOKEN"):
        logging.error("ERROR: Missing TELEGRAM_BOT_TOKEN! Set in api_key.md or as environment variable.")

    # Validate configuration
    validate_config(config, config_file_path=config_file_path)

    if ensure_downloads_dir:
        ensure_download_path()

    return config


def ensure_download_path(path: str = DOWNLOAD_PATH) -> str:
    """Ensure downloads directory exists."""

    os.makedirs(path, exist_ok=True)
    return path


def validate_config(config, *, config_file_path: str = CONFIG_FILE_PATH):
    """
    Validates configuration and displays warnings.

    Args:
        config: Configuration dictionary to validate
    """
    # Check PIN format (exactly 8 digits)
    pin = config.get("PIN_CODE", "")
    if not pin:
        logging.error("ERROR: Missing PIN_CODE in configuration!")
    elif not pin.isdigit() or len(pin) != 8:
        logging.error(
            "ERROR: PIN_CODE format invalid (length=%d, digits_only=%s, expected=8 digits)",
            len(pin),
            pin.isdigit(),
        )
    elif pin == "12345678":
        logging.error("SECURITY: Using default PIN_CODE! Change it immediately for production use.")

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

    # Check config file permissions (Unix only) and auto-fix if possible
    if os.path.exists(config_file_path) and hasattr(os, 'stat'):
        try:
            file_stats = os.stat(config_file_path)
            file_mode = oct(file_stats.st_mode)[-3:]
            if file_mode != '600':
                logging.warning("Config file %s has permissions %s, expected 600", config_file_path, file_mode)
                try:
                    os.chmod(config_file_path, 0o600)
                    logging.info("Fixed %s permissions to 600", config_file_path)
                except OSError as e:
                    logging.warning("Could not fix permissions for %s: %s", config_file_path, e)
        except Exception:
            logging.debug("Unable to verify config file permissions for %s", config_file_path)


def load_authorized_users():
    """
    Loads list of authorized users from JSON file.

    Returns:
        set: Set of user_id for authorized users
    """
    return _replace_runtime_authorized_users(get_authorized_users_repository().load())


def save_authorized_users(authorized_users_set):
    """
    Saves list of authorized users to JSON file.

    Args:
        authorized_users_set: Set of authorized user IDs
    """
    normalized_users = {int(user_id) for user_id in authorized_users_set}
    get_authorized_users_repository().save(normalized_users)
    return _replace_runtime_authorized_users(normalized_users)


# Path to download history file
DOWNLOAD_HISTORY_FILE = "download_history.json"

# Maximum number of history entries to keep
MAX_HISTORY_ENTRIES = 500

# Lock for thread-safe history operations
_history_lock = threading.RLock()

# Lock for thread-safe authorized users operations
_auth_lock = threading.RLock()


def build_runtime_services() -> RuntimeServices:
    """Build runtime persistence services for the current repository paths."""

    return RuntimeServices(
        authorized_users_repository=AuthorizedUsersRepository(
            AUTHORIZED_USERS_FILE,
            lock=_auth_lock,
        ),
        download_history_repository=DownloadHistoryRepository(
            DOWNLOAD_HISTORY_FILE,
            max_entries=MAX_HISTORY_ENTRIES,
            lock=_history_lock,
        ),
    )


RUNTIME_SERVICES = build_runtime_services()


def _refresh_runtime_services_if_needed() -> RuntimeServices:
    """Rebuild runtime services when repository paths changed at runtime."""

    global RUNTIME_SERVICES

    services = RUNTIME_SERVICES
    users_path_changed = services.authorized_users_repository.path != AUTHORIZED_USERS_FILE
    history_path_changed = services.download_history_repository.path != DOWNLOAD_HISTORY_FILE

    if users_path_changed or history_path_changed:
        RUNTIME_SERVICES = build_runtime_services()

    return RUNTIME_SERVICES


def get_runtime_services() -> RuntimeServices:
    """Return active runtime persistence services."""

    return _refresh_runtime_services_if_needed()


def get_authorized_users_repository() -> AuthorizedUsersRepository:
    """Return active authorized users repository."""

    return get_runtime_services().authorized_users_repository


def get_download_history_repository() -> DownloadHistoryRepository:
    """Return active download history repository."""

    return get_runtime_services().download_history_repository


def load_download_history():
    """
    Loads download history from JSON file.

    Returns:
        list: List of download records
    """
    return get_download_history_repository().load()


def save_download_history(history):
    """
    Saves download history to JSON file.

    Args:
        history: List of download records
    """
    get_download_history_repository().save(history)


def add_download_record(
    user_id, title, url, format_type, file_size_mb=None, time_range=None,
    status="success", selected_format=None, error_message=None,
):
    """
    Adds a download record to history.

    Thread-safe: uses lock to prevent race conditions during concurrent downloads.

    Args:
        user_id: Telegram user ID
        title: Video/audio title
        url: YouTube URL
        format_type: Download format (e.g., "video_best", "audio_mp3")
        file_size_mb: File size in MB (optional)
        time_range: Time range dict (optional)
        status: "success" or "failure" (default "success")
        selected_format: Raw format string passed to yt-dlp (optional)
        error_message: Error description when status is "failure" (optional)
    """
    record = DownloadRecord(
        timestamp=datetime.now().isoformat(),
        user_id=user_id,
        title=title,
        url=url,
        format=format_type,
        status=status,
        file_size_mb=round(file_size_mb, 2) if file_size_mb is not None else None,
        time_range=(
            f"{time_range.get('start', '0:00')}-{time_range.get('end', 'end')}"
            if time_range else None
        ),
        selected_format=selected_format,
        error_message=str(error_message)[:200] if error_message else None,
    )
    get_download_history_repository().append(record)


def get_download_stats(user_id=None):
    """
    Gets download statistics.

    Args:
        user_id: Optional user ID to filter stats

    Returns:
        dict: Statistics dictionary
    """
    return get_download_history_repository().stats(user_id=user_id)


CONFIG: dict = {}
BOT_TOKEN = ""
PIN_CODE = ""
ADMIN_CHAT_ID = ""
# Active runtime cache for authorization state. It remains available for legacy
# code paths without an attached AppRuntime, but mutations should still flow
# through the helper API below so the repository and in-memory cache stay in
# sync.
authorized_users: set[int] = set()


def _replace_runtime_authorized_users(user_ids) -> set[int]:
    """Replace runtime authorized-user cache in place and return it."""

    authorized_users.clear()
    authorized_users.update(int(user_id) for user_id in user_ids)
    return authorized_users


def initialize_runtime(
    *,
    config_file_path: str = CONFIG_FILE_PATH,
    env: Mapping[str, str | None] | None = None,
    load_env_file: bool = True,
    ensure_downloads_dir: bool = True,
) -> dict:
    """Load runtime configuration and update exported globals in place."""
    global RUNTIME_SERVICES

    loaded_config = load_config(
        config_file_path=config_file_path,
        env=env,
        load_env_file=load_env_file,
        ensure_downloads_dir=ensure_downloads_dir,
    )
    RUNTIME_SERVICES = build_runtime_services()
    loaded_users = load_authorized_users()

    CONFIG.clear()
    CONFIG.update(loaded_config)

    global BOT_TOKEN, PIN_CODE, ADMIN_CHAT_ID
    BOT_TOKEN = CONFIG["TELEGRAM_BOT_TOKEN"]
    PIN_CODE = CONFIG["PIN_CODE"]
    ADMIN_CHAT_ID = CONFIG.get("ADMIN_CHAT_ID", "")
    _replace_runtime_authorized_users(loaded_users)

    return CONFIG


def get_runtime_config() -> dict:
    """Return the active runtime configuration mapping."""

    return CONFIG


def get_runtime_value(key: str, default=None):
    """Return one value from the active runtime configuration."""

    return CONFIG.get(key, default)


def get_runtime_authorized_users() -> set[int]:
    """Return the active in-memory authorized user set."""

    return authorized_users


def add_runtime_authorized_user(user_id: int) -> bool:
    """Add one authorized user to runtime state and persist it."""

    if user_id in authorized_users:
        return False

    save_authorized_users({*authorized_users, user_id})
    return True


def remove_runtime_authorized_user(user_id: int) -> bool:
    """Remove one authorized user from runtime state and persist it."""

    if user_id not in authorized_users:
        return False

    save_authorized_users(
        {authorized_id for authorized_id in authorized_users if authorized_id != user_id}
    )
    return True


# Initialize runtime state on module load for backward compatibility.
initialize_runtime()
