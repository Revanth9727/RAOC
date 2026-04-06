"""Anthropic Claude API wrapper for RAOC.

LLMClient is the single point of contact for all Claude API calls.
It never logs message content — only token counts.
"""

import asyncio
import logging

import anthropic

from raoc import config
from raoc.substrate.exceptions import LLMError
from raoc.substrate.secret_broker import SecretBroker

logger = logging.getLogger(__name__)


class LLMClient:
    """Wraps the Anthropic client with structured tool-use calls.

    Retrieves the API key from SecretBroker at construction time.
    All calls use config.LLM_MODEL and config.LLM_MAX_TOKENS.
    """

    def __init__(self, secret_broker: SecretBroker) -> None:
        """Initialise the Anthropic client using the key from the broker."""
        api_key = secret_broker.get_anthropic_key()
        self._client = anthropic.Anthropic(api_key=api_key)

    def call(
        self,
        system: str,
        user: str,
        tools: list | None = None,
        model: str | None = None,
    ) -> dict:
        """Call Claude and return the first content block as a dict.

        If *tools* is provided the call uses tool_use with tool_choice='any'.
        If *model* is provided it overrides config.LLM_MODEL for this call.
        Raises LLMError on any API failure.
        Never logs the content of messages — only token usage.
        """
        kwargs: dict = {
            "model": model or config.LLM_MODEL,
            "max_tokens": config.LLM_MAX_TOKENS,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = {"type": "any"}

        try:
            response = self._client.messages.create(**kwargs)
        except Exception as exc:
            raise LLMError(f"Claude API call failed: {exc}") from exc

        total_tokens = response.usage.input_tokens + response.usage.output_tokens
        logger.info("LLM call made, tokens used: %d", total_tokens)

        first_block = response.content[0]
        return first_block.model_dump()

    async def call_async(
        self,
        system: str,
        user: str,
        tools: list | None = None,
        model: str | None = None,
    ) -> dict:
        """Async wrapper around call() — runs in a thread pool to avoid blocking.

        Delegates to the synchronous call() via asyncio.to_thread so the event
        loop is not blocked during the Anthropic API round-trip.
        """
        return await asyncio.to_thread(self.call, system, user, tools, model)
