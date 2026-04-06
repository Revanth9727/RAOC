"""Tests for raoc.substrate.command_wrapper.CommandWrapper.

All tests mock subprocess.run — no real commands are ever executed.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from raoc import config
from raoc.substrate.command_wrapper import CommandWrapper
from raoc.substrate.exceptions import CommandBlockedError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_result(stdout: str = "", stderr: str = "", returncode: int = 0) -> MagicMock:
    """Build a mock CompletedProcess."""
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


def _wrapper(tmp_path: Path) -> CommandWrapper:
    """Return a CommandWrapper whose workspace is a tmp directory."""
    workspace = tmp_path / "raoc_workspace"
    workspace.mkdir()
    return CommandWrapper(workspace=workspace)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestValidCommand:
    """Valid command returns correct exit_code and stdout."""

    def test_exit_code_and_stdout(self, tmp_path):
        wrapper = _wrapper(tmp_path)
        with patch("subprocess.run", return_value=_mock_result(stdout="hello", returncode=0)):
            result = wrapper.run("echo hello")

        assert result["exit_code"] == 0
        assert result["stdout"] == "hello"
        assert result["timed_out"] is False

    def test_result_has_all_keys(self, tmp_path):
        wrapper = _wrapper(tmp_path)
        with patch("subprocess.run", return_value=_mock_result()):
            result = wrapper.run("echo ok")

        assert set(result.keys()) == {"exit_code", "stdout", "stderr", "timed_out", "duration_ms"}

    def test_nonzero_exit_code_returned(self, tmp_path):
        wrapper = _wrapper(tmp_path)
        with patch("subprocess.run", return_value=_mock_result(returncode=1)):
            result = wrapper.run("false")

        assert result["exit_code"] == 1


class TestBlockedPatterns:
    """Commands containing blocked patterns raise CommandBlockedError."""

    def test_rm_rf_blocked(self, tmp_path):
        wrapper = _wrapper(tmp_path)
        with pytest.raises(CommandBlockedError, match="rm -rf"):
            wrapper.run("rm -rf /tmp/foo")

    def test_sudo_blocked(self, tmp_path):
        wrapper = _wrapper(tmp_path)
        with pytest.raises(CommandBlockedError, match="sudo"):
            wrapper.run("sudo apt-get install vim")

    def test_blocked_pattern_case_insensitive(self, tmp_path):
        wrapper = _wrapper(tmp_path)
        with pytest.raises(CommandBlockedError):
            wrapper.run("SUDO do something")

    def test_subprocess_not_called_when_blocked(self, tmp_path):
        wrapper = _wrapper(tmp_path)
        with patch("subprocess.run") as mock_run:
            with pytest.raises(CommandBlockedError):
                wrapper.run("sudo ls")
            mock_run.assert_not_called()


class TestWorkingDirOutsideWorkspace:
    """working_dir outside workspace raises CommandBlockedError."""

    def test_raises_for_etc(self, tmp_path):
        wrapper = _wrapper(tmp_path)
        with pytest.raises(CommandBlockedError, match="outside workspace"):
            wrapper.run("ls", working_dir=Path("/etc"))

    def test_raises_for_home(self, tmp_path):
        wrapper = _wrapper(tmp_path)
        with pytest.raises(CommandBlockedError, match="outside workspace"):
            wrapper.run("ls", working_dir=Path.home())


class TestCommandTooLong:
    """Command exceeding 2000 characters raises CommandBlockedError."""

    def test_raises_on_long_command(self, tmp_path):
        wrapper = _wrapper(tmp_path)
        long_cmd = "echo " + "a" * 2000
        with pytest.raises(CommandBlockedError, match="exceeds"):
            wrapper.run(long_cmd)

    def test_exactly_2000_chars_is_allowed(self, tmp_path):
        wrapper = _wrapper(tmp_path)
        cmd = "e" * 2000
        with patch("subprocess.run", return_value=_mock_result()):
            # Should not raise
            wrapper.run(cmd)


class TestTimeout:
    """TimeoutExpired sets timed_out=True in result."""

    def test_timed_out_true_on_timeout(self, tmp_path):
        wrapper = _wrapper(tmp_path)
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="sleep 99", timeout=30)):
            result = wrapper.run("sleep 99")

        assert result["timed_out"] is True
        assert result["exit_code"] == -1

    def test_stdout_empty_on_timeout(self, tmp_path):
        wrapper = _wrapper(tmp_path)
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="sleep 99", timeout=30)):
            result = wrapper.run("sleep 99")

        assert result["stdout"] == ""
        assert result["stderr"] == ""


class TestOutputTruncation:
    """stdout and stderr longer than MAX_OUTPUT_CHARS are truncated."""

    def test_stdout_truncated(self, tmp_path):
        wrapper = _wrapper(tmp_path)
        long_output = "x" * (config.MAX_OUTPUT_CHARS + 500)
        with patch("subprocess.run", return_value=_mock_result(stdout=long_output)):
            result = wrapper.run("echo x")

        assert len(result["stdout"]) == config.MAX_OUTPUT_CHARS

    def test_stderr_truncated(self, tmp_path):
        wrapper = _wrapper(tmp_path)
        long_err = "e" * (config.MAX_OUTPUT_CHARS + 100)
        with patch("subprocess.run", return_value=_mock_result(stderr=long_err)):
            result = wrapper.run("echo x")

        assert len(result["stderr"]) == config.MAX_OUTPUT_CHARS

    def test_short_output_not_truncated(self, tmp_path):
        wrapper = _wrapper(tmp_path)
        with patch("subprocess.run", return_value=_mock_result(stdout="short")):
            result = wrapper.run("echo short")

        assert result["stdout"] == "short"
