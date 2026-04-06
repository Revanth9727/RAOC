"""VerificationAgent — checks actual outcomes against expected post-execution state.

No LLM call. Uses HostSampler to inspect the file system and compares
against what the execution summary reports.
"""

import logging
from pathlib import Path

from raoc import config
from raoc.db import queries
from raoc.models.job import JobStatus
from raoc.substrate.host_sampler import HostSampler

logger = logging.getLogger(__name__)


def _find_action(actions: list[dict], action_type: str) -> dict | None:
    """Return the first action dict matching action_type, or None."""
    for a in actions:
        atype = a["action_type"]
        if isinstance(atype, str):
            if atype == action_type:
                return a
        else:
            if atype.value == action_type:
                return a
    return None


def _find_all_actions(actions: list[dict], action_type: str) -> list[dict]:
    """Return all action dicts matching action_type."""
    result = []
    for a in actions:
        atype = a["action_type"]
        val = atype if isinstance(atype, str) else atype.value
        if val == action_type:
            result.append(a)
    return result


def _parse_exit_code(output: str | None) -> int | None:
    """Extract exit_code from CMD_EXECUTE output string."""
    if not output:
        return None
    for line in output.splitlines():
        if line.startswith("exit_code="):
            try:
                return int(line.split("=", 1)[1])
            except ValueError:
                pass
    return None


def _parse_stdout(output: str | None) -> str:
    """Extract stdout from CMD_EXECUTE output string."""
    if not output:
        return ""
    lines = output.splitlines()
    capturing = False
    stdout_lines = []
    for line in lines:
        if line.startswith("stdout="):
            capturing = True
            stdout_lines.append(line[len("stdout="):])
        elif line.startswith("stderr=") and capturing:
            break
        elif capturing:
            stdout_lines.append(line)
    return "\n".join(stdout_lines)


def _parse_before_size(output: str | None) -> int:
    """Extract size from FILE_READ output string ('size=N')."""
    if not output:
        return 0
    for line in output.splitlines():
        if line.startswith("size="):
            try:
                return int(line.split("=", 1)[1])
            except ValueError:
                pass
    return 0


def _check(name: str, passed: bool, detail: str) -> dict:
    return {"name": name, "passed": passed, "detail": detail}


