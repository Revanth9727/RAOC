"""Tests for raoc.substrate.screenshot.ScreenshotCapture."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from raoc import config
from raoc.substrate.screenshot import ScreenshotCapture


@pytest.fixture()
def screenshots_dir(tmp_path, monkeypatch):
    """Redirect SCREENSHOTS_DIR to a temp path for each test."""
    shots = tmp_path / "screenshots"
    monkeypatch.setattr(config, "SCREENSHOTS_DIR", shots)
    return shots


def _mock_screenshot():
    """Return a MagicMock that behaves like a PIL Image."""
    img = MagicMock()
    img.save = MagicMock()
    return img


def test_capture_saves_to_correct_path(screenshots_dir):
    """capture() saves the screenshot at SCREENSHOTS_DIR/job_id/label.png."""
    img = _mock_screenshot()
    with patch("pyautogui.screenshot", return_value=img):
        result = ScreenshotCapture().capture("job-123", "before")

    expected = screenshots_dir / "job-123" / "before.png"
    assert result == expected
    img.save.assert_called_once_with(str(expected))


def test_directories_created_if_missing(screenshots_dir):
    """capture() creates the parent directories when they do not exist."""
    assert not screenshots_dir.exists()
    img = _mock_screenshot()
    with patch("pyautogui.screenshot", return_value=img):
        ScreenshotCapture().capture("job-abc", "after")

    assert (screenshots_dir / "job-abc").is_dir()


def test_returns_path_object(screenshots_dir):
    """capture() return value is a pathlib.Path instance."""
    img = _mock_screenshot()
    with patch("pyautogui.screenshot", return_value=img):
        result = ScreenshotCapture().capture("job-xyz", "step1")

    assert isinstance(result, Path)
