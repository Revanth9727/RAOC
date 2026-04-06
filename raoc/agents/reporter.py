"""ReporterAgent — formats and sends the evidence report to Telegram.

Reads verification results and the change_summary from the planning action,
then sends one well-formatted plain English message to the user's phone.
"""

import asyncio
import logging
from pathlib import Path

from sqlalchemy import text as sql_text

from raoc import config
from raoc.db import queries
from raoc.db.schema import get_engine
from raoc.models.job import JobStatus
from raoc.gateway.telegram_bot import TelegramGateway

logger = logging.getLogger(__name__)


def _fire(coro) -> None:
    """Run a coroutine from sync code, compatible with both test and production contexts."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(coro)
    except RuntimeError:
        asyncio.run(coro)


def _get_pdf_inplace_fallback_note(job_id: str, task_type: str, engine) -> str | None:
    """Return a user-facing note if PDF in-place rewriting fell back to DOCX."""
    if task_type != "rewrite_file":
        return None
    try:
        with engine.connect() as conn:
            row = conn.execute(
                sql_text(
                    "SELECT execution_output FROM actions "
                    "WHERE job_id = :jid AND action_type = 'file_write' "
                    "ORDER BY step_index DESC LIMIT 1"
                ),
                {"jid": job_id},
            ).fetchone()
        if row and row[0] and "PDF_INPLACE_FALLBACK" in row[0]:
            return (
                "Note: In-place PDF rewriting was attempted but the rewritten "
                "content exceeded the layout boundaries. Output was saved as "
                "DOCX instead. Original PDF is backed up."
            )
        return None
    except Exception as exc:
        logger.warning("Could not fetch fallback note: %s", exc)
        return None


def _get_change_summary(job_id: str, task_type: str, engine) -> str | None:
    """Fetch the change_summary from the relevant planning action for this job."""
    action_type = "file_write" if task_type == "rewrite_file" else "cmd_execute"
    try:
        with engine.connect() as conn:
            row = conn.execute(
                sql_text(
                    "SELECT change_summary FROM actions "
                    "WHERE job_id = :jid AND action_type = :atype "
                    "ORDER BY step_index DESC LIMIT 1"
                ),
                {"jid": job_id, "atype": action_type},
            ).fetchone()
        return row[0] if row and row[0] else None
    except Exception as exc:
        logger.warning("Could not fetch change_summary: %s", exc)
        return None


def _build_report(
    verification_result: dict,
    change_summary: str | None,
    fallback_note: str | None = None,
) -> str:
    """Build the human-readable report text from a verification result."""
    task_type = verification_result["task_type"]
    all_passed = verification_result["all_passed"]
    before = verification_result.get("before_state", {})
    after = verification_result.get("after_state", {})
    checks = verification_result.get("checks", [])

    if task_type == "rewrite_file":
        original_path = Path(after.get("file_path", ""))
        backup_filename = Path(after.get("backup_path", "")).name or "backup"

        # Detect PDF → DOCX conversion and build a note for the user
        if original_path.suffix.lower() == '.pdf':
            if fallback_note:
                # In-place attempt fell back to DOCX
                display_filename = original_path.stem + config.PDF_OUTPUT_EXTENSION
                format_note = f"{fallback_note}\nOriginal PDF is backed up as {backup_filename}\n\n"
            else:
                display_filename = original_path.stem + config.PDF_OUTPUT_EXTENSION
                format_note = (
                    f"Note: your PDF was converted to DOCX format to allow rewriting.\n"
                    f"Original PDF is backed up as {backup_filename}\n\n"
                )
        else:
            display_filename = original_path.name or "file"
            format_note = ""

        if all_passed:
            summary_text = change_summary or "The file was rewritten as requested."
            return (
                f"{format_note}"
                f"✅ Done — {display_filename} rewritten\n\n"
                f"What I did:\n{summary_text}\n\n"
                f"Backup saved as {backup_filename}"
            )
        else:
            failed = next((c for c in checks if not c["passed"]), {})
            reason = failed.get("detail", "unknown error")
            backup_path = after.get("backup_path", "")
            if backup_path and Path(backup_path).exists():
                restore_note = "Original file is safe — backup was not needed."
            else:
                restore_note = "Original restored from backup successfully."
            return (
                f"{format_note}"
                f"❌ Failed — {display_filename} not changed\n\n"
                f"Reason: {reason}\n"
                f"{restore_note}"
            )

    else:  # run_script
        script_name = Path(before.get("script_path", "")).name or "script"
        exit_code = after.get("exit_code")
        output_lines = after.get("output_lines", [])

        if all_passed:
            summary_text = change_summary or "The script ran successfully."
            output_text = "\n".join(output_lines[:10]) if output_lines else "(no output)"
            return (
                f"✅ Done — {script_name} executed\n\n"
                f"What happened:\n{summary_text}\n\n"
                f"Output:\n{output_text}"
            )
        else:
            stderr_note = after.get("stderr", "")
            stderr_lines = "\n".join(str(stderr_note).splitlines()[:3]) if stderr_note else "see logs"
            return (
                f"❌ Failed — {script_name} did not complete\n\n"
                f"Reason: Exit code {exit_code}\n"
                f"Error: {stderr_lines}"
            )


class ReporterAgent:
    """Formats the verification result and sends the report to Telegram.

    Advances job status to COMPLETED on success, FAILED if all_passed=False.
    """

    def __init__(self, db, gateway: TelegramGateway) -> None:
        """Initialise with db engine (or None) and gateway."""
        self.db = db if (db is None or hasattr(db, "connect")) else None
        self.gateway = gateway
        self._engine = self.db if self.db is not None else get_engine()

    def run(self, job_id: str, verification_result: dict) -> None:
        """Send the evidence report and finalise the job."""
        try:
            # 1. Update to REPORTING
            queries.update_job_status(job_id, JobStatus.REPORTING, engine=self.db)

            # 2. Fetch change_summary and PDF fallback note from planning actions
            task_type = verification_result.get("task_type", "")
            change_summary = _get_change_summary(job_id, task_type, self._engine)
            fallback_note = _get_pdf_inplace_fallback_note(job_id, task_type, self._engine)

            # 3. Build and send report
            report_text = _build_report(verification_result, change_summary, fallback_note)
            _fire(self.gateway.send_message(text=report_text))

            # 4. Finalise job status
            all_passed = verification_result.get("all_passed", False)
            final_status = JobStatus.COMPLETED if all_passed else JobStatus.FAILED
            queries.update_job_status(job_id, final_status, engine=self.db)

            # 5. Audit
            queries.write_audit(job_id, "report_sent", engine=self.db)
            logger.info("Job %s report sent, status → %s", job_id, final_status)

        except Exception as exc:
            queries.update_job_status(
                job_id, JobStatus.FAILED, error=str(exc), engine=self.db
            )
            queries.write_audit(job_id, "job_failed", detail=str(exc), engine=self.db)
            raise