class VerificationAgent:
    """Verifies post-execution file and process state for a completed job.

    Reads current file system state and compares it against the execution
    summary. Advances job status to REPORTING.
    """

    def __init__(self, db, sampler: HostSampler) -> None:
        """Initialise with a database engine (or None) and a HostSampler."""
        self.db = db if (db is None or hasattr(db, "connect")) else None
        self.sampler = sampler

    def run(self, job_id: str, execution_summary: dict) -> dict:
        """Verify outcomes and return a verification_result dict."""
        job = queries.get_job(job_id, engine=self.db)
        actions = execution_summary.get("actions", [])

        if job.task_type == "rewrite_file":
            result = self._verify_rewrite(job_id, job.target_path, job.output_path, actions)
        else:
            result = self._verify_run_script(job_id, job.target_path, actions)

        all_passed = result["all_passed"]
        queries.update_job_status(job_id, JobStatus.REPORTING, engine=self.db)
        queries.write_audit(
            job_id,
            "verification_complete",
            detail=f"all_passed={all_passed}",
            engine=self.db,
        )
        logger.info("Job %s verification complete: all_passed=%s", job_id, all_passed)
        return result

    # ------------------------------------------------------------------
    # Task-specific verifiers
    # ------------------------------------------------------------------

    def _verify_rewrite(
        self, job_id: str, target_path: str, output_path_str: str | None, actions: list[dict]
    ) -> dict:
        """Verify a rewrite_file job.

        target_path is the original input file (used for the backup check).
        output_path_str is where the rewritten file was saved; for PDF inputs
        this is a .docx path distinct from target_path. Falls back to
        target_path when not set (non-PDF rewrites that predate this column).
        """
        original = Path(target_path)
        # For PDF rewrites, output_path is the .docx; for all others it equals original.
        output = Path(output_path_str) if output_path_str else original

        # Derive the actual backup path from the FILE_BACKUP action's execution_output.
        # _do_file_backup stores the full timestamped path there.
        # Fall back to the generic (legacy) path if the action is absent.
        backup_action = _find_action(actions, "file_backup")
        backup_output = backup_action.get("output") if backup_action else None
        if backup_output and backup_output.strip():
            backup = Path(backup_output.strip())
        else:
            backup = config.BACKUPS_DIR / (original.name + ".bak")

        checks = []

        # Check 1: output file exists
        output_exists = output.exists()
        checks.append(_check(
            "target_file_exists",
            output_exists,
            str(output) if output_exists else f"File not found: {output}",
        ))

        # Check 2: backup of original input exists (using actual timestamped path)
        backup_exists = backup.exists()
        checks.append(_check(
            "backup_exists",
            backup_exists,
            str(backup) if backup_exists else f"Backup not found: {backup}",
        ))

        # Check 3: output file is non-empty
        after_size = output.stat().st_size if output_exists else 0
        checks.append(_check(
            "target_not_empty",
            after_size > 0,
            f"size={after_size} bytes",
        ))

        # Check 4 (warn): output size differs from original input size
        file_read_action = _find_action(actions, "file_read")
        before_size = _parse_before_size(file_read_action.get("output") if file_read_action else None)
        if output_exists and before_size > 0:
            changed = after_size != before_size
            checks.append(_check(
                "content_changed",
                True,  # warn only — never blocks all_passed
                "changed" if changed else "WARNING: output size identical to input size",
            ))
        else:
            checks.append(_check("content_changed", True, "could not compare — file or size missing"))

        # before/after state — backup_path uses the actual (timestamped) backup path
        before_state = {"file_path": str(original), "size_bytes": before_size}
        after_state = {
            "file_path": str(output),
            "size_bytes": after_size,
            "backup_path": str(backup),
        }

        # all_passed ignores the warn-only check (index 3)
        critical_checks = checks[:3]
        all_passed = all(c["passed"] for c in critical_checks)

        return {
            "job_id": job_id,
            "task_type": "rewrite_file",
            "all_passed": all_passed,
            "checks": checks,
            "before_state": before_state,
            "after_state": after_state,
        }

    def _verify_run_script(self, job_id: str, target_path: str, actions: list[dict]) -> dict:
        """Verify a run_script job."""
        target = Path(target_path)
        checks = []

        # Identify the CMD_EXECUTE actions; the last one is the run step
        cmd_executes = _find_all_actions(actions, "cmd_execute")
        run_action = cmd_executes[-1] if cmd_executes else None

        output = run_action.get("output") if run_action else None
        exit_code = _parse_exit_code(output)
        stdout = _parse_stdout(output)

        # Check 1: exit_code == 0
        exited_ok = exit_code == 0
        checks.append(_check(
            "exit_code_zero",
            exited_ok,
            f"exit_code={exit_code}",
        ))

        # Check 2 (warn): non-empty stdout
        has_stdout = bool(stdout.strip())
        checks.append(_check(
            "has_stdout",
            True,  # warn only
            "stdout present" if has_stdout else "WARNING: empty stdout",
        ))

        # Check 3: script file exists
        script_exists = target.exists()
        checks.append(_check(
            "script_file_exists",
            script_exists,
            str(target) if script_exists else f"Script not found: {target}",
        ))

        # script_existed: True if there was only 1 CMD_EXECUTE (run only),
        # False if 2 (write + run)
        script_existed = len(cmd_executes) <= 1

        before_state = {
            "script_path": str(target),
            "script_existed": script_existed,
        }
        after_state = {
            "exit_code": exit_code,
            "output_lines": stdout.splitlines()[:20],
        }

        critical_checks = [checks[0], checks[2]]  # exit_code + script_exists
        all_passed = all(c["passed"] for c in critical_checks)

        return {
            "job_id": job_id,
            "task_type": "run_script",
            "all_passed": all_passed,
            "checks": checks,
            "before_state": before_state,
            "after_state": after_state,
        }
