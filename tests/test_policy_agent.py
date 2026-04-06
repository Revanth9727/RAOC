"""Tests for raoc.agents.policy_agent.PolicyAgent.

Tests the scope-aware policy model:
    inside scope_root   → PolicyDecision(status='allowed')
    outside scope_root  → PolicyDecision(status='needs_approval')
    forbidden paths     → PolicyDecision(status='forbidden')
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from raoc import config
from raoc.agents.policy_agent import PolicyAgent
from raoc.db.queries import create_job, update_job_field
from raoc.db.schema import create_tables, get_engine
from raoc.models.action import ActionObject, ActionType
from raoc.models.scope import ScopeApproval
from raoc.substrate.zone_resolver import ZoneResolver


@pytest.fixture()
def db(tmp_path):
    engine = get_engine(db_path=tmp_path / 'test_policy.db')
    create_tables(engine)
    return engine


@pytest.fixture
def fake_workspace(tmp_path, monkeypatch):
    ws = tmp_path / 'raoc_workspace'
    ws.mkdir()
    monkeypatch.setattr(config, 'WORKSPACE', ws)
    return ws


def _make_action(job_id, target_path, action_type='file_write'):
    return ActionObject(
        job_id=job_id,
        step_index=0,
        action_type=action_type,
        risk_level='low',
        target_path=target_path,
        intent='Test action',
    )


# ── Inside scope → allowed ───────────────────────────────────────


def test_all_inside_scope_allowed(db, fake_workspace):
    """Actions inside scope_root return PolicyDecision(status='allowed')."""
    scope_dir = fake_workspace / 'documents'
    scope_dir.mkdir()
    job = create_job('test', engine=db)
    update_job_field(job.job_id, scope_root=str(scope_dir), engine=db)

    action = _make_action(job.job_id, str(scope_dir / 'notes.txt'))
    resolver = ZoneResolver()
    agent = PolicyAgent(db=db, zone_resolver=resolver)
    decision = agent.review_plan(job.job_id, [action])

    assert decision.status == 'allowed'


# ── Outside scope → needs_approval ───────────────────────────────


def test_outside_scope_needs_approval(db, fake_workspace):
    """Actions outside scope_root return PolicyDecision(status='needs_approval')."""
    scope_dir = fake_workspace / 'documents'
    scope_dir.mkdir()
    other_dir = fake_workspace / 'scripts'
    other_dir.mkdir()
    job = create_job('test', engine=db)
    update_job_field(job.job_id, scope_root=str(scope_dir), engine=db)

    action = _make_action(job.job_id, str(other_dir / 'run.py'))
    resolver = ZoneResolver()
    agent = PolicyAgent(db=db, zone_resolver=resolver)
    decision = agent.review_plan(job.job_id, [action])

    assert decision.status == 'needs_approval'
    assert 'run.py' in decision.path


# ── Forbidden → forbidden ────────────────────────────────────────


def test_forbidden_path_blocked(db, fake_workspace):
    """Actions targeting forbidden paths return PolicyDecision(status='forbidden')."""
    scope_dir = fake_workspace / 'documents'
    scope_dir.mkdir()
    job = create_job('test', engine=db)
    update_job_field(job.job_id, scope_root=str(scope_dir), engine=db)

    action = _make_action(job.job_id, str(Path.home() / '.ssh' / 'id_rsa'))
    resolver = ZoneResolver()
    agent = PolicyAgent(db=db, zone_resolver=resolver)
    decision = agent.review_plan(job.job_id, [action])

    assert decision.status == 'forbidden'
    assert '.ssh' in decision.reason


# ── Screenshot always allowed ────────────────────────────────────


def test_screenshot_always_allowed(db, fake_workspace):
    """SCREENSHOT actions are always allowed regardless of path."""
    scope_dir = fake_workspace / 'documents'
    scope_dir.mkdir()
    job = create_job('test', engine=db)
    update_job_field(job.job_id, scope_root=str(scope_dir), engine=db)

    action = _make_action(job.job_id, '', action_type='screenshot')
    resolver = ZoneResolver()
    agent = PolicyAgent(db=db, zone_resolver=resolver)
    decision = agent.review_plan(job.job_id, [action])

    assert decision.status == 'allowed'


# ── Approved path works ──────────────────────────────────────────


def test_previously_approved_path_allowed(db, fake_workspace):
    """If a path was previously approved, check_access returns 'allowed'."""
    scope_dir = fake_workspace / 'documents'
    scope_dir.mkdir()
    other_dir = fake_workspace / 'scripts'
    other_dir.mkdir()
    target = str(other_dir / 'run.py')
    job = create_job('test', engine=db)
    update_job_field(job.job_id, scope_root=str(scope_dir), engine=db)

    action = _make_action(job.job_id, target)
    approval = ScopeApproval(path=target, action='write', job_id=job.job_id)

    resolver = ZoneResolver()
    agent = PolicyAgent(db=db, zone_resolver=resolver)
    decision = agent.review_plan(job.job_id, [action], approved=[approval])

    assert decision.status == 'allowed'


# ── No target path → allowed ─────────────────────────────────────


def test_action_with_no_path_allowed(db, fake_workspace):
    """Actions with no target_path are allowed."""
    scope_dir = fake_workspace / 'documents'
    scope_dir.mkdir()
    job = create_job('test', engine=db)
    update_job_field(job.job_id, scope_root=str(scope_dir), engine=db)

    action = _make_action(job.job_id, '')
    resolver = ZoneResolver()
    agent = PolicyAgent(db=db, zone_resolver=resolver)
    decision = agent.review_plan(job.job_id, [action])

    assert decision.status == 'allowed'
