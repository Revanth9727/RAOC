"""ScreenshotCapture — saves a screenshot of the current screen to the workspace.

All paths come from raoc.config. Uses pyautogui for screen capture and
pathlib for path handling. Never hardcodes paths or strings.
"""

import logging
from pathlib import Path

from raoc import config

logger = logging.getLogger(__name__)


class ScreenshotCapture:
    """Captures screenshots and saves them to the job-scoped screenshots directory.

    All output paths are derived from config.SCREENSHOTS_DIR. Directories are
    created on demand so callers do not need to pre-create them.
    """

    def capture(self, job_id: str, label: str) -> Path:
        """Capture the current screen and save it to SCREENSHOTS_DIR/job_id/label.png.

        Creates the destination directory if it does not already exist.
        Returns the path to the saved screenshot file.
        """
        import pyautogui

        dest_dir = config.SCREENSHOTS_DIR / job_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / f"{label}.png"

        screenshot = pyautogui.screenshot()
        screenshot.save(str(path))

        logger.info("Screenshot saved: %s", path)
        return path
