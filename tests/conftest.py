"""Shared pytest fixtures for the RAOC test suite."""

import pytest

from raoc import config


@pytest.fixture(autouse=True)
def zero_narration_delays(monkeypatch):
    """Set narration sleep delays to zero so tests run at full speed."""
    monkeypatch.setattr(config, "NARRATION_DELAY_BEFORE_PLAN", 0.0)
    monkeypatch.setattr(config, "NARRATION_DELAY_BEFORE_EXECUTION", 0.0)
