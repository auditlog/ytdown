"""Application runtime container built during bootstrap.

Ownership contract:
- configuration values that handlers/services need at runtime are read through
  the attached ``AppRuntime`` when available,
- authorized user membership belongs to runtime state plus the repository
  behind it,
- chat-scoped flow state belongs to ``SessionStore``; ``context.user_data``
  remains only a compatibility bridge for Telegram-specific behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from bot.config import (
    add_download_record,
    get_authorized_users_repository,
    get_download_history_repository,
    get_runtime_config,
    get_runtime_services,
    get_download_stats,
    get_runtime_authorized_users,
)
from bot.repositories import DownloadRecord
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
    authorized_users_set: set[int]
    archive_available: bool = False


def build_app_runtime() -> AppRuntime:
    """Build an application runtime from the active bootstrap state."""
    # Local import avoids a potential circular dependency if bot.archive ever
    # needs to import from bot.runtime in the future.
    from bot.archive import is_7z_available

    return AppRuntime(
        config=get_runtime_config(),
        session_store=session_store,
        security_store=security_store,
        services=get_runtime_services(),
        authorized_users_repository=get_authorized_users_repository(),
        download_history_repository=get_download_history_repository(),
        authorized_users_set=get_runtime_authorized_users(),
        archive_available=is_7z_available(),
    )


def attach_runtime(application: Any, runtime: AppRuntime) -> AppRuntime:
    """Attach runtime to Telegram application storage."""

    application.bot_data[RUNTIME_KEY] = runtime
    return runtime


def get_app_runtime(source: Any) -> AppRuntime | None:
    """Read the application runtime from PTB application/context objects."""

    if isinstance(source, AppRuntime):
        return source

    config = getattr(source, "config", None)
    if isinstance(config, dict):
        candidate = getattr(source, "authorized_users_set", None)
        if candidate is not None:
            return source

    bot_data = getattr(source, "bot_data", None)
    if isinstance(bot_data, dict):
        return bot_data.get(RUNTIME_KEY)

    application = getattr(source, "application", None)
    if application is not None:
        app_bot_data = getattr(application, "bot_data", None)
        if isinstance(app_bot_data, dict):
            return app_bot_data.get(RUNTIME_KEY)

    return None


def get_authorized_user_ids_for(source: Any) -> set[int]:
    """Return the active authorized user set for a runtime-aware caller."""

    runtime = get_app_runtime(source)
    if runtime is not None:
        return runtime.authorized_users_set
    return get_runtime_authorized_users()


def get_config_for(source: Any) -> dict[str, Any]:
    """Return active configuration for a runtime-aware caller."""

    runtime = get_app_runtime(source)
    if runtime is not None:
        return runtime.config
    return get_runtime_config()


def get_config_value_for(source: Any, key: str, default: Any = None) -> Any:
    """Return one configuration value for a runtime-aware caller."""

    return get_config_for(source).get(key, default)


def add_authorized_user_for(source: Any, user_id: int) -> bool:
    """Authorize one user through runtime state when available."""

    runtime = get_app_runtime(source)
    if runtime is None:
        authorized_users = get_runtime_authorized_users()
        if user_id in authorized_users:
            return False
        authorized_users.add(user_id)
        get_authorized_users_repository().save(authorized_users)
        return True

    if user_id in runtime.authorized_users_set:
        return False

    runtime.authorized_users_set.add(user_id)
    runtime.authorized_users_repository.save(runtime.authorized_users_set)
    return True


def remove_authorized_user_for(source: Any, user_id: int) -> bool:
    """Remove one authorized user through runtime state when available."""

    runtime = get_app_runtime(source)
    if runtime is None:
        authorized_users = get_runtime_authorized_users()
        if user_id not in authorized_users:
            return False
        authorized_users.discard(user_id)
        get_authorized_users_repository().save(authorized_users)
        return True

    if user_id not in runtime.authorized_users_set:
        return False

    runtime.authorized_users_set.discard(user_id)
    runtime.authorized_users_repository.save(runtime.authorized_users_set)
    return True


def get_download_stats_for(source: Any, user_id: int | None = None) -> dict:
    """Read download stats from runtime repository when available."""

    runtime = get_app_runtime(source)
    if runtime is not None:
        return runtime.download_history_repository.stats(user_id=user_id)
    return get_download_stats(user_id=user_id)


def record_download_for(
    source: Any,
    user_id: int,
    title: str,
    url: str,
    format_type: str,
    file_size_mb: float | None = None,
    time_range: dict[str, Any] | None = None,
    status: str = "success",
    selected_format: str | None = None,
    error_message: str | None = None,
) -> None:
    """Persist a download record through runtime repository when available."""

    runtime = get_app_runtime(source)
    if runtime is None:
        add_download_record(
            user_id,
            title,
            url,
            format_type,
            file_size_mb=file_size_mb,
            time_range=time_range,
            status=status,
            selected_format=selected_format,
            error_message=error_message,
        )
        return

    runtime.download_history_repository.append(
        DownloadRecord(
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
    )
