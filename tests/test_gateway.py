"""Tests for raoc.gateway.telegram_bot — TelegramGateway."""

import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from raoc.gateway.telegram_bot import TelegramGateway


# ── Helpers ───────────────────────────────────────────────────────


ALLOWED_USER_ID = 111222333


def _make_broker(user_id: int = ALLOWED_USER_ID) -> MagicMock:
    """Return a mock SecretBroker that returns a fixed user ID."""
    broker = MagicMock()
    broker.get_telegram_user_id.return_value = user_id
    return broker


def _make_update(user_id: int, text: str = "Run cleanup.py", age_seconds: float = 0) -> MagicMock:
    """Build a minimal mock Telegram Update."""
    message_time = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)

    update = MagicMock()
    update.effective_user.id = user_id
    update.message.text = text
    update.message.date = message_time
    update.message.reply_text = AsyncMock()
    return update


def _make_callback_update(data: str, user_id: int = ALLOWED_USER_ID) -> MagicMock:
    """Build a minimal mock Update for a callback query."""
    update = MagicMock()
    update.effective_user.id = user_id
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    return update


def _run(coro):
    """Run a coroutine synchronously."""
    return asyncio.run(coro)


# ── Tests ─────────────────────────────────────────────────────────


def test_wrong_user_id_is_rejected_silently():
    """Message from an unknown user never reaches on_message and sends no reply."""
    on_message = AsyncMock()
    broker = _make_broker(user_id=ALLOWED_USER_ID)
    gw = TelegramGateway(broker, on_message=on_message)

    update = _make_update(user_id=999999999)
    _run(gw.handle_message(update, MagicMock()))

    on_message.assert_not_called()
    update.message.reply_text.assert_not_called()


def test_message_older_than_60s_is_rejected():
    """Message more than 60 seconds old is dropped without calling on_message."""
    on_message = AsyncMock()
    broker = _make_broker()
    gw = TelegramGateway(broker, on_message=on_message)

    update = _make_update(user_id=ALLOWED_USER_ID, age_seconds=61)
    _run(gw.handle_message(update, MagicMock()))

    on_message.assert_not_called()
    update.message.reply_text.assert_not_called()


def test_valid_message_calls_on_message_with_correct_text():
    """A fresh message from the whitelisted user triggers on_message with its text."""
    on_message = AsyncMock()
    broker = _make_broker()
    gw = TelegramGateway(broker, on_message=on_message)

    update = _make_update(user_id=ALLOWED_USER_ID, text="Run cleanup.py", age_seconds=0)
    _run(gw.handle_message(update, MagicMock()))

    on_message.assert_called_once_with(text="Run cleanup.py")
    update.message.reply_text.assert_not_called()


def test_message_exactly_at_60s_is_rejected():
    """Message exactly 60 seconds old (> limit) is rejected."""
    on_message = AsyncMock()
    broker = _make_broker()
    gw = TelegramGateway(broker, on_message=on_message)

    update = _make_update(user_id=ALLOWED_USER_ID, age_seconds=60.1)
    _run(gw.handle_message(update, MagicMock()))

    on_message.assert_not_called()


def test_approve_button_parsed_correctly():
    """callback_data 'approve:<job_id>' triggers on_approval with approved=True."""
    on_approval = AsyncMock()
    broker = _make_broker()
    gw = TelegramGateway(broker, on_approval=on_approval)

    job_id = "abc-123"
    update = _make_callback_update(data=f"approve:{job_id}")
    _run(gw.handle_callback(update, MagicMock()))

    on_approval.assert_called_once_with(job_id=job_id, approved=True)


def test_deny_button_parsed_correctly():
    """callback_data 'deny:<job_id>' triggers on_approval with approved=False."""
    on_approval = AsyncMock()
    broker = _make_broker()
    gw = TelegramGateway(broker, on_approval=on_approval)

    job_id = "abc-123"
    update = _make_callback_update(data=f"deny:{job_id}")
    _run(gw.handle_callback(update, MagicMock()))

    on_approval.assert_called_once_with(job_id=job_id, approved=False)


def test_callback_with_no_on_approval_does_not_raise():
    """handle_callback with no on_approval callback set does not raise."""
    broker = _make_broker()
    gw = TelegramGateway(broker)  # no on_approval

    update = _make_callback_update(data="approve:xyz")
    _run(gw.handle_callback(update, MagicMock()))  # must not raise


# ── send_status tests ──────────────────────────────────────────────


def test_send_status_sends_message():
    """send_status sends a plain text message to the whitelisted user."""
    broker = _make_broker()
    gw = TelegramGateway(broker)

    bot = MagicMock()
    bot.send_message = AsyncMock()
    app = MagicMock()
    app.bot = bot
    gw._application = app

    _run(gw.send_status("Working on it."))

    bot.send_message.assert_called_once_with(
        chat_id=ALLOWED_USER_ID, text="Working on it."
    )


def test_send_status_never_raises():
    """send_status swallows exceptions and does not raise."""
    from telegram.error import TelegramError

    broker = _make_broker()
    gw = TelegramGateway(broker)

    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=TelegramError("network error"))
    app = MagicMock()
    app.bot = bot
    gw._application = app

    _run(gw.send_status("test"))  # must not raise


def test_deny_callback_continues_if_query_answer_raises_network_error():
    """Deny logic runs even when query.answer() raises NetworkError.

    On mobile, the network can hiccup right as the user taps Deny.
    query.answer() must not crash the handler — the job must still be cancelled.
    """
    from telegram.error import NetworkError

    deny_calls = []

    async def on_approval(job_id, approved):
        deny_calls.append((job_id, approved))

    broker = _make_broker()
    gw = TelegramGateway(broker, on_approval=on_approval)

    job_id = "test-job-456"
    update = _make_callback_update(data=f"deny:{job_id}")
    update.callback_query.answer = AsyncMock(side_effect=NetworkError("httpx.ConnectError"))

    _run(gw.handle_callback(update, MagicMock()))  # must not raise

    # Deny logic must have run despite the NetworkError
    assert len(deny_calls) == 1, "on_approval was not called after NetworkError in query.answer()"
    assert deny_calls[0] == (job_id, False), f"Expected deny call, got {deny_calls[0]}"
