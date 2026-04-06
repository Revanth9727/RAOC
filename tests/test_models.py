"""Tests for raoc.models — JobRecord, ActionObject, and TaskObject."""

import time
import uuid
from datetime import datetime, timezone

from raoc.models.action import ActionObject, ActionType
from raoc.models.job import JobRecord, JobStatus
from raoc.models.task import TaskObject


def test_job_record_defaults():
    """JobRecord created with only raw_request has correct default values."""
    job = JobRecord(raw_request="Run cleanup.py")

    assert job.raw_request == "Run cleanup.py"
    assert job.status == JobStatus.RECEIVED.value
    assert job.task_type is None
    assert job.target_path is None
    assert job.error_message is None
    assert job.approval_granted is None
    assert isinstance(job.created_at, datetime)
    assert isinstance(job.updated_at, datetime)


def test_job_record_job_id_is_valid_uuid():
    """job_id is a valid UUID4 string."""
    job = JobRecord(raw_request="Rewrite notes.txt")
    # Must not raise
    parsed = uuid.UUID(job.job_id)
    assert parsed.version == 4


def test_update_status_changes_status_and_updated_at():
    """update_status sets the new status and bumps updated_at."""
    job = JobRecord(raw_request="Run cleanup.py")
    original_updated_at = job.updated_at

    # Sleep a tiny amount so the timestamp is guaranteed to differ
    time.sleep(0.01)
    job.update_status(JobStatus.UNDERSTANDING)

    assert job.status == JobStatus.UNDERSTANDING.value
    assert job.updated_at > original_updated_at


# ── ActionObject tests ────────────────────────────────────────────


def test_action_object_creates_correctly():
    """ActionObject stores all required fields and defaults status to pending."""
    action = ActionObject(
        job_id="test-job-id",
        step_index=0,
        action_type=ActionType.FILE_READ,
        risk_level="low",
        target_path="/raoc_workspace/notes.txt",
        intent="Read current file content",
    )

    assert action.job_id == "test-job-id"
    assert action.step_index == 0
    assert action.action_type == ActionType.FILE_READ.value
    assert action.risk_level == "low"
    assert action.status == "pending"
    assert action.command is None
    assert action.execution_output is None
    assert action.verification_result is None
    assert action.completed_at is None
    assert isinstance(action.created_at, datetime)


def test_action_object_action_id_is_valid_uuid():
    """action_id default is a valid UUID4 string."""
    action = ActionObject(
        job_id="test-job-id",
        step_index=1,
        action_type=ActionType.CMD_EXECUTE,
        risk_level="medium",
        target_path="/raoc_workspace/scripts/cleanup.py",
        intent="Execute cleanup.py",
    )
    parsed = uuid.UUID(action.action_id)
    assert parsed.version == 4


def test_action_object_optional_fields():
    """ActionObject accepts command and all optional fields."""
    action = ActionObject(
        job_id="test-job-id",
        step_index=2,
        action_type=ActionType.FILE_WRITE,
        risk_level="medium",
        target_path="/raoc_workspace/notes.txt",
        intent="Write rewritten content",
        command="new file content here",
        status="succeeded",
        execution_output="ok",
        verification_result="passed",
    )

    assert action.command == "new file content here"
    assert action.status == "succeeded"
    assert action.execution_output == "ok"
    assert action.verification_result == "passed"


# ── TaskObject tests ──────────────────────────────────────────────


def test_task_object_creates_correctly():
    """TaskObject stores all fields with correct defaults."""
    task = TaskObject(
        task_type="run_script",
        target_path="cleanup.py",
        instruction="Run the cleanup script",
        risk_level="medium",
    )

    assert task.task_type == "run_script"
    assert task.target_path == "cleanup.py"
    assert task.instruction == "Run the cleanup script"
    assert task.risk_level == "medium"
    assert task.requires_clarification is False
    assert task.clarification_question is None


def test_task_object_with_clarification():
    """TaskObject stores clarification fields when provided."""
    task = TaskObject(
        task_type="rewrite_file",
        target_path="notes.txt",
        instruction="Fix my document",
        risk_level="low",
        requires_clarification=True,
        clarification_question="Which document do you mean?",
    )

    assert task.requires_clarification is True
    assert task.clarification_question == "Which document do you mean?"


