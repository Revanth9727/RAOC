"""PlanningAgent — builds a linear ActionObject plan for a job.

Calls Claude to generate rewritten file content or a new script when needed.
Performs a safety scan during CMD_INSPECT plan construction.
"""

import logging
from pathlib import Path

from raoc import config
from raoc.config import make_timestamped_stem
from raoc.db import queries
from raoc.models.action import ActionObject, ActionType
from raoc.models.job import JobStatus
from raoc.substrate.exceptions import CommandBlockedError, LLMError
from raoc.substrate.llm_client import LLMClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM tool schemas
# ---------------------------------------------------------------------------

_REWRITE_TOOL = {
    "name": "generate_rewritten_content",
    "description": "Rewrite the provided file content following the instruction exactly.",
    "input_schema": {
        "type": "object",
        "properties": {
            "rewritten_content": {
                "type": "string",
                "description": "The complete rewritten file content to save.",
            },
            "change_summary": {
                "type": "string",
                "description": (
                    "2-3 plain English sentences describing what was changed, "
                    "what tone or style shift happened, and what was added or removed. "
                    "No technical jargon. Written for a non-technical user reading on a phone."
                ),
            },
        },
        "required": ["rewritten_content", "change_summary"],
    },
}

_SCRIPT_TOOL = {
    "name": "generate_script",
    "description": "Write a Python or shell script that fulfils the instruction.",
    "input_schema": {
        "type": "object",
        "properties": {
            "script_filename": {
                "type": "string",
                "description": (
                    "A descriptive snake_case .py filename for the script based on what it does. "
                    "Must end in .py. Must NOT reuse any data filename mentioned in the instruction."
                ),
            },
            "script_content": {
                "type": "string",
                "description": "The complete script content.",
            },
            "script_type": {
                "type": "string",
                "enum": ["python3", "bash"],
                "description": "Interpreter to use when running the script.",
            },
            "change_summary": {
                "type": "string",
                "description": (
                    "2-3 plain English sentences describing what the script does "
                    "and what output to expect. No technical jargon."
                ),
            },
        },
        "required": ["script_filename", "script_content", "script_type", "change_summary"],
    },
}

_SCRIPT_SYSTEM = """
You are a precise script writer for macOS.
Write a clean, correct script that fulfils the instruction.
Use Python 3 unless a shell script is clearly more appropriate.
Also provide a plain English summary of what the script does.

RULE — TARGET vs INPUT FILES:

For run_script tasks there are two distinct concepts:

TARGET FILE:
  The script that is created and executed.
  Always a .py or .sh file.
  This is what script_filename must be — always a .py filename.

INPUT FILES:
  Any file the script reads, processes, or references at runtime.
  Could be any extension (.txt, .csv, etc.).
  These belong inside the script code only.
  They must NEVER appear as script_filename.

To determine the target file:
  Look at what the user wants to DO, not what files they mention.
  Generate a descriptive snake_case .py filename for the script
  based on what it does. Do not reuse any filename the user mentioned
  unless they explicitly named a script.

RULE — CMD_EXECUTE command field:
  Must always be 'python3 /path/to/script.py' or 'bash /path/to/script.sh'.
  Must never be a path to a data file of any kind.
""".strip()

_DESCRIBE_TOOL = {
    "name": "describe_script",
    "description": "Describe what an existing script does and what output to expect.",
    "input_schema": {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": (
                    "2-3 plain English sentences describing what the script does "
                    "and what output to expect. No technical jargon."
                ),
            },
        },
        "required": ["description"],
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _detect_interpreter(path: Path) -> str:
    """Return 'python3' for .py files, 'bash' for everything else."""
    return "python3" if path.suffix.lower() == ".py" else "bash"


