"""ExecutionAgent — runs each approved ActionObject in step order.

This is the only agent that touches the file system for writes and runs
real commands. Every step outcome is persisted before moving to the next.
"""

import logging
import shutil
from pathlib import Path

from datetime import datetime

from raoc import config
from raoc.config import make_timestamped_stem
from raoc.db import queries
from raoc.models.action import ActionObject, ActionType
from raoc.models.job import JobStatus
from raoc.substrate.command_wrapper import CommandWrapper
from raoc.substrate.host_sampler import HostSampler

logger = logging.getLogger(__name__)


def _split_rewritten_text(rewritten_text: str, text_blocks: list) -> list:
    """Split rewritten_text proportionally among text_blocks by original char count.

    Returns a list of strings, one per block. The last block absorbs any
    remaining characters to avoid losing content.
    """
    if not text_blocks:
        return []
    if len(text_blocks) == 1:
        return [rewritten_text]

    total_original = sum(len(b.get('original_text', '')) for b in text_blocks)
    if total_original == 0:
        return [rewritten_text] + [''] * (len(text_blocks) - 1)

    parts: list = []
    pos = 0
    total_len = len(rewritten_text)
    for i, block in enumerate(text_blocks):
        if i == len(text_blocks) - 1:
            parts.append(rewritten_text[pos:])
        else:
            share = len(block.get('original_text', '')) / total_original
            chars = int(share * total_len)
            parts.append(rewritten_text[pos: pos + chars])
            pos += chars
    return parts


