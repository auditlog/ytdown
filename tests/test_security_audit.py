"""
Tests for security audit fixes.

Covers: sanitize_filename hardening, _is_admin helper, escape_md helper,
cleanup symlink protection, rate limit on callbacks, PIN validation,
config file permissions, authorized users lock, and exception message hiding.
"""

import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# --- Fix 10: sanitize_filename hardening ---

class TestSanitizeFilename:
    """Tests for hardened sanitize_filename in bot/downloader.py."""

    def test_basic_invalid_chars(self):
        from bot.downloader import sanitize_filename
        result = sanitize_filename('file/name\\test:file')
        assert '/' not in result
        assert '\\' not in result
        assert ':' not in result

    def test_path_traversal_removed(self):
        from bot.downloader import sanitize_filename
        result = sanitize_filename('../../etc/passwd')
        assert '..' not in result

    def test_control_characters_removed(self):
        from bot.downloader import sanitize_filename
        result = sanitize_filename('file\x00name\x01test\x1f')
        assert '\x00' not in result
        assert '\x01' not in result
        assert '\x1f' not in result

    def test_length_limited_to_200(self):
        from bot.downloader import sanitize_filename
        long_name = 'a' * 300
        result = sanitize_filename(long_name)
        assert len(result) <= 200

    def test_empty_filename_fallback(self):
        from bot.downloader import sanitize_filename
        result = sanitize_filename('')
        assert result == 'download'

    def test_whitespace_only_fallback(self):
        from bot.downloader import sanitize_filename
        result = sanitize_filename('   ')
        assert result == 'download'

    def test_only_invalid_chars_fallback(self):
        from bot.downloader import sanitize_filename
        result = sanitize_filename('///\\\\:::')
        # After replacing invalid chars with '-', should still have content
        assert result

    def test_normal_filename_unchanged(self):
        from bot.downloader import sanitize_filename
        result = sanitize_filename('My Video Title 2024')
        assert result == 'My Video Title 2024'

    def test_dots_in_extension_preserved(self):
        from bot.downloader import sanitize_filename
        result = sanitize_filename('video.mp4')
        assert result == 'video.mp4'

    def test_multiple_path_traversal(self):
        from bot.downloader import sanitize_filename
        result = sanitize_filename('....//....//etc//passwd')
        assert '..' not in result


# --- Fix 9: _is_admin helper ---

class TestIsAdmin:
    """Tests for _is_admin helper in bot/telegram_commands.py."""

    def test_admin_matches(self):
        from bot.telegram_commands import _is_admin
        with patch('bot.telegram_commands.ADMIN_CHAT_ID', '12345'):
            assert _is_admin(12345) is True

    def test_admin_does_not_match(self):
        from bot.telegram_commands import _is_admin
        with patch('bot.telegram_commands.ADMIN_CHAT_ID', '12345'):
            assert _is_admin(99999) is False

    def test_no_admin_configured_all_are_admin(self):
        from bot.telegram_commands import _is_admin
        with patch('bot.telegram_commands.ADMIN_CHAT_ID', ''):
            assert _is_admin(99999) is True

    def test_invalid_admin_chat_id(self):
        from bot.telegram_commands import _is_admin
        with patch('bot.telegram_commands.ADMIN_CHAT_ID', 'not_a_number'):
            assert _is_admin(12345) is False


# --- Fix 5: escape_md helper ---

class TestEscapeMd:
    """Tests for escape_md helpers in telegram modules."""

    def test_escape_md_callbacks(self):
        from bot.telegram_callbacks import escape_md
        assert escape_md('hello') == 'hello'
        # Escaped markdown chars should have backslash prefix
        result = escape_md('*bold*')
        assert result == '\\*bold\\*'

    def test_escape_md_commands(self):
        from bot.telegram_commands import escape_md
        assert escape_md('hello') == 'hello'
        result = escape_md('*bold*')
        assert result == '\\*bold\\*'

    def test_escape_md_brackets(self):
        from bot.telegram_callbacks import escape_md
        result = escape_md('[link](url)')
        assert '[' not in result or '\\[' in result

    def test_escape_md_backticks(self):
        from bot.telegram_callbacks import escape_md
        result = escape_md('`code`')
        assert '\\`' in result or '`' not in result

    def test_youtube_title_with_special_chars(self):
        from bot.telegram_callbacks import escape_md
        title = "Why *this* _technique_ works [2024]"
        result = escape_md(title)
        # Should not contain unescaped markdown characters
        assert '\\*' in result or '*' not in result


