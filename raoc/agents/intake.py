"""IntakeAgent — parses a raw user request into a structured TaskObject.

Uses Claude via tool_use to classify the request, then updates the job
record and advances the pipeline status.
"""

import logging

from raoc import config
from raoc.db import queries
from raoc.models.job import JobStatus
from raoc.models.task import TaskObject
from raoc.substrate.exceptions import IntakeError

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """
You are the intake classifier for RAOC, a secure remote Mac operator.

Your job is to parse a user's natural language request into a structured task using the create_task_object tool.

## Task types
- query_action: The user wants to find something AND do something to it in one request.
  Contains both a search/find component and an action component (rewrite or run).
  Signals: "find X and rewrite", "find X and run", "find X and update",
  "search for X and modify", "locate X and change", "find my X and do Y to it"
  Set query_intent to the search part. Set action_instruction to the action part.
  Set implied_task_type to 'rewrite_file' or 'run_script' based on the action.
- query: The user is asking for information about files or workspace contents. They want an answer,
  not an action. No files will be modified. No scripts will be run.
  Signals: "what", "which", "how many", "does X exist", "show me", "list", "find", "search for",
  "what is in", "tell me about"
  IMPORTANT: "find the file with X" alone is a query. "find the file with X and rewrite it" is
  query_action. When in doubt between query and run_script, choose query — it is
  always safer to answer first than to execute.
- run_script: The user wants to run an existing script OR wants you to write a new script and run it.
- rewrite_file: The user wants to read a plain text file and rewrite or edit its contents.

## HOW TO SET PATHS

For task_type = run_script:

  Case A — User wants to run an existing named script:
    existing_script_path = that script filename (e.g. "cleanup.py")
    rewrite_target = null
    instruction = what the user wants

  Case B — User wants you to write a new script:
    existing_script_path = null
    rewrite_target = null
    instruction = full description of what the script should do, including any
                  files it should read or process

    IMPORTANT: Any file mentioned in the request that is NOT a script is an
    input to the script. It belongs in the instruction field only. Never put
    it in existing_script_path.

For task_type = rewrite_file:
    existing_script_path = null
    rewrite_target = the file to rewrite (e.g. "notes.txt")
    instruction = how to rewrite it

HOW TO TELL CASE A FROM CASE B:
  Case A signals: "run X", "execute X", "start X" where X is a .py or .sh filename
  Case B signals: "write a script", "create a script", "make a script",
                  "write code", "write a program", "write something that",
                  "build a script"
  When in doubt: Case B. Let planning create the script.

## risk_level
- high: The task modifies files on disk (rewrite_file always qualifies).
- medium: The task runs code or scripts (run_script).
- low: The task only reads data without changing anything.

## requires_clarification
Set to True ONLY if the task type (run_script vs rewrite_file) absolutely cannot be determined.
If the request is vague but still points to one task type, classify it and set requires_clarification=False.
Always call the create_task_object tool — even for ambiguous requests.

## clarification_question
Required when requires_clarification=True. Must be a single sentence that asks only for the
information needed to classify the task. Never ask for things the system can discover (file size,
line count, etc.). Examples:
- "Which file would you like me to work on, and what should I change about it?"
- "What script would you like me to run?"
- "Which file should I rewrite, and how should it be changed?"
""".strip()

_TOOL = {
    "name": "create_task_object",
    "description": "Parse the user request into a structured task",
    "input_schema": {
        "type": "object",
        "properties": {
            "task_type": {
                "type": "string",
                "enum": ["run_script", "rewrite_file", "query", "query_action"],
                "description": "The category of action to perform",
            },
            "existing_script_path": {
                "type": ["string", "null"],
                "description": (
                    "ONLY set for run_script Case A — the exact .py or .sh filename the user "
                    "named that already exists and should be run as-is. "
                    "null for any new-script request."
                ),
            },
            "rewrite_target": {
                "type": ["string", "null"],
                "description": "For rewrite_file tasks only: the filename to rewrite. null otherwise.",
            },
            "instruction": {
                "type": "string",
                "description": (
                    "Complete instruction. For run_script Case B this must include all filenames "
                    "the script should read or process — those filenames belong here, not in "
                    "existing_script_path."
                ),
            },
            "risk_level": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": "Assessed risk of the action",
            },
            "requires_clarification": {
                "type": "boolean",
                "description": "True only if task type cannot be determined at all",
            },
            "clarification_question": {
                "type": "string",
                "description": "Question to ask the user if requires_clarification is True",
            },
            "query_intent": {
                "type": ["string", "null"],
                "description": (
                    "The specific question or search goal, rephrased as a clear information "
                    "retrieval goal. Set for query and query_action tasks. "
                    "Example: 'find the most recently modified file' "
                    "Example: 'find which file contains resume content' "
                    "Example: 'list all files in the workspace'"
                ),
            },
            "action_instruction": {
                "type": ["string", "null"],
                "description": (
                    "The action to perform once the file is found. "
                    "Only set for query_action tasks. "
                    "Example: 'rewrite to be more formal' "
                    "Example: 'run an analysis script on it'"
                ),
            },
            "implied_task_type": {
                "type": ["string", "null"],
                "enum": ["rewrite_file", "run_script", None],
                "description": (
                    "The kind of action to perform after finding the file. "
                    "Only set for query_action tasks."
                ),
            },
        },
        "required": ["task_type", "instruction", "risk_level", "requires_clarification"],
    },
}


