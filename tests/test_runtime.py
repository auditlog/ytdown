"""Unit tests for runtime-scoped authorization helpers."""

from unittest.mock import Mock

from bot.runtime import (
    AppRuntime,
    add_authorized_user_for,
    get_config_for,
    get_config_value_for,
    get_authorized_user_ids_for,
    remove_authorized_user_for,
)
from bot.session_store import SecurityStore, SessionStore


def _make_runtime(*, config=None, authorized_users=None):
    return AppRuntime(
        config={} if config is None else dict(config),
        session_store=SessionStore(),
        security_store=SecurityStore(),
        services=Mock(),
        authorized_users_repository=Mock(),
        download_history_repository=Mock(),
        authorized_users_set=set() if authorized_users is None else set(authorized_users),
    )


def test_runtime_authorized_user_helpers_use_attached_runtime():
    runtime = _make_runtime(authorized_users={1})
    context = Mock()
    context.bot_data = {"app_runtime": runtime}

    assert get_authorized_user_ids_for(context) is runtime.authorized_users_set
    assert add_authorized_user_for(context, 2) is True
    assert 2 in runtime.authorized_users_set
    runtime.authorized_users_repository.save.assert_called_once_with(runtime.authorized_users_set)

    assert remove_authorized_user_for(context, 1) is True
    assert 1 not in runtime.authorized_users_set
    assert runtime.authorized_users_repository.save.call_count == 2


def test_runtime_config_helpers_use_attached_runtime():
    runtime = _make_runtime(config={"TELEGRAM_BOT_TOKEN": "runtime-token"})
    context = Mock()
    context.bot_data = {"app_runtime": runtime}

    assert get_config_for(context) is runtime.config
    assert get_config_value_for(context, "TELEGRAM_BOT_TOKEN") == "runtime-token"
    assert get_config_value_for(context, "MISSING", "fallback") == "fallback"
