"""Database read/write functions for RAOC.

All functions accept an engine parameter so tests can inject a temp database.
All writes use engine.begin() transactions.
Datetimes are stored and returned as ISO 8601 UTC strings.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.engine import Engine

from raoc.db.schema import actions, audit_log, get_engine, jobs
from raoc.models.action import ActionObject
from raoc.models.job import JobRecord, JobStatus

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _row_to_job(row) -> JobRecord:
    """Convert a database row mapping to a JobRecord."""
    return JobRecord(
        job_id=row['job_id'],
        raw_request=row['raw_request'],
        task_type=row['task_type'],
        target_path=row['target_path'],
        status=row['status'],
        created_at=datetime.fromisoformat(row['created_at']),
        updated_at=datetime.fromisoformat(row['updated_at']),
        error_message=row['error_message'],
        approval_granted=bool(row['approval_granted']) if row['approval_granted'] is not None else None,
        clarification_question=row['clarification_question'],
        output_path=row['output_path'],
        zip_source_path=row['zip_source_path'],
        query_intent=row['query_intent'],
        found_file_path=row['found_file_path'],
        implied_task_type=row['implied_task_type'],
        action_instruction=row['action_instruction'],
    )


def create_job(raw_request: str, engine: Engine = None) -> JobRecord:
    """Create a new job record, insert it into the database, and return it.

    The job starts with status RECEIVED and all optional fields as None.
    """
    if engine is None:
        engine = get_engine()
    job = JobRecord(raw_request=raw_request)
    with engine.begin() as conn:
        conn.execute(jobs.insert().values(
            job_id=job.job_id,
            raw_request=job.raw_request,
            task_type=job.task_type,
            target_path=job.target_path,
            status=job.status,
            created_at=job.created_at.isoformat(),
            updated_at=job.updated_at.isoformat(),
            error_message=job.error_message,
            approval_granted=None,
            clarification_question=None,
            output_path=None,
            zip_source_path=None,
            query_intent=None,
            found_file_path=None,
            implied_task_type=None,
            action_instruction=None,
        ))
    logger.info("Job created: %s", job.job_id)
    return job


def get_job(job_id: str, engine: Engine = None) -> JobRecord:
    """Fetch a job by ID and return it as a JobRecord.

    Raises ValueError if no job with that ID exists.
    """
    if engine is None:
        engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            jobs.select().where(jobs.c.job_id == job_id)
        ).mappings().first()
    if row is None:
        raise ValueError(f"Job not found: {job_id}")
    return _row_to_job(row)


def update_job_status(
    job_id: str,
    status: JobStatus,
    error: Optional[str] = None,
    engine: Engine = None,
) -> None:
    """Update a job's status and updated_at. Optionally set error_message."""
    if engine is None:
        engine = get_engine()
    values = {
        'status': status.value if isinstance(status, JobStatus) else status,
        'updated_at': _now_iso(),
    }
    if error is not None:
        values['error_message'] = error
    with engine.begin() as conn:
        conn.execute(jobs.update().where(jobs.c.job_id == job_id).values(**values))
    logger.info("Job %s status → %s", job_id, status)


def update_job_field(job_id: str, engine: Engine = None, **kwargs) -> None:
    """Update arbitrary fields on a job row.

    Pass field names and values as keyword arguments.
    Always sets updated_at to the current UTC time.
    """
    if engine is None:
        engine = get_engine()
    kwargs['updated_at'] = _now_iso()
    with engine.begin() as conn:
        conn.execute(jobs.update().where(jobs.c.job_id == job_id).values(**kwargs))


def save_action(action: ActionObject, engine: Engine = None) -> None:
    """Insert an ActionObject into the actions table."""
    if engine is None:
        engine = get_engine()
    with engine.begin() as conn:
        conn.execute(actions.insert().values(
            action_id=action.action_id,
            job_id=action.job_id,
            step_index=action.step_index,
            action_type=action.action_type,
            risk_level=action.risk_level,
            target_path=action.target_path,
            intent=action.intent,
            command=action.command,
            change_summary=action.change_summary,
            status=action.status,
            execution_output=action.execution_output,
            verification_result=action.verification_result,
            created_at=action.created_at.isoformat(),
            completed_at=action.completed_at.isoformat() if action.completed_at else None,
        ))
    logger.info("Action saved: %s (job %s step %d)", action.action_id, action.job_id, action.step_index)


