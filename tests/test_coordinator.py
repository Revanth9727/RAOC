"""Tests for raoc.coordinator.PipelineCoordinator."""

import asyncio
import inspect
import time
import zipfile as zipfile_module
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from raoc import config
from raoc import coordinator as coordinator_module
from raoc.coordinator import PipelineCoordinator
from raoc.db.queries import create_job, get_job, update_job_field, update_job_status
from raoc.db.schema import create_tables, get_engine
from raoc.models.job import JobStatus
from raoc.models.task import TaskObject


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    engine = get_engine(db_path=tmp_path / "test_coordinator.db")
    create_tables(engine)
    return engine


def _make_coordinator(db) -> PipelineCoordinator:
    """Build a PipelineCoordinator with all substrate deps mocked."""
    gateway = MagicMock()
    gateway.send_message = AsyncMock()
    gateway.send_approval_request = AsyncMock()
    gateway.send_status = AsyncMock()

    coord = PipelineCoordinator(
        db=db,
        llm=MagicMock(),
        sampler=MagicMock(),
        command_wrapper=MagicMock(),
        gateway=gateway,
        policy_agent=None,
    )
    return coord


def _make_task(requires_clarification=False, clarification_question=None):
    """Return a TaskObject for use as intake.run() return value."""
    return TaskObject(
        task_type="rewrite_file",
        target_path="notes.txt",
        instruction="Rewrite notes.txt",
        risk_level="high",
        requires_clarification=requires_clarification,
        clarification_question=clarification_question,
    )


def _intake_side_effect(db):
    """Return a side_effect that advances a job from RECEIVED to DISCOVERING."""
    def _fn(job_id):
        update_job_status(job_id, JobStatus.DISCOVERING, engine=db)
        return _make_task(requires_clarification=False)
    return _fn


def _planning_side_effect(db):
    """Return a side_effect that advances a job from DISCOVERING to AWAITING_APPROVAL."""
    def _fn(job_id, context):
        update_job_status(job_id, JobStatus.AWAITING_APPROVAL, engine=db)
    return _fn


# ---------------------------------------------------------------------------
# handle_new_message
# ---------------------------------------------------------------------------

class TestHandleNewMessage:
    """handle_new_message creates a job and calls intake."""

    async def test_returns_job_id(self, db):
        coord = _make_coordinator(db)
        coord.intake.run = MagicMock(side_effect=_intake_side_effect(db))
        coord.discovery.run = MagicMock(return_value={})
        coord.planning.run = MagicMock(side_effect=_planning_side_effect(db))

        with patch("raoc.coordinator.queries.get_actions_for_job", return_value=[]):
            job_id = await coord.handle_new_message("Rewrite notes.txt")

        assert job_id is not None
        assert len(job_id) == 36  # UUID

    async def test_intake_is_called(self, db):
        coord = _make_coordinator(db)
        coord.intake.run = MagicMock(side_effect=_intake_side_effect(db))
        coord.discovery.run = MagicMock(return_value={})
        coord.planning.run = MagicMock(side_effect=_planning_side_effect(db))

        with patch("raoc.coordinator.queries.get_actions_for_job", return_value=[]):
            job_id = await coord.handle_new_message("Rewrite notes.txt")

        coord.intake.run.assert_called_once_with(job_id)

    async def test_sends_approval_request_after_planning(self, db):
        coord = _make_coordinator(db)
        coord.intake.run = MagicMock(side_effect=_intake_side_effect(db))
        coord.discovery.run = MagicMock(return_value={})
        coord.planning.run = MagicMock(side_effect=_planning_side_effect(db))

        with patch("raoc.coordinator.queries.get_actions_for_job", return_value=[]):
            await coord.handle_new_message("Rewrite notes.txt")

        coord.gateway.send_approval_request.assert_called_once()


# ---------------------------------------------------------------------------
# handle_approval
# ---------------------------------------------------------------------------

class TestHandleApprovalApproved:
    """handle_approval with approved=True triggers the execution chain."""

    async def test_execution_called(self, db):
        coord = _make_coordinator(db)
        job = create_job("Run cleanup.py", engine=db)
        update_job_status(job.job_id, JobStatus.AWAITING_APPROVAL, engine=db)

        exec_summary = {"job_id": job.job_id, "steps_completed": 0,
                        "steps_failed": 0, "actions": []}
        verify_result = {"all_passed": True, "task_type": "run_script",
                         "checks": [], "before_state": {}, "after_state": {}}

        coord.execution.run = MagicMock(return_value=exec_summary)
        coord.verification.run = MagicMock(return_value=verify_result)
        coord.reporter.run = MagicMock()

        with patch("raoc.coordinator.queries.get_actions_for_job", return_value=[]):
            await coord.handle_approval(job.job_id, approved=True)

        coord.execution.run.assert_called_once()

    async def test_verification_called_after_execution(self, db):
        coord = _make_coordinator(db)
        job = create_job("Run cleanup.py", engine=db)
        update_job_status(job.job_id, JobStatus.AWAITING_APPROVAL, engine=db)

        exec_summary = {"job_id": job.job_id, "steps_completed": 0,
                        "steps_failed": 0, "actions": []}
        verify_result = {"all_passed": True, "task_type": "run_script",
                         "checks": [], "before_state": {}, "after_state": {}}

        coord.execution.run = MagicMock(return_value=exec_summary)
        coord.verification.run = MagicMock(return_value=verify_result)
        coord.reporter.run = MagicMock()

        with patch("raoc.coordinator.queries.get_actions_for_job", return_value=[]):
            await coord.handle_approval(job.job_id, approved=True)

        coord.verification.run.assert_called_once_with(job.job_id, exec_summary)

    async def test_reporter_called_after_verification(self, db):
        coord = _make_coordinator(db)
        job = create_job("Run cleanup.py", engine=db)
        update_job_status(job.job_id, JobStatus.AWAITING_APPROVAL, engine=db)

        exec_summary = {"job_id": job.job_id, "steps_completed": 0,
                        "steps_failed": 0, "actions": []}
        verify_result = {"all_passed": True, "task_type": "run_script",
                         "checks": [], "before_state": {}, "after_state": {}}

        coord.execution.run = MagicMock(return_value=exec_summary)
        coord.verification.run = MagicMock(return_value=verify_result)
        coord.reporter.run = MagicMock()

        with patch("raoc.coordinator.queries.get_actions_for_job", return_value=[]):
            await coord.handle_approval(job.job_id, approved=True)

        coord.reporter.run.assert_called_once_with(job.job_id, verify_result)


