"""Tests for bot.services.auth_service."""

from collections import defaultdict

from bot.services import auth_service as auth


def test_handle_start_marks_awaiting_pin_for_unauthorized():
    user_data = {}

    result = auth.handle_start(
        user_id=123,
        user_name="User",
        authorized_user_ids=set(),
        user_data=user_data,
        block_map=defaultdict(float),
    )

    assert result.awaiting_pin is True
    assert user_data["awaiting_pin"] is True
    assert "8-cyfrowy kod PIN" in result.message


def test_store_and_consume_pending_action():
    user_data = {}

    auth.store_pending_action(user_data, kind="url", payload="https://youtube.com/watch?v=abc")
    pending = auth.consume_pending_action(user_data)

    assert pending is not None
    assert pending.kind == "url"
    assert pending.payload == "https://youtube.com/watch?v=abc"
    assert "pending_url" not in user_data


def test_handle_pin_input_accepts_correct_pin():
    user_data = {"awaiting_pin": True, "pending_audio": {"file_id": "1"}}
    attempts = defaultdict(int)
    block_map = defaultdict(float, {123: 88.0})
    authorized = set()
    added = []

    result = auth.handle_pin_input(
        user_id=123,
        message_text="12345678",
        user_data=user_data,
        pin_code="12345678",
        authorized_user_ids=authorized,
        attempts=attempts,
        block_map=block_map,
        authorize_user=lambda user_id: added.append(user_id),
    )

    assert result.handled is True
    assert result.pending_action is not None
    assert result.pending_action.kind == "audio"
    assert added == [123]
    assert "awaiting_pin" not in user_data
    assert attempts[123] == 0
    assert block_map[123] == 0.0


def test_handle_pin_input_rejects_wrong_pin_and_blocks():
    attempts = defaultdict(int)
    block_map = defaultdict(float)

    result = auth.handle_pin_input(
        user_id=123,
        message_text="00000000",
        user_data={"awaiting_pin": True},
        pin_code="12345678",
        authorized_user_ids=set(),
        attempts=attempts,
        block_map=block_map,
        authorize_user=lambda user_id: None,
        max_attempts=1,
        block_time=60,
    )

    assert result.handled is True
    assert result.blocked is True
    assert result.notify_admin is True
    assert result.attempt_count == 1
    assert block_map[123] > 0


def test_logout_user_clears_session():
    user_data = {"awaiting_pin": True, "pending_url": "x"}
    user_urls = {123: "https://youtube.com/watch?v=abc"}
    user_time_ranges = {123: {"start": "0:10", "end": "0:20"}}
    removed = []
    cleared = []

    success = auth.logout_user(
        user_id=1,
        chat_id=123,
        authorized_user_ids={1},
        remove_authorized_user=lambda user_id: removed.append(user_id),
        user_data=user_data,
        user_urls=user_urls,
        user_time_ranges=user_time_ranges,
        clear_security_state=lambda user_id: cleared.append(user_id),
    )

    assert success is True
    assert removed == [1]
    assert cleared == [1]
    assert user_data == {}
    assert 123 not in user_urls
    assert 123 not in user_time_ranges
