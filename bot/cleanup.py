"""
Cleanup module for YouTube Downloader Telegram Bot.

Handles file cleanup, disk monitoring, and periodic maintenance.
"""

import os
import time
import shutil
import logging
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from bot.config import DOWNLOAD_PATH
from bot.security_limits import PLAYLIST_ARCHIVE_RETENTION_MIN


def cleanup_old_files(directory, max_age_hours=24):
    """
    Deletes files older than specified number of hours.

    Args:
        directory: Directory to clean
        max_age_hours: Maximum file age in hours (default 24)

    Returns:
        int: Number of deleted files
    """
    if not os.path.exists(directory):
        return 0

    current_time = time.time()
    max_age_seconds = max_age_hours * 3600
    deleted_count = 0
    freed_space_mb = 0

    try:
        # Walk through all files in directory and subdirectories (no symlink following)
        for root, dirs, files in os.walk(directory, followlinks=False):
            for filename in files:
                file_path = os.path.join(root, filename)

                # Skip symlinks to prevent traversal attacks
                if os.path.islink(file_path):
                    logging.warning("Skipping symlink during cleanup: %s", file_path)
                    continue

                try:
                    # Check file age
                    file_age = current_time - os.path.getmtime(file_path)

                    if file_age > max_age_seconds:
                        # Get file size before deletion
                        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)

                        # Delete file
                        os.remove(file_path)
                        deleted_count += 1
                        freed_space_mb += file_size_mb

                        logging.info("Deleted old file: %s (%.2f MB)", file_path, file_size_mb)
                except Exception as e:
                    logging.error("Error deleting file %s: %s", file_path, e)

            # Remove empty directories
            try:
                if not os.listdir(root):
                    os.rmdir(root)
                    logging.info("Deleted empty directory: %s", root)
            except OSError as e:
                logging.debug("Skipping empty-directory cleanup for %s: %s", root, e)

    except Exception as e:
        logging.error("Error cleaning directory %s: %s", directory, e)

    if deleted_count > 0:
        logging.info("Cleanup finished: deleted %d files, freed %.2f MB", deleted_count, freed_space_mb)

    return deleted_count


