"""Authorization persistence helpers."""

from __future__ import annotations

import logging

from bot.config import _auth_lock, add_runtime_authorized_user, remove_runtime_authorized_user


def manage_authorized_user(user_id, action='add'):
    """Manage persistent authorized users under the shared _auth_lock."""

    try:
        with _auth_lock:
            if action == 'add':
                if add_runtime_authorized_user(user_id):
                    logging.info("Added user %s to authorized", user_id)
                    return True
                logging.info("User %s is already authorized", user_id)
                return True

            if action == 'remove':
                if remove_runtime_authorized_user(user_id):
                    logging.info("Removed user %s from authorized", user_id)
                    return True
                logging.info("User %s was not authorized", user_id)
                return True

            logging.error("Unknown action: %s", action)
            return False
    except Exception as exc:
        logging.error("Error managing user %s: %s", user_id, exc)
        return False
