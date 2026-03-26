"""PipelineCoordinator — routes jobs through the RAOC agent pipeline.

Receives messages and approval callbacks from the gateway, advances jobs
through each pipeline stage, and coordinates all six agents.
"""

import asyncio
import logging
import zipfile
from pathlib import Path


def _fire(coro) -> None:
    """Send an async gateway call from synchronous coordinator code.

    If a running event loop exists (inside Telegram's async handler), schedules
    the coroutine as a task on that loop (fire-and-forget).
    If no loop is running (tests, CLI), runs it synchronously via asyncio.run().
    """
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(coro)
    except RuntimeError:
        asyncio.run(coro)

from raoc.agents.discovery import DiscoveryAgent
from raoc.agents.execution import ExecutionAgent
from raoc.agents.intake import IntakeAgent
from raoc.agents.planning import PlanningAgent
from raoc.agents.policy_agent import PolicyAgent
from raoc.agents.query_agent import QueryAgent
from raoc.agents.reporter import ReporterAgent
from raoc.agents.verification import VerificationAgent
from raoc import config
from raoc.db import queries
from raoc.models.job import JobStatus

logger = logging.getLogger(__name__)


class PipelineCoordinator:
    """Routes incoming messages and approvals through the agent pipeline.

    Manages the full job lifecycle from RECEIVED through COMPLETED or CANCELLED.
    """

    def __init__(
        self,
        db,
        llm,
        sampler,
        command_wrapper,
        gateway,
        narrator=None,
        policy_agent=None,
    ) -> None:
        """Instantiate all agents and store shared dependencies."""
        self.db = db if (db is None or hasattr(db, "connect")) else None
        self.gateway = gateway
        self.narrator = narrator  # StatusNarrator or None
        self.policy_agent = policy_agent  # None = policy disabled (legacy/test mode)
        self.pending_clarification: dict = {}
        # Maps job_id → extracted file path for ZIP-sourced jobs awaiting cleanup
        self.pending_zip_cleanup: dict = {}
        self.intake = IntakeAgent(db, llm)
        self.discovery = DiscoveryAgent(db, sampler, llm)
        self.planning = PlanningAgent(db, llm)
        self.execution = ExecutionAgent(db, command_wrapper, sampler)
        self.verification = VerificationAgent(db, sampler)
        self.reporter = ReporterAgent(db, gateway)
        self.query_agent = QueryAgent(db, sampler, llm, gateway)

    async def _narrate(self, stage: str, context: dict) -> None:
        """Schedule narration as a fire-and-forget background task, then yield.

        Calls _fire() to create a background task, then awaits asyncio.sleep(0)
        so the event loop can start the narrator task before the pipeline continues.
        If the narrator is None, does nothing.
        """
        if self.narrator is None:
            return
        _fire(self._narrate_and_send(stage, context))
        await asyncio.sleep(0)  # yield so narrator task starts immediately

    async def _narrate_and_send(self, stage: str, context: dict) -> None:
        """Async worker: call narrate_async then send the result via send_status."""
        try:
            message = await self.narrator.narrate_async(stage, context)
            await self.gateway.send_status(message)
        except Exception as exc:
            logger.warning("Narrator failed at stage %s: %s", stage, exc)

    async def _narrate_execution_step_sync(self, action, total_steps: int = 0) -> None:
        """Narrate an execution step synchronously — awaited before the action runs.

        Unlike _narrate(), this is not fire-and-forget. It awaits narrate_async and
        send_status so the message is confirmed delivered before execution proceeds.
        Never raises — failures are logged and swallowed.
        """
        if self.narrator is None:
            return
        try:
            atype_str = (
                action.action_type.value
                if hasattr(action.action_type, 'value')
                else str(action.action_type)
            )
            message = await self.narrator.narrate_async('execution_step', {
                'action_type': atype_str,
                'file_name': Path(action.target_path or '').name,
                'step_index': action.step_index,
                'total_steps': total_steps,
                'intent': action.intent,
            })
            await self.gateway.send_status(message)
        except Exception as exc:
            logger.warning("Execution narrator failed: %s", exc)

    async def handle_new_message(self, text: str) -> str:
        """Route an incoming message: resolve a pending clarification or create a new job.

        Checks for a pending ZIP clarification first (job in AWAITING_APPROVAL with
        zip_source_path set), then the existing intake clarification queue, then
        creates a new job.
        """
        # Check for pending ZIP file clarification (which file to extract from the ZIP)
        zip_job = queries.get_active_zip_clarification_job(engine=self.db)
        if zip_job:
            await self.handle_clarification(zip_job.job_id, text)
            return zip_job.job_id

        if self.pending_clarification:
            job_id = list(self.pending_clarification.keys())[0]
            pending = self.pending_clarification.pop(job_id)
            original = queries.get_job(job_id, engine=self.db).raw_request
            combined = (
                f"Original request: {original}\n"
                f"Context: {pending.get('context', '')}\n"
                f"User's clarification: {text}"
            )
            queries.update_job_field(
                job_id,
                raw_request=combined,
                status='received',
                engine=self.db,
            )
            _fire(self.gateway.send_message(text='Got it. Working on it...'))
            await self.advance(job_id)
            return job_id

        await self.gateway.send_status("Got it. Working on it...")
        job = queries.create_job(text, engine=self.db)
        await self._narrate('message_received', {'raw_request': job.raw_request})
        await self.advance(job.job_id)
        return job.job_id

    async def handle_approval(self, job_id: str, approved: bool) -> None:
        """Handle an Approve or Deny button tap from the user."""
        job = queries.get_job(job_id, engine=self.db)
        if approved:
            queries.update_job_field(job_id, approval_granted=True, engine=self.db)
            await self.advance(job_id)
        else:
            if job.status == JobStatus.CONFIRMING:
                # User rejected the found file — ask for the correct filename
                queries.update_job_status(job_id, JobStatus.AWAITING_APPROVAL, engine=self.db)
                _fire(self.gateway.send_message(
                    text="Okay, which file would you like me to work on?"
                ))
            else:
                queries.update_job_status(job_id, JobStatus.CANCELLED, engine=self.db)
                _fire(self.gateway.send_message(text="Job cancelled. Nothing was executed."))

    async def handle_clarification(self, job_id: str, user_reply: str) -> None:
        """Handle a ZIP clarification reply — extract the named file and continue the pipeline.

        If the named file is not found in the ZIP, sends a re-prompt and waits.
        If found, extracts only that file to config.WORKSPACE, updates job.target_path,
        clears zip_source_path, and re-runs discovery through the full pipeline.
        """
        job = queries.get_job(job_id, engine=self.db)
        zip_path = Path(job.zip_source_path)
        named_file = user_reply.strip()

        # Validate the named file exists inside the ZIP
        try:
            with zipfile.ZipFile(zip_path) as zf:
                available = zf.namelist()
                match = next(
                    (n for n in available if n == named_file or Path(n).name == named_file),
                    None,
                )
                if match is None:
                    _fire(self.gateway.send_message(
                        text=(
                            f"That file was not found in the ZIP. "
                            f"Please reply with one of the filenames listed."
                        )
                    ))
                    return

                # Extract only the matched file to WORKSPACE
                extracted_path = config.WORKSPACE / Path(match).name
                with zf.open(match) as src, open(extracted_path, 'wb') as dst:
                    dst.write(src.read())

        except Exception as exc:
            logger.error("ZIP extraction failed for job %s: %s", job_id, exc)
            _fire(self.gateway.send_message(
                text=f"Could not open the ZIP file: {exc}"
            ))
            return

        logger.info("Extracted %s from %s → %s", match, zip_path, extracted_path)

        # Update job: point at the extracted file, clear the ZIP path
        queries.update_job_field(
            job_id,
            target_path=str(extracted_path),
            zip_source_path=None,
            engine=self.db,
        )
        queries.write_audit(
            job_id,
            "zip_file_extracted",
            detail=f"Extracted {match} → {extracted_path}",
            engine=self.db,
        )

        # Remember the extracted file for cleanup after reporting
        self.pending_zip_cleanup[job_id] = str(extracted_path)

        # Re-run the pipeline from DISCOVERING
        queries.update_job_status(job_id, JobStatus.DISCOVERING, engine=self.db)
        _fire(self.gateway.send_message(text="Got it. Working on it..."))
        await self.advance(job_id)

    async def advance(self, job_id: str) -> None:
        """Advance a job to the next pipeline stage based on its current status."""
        job = queries.get_job(job_id, engine=self.db)

        if job.status == JobStatus.RECEIVED:
            task = self.intake.run(job_id)
            if task.requires_clarification:
                self.pending_clarification[job_id] = {'context': ''}
                _fire(self.gateway.send_message(text=task.clarification_question))
                return
            await self.advance(job_id)

        elif job.status == JobStatus.UNDERSTANDING:
            # Query path — bypasses the entire action pipeline
            if job.task_type == 'query':
                self.query_agent.run(job_id)
            elif job.task_type == 'query_action':
                queries.update_job_status(job_id, JobStatus.SEARCHING, engine=self.db)
                await self.advance(job_id)

        elif job.status == JobStatus.SEARCHING:
            search_result = self.query_agent.run_search_for_action(job_id)
            if search_result['file_found']:
                job = queries.get_job(job_id, engine=self.db)  # re-read for implied_task_type
                queries.update_job_field(
                    job_id,
                    found_file_path=search_result['file_path'],
                    engine=self.db,
                )
                queries.update_job_status(job_id, JobStatus.CONFIRMING, engine=self.db)
                action_verb = (
                    'Rewrite' if job.implied_task_type == 'rewrite_file'
                    else 'Run a script on'
                )
                confirm_text = (
                    f"Found {search_result['file_name']} — {search_result['summary']}. "
                    f"{action_verb} this file?"
                )
                _fire(self.gateway.send_confirmation(confirm_text, job_id))
            # If not found, query_agent already set AWAITING_APPROVAL and sent message

        elif job.status == JobStatus.CONFIRMING:
            if job.approval_granted:
                # User confirmed the file — transition to standard action pipeline
                queries.update_job_field(
                    job_id,
                    task_type=job.implied_task_type,
                    target_path=job.found_file_path,
                    raw_request=job.action_instruction or job.raw_request,
                    approval_granted=None,  # reset so execution approval is separate
                    engine=self.db,
                )
                queries.update_job_status(job_id, JobStatus.DISCOVERING, engine=self.db)
                await self.advance(job_id)
            # else: waiting for user tap

        elif job.status == JobStatus.DISCOVERING:
            job = queries.get_job(job_id, engine=self.db)
            try:
                context = self.discovery.run(job_id)
            except Exception as exc:
                await self._narrate('job_failed', {
                    'reason': str(exc),
                    'file_name': Path(job.target_path or '').name or None,
                })
                raise
            if context is None:
                # Discovery needs clarification — question is stored on the job
                job = queries.get_job(job_id, engine=self.db)
                self.pending_clarification[job_id] = {
                    'context': f"The file '{job.target_path}' was not found in the workspace."
                }
                _fire(self.gateway.send_message(text=job.clarification_question))
                return
            await self._narrate('discovery_complete', {
                'task_type': context.get('task_type', job.task_type),
                'file_name': Path(context.get('target_path', job.target_path or '')).name,
                'size_bytes': context.get('size_bytes', 0),
                'modified_at': str(context.get('modified_at', '')),
                'format_detected': context.get('detected_format', 'text'),
                'format_change': context.get('format_change', False),
                'next_step': 'planning',
            })
            self.planning.run(job_id, context)
            # Policy check — runs after planning, before plan preview
            if self.policy_agent is not None:
                policy_results = self.policy_agent.review_plan(job_id)
                blocked = [r for r in policy_results if r.decision == 'blocked']
                if blocked:
                    bullet_lines = "\n".join(f"• {r.reason}" for r in blocked)
                    message = f"Job blocked by policy. Nothing will execute.\n\n{bullet_lines}"
                    queries.update_job_status(job_id, JobStatus.BLOCKED, engine=self.db)
                    queries.write_audit(job_id, 'job_blocked', detail=message, engine=self.db)
                    _fire(self.gateway.send_message(text=message))
                    return
            # Give background narration time to arrive before sending the plan preview
            await asyncio.sleep(config.NARRATION_DELAY_BEFORE_PLAN)
            # Job is now AWAITING_APPROVAL — send plan preview for human review, then stop.
            # advance() must return here. Execution only starts when handle_approval() is
            # called by the user tapping Approve on Telegram.
            actions = queries.get_actions_for_job(job_id, engine=self.db)
            plan_text = self._build_plan_preview(job_id, actions)
            _fire(self.gateway.send_approval_request(job_id, plan_text))
            return

        elif job.status == JobStatus.AWAITING_APPROVAL:
            if job.approval_granted:
                actions = queries.get_actions_for_job(job_id, engine=self.db)
                # Narrate execution steps synchronously — each message is delivered
                # before execution starts, guaranteeing correct ordering.
                _NARRATED_TYPES = {'file_backup', 'file_write', 'cmd_execute'}
                for action in sorted(actions, key=lambda a: a.step_index):
                    _atype = action.action_type
                    atype_str = _atype.value if hasattr(_atype, 'value') else str(_atype)
                    if atype_str in _NARRATED_TYPES:
                        await self._narrate_execution_step_sync(
                            action, total_steps=len(actions)
                        )
                execution_summary = self.execution.run(job_id, actions)
                verification_result = self.verification.run(job_id, execution_summary)
                self.reporter.run(job_id, verification_result)
                # Clean up extracted ZIP temp file if this was a ZIP-sourced job
                if job_id in self.pending_zip_cleanup:
                    self._cleanup_zip_extracted_file(job_id)
            # else: waiting for user tap, do nothing

        elif job.status == JobStatus.EXECUTING:
            pass  # should not advance here — triggered by approval

    def _cleanup_zip_extracted_file(self, job_id: str) -> None:
        """Delete the temp file extracted from a ZIP after the job has completed.

        Logs the cleanup event to the audit trail.
        """
        extracted_path = Path(self.pending_zip_cleanup.pop(job_id))
        try:
            if extracted_path.exists():
                extracted_path.unlink()
                logger.info("ZIP temp file removed: %s", extracted_path)
            queries.write_audit(
                job_id,
                "zip_extracted_file_cleaned_up",
                detail=str(extracted_path),
                engine=self.db,
            )
        except Exception as exc:
            logger.warning("Could not remove ZIP temp file %s: %s", extracted_path, exc)

    def _build_plan_preview(self, job_id: str, actions: list) -> str:
        """Build a readable plan preview string for Telegram.

        Shows task type, target file, number of steps, and each step's intent.
        If any actions have policy_decision == 'judgment_zone', they appear in
        a separate flagged section after the main step list.
        Ends with 'Approve to execute or Deny to cancel.'
        """
        job = queries.get_job(job_id, engine=self.db)
        task_type = job.task_type or "unknown"
        target = job.target_path or "unknown"
        sorted_actions = sorted(actions, key=lambda a: a.step_index)
        n = len(sorted_actions)

        lines = [
            f"Task: {task_type}",
            f"Target: {target}",
            f"Steps: {n}",
            "",
        ]
        for action in sorted_actions:
            lines.append(f"  {action.step_index + 1}. {action.intent}")

        judgment_items = [
            a for a in sorted_actions
            if str(getattr(a, 'policy_decision', None)) == 'judgment_zone'
        ]
        if judgment_items:
            lines.append("")
            lines.append(f"⚠️ Needs your judgment ({len(judgment_items)} item{'s' if len(judgment_items) != 1 else ''}):")
            for item in judgment_items:
                reason = getattr(item, 'policy_reason', None) or 'Policy could not determine zone.'
                lines.append(f"  • Step {item.step_index + 1} — {reason}")

        lines.append("")
        lines.append("Approve to execute or Deny to cancel.")
        return "\n".join(lines)