def get_disk_usage():
    """
    Checks disk space usage.

    Returns:
        tuple: (used_gb, free_gb, total_gb, usage_percent)
    """
    # Method 1: shutil.disk_usage (newest and most universal)
    try:
        total, used, free = shutil.disk_usage(DOWNLOAD_PATH)
        total_gb = total / (1024 ** 3)
        free_gb = free / (1024 ** 3)
        used_gb = used / (1024 ** 3)
        usage_percent = (used / total) * 100 if total > 0 else 0

        return used_gb, free_gb, total_gb, usage_percent
    except Exception as e:
        logging.warning("shutil.disk_usage failed: %s", e)

    # Method 2: df command (universal for Unix systems)
    try:
        result = subprocess.run(['df', '-BG', DOWNLOAD_PATH], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            if len(lines) >= 2:
                fields = lines[1].split()
                if len(fields) >= 4:
                    total_gb = float(fields[1].replace('G', ''))
                    used_gb = float(fields[2].replace('G', ''))
                    free_gb = float(fields[3].replace('G', ''))
                    usage_percent = (used_gb / total_gb) * 100 if total_gb > 0 else 0

                    logging.info("Used df command to check disk space")
                    return used_gb, free_gb, total_gb, usage_percent
    except Exception as e:
        logging.warning("df command failed: %s", e)

    # Method 3: os.statvfs (fallback for older systems)
    try:
        stat = os.statvfs(DOWNLOAD_PATH)

        if hasattr(stat, 'f_blocks') and hasattr(stat, 'f_frsize') and hasattr(stat, 'f_avail'):
            total_gb = (stat.f_blocks * stat.f_frsize) / (1024 ** 3)
            free_gb = (stat.f_avail * stat.f_frsize) / (1024 ** 3)
            used_gb = total_gb - free_gb
            usage_percent = (used_gb / total_gb) * 100 if total_gb > 0 else 0

            logging.info("Used os.statvfs to check disk space")
            return used_gb, free_gb, total_gb, usage_percent
    except Exception as e:
        logging.warning("os.statvfs failed: %s", e)

    # If all methods failed
    logging.error("All disk space checking methods failed")
    return 0, 0, 0, 0


def monitor_disk_space():
    """
    Monitors disk space and performs cleanup if needed.
    """
    used_gb, free_gb, total_gb, usage_percent = get_disk_usage()

    logging.info("Disk space: %.1f/%.1f GB used (%.1f%%), %.1f GB free", used_gb, total_gb, usage_percent, free_gb)

    # Warning when low space
    if free_gb < 10:
        logging.warning("WARNING: Low disk space! Only %.1f GB remaining.", free_gb)

        # Aggressive cleanup when very low space
        if free_gb < 5:
            logging.warning("Starting aggressive cleanup (files older than 6 hours)...")
            cleanup_old_files(DOWNLOAD_PATH, max_age_hours=6)
        else:
            # Normal cleanup
            cleanup_old_files(DOWNLOAD_PATH, max_age_hours=24)


_ARCHIVE_PREFIXES = ("pl_", "big_")


def _purge_archive_workspaces(chat_dir: Path, retention_min: int) -> int:
    """Remove archive workspaces older than retention_min, unless locked.

    Workspaces are subdirectories named ``pl_*`` or ``big_*``. A
    ``.lock`` file inside a young workspace blocks deletion (used during
    pack/send operations). After 24h workspaces are removed regardless
    of the lock — the long-running cleanup acts as a safety net.
    """

    if not chat_dir.exists():
        return 0

    now = time.time()
    threshold = retention_min * 60
    safety_net = 24 * 3600
    removed = 0

    for entry in chat_dir.iterdir():
        if not entry.is_dir():
            continue
        if not any(entry.name.startswith(p) for p in _ARCHIVE_PREFIXES):
            continue
        try:
            age = now - entry.stat().st_mtime
        except OSError as exc:
            logging.warning("Could not stat %s: %s", entry, exc)
            continue

        lock = entry / ".lock"
        if age <= threshold:
            continue
        if lock.exists() and age <= safety_net:
            continue

        try:
            shutil.rmtree(entry)
            removed += 1
            logging.info("Removed stale archive workspace: %s (age %.1f h)",
                         entry, age / 3600)
        except OSError as exc:
            logging.error("Failed to remove %s: %s", entry, exc)

    return removed


def _purge_pending_archive_jobs(retention_min: int) -> int:
    """Drop pending_archive_jobs entries older than retention_min and delete files."""

    from bot.session_store import pending_archive_jobs

    cutoff = datetime.now() - timedelta(minutes=retention_min)
    removed = 0
    for chat_id in list(pending_archive_jobs):
        bucket = pending_archive_jobs.get(chat_id) or {}
        for token in list(bucket):
            state = bucket[token]
            if state.created_at >= cutoff:
                continue
            bucket.pop(token, None)
            try:
                os.remove(str(state.file_path))
            except OSError:
                pass
            removed += 1
        if not bucket:
            pending_archive_jobs.pop(chat_id, None)
        else:
            pending_archive_jobs[chat_id] = bucket
    return removed


def periodic_cleanup():
    """
    Function run periodically in separate thread.
    """
    while True:
        try:
            # Wait 1 hour
            time.sleep(3600)

            logging.info("Starting periodic file cleanup...")

            # Check disk space
            monitor_disk_space()

            # Perform cleanup
            deleted_count = cleanup_old_files(DOWNLOAD_PATH, max_age_hours=24)

            if deleted_count > 0:
                logging.info("Periodic cleanup: deleted %d old files", deleted_count)

            for chat_dir in Path(DOWNLOAD_PATH).iterdir():
                if chat_dir.is_dir():
                    _purge_archive_workspaces(chat_dir, PLAYLIST_ARCHIVE_RETENTION_MIN)
            _purge_pending_archive_jobs(PLAYLIST_ARCHIVE_RETENTION_MIN)

        except Exception as e:
            logging.error("Error during periodic cleanup: %s", e)
