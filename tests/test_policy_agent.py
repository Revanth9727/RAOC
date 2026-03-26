"""Tests for raoc.agents.policy_agent.PolicyAgent."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from raoc.agents.policy_agent import PolicyAgent
from raoc.db.queries import (
    create_job,
    get_actions_for_job,
    save_action,
    update_job_field,
)
from raoc.db.schema import create_tables, get_engine
from raoc.models.action import ActionObject
from raoc.models.policy import PolicyDecision, ZoneType
from raoc.substrate.exceptions import AmbiguousZoneError
from raoc.substrate.zone_resolver import ZoneResolver


@pytest.fixture()
def db(tmp_path):
    engine = get_engine(db_path=tmp_path / 'test_policy.db')
    create_tables(engine)
    return engine


def _make_resolver(zone: ZoneType, raises_ambiguous: bool = False) -> ZoneResolver:
    """Return a ZoneResolver mock that always returns the given zone (or raises)."""
    resolver = MagicMock(spec=ZoneResolver)
    if raises_ambiguous:
        resolver.resolve.side_effect = AmbiguousZoneError('/some/path')
    else:
        resolver.resolve.return_value = zone
    return resolver


def _make_action(db, job_id: str, action_type: str = 'file_write',
                 target_path: str = '/tmp/foo.txt') -> ActionObject:
    action = ActionObject(
        job_id=job_id,
        step_index=0,
        action_type=action_type,
        risk_level='low',
        target_path=target_path,
        intent='Test action',
    )
    save_action(action, engine=db)
    return action


def _make_job(db) -> str:
    job = create_job('test request', engine=db)
    update_job_field(job.job_id, task_type='rewrite_file',
                     target_path='/tmp/foo.txt', engine=db)
    return job.job_id


# --- Individual decision path tests ---

def test_safe_workspace_returns_auto_approved(db):
    job_id = _make_job(db)
    _make_action(db, job_id, action_type='file_write')
    resolver = _make_resolver(ZoneType.SAFE_WORKSPACE)
    agent = PolicyAgent(db, resolver)
    results = agent.review_plan(job_id)
    assert results[0].decision == PolicyDecision.AUTO_APPROVED


def test_forbidden_zone_returns_blocked(db):
    job_id = _make_job(db)
    _make_action(db, job_id, action_type='file_read', target_path=str(Path.home() / '.ssh' / 'config'))
    resolver = _make_resolver(ZoneType.FORBIDDEN)
    agent = PolicyAgent(db, resolver)
    results = agent.review_plan(job_id)
    assert results[0].decision == PolicyDecision.BLOCKED
    assert results[0].zone == ZoneType.FORBIDDEN


def test_read_only_write_returns_blocked(db):
    job_id = _make_job(db)
    _make_action(db, job_id, action_type='file_write')
    resolver = _make_resolver(ZoneType.READ_ONLY)
    agent = PolicyAgent(db, resolver)
    results = agent.review_plan(job_id)
    assert results[0].decision == PolicyDecision.BLOCKED


def test_read_only_read_returns_auto_approved(db):
    job_id = _make_job(db)
    _make_action(db, job_id, action_type='file_read')
    resolver = _make_resolver(ZoneType.READ_ONLY)
    agent = PolicyAgent(db, resolver)
    results = agent.review_plan(job_id)
    assert results[0].decision == PolicyDecision.AUTO_APPROVED


def test_restricted_non_cmd_returns_approval_required(db):
    job_id = _make_job(db)
    _make_action(db, job_id, action_type='file_write')
    resolver = _make_resolver(ZoneType.RESTRICTED)
    agent = PolicyAgent(db, resolver)
    results = agent.review_plan(job_id)
    assert results[0].decision == PolicyDecision.APPROVAL_REQUIRED


def test_cmd_execute_always_approval_required_even_in_safe_workspace(db):
    """CMD_EXECUTE in safe_workspace must still return approval_required (capability override)."""
    job_id = _make_job(db)
    _make_action(db, job_id, action_type='cmd_execute')
    resolver = _make_resolver(ZoneType.SAFE_WORKSPACE)
    agent = PolicyAgent(db, resolver)
    results = agent.review_plan(job_id)
    assert results[0].decision == PolicyDecision.APPROVAL_REQUIRED


def test_ambiguous_zone_returns_judgment_zone(db):
    job_id = _make_job(db)
    _make_action(db, job_id, action_type='file_write')
    resolver = _make_resolver(ZoneType.RESTRICTED, raises_ambiguous=True)
    agent = PolicyAgent(db, resolver)
    results = agent.review_plan(job_id)
    assert results[0].decision == PolicyDecision.JUDGMENT_ZONE


def test_review_plan_stamps_all_fields_on_actions(db):
    """review_plan() must persist policy_decision, policy_reason, target_zone on every action."""
    job_id = _make_job(db)
    _make_action(db, job_id, action_type='file_write', target_path='/tmp/a.txt')
    _make_action(db, job_id, action_type='file_read', target_path='/tmp/b.txt')
    resolver = _make_resolver(ZoneType.SAFE_WORKSPACE)
    agent = PolicyAgent(db, resolver)
    results = agent.review_plan(job_id)

    # All results have decisions
    assert len(results) == 2
    for r in results:
        assert r.decision is not None
        assert r.zone is not None
        assert r.reason is not None

    # DB rows are stamped
    persisted = get_actions_for_job(job_id, engine=db)
    for a in persisted:
        assert a.policy_decision is not None
        assert a.policy_reason is not None
        assert a.target_zone is not None
