"""
Cleanup module for YouTube Downloader Telegram Bot.

Handles file cleanup, disk monitoring, and periodic maintenance.
"""

import os
import time
import shutil
import logging
import subprocess

from bot.config import DOWNLOAD_PATH


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
        # Walk through all files in directory and subdirectories
        for root, dirs, files in os.walk(directory):
            for filename in files:
                file_path = os.path.join(root, filename)

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

                        logging.info(f"Deleted old file: {file_path} ({file_size_mb:.2f} MB)")
                except Exception as e:
                    logging.error(f"Error deleting file {file_path}: {e}")

            # Remove empty directories
            try:
                if not os.listdir(root):
                    os.rmdir(root)
                    logging.info(f"Deleted empty directory: {root}")
            except:
                pass

    except Exception as e:
        logging.error(f"Error cleaning directory {directory}: {e}")

    if deleted_count > 0:
        logging.info(f"Cleanup finished: deleted {deleted_count} files, freed {freed_space_mb:.2f} MB")

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
        logging.warning(f"shutil.disk_usage failed: {e}")

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
        logging.warning(f"df command failed: {e}")

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
        logging.warning(f"os.statvfs failed: {e}")

    # If all methods failed
    logging.error("All disk space checking methods failed")
    return 0, 0, 0, 0


def monitor_disk_space():
    """
    Monitors disk space and performs cleanup if needed.
    """
    used_gb, free_gb, total_gb, usage_percent = get_disk_usage()

    logging.info(f"Disk space: {used_gb:.1f}/{total_gb:.1f} GB used ({usage_percent:.1f}%), {free_gb:.1f} GB free")

    # Warning when low space
    if free_gb < 10:
        logging.warning(f"WARNING: Low disk space! Only {free_gb:.1f} GB remaining.")

        # Aggressive cleanup when very low space
        if free_gb < 5:
            logging.warning("Starting aggressive cleanup (files older than 6 hours)...")
            cleanup_old_files(DOWNLOAD_PATH, max_age_hours=6)
        else:
            # Normal cleanup
            cleanup_old_files(DOWNLOAD_PATH, max_age_hours=24)


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
                logging.info(f"Periodic cleanup: deleted {deleted_count} old files")

        except Exception as e:
            logging.error(f"Error during periodic cleanup: {e}")