# --- Fix 6: cleanup symlink protection ---

class TestCleanupSymlinkProtection:
    """Tests for symlink protection in bot/cleanup.py."""

    def test_symlink_skipped_during_cleanup(self, temp_dir):
        """Verify symlinks are not followed or deleted during cleanup."""
        import time
        from bot.cleanup import cleanup_old_files

        # Create a real file
        real_file = os.path.join(temp_dir, 'real_file.txt')
        with open(real_file, 'w') as f:
            f.write('test')
        # Make it old
        old_time = time.time() - 48 * 3600
        os.utime(real_file, (old_time, old_time))

        # Create a symlink (if supported by OS)
        symlink_path = os.path.join(temp_dir, 'symlink_file.txt')
        try:
            os.symlink(real_file, symlink_path)
            os.utime(symlink_path, (old_time, old_time), follow_symlinks=False)
        except (OSError, NotImplementedError):
            pytest.skip("Symlinks not supported on this platform")

        deleted = cleanup_old_files(temp_dir, max_age_hours=24)

        # Real file should be deleted, symlink should be skipped
        assert not os.path.exists(real_file)
        # Symlink target is deleted so symlink is now dangling,
        # but cleanup should have skipped the symlink itself
        assert deleted == 1  # Only the real file


# --- Fix 11: rate limit on callbacks ---

class TestCallbackRateLimit:
    """Tests for rate limit enforcement on callback queries."""

    def test_handle_callback_imports_check_rate_limit(self):
        """Verify check_rate_limit is imported in telegram_callbacks."""
        from bot.telegram_callbacks import check_rate_limit
        assert callable(check_rate_limit)


# --- Fix 3: PIN validation ---

class TestPinValidation:
    """Tests for PIN validation security fixes."""

    def test_pin_validation_does_not_log_pin_value(self):
        """Verify PIN value is not logged when format is invalid."""
        import logging
        from bot.config import validate_config

        with patch.object(logging, 'error') as mock_log:
            validate_config({'PIN_CODE': 'abc123', 'TELEGRAM_BOT_TOKEN': '', 'GROQ_API_KEY': '', 'CLAUDE_API_KEY': '', 'ADMIN_CHAT_ID': ''})
            # Check that no log call contains the PIN value
            for call in mock_log.call_args_list:
                log_msg = str(call)
                assert 'abc123' not in log_msg, "PIN value should not appear in logs"

    def test_pin_validation_accepts_any_length_digits(self):
        """Verify PIN validation accepts any length numeric PIN."""
        import logging
        from bot.config import validate_config

        with patch.object(logging, 'error') as mock_log:
            validate_config({'PIN_CODE': '1234', 'TELEGRAM_BOT_TOKEN': '', 'GROQ_API_KEY': '', 'CLAUDE_API_KEY': '', 'ADMIN_CHAT_ID': ''})
            # Should NOT log an error about format (4-digit PIN is valid)
            for call in mock_log.call_args_list:
                log_msg = str(call)
                assert 'format invalid' not in log_msg

    def test_default_pin_raises_error_level(self):
        """Verify default PIN triggers error-level log, not just warning."""
        import logging
        from bot.config import validate_config

        with patch.object(logging, 'error') as mock_error:
            validate_config({'PIN_CODE': '12345678', 'TELEGRAM_BOT_TOKEN': '', 'GROQ_API_KEY': '', 'CLAUDE_API_KEY': '', 'ADMIN_CHAT_ID': ''})
            error_msgs = [str(call) for call in mock_error.call_args_list]
            assert any('default' in msg.lower() or 'SECURITY' in msg for msg in error_msgs), \
                "Default PIN should trigger error-level security log"


# --- Fix 1: config file permissions ---

class TestConfigFilePermissions:
    """Tests for auto-fixing config file permissions."""

    def test_chmod_attempted_on_wrong_permissions(self):
        """Verify os.chmod is called when permissions are wrong."""
        from bot.config import validate_config

        with patch('bot.config.os.path.exists', return_value=True), \
             patch('bot.config.os.stat') as mock_stat, \
             patch('bot.config.os.chmod') as mock_chmod:
            mock_stat.return_value = Mock()
            mock_stat.return_value.st_mode = 0o100644  # 644 permissions
            validate_config({'PIN_CODE': '12345678', 'TELEGRAM_BOT_TOKEN': '', 'GROQ_API_KEY': '', 'CLAUDE_API_KEY': '', 'ADMIN_CHAT_ID': ''})
            mock_chmod.assert_called_once()