class TestHandleApprovalDenied:
    """handle_approval with approved=False sends cancellation message."""

    async def test_sends_cancellation_message(self, db):
        coord = _make_coordinator(db)
        job = create_job("Run cleanup.py", engine=db)
        update_job_status(job.job_id, JobStatus.AWAITING_APPROVAL, engine=db)

        await coord.handle_approval(job.job_id, approved=False)

        coord.gateway.send_message.assert_called_once()
        text = coord.gateway.send_message.call_args.kwargs["text"]
        assert "cancelled" in text.lower()

    async def test_message_says_nothing_executed(self, db):
        coord = _make_coordinator(db)
        job = create_job("Run cleanup.py", engine=db)
        update_job_status(job.job_id, JobStatus.AWAITING_APPROVAL, engine=db)

        await coord.handle_approval(job.job_id, approved=False)

        text = coord.gateway.send_message.call_args.kwargs["text"]
        assert "Nothing was executed" in text

    async def test_job_status_set_to_cancelled(self, db):
        coord = _make_coordinator(db)
        job = create_job("Run cleanup.py", engine=db)
        update_job_status(job.job_id, JobStatus.AWAITING_APPROVAL, engine=db)

        await coord.handle_approval(job.job_id, approved=False)

        updated = get_job(job.job_id, engine=db)
        assert updated.status == JobStatus.CANCELLED

    async def test_execution_not_called(self, db):
        coord = _make_coordinator(db)
        job = create_job("Run cleanup.py", engine=db)
        update_job_status(job.job_id, JobStatus.AWAITING_APPROVAL, engine=db)

        coord.execution.run = MagicMock()

        await coord.handle_approval(job.job_id, approved=False)

        coord.execution.run.assert_not_called()


# ---------------------------------------------------------------------------
# advance routing
# ---------------------------------------------------------------------------

class TestAdvance:
    """advance routes to the correct agent based on job status."""

    async def test_received_calls_intake(self, db):
        coord = _make_coordinator(db)
        job = create_job("Rewrite notes.txt", engine=db)

        coord.intake.run = MagicMock(side_effect=_intake_side_effect(db))
        coord.discovery.run = MagicMock(return_value={})
        coord.planning.run = MagicMock(side_effect=_planning_side_effect(db))

        with patch("raoc.coordinator.queries.get_actions_for_job", return_value=[]):
            await coord.advance(job.job_id)

        coord.intake.run.assert_called_once_with(job.job_id)

    async def test_discovering_calls_discovery_and_planning(self, db):
        coord = _make_coordinator(db)
        job = create_job("Rewrite notes.txt", engine=db)
        update_job_status(job.job_id, JobStatus.DISCOVERING, engine=db)

        coord.discovery.run = MagicMock(return_value={})
        coord.planning.run = MagicMock(side_effect=_planning_side_effect(db))

        with patch("raoc.coordinator.queries.get_actions_for_job", return_value=[]):
            await coord.advance(job.job_id)

        coord.discovery.run.assert_called_once_with(job.job_id)
        coord.planning.run.assert_called_once()

    async def test_awaiting_approval_without_grant_does_nothing(self, db):
        coord = _make_coordinator(db)
        job = create_job("Rewrite notes.txt", engine=db)
        update_job_status(job.job_id, JobStatus.AWAITING_APPROVAL, engine=db)

        coord.execution.run = MagicMock()

        await coord.advance(job.job_id)

        coord.execution.run.assert_not_called()

    async def test_awaiting_approval_with_grant_calls_execution(self, db):
        coord = _make_coordinator(db)
        job = create_job("Rewrite notes.txt", engine=db)
        update_job_status(job.job_id, JobStatus.AWAITING_APPROVAL, engine=db)
        update_job_field(job.job_id, approval_granted=True, engine=db)

        exec_summary = {"job_id": job.job_id, "steps_completed": 0,
                        "steps_failed": 0, "actions": []}
        verify_result = {"all_passed": True, "task_type": "run_script",
                         "checks": [], "before_state": {}, "after_state": {}}

        coord.execution.run = MagicMock(return_value=exec_summary)
        coord.verification.run = MagicMock(return_value=verify_result)
        coord.reporter.run = MagicMock()

        with patch("raoc.coordinator.queries.get_actions_for_job", return_value=[]):
            await coord.advance(job.job_id)

        coord.execution.run.assert_called_once()


# ---------------------------------------------------------------------------
# Clarification flow
# ---------------------------------------------------------------------------