class IntakeAgent:
    """Parses a raw user request into a TaskObject via Claude tool_use.

    Updates job status to UNDERSTANDING while working, then to DISCOVERING
    on success or AWAITING_APPROVAL if clarification is needed.
    """

    def __init__(self, db, llm=None) -> None:
        """Initialise with a database engine (or None for default) and an optional LLMClient."""
        # Accept a SQLAlchemy Engine or None; ignore non-engine objects (e.g. the
        # queries module passed by mistake) so queries fall back to their default engine.
        self.db = db if (db is None or hasattr(db, "connect")) else None
        self.llm = llm

    def run(self, job_id: str) -> TaskObject:
        """Parse the job's raw request and return a TaskObject.

        Advances status to DISCOVERING on success, AWAITING_APPROVAL if
        clarification is required, or FAILED on any error.
        """
        try:
            # 1. Read job
            job = queries.get_job(job_id, engine=self.db)

            # 2. Update status to UNDERSTANDING
            queries.update_job_status(job_id, JobStatus.UNDERSTANDING, engine=self.db)
            queries.write_audit(job_id, "intake_started", engine=self.db)

            # 3. Call Claude via tool_use
            response_block = self.llm.call(
                system=_SYSTEM_PROMPT,
                user=job.raw_request,
                tools=[_TOOL],
            )

            if response_block.get("type") != "tool_use":
                raise IntakeError(
                    f"Expected tool_use response block, got: {response_block.get('type')}"
                )

            tool_input: dict = response_block.get("input", {})

            # 4. Derive target_path and supplementary fields
            task_type = tool_input.get("task_type", "run_script")
            if task_type == "run_script":
                # Case A: existing script named explicitly; Case B: None (planner generates)
                target_path = tool_input.get("existing_script_path") or None
            elif task_type == "rewrite_file":
                target_path = tool_input.get("rewrite_target") or None
            else:
                # query / query_action: no target file at intake time
                target_path = None

            query_intent = tool_input.get("query_intent") or None
            action_instruction = tool_input.get("action_instruction") or None
            implied_task_type = tool_input.get("implied_task_type") or None

            # 5. Parse into TaskObject
            task = TaskObject(
                task_type=task_type,
                target_path=target_path,
                instruction=tool_input.get("instruction", ""),
                risk_level=tool_input.get(
                    "risk_level",
                    "low" if task_type in ("query", "query_action") else "medium",
                ),
                requires_clarification=tool_input.get("requires_clarification", False),
                clarification_question=tool_input.get("clarification_question"),
                query_intent=query_intent,
                action_instruction=action_instruction,
                implied_task_type=implied_task_type,
            )

            # 6. Needs clarification
            if task.requires_clarification:
                queries.update_job_field(
                    job_id,
                    clarification_question=task.clarification_question,
                    engine=self.db,
                )
                queries.update_job_status(
                    job_id, JobStatus.AWAITING_APPROVAL, engine=self.db
                )
                queries.write_audit(
                    job_id,
                    "intake_needs_clarification",
                    detail=task.clarification_question,
                    engine=self.db,
                )
                logger.info("Job %s needs clarification", job_id)
                return task

            # 7. All clear — write fields and advance
            if task.task_type in ("query", "query_action"):
                # Read-only and combined query_action tasks skip the action pipeline
                queries.update_job_field(
                    job_id,
                    task_type=task.task_type,
                    query_intent=task.query_intent,
                    action_instruction=task.action_instruction,
                    implied_task_type=task.implied_task_type,
                    engine=self.db,
                )
                queries.update_job_status(job_id, JobStatus.UNDERSTANDING, engine=self.db)
            else:
                queries.update_job_field(
                    job_id,
                    task_type=task.task_type,
                    target_path=task.target_path,
                    scope_root=str(config.WORKSPACE),
                    engine=self.db,
                )
                queries.update_job_status(job_id, JobStatus.DISCOVERING, engine=self.db)
            queries.write_audit(job_id, "intake_complete", engine=self.db)
            logger.info("Job %s intake complete: %s", job_id, task.task_type)
            return task

        except IntakeError:
            queries.update_job_status(
                job_id, JobStatus.FAILED, error="Intake classification failed", engine=self.db
            )
            queries.write_audit(job_id, "job_failed", engine=self.db)
            raise
        except Exception as exc:
            queries.update_job_status(
                job_id, JobStatus.FAILED, error=str(exc), engine=self.db
            )
            queries.write_audit(job_id, "job_failed", detail=str(exc), engine=self.db)
            raise