def _determine_pdf_write_strategy(
    text_blocks: list,
    rewritten_text: str,
    extraction_method: str,
    output_stem: str = "",
) -> tuple:
    """Determine whether to rewrite a PDF in-place or convert to DOCX.

    Returns (write_strategy, user_message) where write_strategy is either
    'pdf_inplace' or 'pdf_to_docx'.
    """
    if extraction_method == 'pdf_ocr':
        return (
            'pdf_to_docx',
            "This PDF was read using OCR. Layout cannot be preserved. "
            "Output will be saved as a DOCX file.",
        )

    if not text_blocks:
        return (
            'pdf_to_docx',
            "PDF text blocks could not be extracted. "
            "Output will be saved as a DOCX file.",
        )

    total_original = sum(len(b['original_text']) for b in text_blocks)
    total_rewritten = len(rewritten_text)

    if total_original == 0:
        return (
            'pdf_to_docx',
            "Could not measure original text length. Output will be saved as a DOCX file.",
        )

    diff = abs(total_rewritten - total_original) / total_original

    if diff <= config.PDF_REWRITE_LENGTH_TOLERANCE:
        return (
            'pdf_inplace',
            "Layout will be preserved. Text will be rewritten in place.",
        )
    else:
        pct = int(diff * 100)
        direction = "longer" if total_rewritten > total_original else "shorter"
        suffix = f" {output_stem}" if output_stem else ""
        return (
            'pdf_to_docx',
            f"The rewritten content is {pct}% {direction} than the original. "
            f"Rewriting in place would break the layout. "
            f"Output will be saved as{suffix} a DOCX file.",
        )


def _safety_scan(content: str, job_id: str, db) -> None:
    """Raise CommandBlockedError if content contains a blocked pattern."""
    for pattern in config.BLOCKED_PATTERNS:
        if pattern in content:
            msg = f"Blocked pattern found in script: '{pattern}'"
            queries.update_job_status(job_id, JobStatus.BLOCKED, error=msg, engine=db)
            queries.write_audit(job_id, "job_blocked", detail=msg, engine=db)
            raise CommandBlockedError(msg)