class TestClarificationFlow:
    """Ambiguous messages trigger a clarification question; the answer resumes the job."""

    async def test_ambiguous_message_sends_clarification_to_gateway(self, db):
        coord = _make_coordinator(db)
        question = "Which file should I fix, and what changes do you want?"
        coord.intake.run = MagicMock(
            return_value=_make_task(requires_clarification=True, clarification_question=question)
        )

        await coord.handle_new_message("fix my file")

        coord.gateway.send_message.assert_called_once()
        call_text = coord.gateway.send_message.call_args.kwargs["text"]
        assert call_text == question

    async def test_ambiguous_message_populates_pending_clarification(self, db):
        coord = _make_coordinator(db)
        coord.intake.run = MagicMock(
            return_value=_make_task(
                requires_clarification=True,
                clarification_question="Which file?",
            )
        )

        job_id = await coord.handle_new_message("fix my file")

        assert job_id in coord.pending_clarification
        assert isinstance(coord.pending_clarification[job_id], dict)

    async def test_second_message_reuses_same_job_not_new(self, db):
        coord = _make_coordinator(db)
        question = "Which file should I fix?"
        coord.intake.run = MagicMock(
            return_value=_make_task(requires_clarification=True, clarification_question=question)
        )

        first_job_id = await coord.handle_new_message("fix my file")

        # Second message: clarification answer — intake now resolves cleanly
        coord.intake.run = MagicMock(side_effect=_intake_side_effect(db))
        coord.discovery.run = MagicMock(return_value={})
        coord.planning.run = MagicMock(side_effect=_planning_side_effect(db))

        with patch("raoc.coordinator.queries.get_actions_for_job", return_value=[]):
            second_job_id = await coord.handle_new_message("notes.txt — make it more formal")

        assert second_job_id == first_job_id

    async def test_second_message_clears_pending_clarification(self, db):
        coord = _make_coordinator(db)
        coord.intake.run = MagicMock(
            return_value=_make_task(requires_clarification=True, clarification_question="Which file?")
        )
        await coord.handle_new_message("fix my file")

        coord.intake.run = MagicMock(side_effect=_intake_side_effect(db))
        coord.discovery.run = MagicMock(return_value={})
        coord.planning.run = MagicMock(side_effect=_planning_side_effect(db))

        with patch("raoc.coordinator.queries.get_actions_for_job", return_value=[]):
            await coord.handle_new_message("notes.txt — make it more formal")

        assert len(coord.pending_clarification) == 0

    async def test_pipeline_continues_after_clarification(self, db):
        coord = _make_coordinator(db)
        coord.intake.run = MagicMock(
            return_value=_make_task(requires_clarification=True, clarification_question="Which file?")
        )
        await coord.handle_new_message("fix my file")

        coord.intake.run = MagicMock(side_effect=_intake_side_effect(db))
        coord.discovery.run = MagicMock(return_value={})
        coord.planning.run = MagicMock(side_effect=_planning_side_effect(db))

        with patch("raoc.coordinator.queries.get_actions_for_job", return_value=[]):
            await coord.handle_new_message("notes.txt — make it more formal")

        coord.gateway.send_approval_request.assert_called_once()

    async def test_clarification_reply_passed_to_intake_not_parsed(self, db):
        """Any natural phrasing is forwarded to intake via combined message, not parsed."""
        coord = _make_coordinator(db)
        coord.intake.run = MagicMock(
            return_value=_make_task(requires_clarification=True, clarification_question="Which file?")
        )
        await coord.handle_new_message("fix my file")

        coord.intake.run = MagicMock(side_effect=_intake_side_effect(db))
        coord.discovery.run = MagicMock(return_value={})
        coord.planning.run = MagicMock(side_effect=_planning_side_effect(db))

        with patch("raoc.coordinator.queries.get_actions_for_job", return_value=[]):
            await coord.handle_new_message("yeah the second one is fine")

        # Intake must have been called with the combined context message
        call_arg = coord.intake.run.call_args[0][0]
        job = get_job(call_arg, engine=db)
        assert "Original request:" in job.raw_request
        assert "yeah the second one is fine" in job.raw_request

    async def test_discovery_none_triggers_clarification(self, db):
        """When discovery returns None the coordinator sends the question and waits."""
        coord = _make_coordinator(db)
        coord.intake.run = MagicMock(side_effect=_intake_side_effect(db))
        coord.discovery.run = MagicMock(return_value=None)  # file not found

        from raoc.db.queries import update_job_field
        def _discovery_with_question(job_id):
            update_job_field(
                job_id,
                clarification_question="Which file did you mean?",
                engine=db,
            )
            return None
        coord.discovery.run = MagicMock(side_effect=_discovery_with_question)

        await coord.handle_new_message("rewrite my file")

        coord.gateway.send_message.assert_called()
        sent_text = coord.gateway.send_message.call_args.kwargs["text"]
        assert "Which file did you mean?" in sent_text


# ---------------------------------------------------------------------------
# ZIP clarification flow
# ---------------------------------------------------------------------------

