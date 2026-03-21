"""Application runtime container built during bootstrap."""

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
