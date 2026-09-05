"""Public modules, imported on demand so non-Telegram entry points stay lightweight."""

from importlib import import_module

__all__ = [
    "cli",
    "config",
    "runtime",
    "security",
    "session_context",
    "session_store",
    "telegram_callbacks",
    "telegram_commands",
    "transcription",
]


def __getattr__(name):
    if name in __all__:
        module = import_module(f"bot.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
