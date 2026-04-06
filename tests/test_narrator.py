"""Tests for raoc.substrate.status_narrator — StatusNarrator."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from raoc import config
from raoc.substrate.exceptions import LLMError
from raoc.substrate.status_narrator import StatusNarrator


def _mock_llm(text: str = "Working on it.") -> MagicMock:
    """Return a mock LLMClient whose call() returns a text block dict."""
    llm = MagicMock()
    llm.call.return_value = {"type": "text", "text": text}
    return llm


def test_narrate_returns_string():
    """narrate() returns a non-empty string for a valid stage and context."""
    narrator = StatusNarrator(_mock_llm("Reading notes.txt now."))

    result = narrator.narrate('message_received', {'raw_request': 'rewrite notes.txt'})

    assert isinstance(result, str)
    assert len(result) > 0


def test_narrate_never_raises_on_llm_failure():
    """narrate() returns a fallback string when LLMClient raises LLMError."""
    llm = MagicMock()
    llm.call.side_effect = LLMError("API unavailable")

    narrator = StatusNarrator(llm)

    result = narrator.narrate('discovery_complete', {'file_name': 'notes.txt'})

    assert isinstance(result, str)
    assert len(result) > 0  # fallback returned, no exception raised


def test_narrate_uses_haiku_model():
    """narrate() passes NARRATOR_MODEL to LLMClient, not LLM_MODEL."""
    llm = _mock_llm("Status update.")
    narrator = StatusNarrator(llm)

    narrator.narrate('intake_complete', {'task_type': 'rewrite_file', 'target': 'notes.txt',
                                          'instruction': 'rewrite it'})

    call_kwargs = llm.call.call_args
    # The model kwarg must be NARRATOR_MODEL
    passed_model = call_kwargs.kwargs.get('model') or (
        call_kwargs.args[3] if len(call_kwargs.args) > 3 else None
    )
    assert passed_model == config.NARRATOR_MODEL
    assert passed_model != config.LLM_MODEL


def test_narrate_fallback_contains_stage_name():
    """When LLMClient raises, the fallback string references the stage."""
    llm = MagicMock()
    llm.call.side_effect = LLMError("timeout")

    narrator = StatusNarrator(llm)
    result = narrator.narrate('discovery_complete', {})

    # Fallback is derived from stage name, so should contain 'discovery'
    assert 'discovery' in result.lower()


# ── narrate_async tests ─────────────────────────────────────────────────────


def _mock_llm_with_async(text: str = "Working on it.") -> MagicMock:
    """Return a mock LLMClient whose call_async() returns a text block dict."""
    llm = MagicMock()
    llm.call.return_value = {"type": "text", "text": text}
    llm.call_async = AsyncMock(return_value={"type": "text", "text": text})
    return llm


async def test_narrate_async_returns_string():
    """narrate_async() returns a non-empty string for a valid stage and context."""
    narrator = StatusNarrator(_mock_llm_with_async("File found. Building plan."))

    result = await narrator.narrate_async('discovery_complete', {'file_name': 'notes.txt'})

    assert isinstance(result, str)
    assert len(result) > 0


async def test_narrate_async_never_raises_on_llm_failure():
    """narrate_async() returns a fallback string when call_async raises LLMError."""
    llm = MagicMock()
    llm.call_async = AsyncMock(side_effect=LLMError("API unavailable"))

    narrator = StatusNarrator(llm)

    result = await narrator.narrate_async('discovery_complete', {'file_name': 'notes.txt'})

    assert isinstance(result, str)
    assert len(result) > 0  # fallback returned, no exception raised