# --- Fix 7: authorized users lock ---

class TestAuthorizedUsersLock:
    """Tests for thread-safe authorized users operations."""

    def test_auth_lock_exists(self):
        """Verify _auth_lock is defined in config module."""
        from bot.config import _auth_lock
        assert isinstance(_auth_lock, type(threading.Lock()))

    def test_manage_authorized_user_uses_lock(self):
        """Verify manage_authorized_user source code uses _auth_lock."""
        import inspect
        from bot.security import manage_authorized_user
        source = inspect.getsource(manage_authorized_user)
        assert '_auth_lock' in source, "manage_authorized_user should use _auth_lock"


# --- Fix 15: DOWNLOAD_PATH absolute ---

class TestDownloadPathAbsolute:
    """Tests for absolute DOWNLOAD_PATH."""

    def test_download_path_is_absolute(self):
        from bot.config import DOWNLOAD_PATH
        assert os.path.isabs(DOWNLOAD_PATH), f"DOWNLOAD_PATH should be absolute, got: {DOWNLOAD_PATH}"


# --- Fix 14: __init__.py exports ---

class TestInitExports:
    """Tests for sensitive values removed from __all__."""

    def test_pin_code_not_in_all(self):
        import bot
        assert 'PIN_CODE' not in bot.__all__

    def test_bot_token_not_in_all(self):
        import bot
        assert 'BOT_TOKEN' not in bot.__all__


# --- Fix 2: exception message hiding ---

class TestExceptionMessageHiding:
    """Tests verifying str(e) is not sent to users."""

    def test_download_file_error_hides_details(self):
        """Verify download_file error message doesn't contain exception details."""
        # This is a static analysis check — verify the source code
        import inspect
        from bot.telegram_callbacks import download_file
        source = inspect.getsource(download_file)
        # The error handler should not send str(e) to user
        assert 'f"Wystąpił błąd: {str(e)}"' not in source
        assert "Wystąpił błąd podczas pobierania" in source

    def test_audio_upload_error_hides_details(self):
        """Verify process_audio_file error message doesn't contain exception details."""
        import inspect
        from bot.telegram_commands import process_audio_file
        source = inspect.getsource(process_audio_file)
        assert 'f"Błąd przetwarzania pliku audio: {str(e)}"' not in source
        assert "Błąd przetwarzania pliku audio. Spróbuj ponownie." in source


# --- Fix 12: API log truncation ---

class TestApiLogTruncation:
    """Tests verifying API response logs are truncated."""

    def test_groq_error_log_truncated(self):
        """Verify Groq API error response is truncated in logs."""
        import inspect
        from bot.transcription import transcribe_audio
        source = inspect.getsource(transcribe_audio)
        assert 'response.text[:500]' in source

    def test_claude_error_log_truncated(self):
        """Verify Claude API error response is truncated in logs."""
        import inspect
        from bot.transcription import generate_summary, post_process_transcript
        summary_source = inspect.getsource(generate_summary)
        postprocess_source = inspect.getsource(post_process_transcript)
        assert 'response.text[:500]' in summary_source
        assert 'response.text[:500]' in postprocess_source


# --- Fix 13: subprocess timeouts ---

class TestSubprocessTimeouts:
    """Tests verifying subprocess calls have timeouts."""

    def test_find_silence_points_has_timeout(self):
        import inspect
        from bot.transcription import find_silence_points
        source = inspect.getsource(find_silence_points)
        assert 'timeout=' in source

    def test_split_mp3_has_timeout(self):
        import inspect
        from bot.transcription import split_mp3
        source = inspect.getsource(split_mp3)
        assert 'timeout=' in source


# --- Fix 2c: CONFIG_FILE_PATH not exposed to users ---

class TestConfigFilePathHidden:
    """Tests verifying CONFIG_FILE_PATH is not shown to users."""

    def test_callbacks_no_config_file_path_import(self):
        """Verify CONFIG_FILE_PATH is not imported in telegram_callbacks."""
        import inspect
        import bot.telegram_callbacks as cb
        source = inspect.getsource(cb)
        # Should not reference CONFIG_FILE_PATH in user-facing messages
        assert 'CONFIG_FILE_PATH' not in source