class ExecutionAgent:
    """Executes a list of ActionObjects produced by PlanningAgent.

    Processes steps in step_index order. Stops immediately on any failure
    and marks the job FAILED (or BLOCKED for safety violations).
    """

    def __init__(
        self,
        db,
        command_wrapper: CommandWrapper,
        sampler: HostSampler,
        zone_resolver=None,
    ) -> None:
        """Initialise with db engine, command wrapper, sampler, and optional zone resolver."""
        self.db = db if (db is None or hasattr(db, "connect")) else None
        self.command_wrapper = command_wrapper
        self.sampler = sampler
        self.zone_resolver = zone_resolver

    def run(self, job_id: str, actions: list[ActionObject]) -> dict:
        """Execute all actions in order and return an execution_summary dict."""
        queries.update_job_status(job_id, JobStatus.EXECUTING, engine=self.db)
        queries.write_audit(job_id, "execution_started", engine=self.db)

        job = queries.get_job(job_id, engine=self.db)
        job_created_at: datetime = job.created_at

        ordered = sorted(actions, key=lambda a: a.step_index)
        steps_completed = 0
        steps_failed = 0
        action_results: list[dict] = []

        for action in ordered:
            queries.update_action_status(action.action_id, "running", engine=self.db)

            status, output = self._execute_action(job_id, action, job_created_at)

            queries.update_action_status(
                action.action_id, status, output=output, engine=self.db
            )
            queries.write_audit(
                job_id,
                action.action_type if isinstance(action.action_type, str) else action.action_type.value,
                detail=status,
                engine=self.db,
            )

            action_results.append({
                "action_id": action.action_id,
                "step_index": action.step_index,
                "action_type": action.action_type,
                "status": status,
                "output": output,
            })

            if status == "failed":
                steps_failed += 1
                queries.update_job_status(
                    job_id, JobStatus.FAILED,
                    error=f"Step {action.step_index} ({action.action_type}) failed",
                    engine=self.db,
                )
                queries.write_audit(job_id, "job_failed", engine=self.db)
                break
            elif status == "blocked":
                steps_failed += 1
                # BLOCKED already set inside _execute_action
                break
            else:
                steps_completed += 1

        else:
            # All steps completed without breaking
            queries.update_job_status(job_id, JobStatus.VERIFYING, engine=self.db)
            queries.write_audit(job_id, "execution_complete", engine=self.db)

        return {
            "job_id": job_id,
            "steps_completed": steps_completed,
            "steps_failed": steps_failed,
            "actions": action_results,
        }

    # ------------------------------------------------------------------
    # Per-action dispatch
    # ------------------------------------------------------------------

    def _execute_action(
        self, job_id: str, action: ActionObject, job_created_at: datetime
    ) -> tuple[str, str | None]:
        """Dispatch to the correct handler. Returns (status, output)."""
        action_type = (
            action.action_type
            if isinstance(action.action_type, ActionType)
            else ActionType(action.action_type)
        )

        try:
            # Final safety net: verify path is inside workspace before any operation
            if self.zone_resolver is not None and action.target_path:
                self.zone_resolver.assert_allowed(Path(action.target_path))

            if action_type == ActionType.CMD_INSPECT:
                return self._do_cmd_inspect(job_id, action)
            elif action_type == ActionType.CMD_EXECUTE:
                return self._do_cmd_execute(action)
            elif action_type == ActionType.FILE_READ:
                return self._do_file_read(action)
            elif action_type == ActionType.FILE_BACKUP:
                return self._do_file_backup(action, job_created_at)
            elif action_type == ActionType.FILE_WRITE:
                return self._do_file_write(action, job_created_at)
            else:
                return "failed", f"Unknown action type: {action_type}"
        except Exception as exc:
            logger.exception("Unhandled error in step %d: %s", action.step_index, exc)
            return "failed", str(exc)

    def _do_cmd_inspect(self, job_id: str, action: ActionObject) -> tuple[str, str | None]:
        try:
            content = self.sampler.read_text_file(Path(action.target_path))
        except Exception:
            # File may not exist yet (generated script); fall back to command field
            content = action.command or ""

        for pattern in config.BLOCKED_PATTERNS:
            if pattern in content:
                msg = f"Blocked pattern found during inspection: '{pattern}'"
                queries.update_job_status(
                    job_id, JobStatus.BLOCKED, error=msg, engine=self.db
                )
                queries.write_audit(job_id, "job_blocked", detail=msg, engine=self.db)
                return "blocked", msg

        return "succeeded", None

    def _do_cmd_execute(self, action: ActionObject) -> tuple[str, str]:
        result = self.command_wrapper.run(
            action.command, working_dir=config.WORKSPACE
        )
        output = (
            f"exit_code={result['exit_code']}\n"
            f"stdout={result['stdout']}\n"
            f"stderr={result['stderr']}"
        )
        status = "succeeded" if result["exit_code"] == 0 else "failed"
        return status, output

    def _do_file_read(self, action: ActionObject) -> tuple[str, str]:
        """Read target file to capture its before-state size.

        Falls back to byte-size for non-UTF-8 files (e.g. DOCX, PDF).
        """
        target = Path(action.target_path)
        try:
            content = self.sampler.read_text_file(target)
            return "succeeded", f"size={len(content)}"
        except Exception:
            # Non-text-readable files (DOCX, PDF) — fall back to byte size
            size = target.stat().st_size if target.exists() else 0
            return "succeeded", f"size={size}"

    def _do_file_backup(self, action: ActionObject, job_created_at: datetime) -> tuple[str, str]:
        """Back up the source file with a timestamped name derived from job_created_at."""
        src = Path(action.target_path)
        config.BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        ts_stem = make_timestamped_stem(src.name, job_created_at)
        dest = config.BACKUPS_DIR / f"{ts_stem}{src.suffix}.bak"
        shutil.copy2(src, dest)
        logger.info("Backup created: %s → %s", src, dest)
        return "succeeded", str(dest)

    def _write_file(self, target: Path, content: str, detected_format: str | None = None) -> None:
        """Write content to target, dispatching by detected_format and extension.

        detected_format == 'pdf'      → should never be reached; raises UnsupportedFileTypeError
        target.suffix == '.docx'
          or detected_format == 'docx' → python-docx Document, one paragraph per non-empty line
        all other cases               → UTF-8 text write
        """
        from raoc.substrate.exceptions import UnsupportedFileTypeError

        ext = target.suffix.lower()

        if detected_format == 'pdf' or ext == '.pdf':
            raise UnsupportedFileTypeError(
                "PDF output is not supported — PDF rewrite produces a DOCX. "
                "This path should never be reached."
            )
        elif ext == '.docx' or detected_format == 'docx':
            from docx import Document
            doc = Document()
            for line in content.split('\n'):
                if line.strip():
                    doc.add_paragraph(line)
            doc.save(str(target))
        else:
            target.write_text(content, encoding='utf-8')

    def _pdf_insert_textbox(self, page, rect, text: str, fontsize: float) -> float:
        """Wrapper around pymupdf insert_textbox for testability.

        Returns excess height if positive (overflow), non-positive if fits.
        """
        return page.insert_textbox(rect, text, fontsize=fontsize, fontname="helv")

    def _do_pdf_inplace(
        self, action: ActionObject, source_pdf: Path, job_created_at: datetime
    ) -> tuple[str, str | None]:
        """Rewrite PDF text in place using pymupdf.

        For each text block: draws a white rect over the original bbox, then
        inserts the proportionally-split rewritten text.  If any block
        overflows, falls back to the pdf_to_docx pipeline, logs the event,
        and returns a note in execution_output.
        """
        import fitz

        text_blocks = getattr(action, 'text_blocks', None) or []
        ts_stem = make_timestamped_stem(source_pdf.name, job_created_at)
        output_pdf = source_pdf.parent / f"{ts_stem}.pdf"

        fallback_reason: str | None = None
        doc = None
        try:
            doc = fitz.open(str(source_pdf))
            rewritten_parts = _split_rewritten_text(action.command or "", text_blocks)

            for i, block in enumerate(text_blocks):
                if i >= len(rewritten_parts):
                    break
                page = doc[block['page']]
                rect = fitz.Rect(block['bbox'])

                # White out the original text area
                page.draw_rect(rect, color=(1, 1, 1), fill=(1, 1, 1))

                remainder = self._pdf_insert_textbox(
                    page, rect, rewritten_parts[i], block['font_size']
                )
                # In pymupdf: positive = space remaining (fits),
                # negative = text overflowed the bounding box
                if remainder < 0:
                    fallback_reason = (
                        f"Rewritten content overflowed block {i} bounding box"
                    )
                    break

            if fallback_reason is None:
                doc.save(str(output_pdf))
                doc.close()
                doc = None
                return "succeeded", None

        except Exception as exc:
            fallback_reason = str(exc)
        finally:
            if doc is not None:
                doc.close()

        # ── Fallback to pdf_to_docx ───────────────────────────────────────
        fallback_note = f"PDF_INPLACE_FALLBACK: {fallback_reason}"
        queries.write_audit(
            action.job_id, "pdf_inplace_fallback",
            detail=fallback_note, engine=self.db
        )
        logger.warning(
            "PDF in-place rewrite failed (%s) — falling back to DOCX", fallback_reason
        )

        # Write rewritten content as DOCX
        docx_output = source_pdf.parent / f"{ts_stem}.docx"
        try:
            docx_output.parent.mkdir(parents=True, exist_ok=True)
            self._write_file(docx_output, action.command or "", detected_format='docx')
            return "succeeded", fallback_note
        except Exception as exc:
            return "failed", f"{fallback_note} | DOCX fallback also failed: {exc}"

    def _do_file_write(self, action: ActionObject, job_created_at: datetime) -> tuple[str, str | None]:
        """Write action.command content to the target file.

        Dispatches to _do_pdf_inplace when write_strategy == 'pdf_inplace'.
        The restore-on-failure backup path is the timestamped form derived from
        job_created_at, matching what _do_file_backup created.
        """
        detected_format = getattr(action, 'detected_format', None)
        write_strategy = getattr(action, 'write_strategy', None)
        target = Path(action.target_path)

        # ── PDF in-place ──────────────────────────────────────────────────
        if write_strategy == 'pdf_inplace':
            return self._do_pdf_inplace(action, target, job_created_at)

        # For PDF→DOCX (detected_format='pdf', target already '.docx'):
        #   compute a timestamped output path so parallel jobs never collide,
        #   then write as DOCX (passing 'pdf' to _write_file would raise).
        # In production the timestamped path is already in action.target_path
        # (set by planning); this branch handles direct test invocations where
        # the action carries the original (untimedstamped) .docx path.
        if detected_format == 'pdf' and target.suffix.lower() == '.docx':
            ts_stem = make_timestamped_stem(target.name, job_created_at)
            target = target.parent / f"{ts_stem}{target.suffix}"
            effective_format = 'docx'
        else:
            effective_format = detected_format

        # The restore-on-failure backup must match the timestamped name _do_file_backup used.
        src_for_backup = Path(action.target_path)
        ts_bak_stem = make_timestamped_stem(src_for_backup.name, job_created_at)
        backup = config.BACKUPS_DIR / f"{ts_bak_stem}{src_for_backup.suffix}.bak"

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            self._write_file(target, action.command or "", detected_format=effective_format)
            return "succeeded", None
        except Exception as exc:
            logger.error("FILE_WRITE failed: %s — attempting restore", exc)
            if backup.exists():
                try:
                    shutil.copy2(backup, src_for_backup)
                    logger.info("Restore succeeded: %s", src_for_backup)
                except Exception as restore_exc:
                    logger.error("Restore also failed: %s", restore_exc)
            return "failed", str(exc)
