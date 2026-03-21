"""Application runtime container built during bootstrap."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.config import (
    get_authorized_users_repository,
    get_download_history_repository,
    get_runtime_config,
    get_runtime_services,
)
from bot.session_store import security_store, session_store


RUNTIME_KEY = "app_runtime"


@dataclass(frozen=True)
class AppRuntime:
    """Concrete runtime dependencies shared by the application."""

    config: dict[str, Any]
    session_store: Any
    security_store: Any
    services: Any
    authorized_users_repository: Any
    download_history_repository: Any


def build_app_runtime() -> AppRuntime:
    """Build an application runtime from the active bootstrap state."""

    return AppRuntime(
        config=get_runtime_config(),
        session_store=session_store,
        security_store=security_store,
        services=get_runtime_services(),
        authorized_users_repository=get_authorized_users_repository(),
        download_history_repository=get_download_history_repository(),
    )


def attach_runtime(application: Any, runtime: AppRuntime) -> AppRuntime:
    """Attach runtime to Telegram application storage."""

    application.bot_data[RUNTIME_KEY] = runtime
    return runtime


def get_app_runtime(source: Any) -> AppRuntime | None:
    """Read the application runtime from PTB application/context objects."""

    bot_data = getattr(source, "bot_data", None)
    if isinstance(bot_data, dict):
        return bot_data.get(RUNTIME_KEY)

    application = getattr(source, "application", None)
    if application is not None:
        app_bot_data = getattr(application, "bot_data", None)
        if isinstance(app_bot_data, dict):
            return app_bot_data.get(RUNTIME_KEY)

    return None
