"""Telegram commands for connecting the bot to a Spotify user account."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.runtime import get_authorized_user_ids_for, get_config_value_for
from bot.spotify_oauth import (
    SpotifyOAuthError,
    build_spotify_authorization_url,
    clear_spotify_authorization,
    has_spotify_authorization,
)


def _can_manage_spotify(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    if user_id not in get_authorized_user_ids_for(context):
        return False
    admin_chat_id = str(get_config_value_for(context, "ADMIN_CHAT_ID", "") or "")
    if not admin_chat_id:
        return True
    try:
        return user_id == int(admin_chat_id)
    except (TypeError, ValueError):
        return False


async def spotify_login_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Send an admin a one-use Spotify OAuth authorization link."""

    user_id = update.effective_user.id
    if not _can_manage_spotify(context, user_id):
        await update.effective_message.reply_text(
            "Tylko administrator bota może połączyć globalne konto Spotify."
        )
        return
    try:
        authorization_url = build_spotify_authorization_url(user_id)
    except SpotifyOAuthError as exc:
        if exc.reason == "missing_redirect_uri":
            message = "Brak SPOTIFY_REDIRECT_URI w konfiguracji bota."
        elif exc.reason == "invalid_redirect_uri":
            message = "SPOTIFY_REDIRECT_URI musi być poprawnym adresem HTTPS."
        else:
            message = "Brak poprawnych danych aplikacji Spotify w konfiguracji."
        await update.effective_message.reply_text(message)
        return

    prefix = (
        "Konto Spotify jest już połączone. Możesz odnowić lub zmienić zgodę.\n\n"
        if has_spotify_authorization()
        else "Połącz konto Spotify, które jest właścicielem playlist.\n\n"
    )
    redirect_uri = get_config_value_for(context, "SPOTIFY_REDIRECT_URI", "")
    await update.effective_message.reply_text(
        prefix
        + "Spotify poprosi wyłącznie o odczyt prywatnych i współdzielonych playlist. "
        "Po zatwierdzeniu wróć do Telegrama i ponownie wyślij link do playlisty.\n\n"
        f"Adres callbacku skonfigurowany w Spotify Developer Dashboard:\n{redirect_uri}",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("Połącz konto Spotify", url=authorization_url)]]
        ),
        disable_web_page_preview=True,
    )


async def spotify_logout_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Remove the shared Spotify OAuth authorization."""

    user_id = update.effective_user.id
    if not _can_manage_spotify(context, user_id):
        await update.effective_message.reply_text(
            "Tylko administrator bota może odłączyć konto Spotify."
        )
        return
    removed = clear_spotify_authorization()
    await update.effective_message.reply_text(
        "Konto Spotify zostało odłączone."
        if removed
        else "Konto Spotify nie było połączone."
    )
