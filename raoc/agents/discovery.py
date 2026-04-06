"""DiscoveryAgent — resolves the target path and samples file state.

Uses HostSampler to inspect the workspace and, when a file is not found by
exact match, calls the LLM to fuzzy-resolve the filename before giving up.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

from raoc import config
from raoc.db import queries
from raoc.models.job import JobStatus
from raoc.models.scope import NeedsPermission
from raoc.substrate.exceptions import (
    ExtractionError,
    FileTooLargeError,
    ScopeViolationError,
    ZipFileDetectedError,
)
from raoc.substrate.host_sampler import HostSampler

logger = logging.getLogger(__name__)

_RESOLVE_SYSTEM = (
    "You are helping resolve a filename. The user mentioned a file that was not found exactly. "
    "Look at the available files and determine what they most likely meant. Consider: case "
    "differences, partial names, common typos, singular/plural differences. If you are confident "
    "about the match, return it. Only ask the user if you genuinely cannot determine which file "
    "they meant."
)

_RESOLVE_TOOL = {
    "name": "resolve_file",
    "description": (
        "Given a filename the user mentioned and the actual files in the workspace, "
        "find the best match or determine that clarification is needed."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "best_match": {
                "type": ["string", "null"],
                "description": "The filename from the workspace that most likely matches. null if unclear.",
            },
            "confidence": {
                "type": "string",
                "enum": ["high", "medium", "low"],
            },
            "clarification_needed": {
                "type": "boolean",
            },
            "clarification_question": {
                "type": ["string", "null"],
                "description": "One-sentence question for the user if confidence is low or no match found.",
            },
        },
        "required": ["best_match", "confidence", "clarification_needed"],
    },
}


class DiscoveryAgent:
    """Resolves the target file and produces a ContextPackage dict.

    Enforces workspace boundaries, checks file lock state, and reads
    text content for rewrite tasks.
    """

    def __init__(self, db, sampler: HostSampler, llm=None, zone_resolver=None) -> None:
        """Initialise with a database engine (or None), a HostSampler, and optional dependencies."""
        self.db = db if (db is None or hasattr(db, "connect")) else None
        self.sampler = sampler
        self.llm = llm
        self.zone_resolver = zone_resolver

    def run(self, job_id: str) -> dict:
        """Resolve the target file and return a ContextPackage dict.

        Advances job status to PLANNING on success.
        Sets BLOCKED on scope violations, FAILED on all other errors.
        """
        try:
            # 1. Read job
            job = queries.get_job(job_id, engine=self.db)

            # 2 & 3. Status + audit
            queries.update_job_status(job_id, JobStatus.DISCOVERING, engine=self.db)
            queries.write_audit(job_id, "discovery_started", engine=self.db)

            # 4a. New-script shortcut — no file to look up
            if job.task_type == "run_script" and not job.target_path:
                context_package = {
                    "job_id": job_id,
                    "task_type": "run_script",
                    "target_path": None,
                    "file_metadata": None,
                    "file_content": None,
                    "script_exists": False,
                    "instruction": job.raw_request,
                    "discovered_at": datetime.now(timezone.utc).isoformat(),
                }
                queries.update_job_status(job_id, JobStatus.PLANNING, engine=self.db)
                queries.write_audit(
                    job_id, "discovery_complete",
                    detail="no existing script — planner will create",
                    engine=self.db,
                )
                logger.info("Job %s discovery complete: new script task", job_id)
                return context_package

            # 4b. Resolve target path for existing file tasks
            resolve_result = self._resolve_path(job_id, job.target_path)

            # _resolve_path returns None when clarification was requested
            if resolve_result is None:
                return None

            # _resolve_path returns NeedsPermission for outside-scope paths
            if isinstance(resolve_result, NeedsPermission):
                return resolve_result

            resolved_path = resolve_result

            # 5. Sample file metadata
            file_metadata = self.sampler.sample_file(resolved_path)

            # 6. Check lock
            if file_metadata["is_locked"]:
                msg = "File is currently open in another application."
                queries.update_job_status(job_id, JobStatus.FAILED, error=msg, engine=self.db)
                queries.write_audit(job_id, "job_failed", detail=msg, engine=self.db)
                raise RuntimeError(msg)

            # 7 & 8. Read content based on task type
            file_content: str | None = None
            script_exists: bool = file_metadata["exists"]

            # For run_script, only treat the resolved file as an existing runnable
            # script if it has a recognised script extension. A .txt or other data
            # file is not executable as a script even if it exists in the workspace.
            if job.task_type == "run_script" and script_exists:
                if resolved_path.suffix.lower() not in (".py", ".sh"):
                    script_exists = False

            if job.task_type == "rewrite_file":
                try:
                    file_content, detected_format, output_path, text_blocks, extraction_method = (
                        self.sampler.extract_text_for_rewrite(resolved_path)
                    )
                except ZipFileDetectedError as exc:
                    question = (
                        f"This is a ZIP file containing {len(exc.contents)} items:\n"
                        + "\n".join(f"  \u2022 {f}" for f in exc.contents)
                        + "\n\nWhich file would you like me to extract and rewrite?"
                    )
                    queries.update_job_field(
                        job_id, clarification_question=question, engine=self.db
                    )
                    queries.update_job_field(
                        job_id, zip_source_path=str(exc.path), engine=self.db
                    )
                    queries.update_job_status(
                        job_id, JobStatus.AWAITING_APPROVAL, engine=self.db
                    )
                    queries.write_audit(
                        job_id, "discovery_zip_detected", detail=question, engine=self.db
                    )
                    logger.info("Job %s ZIP clarification needed: %s", job_id, exc.path)
                    return None
                except ExtractionError as exc:
                    msg = str(exc)
                    queries.update_job_status(
                        job_id, JobStatus.FAILED, error=msg, engine=self.db
                    )
                    queries.write_audit(job_id, "discovery_failed", detail=msg, engine=self.db)
                    raise RuntimeError(msg) from exc
                except FileTooLargeError as exc:
                    msg = str(exc)
                    queries.update_job_status(
                        job_id, JobStatus.FAILED, error=msg, engine=self.db
                    )
                    queries.write_audit(job_id, "job_failed", detail=msg, engine=self.db)
                    raise RuntimeError(msg) from exc

                # Step 2: Log extraction results immediately
                logger.info(
                    "Rewrite extraction: path=%s size=%s format=%s method=%s content_len=%s",
                    resolved_path,
                    file_metadata["size_bytes"],
                    detected_format,
                    extraction_method,
                    len(file_content or ""),
                )

                # Step 1: Separate "truly empty" from "extraction failed"
                if file_metadata["size_bytes"] == 0:
                    msg = f"{resolved_path.name} is empty on disk (0 bytes). Cannot rewrite an empty file."
                    queries.update_job_status(job_id, JobStatus.FAILED, error=msg, engine=self.db)
                    queries.write_audit(job_id, "discovery_failed", detail=msg, engine=self.db)
                    raise RuntimeError(msg)

                if not file_content or len(file_content.strip()) == 0:
                    msg = (
                        f"{resolved_path.name} is not empty on disk "
                        f"({file_metadata['size_bytes']} bytes), but text extraction "
                        f"returned no content. Detected format: {detected_format}. "
                        f"Extraction method: {extraction_method}. "
                        f"The file may be corrupted, password-protected, or in an "
                        f"unsupported format."
                    )
                    queries.update_job_status(job_id, JobStatus.FAILED, error=msg, engine=self.db)
                    queries.write_audit(job_id, "discovery_failed", detail=msg, engine=self.db)
                    raise RuntimeError(msg)

            elif job.task_type == "run_script":
                if script_exists:
                    try:
                        file_content = self.sampler.read_text_file(resolved_path)
                    except (FileTooLargeError, ValueError) as exc:
                        msg = str(exc)
                        queries.update_job_status(
                            job_id, JobStatus.FAILED, error=msg, engine=self.db
                        )
                        queries.write_audit(job_id, "job_failed", detail=msg, engine=self.db)
                        raise RuntimeError(msg) from exc
                # If not exists, planner will write the script — content stays None

            # 9. Build ContextPackage
            context_package = {
                "job_id": job_id,
                "task_type": job.task_type,
                "target_path": str(resolved_path),
                "file_metadata": file_metadata,
                "file_content": file_content,
                "script_exists": script_exists,
                "discovered_at": datetime.now(timezone.utc).isoformat(),
            }

            # For rewrite_file, record where execution should write the output.
            # PDF → DOCX conversion produces a different output path than input.
            if job.task_type == "rewrite_file":
                context_package["detected_format"] = detected_format
                context_package["output_path"] = str(output_path)
                context_package["text_blocks"] = text_blocks if detected_format == 'pdf' else []
                context_package["extraction_method"] = extraction_method if detected_format == 'pdf' else 'text'
                if output_path != resolved_path:
                    context_package["format_change"] = True
                    context_package["format_change_note"] = (
                        f"Input is a PDF. Output will be saved as "
                        f"{output_path.name} (DOCX format) to preserve layout. "
                        f"The original PDF will be backed up."
                    )
                else:
                    context_package["format_change"] = False
                    context_package["format_change_note"] = None

            # 10 & 11 & 12. Persist resolved path, set scope_root, advance status, audit
            scope_root = str(resolved_path.parent)
            field_updates: dict = {"target_path": str(resolved_path), "scope_root": scope_root}
            if job.task_type == "rewrite_file":
                field_updates["output_path"] = str(output_path)
            queries.update_job_field(job_id, engine=self.db, **field_updates)
            queries.update_job_status(job_id, JobStatus.PLANNING, engine=self.db)
            queries.write_audit(job_id, "discovery_complete", engine=self.db)
            logger.info("Job %s discovery complete: %s (scope_root=%s)", job_id, resolved_path, scope_root)
            return context_package

        except ScopeViolationError:
            raise  # already handled in _resolve_path
        except Exception as exc:
            queries.update_job_status(
                job_id, JobStatus.FAILED, error=str(exc), engine=self.db
            )
            queries.write_audit(job_id, "job_failed", detail=str(exc), engine=self.db)
            raise

    def _resolve_path(self, job_id: str, target_path: str | None) -> Path | NeedsPermission | None:
        """Resolve target_path to an absolute Path.

        Returns:
            Path: resolved path if inside scope or workspace
            NeedsPermission: if path is outside scope and needs user approval
            None: if clarification was requested (LLM fuzzy resolution)

        Raises ScopeViolationError for forbidden paths.
        Raises RuntimeError if a filename cannot be found.
        """
        if not target_path:
            # No path provided — return the workspace scripts dir as placeholder
            return config.SCRIPTS_DIR

        path = Path(target_path)

        if path.is_absolute():
            resolved = path.resolve()

            # Check forbidden paths — always blocked
            if self.zone_resolver is not None and self.zone_resolver.is_forbidden(resolved):
                msg = (
                    f"'{resolved.name}' is a forbidden system path. "
                    f"RAOC cannot access this location."
                )
                queries.update_job_status(
                    job_id, JobStatus.FAILED, error=msg, engine=self.db
                )
                queries.write_audit(job_id, "discovery_failed", detail=msg, engine=self.db)
                raise ScopeViolationError(msg)

            # Check scope — get the job's scope_root
            job = queries.get_job(job_id, engine=self.db)
            scope_root = Path(job.scope_root) if job.scope_root else config.WORKSPACE

            if self.zone_resolver is not None:
                access = self.zone_resolver.check_access(resolved, scope_root, 'read')
            else:
                # Fallback: check workspace boundary
                try:
                    resolved.relative_to(config.WORKSPACE.resolve())
                    access = 'allowed'
                except ValueError:
                    access = 'needs_approval'

            if access == 'needs_approval':
                return NeedsPermission(
                    reason='path_outside_scope',
                    path=str(resolved),
                    requested_action='read',
                )
            elif access == 'forbidden':
                msg = (
                    f"'{resolved.name}' is a forbidden system path. "
                    f"RAOC cannot access this location."
                )
                queries.update_job_status(
                    job_id, JobStatus.FAILED, error=msg, engine=self.db
                )
                queries.write_audit(job_id, "discovery_failed", detail=msg, engine=self.db)
                raise ScopeViolationError(msg)

            return resolved

        # Filename only — search inside scope_root (not whole workspace)
        job = queries.get_job(job_id, engine=self.db)

        if not job.scope_root:
            # No scope_root set yet — do NOT search broadly.
            # Ask the user for the full path instead of guessing.
            question = (
                f"I found a filename '{path.name}' but no directory context. "
                f"Which folder is it in? Reply with the full path."
            )
            queries.update_job_field(
                job_id, clarification_question=question, engine=self.db,
            )
            queries.update_job_status(job_id, JobStatus.AWAITING_APPROVAL, engine=self.db)
            queries.write_audit(
                job_id, 'discovery_filename_no_scope',
                detail=f'filename only ({path.name}), no scope_root set',
                engine=self.db,
            )
            return None

        search_root = Path(job.scope_root)
        matches = list(search_root.rglob(path.name))
        if matches:
            if len(matches) > 1:
                # Multiple files with same name — ask user to clarify
                match_list = '\n'.join(f'  • {m}' for m in matches[:5])
                question = (
                    f"Found {len(matches)} files named '{path.name}':\n"
                    f"{match_list}\n\n"
                    f"Which one do you mean? Reply with the full path."
                )
                queries.update_job_field(
                    job_id, clarification_question=question, engine=self.db,
                )
                queries.update_job_status(job_id, JobStatus.AWAITING_APPROVAL, engine=self.db)
                queries.write_audit(
                    job_id, 'discovery_ambiguous_filename',
                    detail=f'{len(matches)} matches for {path.name}', engine=self.db,
                )
                return None
            return matches[0].resolve()

        # Not found — try LLM fuzzy resolution
        return self._resolve_with_llm(job_id, path.name)

    def _resolve_with_llm(self, job_id: str, original_name: str) -> Path | None:
        """Use the LLM to fuzzy-match original_name against files in scope.

        Returns a resolved Path if confidence is high.
        Returns None (and sets job to AWAITING_APPROVAL) when clarification is needed.
        """
        job = queries.get_job(job_id, engine=self.db)
        search_root = Path(job.scope_root) if job.scope_root else config.WORKSPACE
        all_files = [
            f.name for f in search_root.rglob("*") if f.is_file()
        ]

        if self.llm is not None:
            response = self.llm.call(
                system=_RESOLVE_SYSTEM,
                user=f"User mentioned: {original_name}\nAvailable files: {all_files}",
                tools=[_RESOLVE_TOOL],
            )
            tool_input = response.get("input", {})
            best_match = tool_input.get("best_match")
            confidence = tool_input.get("confidence", "low")
            clarification_needed = tool_input.get("clarification_needed", True)
            question = tool_input.get("clarification_question")

            if confidence == "high" and best_match:
                candidates = list(config.WORKSPACE.rglob(best_match))
                if candidates:
                    logger.info(
                        "Resolved '%s' to '%s' automatically", original_name, best_match
                    )
                    return candidates[0].resolve()

            if clarification_needed and question:
                queries.update_job_field(
                    job_id, clarification_question=question, engine=self.db
                )
                queries.update_job_status(
                    job_id, JobStatus.AWAITING_APPROVAL, engine=self.db
                )
                queries.write_audit(
                    job_id, "discovery_needs_clarification", detail=question, engine=self.db
                )
                return None

        # No LLM, or LLM gave no useful match — list workspace files
        if all_files:
            file_list = "\n".join(f"  \u2022 {f}" for f in all_files)
            msg = (
                f"I could not find '{original_name}' in your workspace.\n"
                f"Here is what I have available:\n{file_list}\n"
                f"Which one did you want to work on?"
            )
        else:
            msg = f"I could not find '{original_name}' and your workspace is empty."

        queries.update_job_field(
            job_id, clarification_question=msg, engine=self.db
        )
        queries.update_job_status(job_id, JobStatus.AWAITING_APPROVAL, engine=self.db)
        queries.write_audit(
            job_id, "discovery_needs_clarification", detail=msg, engine=self.db
        )
        return None
