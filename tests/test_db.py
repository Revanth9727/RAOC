"""Tests for raoc.db.schema and raoc.db.queries."""

import time

import pytest
from sqlalchemy import inspect

from raoc.db.queries import (
    create_job,
    get_actions_for_job,
    get_audit_log,
    get_job,
    save_action,
    update_action_policy,
    update_job_status,
    write_audit,
)
from raoc.db.schema import create_tables, get_engine
from raoc.models.action import ActionObject, ActionType
from raoc.models.job import JobStatus


def test_create_tables_creates_all_three_tables(tmp_path):
    """create_tables() creates jobs, actions, and audit_log tables."""
    db_file = tmp_path / 'test_raoc.db'
    engine = get_engine(db_path=db_file)
    create_tables(engine)

    inspector = inspect(engine)
    table_names = inspector.get_table_names()

    assert 'jobs' in table_names
    assert 'actions' in table_names
    assert 'audit_log' in table_names


def test_create_tables_is_idempotent(tmp_path):
    """Calling create_tables() twice does not raise an error."""
    db_file = tmp_path / 'test_raoc.db'
    engine = get_engine(db_path=db_file)
    create_tables(engine)
    create_tables(engine)  # must not raise

    inspector = inspect(engine)
    assert len(inspector.get_table_names()) == 3


def test_jobs_table_columns(tmp_path):
    """jobs table has all required columns."""
    db_file = tmp_path / 'test_raoc.db'
    engine = get_engine(db_path=db_file)
    create_tables(engine)

    inspector = inspect(engine)
    columns = {col['name'] for col in inspector.get_columns('jobs')}

    assert columns == {
        'job_id', 'raw_request', 'task_type', 'target_path',
        'status', 'created_at', 'updated_at', 'error_message', 'approval_granted',
        'clarification_question', 'output_path', 'zip_source_path', 'query_intent',
        'found_file_path', 'implied_task_type', 'action_instruction',
    }


def test_actions_table_columns(tmp_path):
    """actions table has all required columns."""
    db_file = tmp_path / 'test_raoc.db'
    engine = get_engine(db_path=db_file)
    create_tables(engine)

    inspector = inspect(engine)
    columns = {col['name'] for col in inspector.get_columns('actions')}

    assert columns == {
        'action_id', 'job_id', 'step_index', 'action_type', 'risk_level',
        'target_path', 'intent', 'command', 'change_summary', 'status',
        'execution_output', 'verification_result', 'created_at', 'completed_at',
        'policy_decision', 'policy_reason', 'target_zone',
    }


def test_audit_log_table_columns(tmp_path):
    """audit_log table has all required columns."""
    db_file = tmp_path / 'test_raoc.db'
    engine = get_engine(db_path=db_file)
    create_tables(engine)

    inspector = inspect(engine)
    columns = {col['name'] for col in inspector.get_columns('audit_log')}

    assert columns == {'id', 'job_id', 'event', 'detail', 'created_at'}


# ── Shared fixture ────────────────────────────────────────────────


@pytest.fixture()
def db(tmp_path):
    """Return an engine backed by a fresh temp database with all tables created."""
    engine = get_engine(db_path=tmp_path / 'test_raoc.db')
    create_tables(engine)
    return engine


# ── Query tests ───────────────────────────────────────────────────


def test_create_job_writes_and_reads_back(db):
    """create_job inserts a record; get_job returns a matching JobRecord."""
    job = create_job("Run cleanup.py", engine=db)

    fetched = get_job(job.job_id, engine=db)

    assert fetched.job_id == job.job_id
    assert fetched.raw_request == "Run cleanup.py"
    assert fetched.status == JobStatus.RECEIVED.value
    assert fetched.task_type is None
    assert fetched.target_path is None
    assert fetched.error_message is None
    assert fetched.approval_granted is None


def test_get_job_raises_value_error_for_unknown_id(db):
    """get_job raises ValueError when no matching job exists."""
    with pytest.raises(ValueError, match="Job not found"):
        get_job("nonexistent-id", engine=db)


def test_update_job_status_changes_status_and_updated_at(db):
    """update_job_status sets the new status and bumps updated_at."""
    job = create_job("Rewrite notes.txt", engine=db)
    original_updated_at = job.updated_at

    time.sleep(0.01)
    update_job_status(job.job_id, JobStatus.UNDERSTANDING, engine=db)

    fetched = get_job(job.job_id, engine=db)
    assert fetched.status == JobStatus.UNDERSTANDING.value
    assert fetched.updated_at > original_updated_at


def test_update_job_status_sets_error_message(db):
    """update_job_status stores error_message when provided."""
    job = create_job("Run bad.py", engine=db)
    update_job_status(job.job_id, JobStatus.FAILED, error="Script not found", engine=db)

    fetched = get_job(job.job_id, engine=db)
    assert fetched.status == JobStatus.FAILED.value
    assert fetched.error_message == "Script not found"


def test_save_action_writes_and_reads_back(db):
    """save_action inserts an action; it can be retrieved via the actions table."""
    from sqlalchemy import text

    job = create_job("Run cleanup.py", engine=db)
    action = ActionObject(
        job_id=job.job_id,
        step_index=0,
        action_type=ActionType.FILE_READ,
        risk_level="low",
        target_path="/raoc_workspace/notes.txt",
        intent="Read current file content",
    )
    save_action(action, engine=db)

    with db.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM actions WHERE action_id = :id"),
            {"id": action.action_id},
        ).mappings().first()

    assert row is not None
    assert row['job_id'] == job.job_id
    assert row['step_index'] == 0
    assert row['action_type'] == ActionType.FILE_READ.value
    assert row['risk_level'] == "low"
    assert row['status'] == "pending"


def test_write_audit_and_get_audit_log(db):
    """write_audit appends entries; get_audit_log returns them in order."""
    job = create_job("Run cleanup.py", engine=db)

    write_audit(job.job_id, "intake_started", engine=db)
    write_audit(job.job_id, "intake_complete", detail="task=run_script", engine=db)

    log = get_audit_log(job.job_id, engine=db)

    assert len(log) == 2
    assert log[0]['event'] == "intake_started"
    assert log[0]['detail'] is None
    assert log[1]['event'] == "intake_complete"
    assert log[1]['detail'] == "task=run_script"
    assert log[0]['id'] < log[1]['id']



def test_action_object_has_policy_fields():
    a = ActionObject(
        job_id='job1',
        step_index=0,
        action_type='file_write',
        risk_level='low',
        target_path='/tmp/foo.txt',
        intent='Write foo',
    )
    assert a.policy_decision is None
    assert a.policy_reason is None
    assert a.target_zone is None


def test_update_action_policy_persists_fields(tmp_path):
    engine = get_engine(db_path=tmp_path / 'test_policy_fields.db')
    create_tables(engine)

    job = create_job('test', engine=engine)
    action = ActionObject(
        job_id=job.job_id,
        step_index=0,
        action_type='file_write',
        risk_level='low',
        target_path='/tmp/foo.txt',
        intent='Write foo',
    )
    save_action(action, engine=engine)

    update_action_policy(
        action_id=action.action_id,
        decision='blocked',
        reason='~/.ssh is forbidden.',
        zone='forbidden',
        engine=engine,
    )

    actions = get_actions_for_job(job.job_id, engine=engine)
    assert actions[0].policy_decision == 'blocked'
    assert actions[0].policy_reason == '~/.ssh is forbidden.'
    assert actions[0].target_zone == 'forbidden'
