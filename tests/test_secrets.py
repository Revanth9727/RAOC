"""Tests for raoc.substrate.secret_broker — SecretBroker."""

import logging

import pytest

from raoc.substrate.secret_broker import SecretBroker


def test_get_anthropic_key_returns_mocked_value(mocker):
    """get_anthropic_key returns the value from keyring."""
    mocker.patch('keyring.get_password', return_value='sk-test-key-12345')
    broker = SecretBroker()
    assert broker.get_anthropic_key() == 'sk-test-key-12345'


def test_get_anthropic_key_raises_when_not_found(mocker):
    """get_anthropic_key raises RuntimeError when keyring returns None."""
    mocker.patch('keyring.get_password', return_value=None)
    broker = SecretBroker()
    with pytest.raises(RuntimeError, match="Secret not found in Keychain"):
        broker.get_anthropic_key()


def test_get_anthropic_key_never_logs_secret_value(mocker, caplog):
    """The secret value is never passed to the logger."""
    secret = 'sk-super-secret-value'
    mocker.patch('keyring.get_password', return_value=secret)
    broker = SecretBroker()

    with caplog.at_level(logging.DEBUG, logger='raoc.substrate.secret_broker'):
        broker.get_anthropic_key()

    for record in caplog.records:
        assert secret not in record.getMessage(), (
            f"Secret value appeared in log message: {record.getMessage()}"
        )


def test_get_telegram_token_returns_mocked_value(mocker):
    """get_telegram_token returns the value from keyring."""
    mocker.patch('keyring.get_password', return_value='1234567890:ABCdef')
    broker = SecretBroker()
    assert broker.get_telegram_token() == '1234567890:ABCdef'


def test_get_telegram_token_raises_when_not_found(mocker):
    """get_telegram_token raises RuntimeError when keyring returns None."""
    mocker.patch('keyring.get_password', return_value=None)
    broker = SecretBroker()
    with pytest.raises(RuntimeError, match="Secret not found in Keychain"):
        broker.get_telegram_token()


def test_get_telegram_user_id_returns_int(mocker):
    """get_telegram_user_id returns the user ID as an int."""
    mocker.patch('keyring.get_password', return_value='987654321')
    broker = SecretBroker()
    result = broker.get_telegram_user_id()
    assert result == 987654321
    assert isinstance(result, int)


def test_get_telegram_user_id_raises_when_not_found(mocker):
    """get_telegram_user_id raises RuntimeError when keyring returns None."""
    mocker.patch('keyring.get_password', return_value=None)
    broker = SecretBroker()
    with pytest.raises(RuntimeError, match="Secret not found in Keychain"):
        broker.get_telegram_user_id()