def _make_action(
    job_id: str,
    step_index: int,
    action_type: ActionType,
    risk_level: str,
    target_path: str,
    intent: str,
    command: str | None = None,
    change_summary: str | None = None,
) -> ActionObject:
    """Construct an ActionObject with the given parameters."""
    return ActionObject(
        job_id=job_id,
        step_index=step_index,
        action_type=action_type,
        risk_level=risk_level,
        target_path=target_path,
        intent=intent,
        command=command,
        change_summary=change_summary,
    )


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class PlanningAgent:
    """Builds a linear list of ActionObjects for a job and saves them to db.

    Calls Claude to generate rewritten content (rewrite_file) or a new script
    (run_script when the file does not exist). Runs a safety scan on all
    script content before adding CMD_INSPECT to the plan.
    """

    def __init__(self, db, llm: LLMClient) -> None:
        """Initialise with a database engine (or None) and an LLMClient."""
        self.db = db if (db is None or hasattr(db, "connect")) else None
        self.llm = llm

    def run(self, job_id: str, context: dict) -> list[ActionObject]:
        """Build the action plan, save it, and advance status to AWAITING_APPROVAL.

        Returns the list of ActionObjects in execution order.
        """
        try:
            job = queries.get_job(job_id, engine=self.db)
            queries.update_job_status(job_id, JobStatus.PLANNING, engine=self.db)
            queries.write_audit(job_id, "planning_started", engine=self.db)

            task_type = context["task_type"]
            target_path = context["target_path"]
            file_content = context.get("file_content")
            script_exists = context.get("script_exists", False)

            if task_type == "rewrite_file":
                actions = self._plan_rewrite(
                    job_id, job.raw_request, target_path, file_content,
                    context=context, job_created_at=job.created_at,
                )
            elif task_type == "run_script":
                actions = self._plan_run_script(
                    job_id, job.raw_request, target_path, file_content, script_exists
                )
                # Only validate LLM-generated plans (new scripts).
                # For existing scripts the plan is deterministic — a bad target_path
                # is an intake misclassification, not a planning error.
                if not script_exists:
                    try:
                        self._validate_actions(actions, task_type)
                    except ValueError as validation_error:
                        logger.warning("Plan validation failed, retrying: %s", validation_error)
                        actions = self._plan_run_script(
                            job_id, job.raw_request, target_path, file_content,
                            script_exists, retry_hint=str(validation_error)
                        )
                        try:
                            self._validate_actions(actions, task_type)
                        except ValueError:
                            msg = "Could not build a valid plan. Please rephrase your request."
                            queries.update_job_status(
                                job_id, JobStatus.FAILED, error=msg, engine=self.db
                            )
                            queries.write_audit(job_id, "job_failed", detail=msg, engine=self.db)
                            raise ValueError(msg)
            else:
                raise ValueError(f"Unknown task_type: {task_type}")

            for action in actions:
                queries.save_action(action, engine=self.db)

            # For new run_script jobs target_path starts as None; persist the
            # generated script path so downstream agents (verification, reporter)
            # can find the file without reading actions.
            if task_type == "run_script" and not script_exists:
                write_action = next(
                    (a for a in actions if a.action_type == ActionType.FILE_WRITE), None
                )
                if write_action:
                    queries.update_job_field(
                        job_id, target_path=write_action.target_path, engine=self.db
                    )

            queries.update_job_status(job_id, JobStatus.AWAITING_APPROVAL, engine=self.db)
            queries.write_audit(
                job_id, "plan_built", detail=f"{len(actions)} steps", engine=self.db
            )
            logger.info("Job %s plan built: %d steps", job_id, len(actions))
            return actions

        except (CommandBlockedError, LLMError):
            raise
        except Exception as exc:
            queries.update_job_status(
                job_id, JobStatus.FAILED, error=str(exc), engine=self.db
            )
            queries.write_audit(job_id, "job_failed", detail=str(exc), engine=self.db)
            raise

    # ------------------------------------------------------------------
    # Plan builders
    # ------------------------------------------------------------------

    def _plan_rewrite(
        self,
        job_id: str,
        instruction: str,
        target_path: str,
        file_content: str | None,
        context: dict | None = None,
        job_created_at=None,
    ) -> list[ActionObject]:
        """Build the 3-step rewrite_file plan.

        When job_created_at is provided, timestamped filenames are included in
        the FILE_BACKUP and FILE_WRITE intent strings so the plan preview shows
        the user the exact names that will be created.  For PDF → DOCX rewrites
        the FILE_WRITE target_path is set to the timestamped DOCX path and
        job.output_path is updated in the database.
        """
        # Call Claude to produce the rewritten content
        response = self.llm.call(
            system=(
                "You are a precise file editor. "
                "Rewrite the provided file content exactly as instructed. "
                "Also provide a plain English summary of what you changed."
            ),
            user=(
                f"Instruction: {instruction}\n\n"
                f"Current file content:\n{file_content or ''}"
            ),
            tools=[_REWRITE_TOOL],
        )
        tool_input = response.get("input", {})
        rewritten = tool_input.get("rewritten_content", "")
        change_summary = tool_input.get("change_summary", "")

        ctx = context or {}
        output_path = ctx.get("output_path") or target_path
        format_change = ctx.get("format_change", False)
        format_change_note = ctx.get("format_change_note")
        detected_format = ctx.get("detected_format")
        text_blocks = ctx.get("text_blocks") or []
        extraction_method = ctx.get("extraction_method") or 'text'

        # ── Timestamped backup intent ──────────────────────────────────────
        src = Path(target_path)
        if job_created_at:
            ts_stem = make_timestamped_stem(src.name, job_created_at)
            bak_name = f"{ts_stem}{src.suffix}.bak"
        else:
            bak_name = src.name + ".bak"
        backup_intent = f"Back up original file to .backups/{bak_name}"

        # ── PDF write strategy ────────────────────────────────────────────
        write_strategy = None
        strategy_message = None
        if detected_format == 'pdf':
            out_stem = Path(output_path).stem if output_path else ""
            write_strategy, strategy_message = _determine_pdf_write_strategy(
                text_blocks, rewritten, extraction_method, out_stem
            )

        # ── Output path and intent ────────────────────────────────────────
        if detected_format == 'pdf' and write_strategy == 'pdf_inplace':
            # In-place: target stays as the original PDF; execution computes
            # a timestamped .pdf sibling.  Persist the expected output path.
            if job_created_at:
                ts_pdf_stem = make_timestamped_stem(src.name, job_created_at)
                ts_pdf_output = str(src.parent / f"{ts_pdf_stem}.pdf")
            else:
                ts_pdf_output = str(src.parent / f"{src.stem}_rewritten.pdf")
            queries.update_job_field(job_id, output_path=ts_pdf_output, engine=self.db)
            write_target = target_path  # execution reads from original PDF
            write_filename = Path(ts_pdf_output).name
            write_intent = f"Rewrite text in place in {write_filename}. {strategy_message}"
        else:
            # ── Timestamped output path for PDF → DOCX or non-PDF ──────────
            if format_change and job_created_at and output_path:
                out = Path(output_path)
                ts_out_stem = make_timestamped_stem(out.name, job_created_at)
                ts_output_path = str(out.parent / f"{ts_out_stem}{out.suffix}")
                queries.update_job_field(job_id, output_path=ts_output_path, engine=self.db)
                output_path = ts_output_path

            write_target = output_path
            write_filename = Path(output_path).name
            write_intent = f"Write rewritten content to {write_filename}"
            if format_change and format_change_note:
                write_intent = f"{write_intent}. {format_change_note}"
            if strategy_message:
                write_intent = f"{write_intent}. {strategy_message}"

        write_action = _make_action(job_id, 2, ActionType.FILE_WRITE, "medium",
                                    write_target, write_intent,
                                    command=rewritten, change_summary=change_summary)
        write_action.detected_format = detected_format
        write_action.write_strategy = write_strategy
        write_action.text_blocks = text_blocks

        return [
            _make_action(job_id, 0, ActionType.FILE_READ, "low",
                         target_path, "Read current file content"),
            _make_action(job_id, 1, ActionType.FILE_BACKUP, "low",
                         target_path, backup_intent),
            write_action,
        ]

    def _plan_run_script(
        self,
        job_id: str,
        instruction: str,
        target_path: str | None,
        file_content: str | None,
        script_exists: bool,
        retry_hint: str | None = None,
    ) -> list[ActionObject]:
        """Build the 2-step (exists) or 3-step (new) run_script plan."""
        if script_exists:
            path = Path(target_path)
            interpreter = _detect_interpreter(path)
            run_command = f"{interpreter} {target_path}"

            # Safety scan existing content
            _safety_scan(file_content or "", job_id, self.db)

            # Ask Claude to describe what the script does
            desc_response = self.llm.call(
                system=(
                    "You are a helpful assistant. Describe what the following script does "
                    "and what output to expect, in 2-3 plain English sentences for a non-technical user."
                ),
                user=f"Script content:\n{file_content or ''}",
                tools=[_DESCRIBE_TOOL],
            )
            change_summary = desc_response.get("input", {}).get("description", "")

            return [
                _make_action(job_id, 0, ActionType.CMD_INSPECT, "low",
                             target_path, "Safety review of script content",
                             command=file_content),
                _make_action(job_id, 1, ActionType.CMD_EXECUTE, "medium",
                             target_path, "Run the script",
                             command=run_command, change_summary=change_summary),
            ]

        else:
            # Call Claude to generate the script
            user_msg = f"Instruction: {instruction}"
            if retry_hint:
                user_msg += f"\n\nPrevious plan was invalid. Error: {retry_hint}\nPlease fix the plan."

            response = self.llm.call(
                system=_SCRIPT_SYSTEM,
                user=user_msg,
                tools=[_SCRIPT_TOOL],
            )
            tool_input = response.get("input", {})
            generated_content = tool_input.get("script_content", "")
            script_type = tool_input.get("script_type", "python3")
            change_summary = tool_input.get("change_summary", "")
            script_filename = tool_input.get("script_filename", "generated_script.py")

            script_path = config.SCRIPTS_DIR / script_filename
            run_command = f"{script_type} {script_path}"

            # Safety scan generated content before adding to plan
            _safety_scan(generated_content, job_id, self.db)

            return [
                _make_action(job_id, 0, ActionType.FILE_WRITE, "medium",
                             str(script_path), "Write generated script to workspace",
                             command=generated_content, change_summary=change_summary),
                _make_action(job_id, 1, ActionType.CMD_INSPECT, "low",
                             str(script_path), "Safety review of generated script",
                             command=generated_content),
                _make_action(job_id, 2, ActionType.CMD_EXECUTE, "medium",
                             str(script_path), "Run the script",
                             command=run_command, change_summary=change_summary),
            ]

    def _validate_actions(self, actions: list[ActionObject], task_type: str) -> None:
        """Validate that a run_script plan has correct target paths and commands.

        Raises ValueError if any CMD_EXECUTE or CMD_INSPECT step targets a non-script
        file, or if CMD_EXECUTE does not invoke a script runner.
        Only applies to run_script plans.
        """
        if task_type != "run_script":
            return

        for action in actions:
            if action.action_type in (ActionType.CMD_EXECUTE, ActionType.CMD_INSPECT):
                path = str(action.target_path or "")
                if not any(path.endswith(ext) for ext in (".py", ".sh")):
                    raise ValueError(
                        f"{action.action_type} has invalid target_path. "
                        f"Must be a script file not a data file."
                    )

            if action.action_type == ActionType.CMD_EXECUTE:
                cmd = str(action.command or "")
                if not any(
                    cmd.strip().startswith(runner) for runner in ("python3", "bash", "sh")
                ):
                    raise ValueError(
                        f"CMD_EXECUTE command must run a script. Got: {cmd[:50]}"
                    )

        has_execute = any(a.action_type == ActionType.CMD_EXECUTE for a in actions)
        if not has_execute:
            raise ValueError("run_script plan missing CMD_EXECUTE step.")
