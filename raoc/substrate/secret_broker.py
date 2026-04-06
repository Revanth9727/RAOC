"""Secure access to macOS Keychain secrets for RAOC.

SecretBroker is the only component that touches the Keychain.
Secret values are never logged, stored, or passed as arguments — only
returned directly to the caller.
"""

import logging

import keyring

from raoc import config

logger = logging.getLogger(__name__)


class SecretBroker:
    """Retrieves secrets from the macOS Keychain by service and key name.

    Only logs that a secret was accessed — never the value itself.
    Raises RuntimeError if a requested secret is not found in the Keychain.
    """

    def get_anthropic_key(self) -> str:
        """Retrieve the Anthropic API key from the Keychain.

        Raises RuntimeError if the key is not present.
        """
        value = keyring.get_password(config.KEYCHAIN_SERVICE, config.KEYCHAIN_ANTHROPIC_KEY)
        if value is None:
            raise RuntimeError(
                f"Secret not found in Keychain: {config.KEYCHAIN_ANTHROPIC_KEY}"
            )
        logger.info("Secret accessed: %s", config.KEYCHAIN_ANTHROPIC_KEY)
        return value

    def get_telegram_token(self) -> str:
        """Retrieve the Telegram bot token from the Keychain.

        Raises RuntimeError if the token is not present.
        """
        value = keyring.get_password(config.KEYCHAIN_SERVICE, config.KEYCHAIN_TELEGRAM_TOKEN)
        if value is None:
            raise RuntimeError(
                f"Secret not found in Keychain: {config.KEYCHAIN_TELEGRAM_TOKEN}"
            )
        logger.info("Secret accessed: %s", config.KEYCHAIN_TELEGRAM_TOKEN)
        return value

    def get_telegram_user_id(self) -> int:
        """Retrieve the Telegram user ID from the Keychain and return it as int.

        Raises RuntimeError if the user ID is not present.
        """
        value = keyring.get_password(config.KEYCHAIN_SERVICE, config.KEYCHAIN_TELEGRAM_USER_ID)
        if value is None:
            raise RuntimeError(
                f"Secret not found in Keychain: {config.KEYCHAIN_TELEGRAM_USER_ID}"
            )
        logger.info("Secret accessed: %s", config.KEYCHAIN_TELEGRAM_USER_ID)
        return int(value)
