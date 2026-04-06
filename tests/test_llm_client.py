"""Tests for raoc.substrate.llm_client."""

from unittest.mock import MagicMock, patch

import pytest

from raoc.substrate.exceptions import LLMError
from raoc.substrate.llm_client import LLMClient
from raoc.substrate.secret_broker import SecretBroker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(fake_key: str = "sk-test") -> tuple[LLMClient, MagicMock]:
    """Return an LLMClient and the underlying mock Anthropic client."""
    broker = MagicMock(spec=SecretBroker)
    broker.get_anthropic_key.return_value = fake_key

    mock_anthropic_instance = MagicMock()
    with patch("raoc.substrate.llm_client.anthropic.Anthropic", return_value=mock_anthropic_instance):
        client = LLMClient(broker)

    return client, mock_anthropic_instance


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLLMClientCall:
    """call() returns the first content block as a dict."""

    def test_returns_first_content_block_dict(self):
        client, mock_api = _make_client()

        fake_block = MagicMock()
        fake_block.model_dump.return_value = {"type": "text", "text": "hello"}

        fake_response = MagicMock()
        fake_response.content = [fake_block]
        fake_response.usage.input_tokens = 10
        fake_response.usage.output_tokens = 5

        mock_api.messages.create.return_value = fake_response

        result = client.call(system="sys", user="usr")

        assert result == {"type": "text", "text": "hello"}

    def test_passes_tools_and_tool_choice_when_tools_provided(self):
        client, mock_api = _make_client()

        fake_block = MagicMock()
        fake_block.model_dump.return_value = {"type": "tool_use", "name": "do_thing"}

        fake_response = MagicMock()
        fake_response.content = [fake_block]
        fake_response.usage.input_tokens = 20
        fake_response.usage.output_tokens = 10

        mock_api.messages.create.return_value = fake_response

        tools = [{"name": "do_thing", "description": "...", "input_schema": {}}]
        result = client.call(system="sys", user="usr", tools=tools)

        call_kwargs = mock_api.messages.create.call_args.kwargs
        assert call_kwargs["tools"] == tools
        assert call_kwargs["tool_choice"] == {"type": "any"}
        assert result == {"type": "tool_use", "name": "do_thing"}

    def test_no_tools_kwarg_when_tools_is_none(self):
        client, mock_api = _make_client()

        fake_block = MagicMock()
        fake_block.model_dump.return_value = {"type": "text", "text": "ok"}

        fake_response = MagicMock()
        fake_response.content = [fake_block]
        fake_response.usage.input_tokens = 5
        fake_response.usage.output_tokens = 3

        mock_api.messages.create.return_value = fake_response

        client.call(system="sys", user="usr")

        call_kwargs = mock_api.messages.create.call_args.kwargs
        assert "tools" not in call_kwargs
        assert "tool_choice" not in call_kwargs


class TestLLMClientError:
    """call() raises LLMError on API failure."""

    def test_raises_llm_error_on_api_exception(self):
        client, mock_api = _make_client()
        mock_api.messages.create.side_effect = RuntimeError("network down")

        with pytest.raises(LLMError, match="Claude API call failed"):
            client.call(system="sys", user="usr")


class TestLLMClientSecretNotLogged:
    """The API key must never appear in log output."""

    def test_api_key_not_in_log_records(self, caplog):
        fake_key = "sk-super-secret-key"
        broker = MagicMock(spec=SecretBroker)
        broker.get_anthropic_key.return_value = fake_key

        mock_anthropic_instance = MagicMock()
        fake_block = MagicMock()
        fake_block.model_dump.return_value = {"type": "text", "text": "hi"}
        fake_response = MagicMock()
        fake_response.content = [fake_block]
        fake_response.usage.input_tokens = 1
        fake_response.usage.output_tokens = 1
        mock_anthropic_instance.messages.create.return_value = fake_response

        with patch("raoc.substrate.llm_client.anthropic.Anthropic", return_value=mock_anthropic_instance):
            client = LLMClient(broker)

        import logging
        with caplog.at_level(logging.DEBUG, logger="raoc.substrate.llm_client"):
            client.call(system="sys", user="usr")

        for record in caplog.records:
            assert fake_key not in record.getMessage(), (
                f"API key found in log record: {record.getMessage()}"
            )