class TestZipClarification:
    """handle_clarification extracts the named file or re-prompts on invalid filename."""

    def _setup_zip_job(self, db, tmp_path):
        """Create a ZIP in tmp_path and a job record pointing at it."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        zip_path = ws / "archive.zip"
        with zipfile_module.ZipFile(zip_path, 'w') as zf:
            zf.writestr("notes.txt", "Original notes content.")
            zf.writestr("data.csv", "col1,col2\n1,2\n")

        job = create_job("Rewrite notes.txt from archive.zip", engine=db)
        update_job_field(
            job.job_id,
            task_type="rewrite_file",
            target_path=str(zip_path),
            zip_source_path=str(zip_path),
            clarification_question="Which file?",
            engine=db,
        )
        update_job_status(job.job_id, JobStatus.AWAITING_APPROVAL, engine=db)
        return job.job_id, zip_path, ws

    async def test_clarification_extracts_named_file_from_zip(self, db, tmp_path):
        """handle_clarification extracts the named file to WORKSPACE."""
        coord = _make_coordinator(db)
        job_id, zip_path, ws = self._setup_zip_job(db, tmp_path)

        # Mock downstream pipeline so it doesn't run full discovery/planning
        coord.discovery.run = MagicMock(return_value=None)

        with patch.object(config, "WORKSPACE", ws):
            await coord.handle_clarification(job_id, "notes.txt")

        extracted = ws / "notes.txt"
        assert extracted.exists()
        assert extracted.read_text() == "Original notes content."

    async def test_clarification_updates_target_path(self, db, tmp_path):
        """After extraction, job.target_path is updated to the extracted file."""
        coord = _make_coordinator(db)
        job_id, zip_path, ws = self._setup_zip_job(db, tmp_path)

        coord.discovery.run = MagicMock(return_value=None)

        with patch.object(config, "WORKSPACE", ws):
            await coord.handle_clarification(job_id, "notes.txt")

        updated = get_job(job_id, engine=db)
        assert updated.target_path is not None
        assert "notes.txt" in updated.target_path
        assert str(zip_path) not in updated.target_path

    async def test_clarification_clears_zip_source_path(self, db, tmp_path):
        """After extraction, zip_source_path is cleared from the job."""
        coord = _make_coordinator(db)
        job_id, zip_path, ws = self._setup_zip_job(db, tmp_path)

        coord.discovery.run = MagicMock(return_value=None)

        with patch.object(config, "WORKSPACE", ws):
            await coord.handle_clarification(job_id, "notes.txt")

        updated = get_job(job_id, engine=db)
        assert updated.zip_source_path is None

    async def test_clarification_rejects_invalid_filename(self, db, tmp_path):
        """handle_clarification sends a re-prompt when the filename is not in the ZIP."""
        coord = _make_coordinator(db)
        job_id, zip_path, ws = self._setup_zip_job(db, tmp_path)

        with patch.object(config, "WORKSPACE", ws):
            await coord.handle_clarification(job_id, "nonexistent.txt")

        # Job should remain in AWAITING_APPROVAL
        updated = get_job(job_id, engine=db)
        assert updated.status == JobStatus.AWAITING_APPROVAL

        # Gateway should have sent a helpful re-prompt
        coord.gateway.send_message.assert_called_once()
        text = coord.gateway.send_message.call_args.kwargs["text"]
        assert "not found in the ZIP" in text or "not found" in text.lower()


# ---------------------------------------------------------------------------
# Narrator integration tests
# ---------------------------------------------------------------------------


def _make_coordinator_with_narrator(db) -> PipelineCoordinator:
    """Build a coordinator with a mock narrator and send_status on the gateway."""
    gateway = MagicMock()
    gateway.send_message = AsyncMock()
    gateway.send_approval_request = AsyncMock()
    gateway.send_status = AsyncMock()

    narrator = MagicMock()
    narrator.narrate.return_value = "Status message."
    narrator.narrate_async = AsyncMock(return_value="Status message.")

    coord = PipelineCoordinator(
        db=db,
        llm=MagicMock(),
        sampler=MagicMock(),
        command_wrapper=MagicMock(),
        gateway=gateway,
        narrator=narrator,
    )
    return coord


class TestQueryBypassesActionPipeline:
    """Query tasks skip discovery, planning, execution, and verification entirely."""

    async def test_query_bypasses_action_pipeline(self, db):
        """query_agent.run is called; planning and execution agents are never called."""
        coord = _make_coordinator(db)

        # Replace query_agent with a mock
        coord.query_agent = MagicMock()
        coord.query_agent.run = MagicMock(return_value="There are 3 files.")

        # Mock pipeline agents so we can assert they were never called
        coord.planning.run = MagicMock()
        coord.execution.run = MagicMock()

        def _intake_query_effect(job_id):
            update_job_status(job_id, JobStatus.UNDERSTANDING, engine=db)
            update_job_field(job_id, task_type="query", query_intent="how many files?", engine=db)
            return TaskObject(
                task_type="query",
                instruction="how many files?",
                risk_level="low",
                query_intent="how many files?",
            )

        coord.intake.run = MagicMock(side_effect=_intake_query_effect)

        await coord.handle_new_message("how many files do I have?")

        coord.planning.run.assert_not_called()
        coord.execution.run.assert_not_called()
        coord.query_agent.run.assert_called_once()


class TestQueryActionFlow:
    """query_action jobs: search → confirm → pipeline."""

    def _make_qa_coordinator(self, db):
        """Build a coordinator with send_confirmation on the gateway."""
        gateway = MagicMock()
        gateway.send_message = AsyncMock()
        gateway.send_approval_request = AsyncMock()
        gateway.send_confirmation = AsyncMock()
        gateway.send_status = AsyncMock()
        return PipelineCoordinator(
            db=db,
            llm=MagicMock(),
            sampler=MagicMock(),
            command_wrapper=MagicMock(),
            gateway=gateway,
        )

    def _intake_qa_effect(self, db, found_path="/fake/ws/resume.docx"):
        """Intake side effect: sets status to UNDERSTANDING for a query_action job."""
        def _fn(job_id):
            update_job_status(job_id, JobStatus.UNDERSTANDING, engine=db)
            update_job_field(
                job_id,
                task_type="query_action",
                query_intent="find resume file",
                action_instruction="rewrite to be more formal",
                implied_task_type="rewrite_file",
                engine=db,
            )
            return TaskObject(
                task_type="query_action",
                instruction="find my resume and rewrite it formally",
                risk_level="high",
                query_intent="find resume file",
                action_instruction="rewrite to be more formal",
                implied_task_type="rewrite_file",
            )
        return _fn

    async def test_query_action_searches_before_planning(self, db):
        """query_agent.run_search_for_action is called; send_confirmation is called."""
        coord = self._make_qa_coordinator(db)
        coord.query_agent.run_search_for_action = MagicMock(return_value={
            "file_found": True,
            "file_path": "/fake/ws/resume.docx",
            "file_name": "resume.docx",
            "confidence": 0.9,
            "summary": "a resume for John Smith, last updated 2 days ago",
        })
        coord.planning.run = MagicMock()
        coord.intake.run = MagicMock(side_effect=self._intake_qa_effect(db))

        await coord.handle_new_message("find my resume and rewrite it formally")

        coord.query_agent.run_search_for_action.assert_called_once()
        coord.gateway.send_confirmation.assert_called_once()
        coord.planning.run.assert_not_called()

        job_id = coord.query_agent.run_search_for_action.call_args[0][0]
        assert get_job(job_id, engine=db).status == JobStatus.CONFIRMING

    async def test_query_action_yes_continues_to_pipeline(self, db):
        """After Yes, job.target_path is the found file and pipeline advances."""
        coord = self._make_qa_coordinator(db)

        # Set up a CONFIRMING job with found_file_path
        job = create_job("find my resume and rewrite it formally", engine=db)
        update_job_status(job.job_id, JobStatus.CONFIRMING, engine=db)
        update_job_field(
            job.job_id,
            task_type="query_action",
            found_file_path="/fake/ws/resume.docx",
            implied_task_type="rewrite_file",
            action_instruction="rewrite to be more formal",
            engine=db,
        )

        coord.discovery.run = MagicMock(return_value=None)  # stop after DISCOVERING
        await coord.handle_approval(job.job_id, approved=True)

        updated = get_job(job.job_id, engine=db)
        assert updated.target_path == "/fake/ws/resume.docx"
        assert updated.task_type == "rewrite_file"

    async def test_query_action_no_asks_for_filename(self, db):
        """After No, gateway sends a filename prompt and job is AWAITING_APPROVAL."""
        coord = self._make_qa_coordinator(db)

        job = create_job("find my resume and rewrite it formally", engine=db)
        update_job_status(job.job_id, JobStatus.CONFIRMING, engine=db)
        update_job_field(
            job.job_id,
            task_type="query_action",
            found_file_path="/fake/ws/resume.docx",
            implied_task_type="rewrite_file",
            engine=db,
        )

        await coord.handle_approval(job.job_id, approved=False)

        coord.gateway.send_message.assert_called_once()
        text = coord.gateway.send_message.call_args.kwargs["text"]
        assert "which file" in text.lower()

        assert get_job(job.job_id, engine=db).status == JobStatus.AWAITING_APPROVAL

    async def test_query_action_file_not_found_asks_user(self, db):
        """When search returns file_found=False, AWAITING_APPROVAL and message sent."""
        coord = self._make_qa_coordinator(db)
        coord.query_agent.run_search_for_action = MagicMock(return_value={
            "file_found": False,
            "file_path": None,
            "file_name": None,
            "confidence": 0.0,
            "summary": "No matching file found",
        })
        # Simulate query_agent already setting AWAITING_APPROVAL and sending message
        def _search_side_effect(job_id):
            update_job_status(job_id, JobStatus.AWAITING_APPROVAL, engine=db)
            return {
                "file_found": False,
                "file_path": None,
                "file_name": None,
                "confidence": 0.0,
                "summary": "No matching file found",
            }
        coord.query_agent.run_search_for_action = MagicMock(side_effect=_search_side_effect)

        coord.intake.run = MagicMock(side_effect=self._intake_qa_effect(db))
        await coord.handle_new_message("find my resume and rewrite it formally")

        assert get_job(
            coord.query_agent.run_search_for_action.call_args[0][0], engine=db
        ).status == JobStatus.AWAITING_APPROVAL


class TestNarratorIntegration:
    """StatusNarrator is called at each pipeline stage and its output is sent."""

    async def test_narrator_called_after_intake(self, db):
        """After handle_new_message, narrate_async called with 'message_received'."""
        coord = _make_coordinator_with_narrator(db)
        coord.intake.run = MagicMock(side_effect=_intake_side_effect(db))
        coord.discovery.run = MagicMock(return_value={})
        coord.planning.run = MagicMock(side_effect=_planning_side_effect(db))

        with patch("raoc.coordinator.queries.get_actions_for_job", return_value=[]):
            await coord.handle_new_message("Rewrite notes.txt")

        called_stages = [c.args[0] for c in coord.narrator.narrate_async.call_args_list]
        assert 'message_received' in called_stages

        # send_status must have been called (via _narrate_and_send background task)
        assert coord.gateway.send_status.called

    async def test_narrator_failure_does_not_stop_pipeline(self, db):
        """If narrate_async raises, the pipeline continues and job advances."""
        coord = _make_coordinator_with_narrator(db)
        coord.narrator.narrate_async.side_effect = Exception("Narrator exploded")

        coord.intake.run = MagicMock(side_effect=_intake_side_effect(db))
        coord.discovery.run = MagicMock(return_value={})
        coord.planning.run = MagicMock(side_effect=_planning_side_effect(db))

        with patch("raoc.coordinator.queries.get_actions_for_job", return_value=[]):
            job_id = await coord.handle_new_message("Rewrite notes.txt")

        # Job must have advanced to AWAITING_APPROVAL despite narrator failure
        job = get_job(job_id, engine=db)
        assert job.status == JobStatus.AWAITING_APPROVAL.value

    async def test_status_sent_at_every_stage(self, db):
        """send_status called for kept stages; removed stages are absent."""
        coord = _make_coordinator_with_narrator(db)

        coord.intake.run = MagicMock(side_effect=_intake_side_effect(db))
        coord.discovery.run = MagicMock(return_value={'task_type': 'rewrite_file',
                                                       'target_path': 'notes.txt'})
        coord.planning.run = MagicMock(side_effect=_planning_side_effect(db))

        action = MagicMock()
        action.step_index = 0
        action.action_type.value = 'file_write'
        action.target_path = 'notes.txt'
        action.intent = 'Write rewritten content'

        exec_summary = {"job_id": "x", "steps_completed": 1, "steps_failed": 0, "actions": []}
        verify_result = {"all_passed": True, "task_type": "rewrite_file",
                         "checks": [], "before_state": {}, "after_state": {}}

        coord.execution.run = MagicMock(return_value=exec_summary)
        coord.verification.run = MagicMock(return_value=verify_result)
        coord.reporter.run = MagicMock()

        with patch("raoc.coordinator.queries.get_actions_for_job", return_value=[action]):
            job_id = await coord.handle_new_message("Rewrite notes.txt")
            update_job_field(job_id, approval_granted=True, engine=db)
            await coord.handle_approval(job_id, approved=True)

        called_stages = [c.args[0] for c in coord.narrator.narrate_async.call_args_list]

        # Required stages must be present
        for required in ('message_received', 'discovery_complete', 'execution_step'):
            assert required in called_stages, f"Stage '{required}' not narrated. Got: {called_stages}"

        # Removed stages must be absent
        for removed in ('discovery_started', 'planning_started', 'planning_complete',
                        'intake_complete', 'verification_complete', 'execution_complete'):
            assert removed not in called_stages, f"Stage '{removed}' should have been removed. Got: {called_stages}"

    async def test_narrator_is_fire_and_forget(self, db):
        """narrate_async is scheduled as a background task; pipeline does not wait."""
        coord = _make_coordinator_with_narrator(db)

        # Replace narrate_async with a slow coroutine (2 seconds)
        async def slow_narrate_async(stage, context):
            await asyncio.sleep(2)
            return "Slow status."

        coord.narrator.narrate_async = slow_narrate_async

        coord.intake.run = MagicMock(side_effect=_intake_side_effect(db))
        coord.discovery.run = MagicMock(return_value={'task_type': 'rewrite_file',
                                                       'target_path': 'notes.txt'})
        coord.planning.run = MagicMock(side_effect=_planning_side_effect(db))

        start = time.monotonic()
        with patch("raoc.coordinator.queries.get_actions_for_job", return_value=[]):
            job_id = await coord.handle_new_message("Rewrite notes.txt")
        elapsed = time.monotonic() - start

        # Pipeline advanced to AWAITING_APPROVAL
        job = get_job(job_id, engine=db)
        assert job.status == JobStatus.AWAITING_APPROVAL.value

        # Pipeline completed quickly — did not wait for 2s narration
        assert elapsed < 1.0, f"Pipeline waited for narration: {elapsed:.2f}s"

    async def test_plan_preview_sent_after_discovery_narration(self, db, monkeypatch):
        """send_status (narrator) is called before send_approval_request (plan preview)."""
        # Override the conftest zero-delay fixture — need real delay for ordering test
        monkeypatch.setattr(config, "NARRATION_DELAY_BEFORE_PLAN", 0.4)

        coord = _make_coordinator_with_narrator(db)
        call_order = []

        async def tracked_send_status(text):
            call_order.append('send_status')

        async def tracked_narrate_async(stage, context):
            await asyncio.sleep(0.1)  # simulate API call delay
            return "Working on it."

        async def tracked_send_approval_request(job_id, plan_text):
            call_order.append('send_approval_request')

        coord.narrator.narrate_async = tracked_narrate_async
        coord.gateway.send_status = tracked_send_status
        coord.gateway.send_approval_request = tracked_send_approval_request

        coord.intake.run = MagicMock(side_effect=_intake_side_effect(db))
        coord.discovery.run = MagicMock(return_value={'task_type': 'rewrite_file',
                                                       'target_path': 'notes.txt'})
        coord.planning.run = MagicMock(side_effect=_planning_side_effect(db))

        with patch("raoc.coordinator.queries.get_actions_for_job", return_value=[]):
            await coord.handle_new_message("Rewrite notes.txt")

        # Allow background send_approval_request task to complete
        await asyncio.sleep(0)

        assert 'send_status' in call_order, "send_status (narrator) was never called"
        assert 'send_approval_request' in call_order, "send_approval_request was never called"
        si = call_order.index('send_status')
        api = call_order.index('send_approval_request')
        assert si < api, (
            f"send_status (at {si}) must come before send_approval_request (at {api}). "
            f"Full order: {call_order}"
        )

    async def test_execution_narration_sent_before_action(self, db):
        """send_status for execution_step is delivered before execution_agent.run."""
        coord = _make_coordinator_with_narrator(db)
        call_order = []

        async def tracked_send_status(text):
            call_order.append('send_status')

        def tracked_execution_run(job_id, actions):
            call_order.append('execution_run')
            return {"job_id": job_id, "steps_completed": 1, "steps_failed": 0, "actions": []}

        coord.gateway.send_status = tracked_send_status
        coord.execution.run = tracked_execution_run
        coord.verification.run = MagicMock(return_value={
            "all_passed": True, "task_type": "rewrite_file",
            "checks": [], "before_state": {}, "after_state": {}
        })
        coord.reporter.run = MagicMock()

        job = create_job("Back up and rewrite notes.txt", engine=db)
        update_job_status(job.job_id, JobStatus.AWAITING_APPROVAL, engine=db)
        update_job_field(job.job_id, approval_granted=True, engine=db)

        action = MagicMock()
        action.step_index = 0
        action.action_type.value = 'file_backup'
        action.target_path = 'notes.txt'
        action.intent = 'Back up original file'

        with patch("raoc.coordinator.queries.get_actions_for_job", return_value=[action]):
            await coord.advance(job.job_id)

        assert 'send_status' in call_order, "Execution narrator (send_status) was never called"
        assert 'execution_run' in call_order, "execution.run was never called"
        si = call_order.index('send_status')
        ei = call_order.index('execution_run')
        assert si < ei, (
            f"send_status (at {si}) must come before execution_run (at {ei}). "
            f"Full order: {call_order}"
        )

    def test_await_sleep_not_blocking_sleep_used(self):
        """coordinator.py never calls time.sleep — only asyncio.sleep."""
        source = inspect.getsource(coordinator_module)
        assert 'time.sleep' not in source, (
            "time.sleep() found in coordinator.py — must use await asyncio.sleep() instead"
        )
        assert 'asyncio.sleep' in source, (
            "asyncio.sleep not found in coordinator.py"
        )


# ---------------------------------------------------------------------------
# Approval gate tests
# ---------------------------------------------------------------------------


class TestApprovalGate:
    """The pipeline must stop at AWAITING_APPROVAL and only continue after handle_approval."""

    async def test_first_message_is_immediate_acknowledgement(self, db):
        """send_status('Got it...') is the very first call — before create_job or narrator."""
        gateway = MagicMock()
        gateway.send_message = AsyncMock()
        gateway.send_approval_request = AsyncMock()

        call_order = []

        async def tracked_send_status(text):
            call_order.append(('send_status', text))

        gateway.send_status = tracked_send_status

        narrator = MagicMock()
        narrator.narrate_async = AsyncMock(return_value="Status message.")

        coord = PipelineCoordinator(
            db=db,
            llm=MagicMock(),
            sampler=MagicMock(),
            command_wrapper=MagicMock(),
            gateway=gateway,
            narrator=narrator,
        )

        # Patch create_job to record when it is called
        original_create_job = coordinator_module.queries.create_job

        def tracked_create_job(text, engine=None):
            call_order.append('create_job')
            return original_create_job(text, engine=engine)

        coord.intake.run = MagicMock(side_effect=_intake_side_effect(db))
        coord.discovery.run = MagicMock(return_value={})
        coord.planning.run = MagicMock(side_effect=_planning_side_effect(db))

        with patch("raoc.coordinator.queries.create_job", side_effect=tracked_create_job):
            with patch("raoc.coordinator.queries.get_actions_for_job", return_value=[]):
                await coord.handle_new_message("Rewrite notes.txt")

        # The very first call must be send_status with "Got it"
        assert len(call_order) > 0, "No calls were recorded"
        first = call_order[0]
        assert first[0] == 'send_status', (
            f"First call was not send_status — got {first}. Full order: {call_order}"
        )
        assert "Got it" in first[1] or "got it" in first[1].lower(), (
            f"First send_status text does not contain 'Got it': {first[1]!r}"
        )

        # create_job must come after send_status
        send_status_idx = next(i for i, c in enumerate(call_order) if c == first)
        create_job_idx = call_order.index('create_job')
        assert send_status_idx < create_job_idx, (
            f"send_status (at {send_status_idx}) must come before create_job "
            f"(at {create_job_idx}). Full order: {call_order}"
        )

    async def test_pipeline_stops_at_awaiting_approval(self, db):
        """advance() returns after sending plan preview; execution is never called."""
        coord = _make_coordinator(db)
        coord.intake.run = MagicMock(side_effect=_intake_side_effect(db))
        coord.discovery.run = MagicMock(return_value={})
        coord.planning.run = MagicMock(side_effect=_planning_side_effect(db))
        coord.execution.run = MagicMock()

        with patch("raoc.coordinator.queries.get_actions_for_job", return_value=[]):
            job_id = await coord.handle_new_message("Rewrite notes.txt")

        # Pipeline must have stopped — execution never called
        coord.execution.run.assert_not_called()

        # Job status must be AWAITING_APPROVAL
        job = get_job(job_id, engine=db)
        assert job.status == JobStatus.AWAITING_APPROVAL, (
            f"Expected AWAITING_APPROVAL, got {job.status}"
        )

        # Plan preview must have been sent
        coord.gateway.send_approval_request.assert_called_once()

    async def test_execution_only_starts_after_handle_approval(self, db):
        """Execution does not run after advance(); only runs after handle_approval(True)."""
        coord = _make_coordinator(db)
        coord.intake.run = MagicMock(side_effect=_intake_side_effect(db))
        coord.discovery.run = MagicMock(return_value={})
        coord.planning.run = MagicMock(side_effect=_planning_side_effect(db))

        exec_summary = {"job_id": "x", "steps_completed": 0, "steps_failed": 0, "actions": []}
        verify_result = {"all_passed": True, "task_type": "rewrite_file",
                         "checks": [], "before_state": {}, "after_state": {}}

        coord.execution.run = MagicMock(return_value=exec_summary)
        coord.verification.run = MagicMock(return_value=verify_result)
        coord.reporter.run = MagicMock()

        with patch("raoc.coordinator.queries.get_actions_for_job", return_value=[]):
            job_id = await coord.handle_new_message("Rewrite notes.txt")

        # Execution must NOT have run yet
        coord.execution.run.assert_not_called()

        # Now user approves
        with patch("raoc.coordinator.queries.get_actions_for_job", return_value=[]):
            await coord.handle_approval(job_id, approved=True)

        # Now execution must have run
        coord.execution.run.assert_called_once()

    async def test_execution_narration_never_before_plan_preview(self, db):
        """No execution_step narration arrives before send_approval_request is called."""
        coord = _make_coordinator_with_narrator(db)

        exec_summary = {"job_id": "x", "steps_completed": 1, "steps_failed": 0, "actions": []}
        verify_result = {"all_passed": True, "task_type": "rewrite_file",
                         "checks": [], "before_state": {}, "after_state": {}}

        coord.execution.run = MagicMock(return_value=exec_summary)
        coord.verification.run = MagicMock(return_value=verify_result)
        coord.reporter.run = MagicMock()

        action = MagicMock()
        action.step_index = 0
        action.action_type.value = 'file_write'
        action.target_path = 'notes.txt'
        action.intent = 'Write rewritten content'

        coord.intake.run = MagicMock(side_effect=_intake_side_effect(db))
        coord.discovery.run = MagicMock(return_value={'task_type': 'rewrite_file',
                                                       'target_path': 'notes.txt'})
        coord.planning.run = MagicMock(side_effect=_planning_side_effect(db))

        # Run through to AWAITING_APPROVAL (pipeline should stop here)
        with patch("raoc.coordinator.queries.get_actions_for_job", return_value=[action]):
            job_id = await coord.handle_new_message("Rewrite notes.txt")

        await asyncio.sleep(0)

        # Capture narrator stages called so far (before approval)
        stages_before_approval = [
            c.args[0] for c in coord.narrator.narrate_async.call_args_list
        ]

        # execution_step must NOT have been narrated yet
        assert 'execution_step' not in stages_before_approval, (
            f"execution_step narration fired before plan preview/approval. "
            f"Stages so far: {stages_before_approval}"
        )

        # send_approval_request must have been called (plan preview sent)
        coord.gateway.send_approval_request.assert_called_once()

        # Now approve — execution_step should fire during execution
        with patch("raoc.coordinator.queries.get_actions_for_job", return_value=[action]):
            await coord.handle_approval(job_id, approved=True)

        stages_after_approval = [
            c.args[0] for c in coord.narrator.narrate_async.call_args_list
        ]
        assert 'execution_step' in stages_after_approval, (
            "execution_step narration never fired after approval"
        )

    async def test_no_received_processing_message_sent(self, db):
        """No send_status or send_message call contains 'Received' or 'Processing'."""
        coord = _make_coordinator(db)
        coord.intake.run = MagicMock(side_effect=_intake_side_effect(db))
        coord.discovery.run = MagicMock(return_value={})
        coord.planning.run = MagicMock(side_effect=_planning_side_effect(db))

        with patch("raoc.coordinator.queries.get_actions_for_job", return_value=[]):
            await coord.handle_new_message("Rewrite notes.txt")

        await asyncio.sleep(0)

        all_texts = []
        for call in coord.gateway.send_status.call_args_list:
            text = call.args[0] if call.args else call.kwargs.get('text', '')
            all_texts.append(text)
        for call in coord.gateway.send_message.call_args_list:
            text = call.args[0] if call.args else call.kwargs.get('text', '')
            all_texts.append(text)

        for text in all_texts:
            assert 'Received' not in text, (
                f"Found forbidden 'Received' in message: {text!r}"
            )
            assert 'Processing' not in text, (
                f"Found forbidden 'Processing' in message: {text!r}"
            )

    async def test_no_execution_narration_before_approval(self, db):
        """advance() to AWAITING_APPROVAL produces no execution_step narration."""
        coord = _make_coordinator_with_narrator(db)
        coord.intake.run = MagicMock(side_effect=_intake_side_effect(db))
        coord.discovery.run = MagicMock(return_value={'task_type': 'rewrite_file',
                                                       'target_path': 'notes.txt'})
        coord.planning.run = MagicMock(side_effect=_planning_side_effect(db))

        action = MagicMock()
        action.step_index = 0
        action.action_type.value = 'file_write'
        action.target_path = 'notes.txt'
        action.intent = 'Write rewritten content'

        exec_summary = {"job_id": "x", "steps_completed": 1, "steps_failed": 0, "actions": []}
        verify_result = {"all_passed": True, "task_type": "rewrite_file",
                         "checks": [], "before_state": {}, "after_state": {}}

        coord.execution.run = MagicMock(return_value=exec_summary)
        coord.verification.run = MagicMock(return_value=verify_result)
        coord.reporter.run = MagicMock()

        with patch("raoc.coordinator.queries.get_actions_for_job", return_value=[action]):
            job_id = await coord.handle_new_message("Rewrite notes.txt")

        await asyncio.sleep(0)

        # Collect all stages narrated before approval
        stages_before = [c.args[0] for c in coord.narrator.narrate_async.call_args_list]
        assert 'execution_step' not in stages_before, (
            f"execution_step narrated before approval. Stages: {stages_before}"
        )

        # Approve — execution_step must appear only now
        with patch("raoc.coordinator.queries.get_actions_for_job", return_value=[action]):
            await coord.handle_approval(job_id, approved=True)

        stages_after = [c.args[0] for c in coord.narrator.narrate_async.call_args_list]
        assert 'execution_step' in stages_after, (
            "execution_step narration never fired after handle_approval(True)"
        )


# ---------------------------------------------------------------------------
# Policy integration tests
# ---------------------------------------------------------------------------

from raoc.models.policy import PolicyDecision, PolicyResult, ZoneType


def _make_policy_result(decision: PolicyDecision, reason: str = 'test reason') -> PolicyResult:
    return PolicyResult(
        action_id='action-1',
        decision=decision,
        zone=ZoneType.SAFE_WORKSPACE,
        reason=reason,
    )


def _make_coordinator_with_policy(db, policy_results=None) -> PipelineCoordinator:
    """Build a coordinator with a mocked PolicyAgent."""
    gateway = MagicMock()
    gateway.send_message = AsyncMock()
    gateway.send_approval_request = AsyncMock()
    gateway.send_status = AsyncMock()
    gateway.send_confirmation = AsyncMock()

    if policy_results is None:
        policy_results = [_make_policy_result(PolicyDecision.AUTO_APPROVED)]

    policy_agent = MagicMock()
    policy_agent.review_plan.return_value = policy_results

    return PipelineCoordinator(
        db=db,
        llm=MagicMock(),
        sampler=MagicMock(),
        command_wrapper=MagicMock(),
        gateway=gateway,
        policy_agent=policy_agent,
    )


@pytest.mark.asyncio
async def test_blocked_policy_stops_pipeline_before_preview(db):
    """When PolicyAgent returns blocked, coordinator sends blocked message and does NOT send plan preview."""
    from pathlib import Path
    from raoc.db.queries import save_action
    from raoc.models.action import ActionObject

    blocked_result = _make_policy_result(
        PolicyDecision.BLOCKED,
        reason='~/.ssh/config is in the forbidden zone. This is a permanent restriction.',
    )
    coord = _make_coordinator_with_policy(db, policy_results=[blocked_result])

    job = create_job('rewrite ~/.ssh/config', engine=db)
    update_job_field(job.job_id, task_type='rewrite_file',
                     target_path=str(Path.home() / '.ssh' / 'config'), engine=db)
    update_job_status(job.job_id, JobStatus.DISCOVERING, engine=db)

    coord.discovery.run = MagicMock(return_value={
        'task_type': 'rewrite_file',
        'target_path': str(Path.home() / '.ssh' / 'config'),
        'size_bytes': 100, 'modified_at': '',
        'detected_format': 'text', 'format_change': False,
    })
    coord.planning.run = MagicMock(side_effect=lambda job_id, ctx: update_job_status(
        job_id, JobStatus.AWAITING_APPROVAL, engine=db))

    await coord.advance(job.job_id)

    coord.gateway.send_approval_request.assert_not_called()
    assert coord.gateway.send_message.called
    call_text = coord.gateway.send_message.call_args.kwargs.get('text', '')
    assert 'blocked' in call_text.lower() or 'forbidden' in call_text.lower()


@pytest.mark.asyncio
async def test_judgment_zone_items_appear_in_plan_preview(db):
    """When actions have policy_decision=judgment_zone, _build_plan_preview includes flagged section."""
    from raoc.db.queries import save_action
    from raoc.models.action import ActionObject

    coord = _make_coordinator_with_policy(db)

    job = create_job('test', engine=db)
    update_job_field(job.job_id, task_type='rewrite_file',
                     target_path='~/Documents/project/notes.txt', engine=db)
    action = ActionObject(
        job_id=job.job_id,
        step_index=0,
        action_type='file_write',
        risk_level='low',
        target_path='~/Documents/project/notes.txt',
        intent='Rewrite notes.txt',
    )
    save_action(action, engine=db)

    from raoc.db.queries import get_actions_for_job, update_action_policy
    update_action_policy(
        action_id=action.action_id,
        decision='judgment_zone',
        reason='~/Documents/project/ matches two zones at equal depth.',
        zone='restricted',
        engine=db,
    )
    actions = get_actions_for_job(job.job_id, engine=db)
    preview = coord._build_plan_preview(job.job_id, actions)

    assert '⚠️' in preview or 'judgment' in preview.lower()
    assert '~/Documents/project/' in preview