def update_action_status(
    action_id: str,
    status: str,
    output: Optional[str] = None,
    engine: Engine = None,
) -> None:
    """Update an action's status and optionally its execution_output."""
    if engine is None:
        engine = get_engine()
    values = {'status': status}
    if output is not None:
        values['execution_output'] = output
    with engine.begin() as conn:
        conn.execute(actions.update().where(actions.c.action_id == action_id).values(**values))


def update_action_policy(
    action_id: str,
    decision: str,
    reason: str,
    zone: str,
    engine: Engine = None,
) -> None:
    """Stamp policy_decision, policy_reason, and target_zone onto an existing action row.

    Called by PolicyAgent after PlanningAgent has already inserted the action.
    Uses UPDATE, not INSERT.
    """
    if engine is None:
        engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            actions.update()
            .where(actions.c.action_id == action_id)
            .values(
                policy_decision=decision,
                policy_reason=reason,
                target_zone=zone,
            )
        )
    logger.info("Policy stamped on action %s: %s", action_id, decision)


def get_actions_for_job(job_id: str, engine: Engine = None) -> list:
    """Return all ActionObjects for a job, ordered by step_index.

    Returns an empty list if the job has no actions yet.
    """
    if engine is None:
        engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            actions.select()
            .where(actions.c.job_id == job_id)
            .order_by(actions.c.step_index)
        ).mappings().all()
    return [_row_to_action(row) for row in rows]


def _row_to_action(row) -> ActionObject:
    """Convert a database row mapping to an ActionObject."""
    return ActionObject(
        action_id=row['action_id'],
        job_id=row['job_id'],
        step_index=row['step_index'],
        action_type=row['action_type'],
        risk_level=row['risk_level'],
        target_path=row['target_path'],
        intent=row['intent'],
        command=row['command'],
        change_summary=row['change_summary'],
        status=row['status'],
        execution_output=row['execution_output'],
        verification_result=row['verification_result'],
        policy_decision=row['policy_decision'],
        policy_reason=row['policy_reason'],
        target_zone=row['target_zone'],
        created_at=datetime.fromisoformat(row['created_at']),
        completed_at=datetime.fromisoformat(row['completed_at']) if row['completed_at'] else None,
    )


def write_audit(
    job_id: str,
    event: str,
    detail: Optional[str] = None,
    engine: Engine = None,
) -> None:
    """Append an audit log entry for a job event."""
    if engine is None:
        engine = get_engine()
    with engine.begin() as conn:
        conn.execute(audit_log.insert().values(
            job_id=job_id,
            event=event,
            detail=detail,
            created_at=_now_iso(),
        ))
    logger.info("Audit: job=%s event=%s", job_id, event)


def get_active_zip_clarification_job(engine: Engine = None):
    """Return the most recent AWAITING_APPROVAL job with zip_source_path set, or None.

    Used by the coordinator to detect when an incoming text message is a reply
    to a ZIP file contents question rather than a new job request.
    """
    if engine is None:
        engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            jobs.select()
            .where(jobs.c.status == 'awaiting_approval')
            .where(jobs.c.zip_source_path.isnot(None))
            .order_by(jobs.c.updated_at.desc())
            .limit(1)
        ).mappings().first()
    if row is None:
        return None
    return _row_to_job(row)


def get_audit_log(job_id: str, engine: Engine = None) -> list[dict]:
    """Return all audit log entries for a job, ordered by id ascending."""
    if engine is None:
        engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            audit_log.select()
            .where(audit_log.c.job_id == job_id)
            .order_by(audit_log.c.id)
        ).mappings().all()
    return [dict(row) for row in rows]
