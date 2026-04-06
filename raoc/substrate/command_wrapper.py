"""Safe shell command execution for RAOC.

CommandWrapper is the only component that runs subprocesses.
It enforces workspace boundaries, blocks dangerous patterns, and
caps output length before returning results to the caller.
"""

import logging
import subprocess
import time
from pathlib import Path

from raoc import config
from raoc.substrate.exceptions import CommandBlockedError

logger = logging.getLogger(__name__)

_MAX_COMMAND_LENGTH = 2000
_SAFE_ENV = {"PATH": "/usr/bin:/bin:/usr/local/bin"}


class CommandWrapper:
    """Runs shell commands inside the workspace with strict safety guards.

    Raises CommandBlockedError before execution if any safety check fails.
    Never raises on non-zero exit codes — callers inspect exit_code instead.
    """

    def __init__(self, workspace: Path = None) -> None:
        """Initialise with a workspace path (defaults to config.WORKSPACE)."""
        self.workspace = workspace or config.WORKSPACE

    def run(self, command: str, working_dir: Path = None) -> dict:
        """Run *command* inside the workspace and return a result dict.

        Result keys: exit_code, stdout, stderr, timed_out, duration_ms.

        Pre-flight checks (all raise CommandBlockedError on failure):
        - working_dir must be inside self.workspace
        - command must not contain any blocked pattern (case-insensitive)
        - command length must not exceed 2000 characters
        """
        cwd = (working_dir or self.workspace).resolve()

        # 1. Workspace boundary
        try:
            cwd.relative_to(self.workspace.resolve())
        except ValueError:
            raise CommandBlockedError(
                f"working_dir outside workspace: {cwd}"
            )

        # 2. Blocked patterns
        command_lower = command.lower()
        for pattern in config.BLOCKED_PATTERNS:
            if pattern in command_lower:
                raise CommandBlockedError(
                    f"Command contains blocked pattern: '{pattern}'"
                )

        # 3. Length cap
        if len(command) > _MAX_COMMAND_LENGTH:
            raise CommandBlockedError(
                f"Command exceeds {_MAX_COMMAND_LENGTH} characters: {len(command)}"
            )

        logger.info("Running command (length=%d, cwd=%s)", len(command), cwd)

        start = time.monotonic()
        timed_out = False
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=cwd,
                env=_SAFE_ENV,
                timeout=config.MAX_COMMAND_TIMEOUT,
                stdin=subprocess.DEVNULL,
            )
            exit_code = result.returncode
            stdout = result.stdout
            stderr = result.stderr
        except subprocess.TimeoutExpired:
            timed_out = True
            exit_code = -1
            stdout = ""
            stderr = ""

        duration_ms = int((time.monotonic() - start) * 1000)

        return {
            "exit_code": exit_code,
            "stdout": stdout[: config.MAX_OUTPUT_CHARS],
            "stderr": stderr[: config.MAX_OUTPUT_CHARS],
            "timed_out": timed_out,
            "duration_ms": duration_ms,
        }
