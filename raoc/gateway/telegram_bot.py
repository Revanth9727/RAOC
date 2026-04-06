"""Telegram gateway for RAOC — incoming messages and outgoing reports.

TelegramGateway is the sole interface between the user's phone and the pipeline.
It enforces user ID whitelisting and message age checks before passing anything
to the coordinator callback.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from raoc.substrate.secret_broker import SecretBroker

logger = logging.getLogger(__name__)

_MAX_MESSAGE_AGE_SECONDS = 60


class TelegramGateway:
    """Sends and receives Telegram messages on behalf of RAOC.

    Only accepts messages from the whitelisted user ID retrieved via SecretBroker.
    All incoming messages are age-checked before being forwarded to on_message.
    """

    def __init__(self, secret_broker: SecretBroker, on_message: callable = None, on_approval: callable = None):
        """Initialise the gateway.

        on_message: called with (text: str) for each valid incoming message.
        on_approval: called with (job_id: str, approved: bool) for button taps.
        """
        self.broker = secret_broker
        self.on_message = on_message
        self.on_approval = on_approval
        self._application: Application = None

    # ── Incoming handlers ────────────────────────────────────────

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle an incoming text message.

        Rejects messages from unknown users silently.
        Rejects messages older than 60 seconds.
        Forwards valid messages to on_message callback.
        """
        allowed_user_id = self.broker.get_telegram_user_id()

        if update.effective_user.id != allowed_user_id:
            logger.warning(
                "Message rejected: unknown user_id=%s", update.effective_user.id
            )
            return

        message_time = update.message.date
        if message_time.tzinfo is None:
            message_time = message_time.replace(tzinfo=timezone.utc)
        age_seconds = (datetime.now(timezone.utc) - message_time).total_seconds()
        if age_seconds > _MAX_MESSAGE_AGE_SECONDS:
            logger.warning("Message rejected: age=%.1fs exceeds limit", age_seconds)
            return

        text = update.message.text or ""
        logger.info("Message received from whitelisted user")

        if self.on_message:
            await self.on_message(text=text)

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle an inline keyboard button tap (Approve / Deny).

        Parses callback_data in the form 'approve:<job_id>' or 'deny:<job_id>'
        and forwards the decision to on_approval callback.
        """
        query = update.callback_query
        try:
            await query.answer()
        except Exception as exc:
            logger.warning("Could not answer callback query: %s", exc)
            # Continue anyway — the important thing is handling the
            # approve/deny logic, not acknowledging the button tap

        data = query.data or ""
        if ":" not in data:
            logger.warning("Callback with unrecognised data: %s", data)
            return

        action, job_id = data.split(":", 1)
        approved = action == "approve"

        logger.info("Approval callback: job=%s approved=%s", job_id, approved)

        if self.on_approval:
            try:
                await self.on_approval(job_id=job_id, approved=approved)
            except Exception as exc:
                logger.error("on_approval callback raised: %s", exc)

    # ── Outgoing methods ─────────────────────────────────────────

    async def send_message(self, text: str) -> None:
        """Send a plain text message to the whitelisted user."""
        user_id = self.broker.get_telegram_user_id()
        await self._application.bot.send_message(chat_id=user_id, text=text)

    async def send_status(self, text: str) -> None:
        """Send an ephemeral status narration to the whitelisted user.

        Fire and forget — does not wait for delivery confirmation.
        Never raises; logs failures silently.
        """
        try:
            user_id = self.broker.get_telegram_user_id()
            await self._application.bot.send_message(chat_id=user_id, text=text)
        except Exception as exc:
            logger.warning("send_status failed: %s", exc)

    async def send_confirmation(self, text: str, job_id: str) -> None:
        """Send a confirmation message with Yes/No buttons for a query_action search result.

        Yes maps to handle_approval(job_id, approved=True).
        No maps to handle_approval(job_id, approved=False).
        Reuses the approve/deny callback infrastructure.
        """
        user_id = self.broker.get_telegram_user_id()
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Yes", callback_data=f"approve:{job_id}"),
                InlineKeyboardButton("No",  callback_data=f"deny:{job_id}"),
            ]
        ])
        await self._application.bot.send_message(
            chat_id=user_id, text=text, reply_markup=keyboard
        )

    async def send_approval_request(self, job_id: str, plan_text: str) -> None:
        """Send the plan preview with Approve and Deny inline buttons."""
        user_id = self.broker.get_telegram_user_id()
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Approve", callback_data=f"approve:{job_id}"),
                InlineKeyboardButton("Deny",    callback_data=f"deny:{job_id}"),
            ]
        ])
        await self._application.bot.send_message(
            chat_id=user_id, text=plan_text, reply_markup=keyboard
        )

    # ── Error handler ─────────────────────────────────────────────

    async def handle_error(self, update, context) -> None:
        """Catch-all error handler — logs the exception and notifies the user."""
        logger.error("Unhandled pipeline error", exc_info=context.error)
        try:
            await context.bot.send_message(
                chat_id=self.broker.get_telegram_user_id(),
                text="Something went wrong on my end. Please try again.",
            )
        except Exception:
            pass

    # ── Bot lifecycle ─────────────────────────────────────────────

    def run(self) -> None:
        """Build and start the Telegram bot. Blocks until stopped."""
        token = self.broker.get_telegram_token()
        app = Application.builder().token(token).build()
        self._application = app

        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        app.add_handler(CallbackQueryHandler(self.handle_callback))
        app.add_error_handler(self.handle_error)

        logger.info("Telegram gateway starting")
        app.run_polling()
