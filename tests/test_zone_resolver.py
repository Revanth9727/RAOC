"""Tests for raoc.substrate.zone_resolver.ZoneResolver.

Tests the scope-aware access model:
    inside scope_root  → 'allowed'
    outside scope_root → 'needs_approval'
    forbidden paths    → 'forbidden'
"""

from pathlib import Path

import pytest

from raoc import config
from raoc.substrate.zone_resolver import ZoneResolver


@pytest.fixture
def resolver():
    return ZoneResolver()


@pytest.fixture
def fake_workspace(tmp_path, monkeypatch):
    ws = tmp_path / 'raoc_workspace'
    ws.mkdir()
    monkeypatch.setattr(config, 'WORKSPACE', ws)
    return ws


# ── is_inside_scope ──────────────────────────────────────────────


def test_path_inside_scope_root(resolver, fake_workspace):
    """A path inside scope_root is inside scope."""
    scope_root = fake_workspace / 'documents'
    scope_root.mkdir()
    target = scope_root / 'notes.txt'
    assert resolver.is_inside_scope(target, scope_root) is True


def test_path_outside_scope_root(resolver, fake_workspace):
    """A path outside scope_root is not inside scope."""
    scope_root = fake_workspace / 'documents'
    scope_root.mkdir()
    other = fake_workspace / 'scripts'
    other.mkdir()
    target = other / 'run.py'
    assert resolver.is_inside_scope(target, scope_root) is False


# ── is_inside_workspace ──────────────────────────────────────────


def test_workspace_path_is_in_workspace(resolver, fake_workspace):
    target = fake_workspace / 'notes.txt'
    assert resolver.is_inside_workspace(target) is True


def test_path_outside_workspace_is_not(resolver, fake_workspace):
    target = Path.home() / 'Documents' / 'secret.txt'
    assert resolver.is_inside_workspace(target) is False


# ── is_forbidden ─────────────────────────────────────────────────


def test_ssh_is_forbidden(resolver):
    target = Path.home() / '.ssh' / 'id_rsa'
    assert resolver.is_forbidden(target) is True


def test_etc_is_forbidden(resolver):
    target = Path('/etc/passwd')
    assert resolver.is_forbidden(target) is True


def test_workspace_is_not_forbidden(resolver, fake_workspace):
    target = fake_workspace / 'notes.txt'
    assert resolver.is_forbidden(target) is False


# ── check_access ─────────────────────────────────────────────────


def test_inside_scope_allowed(resolver, fake_workspace):
    scope_root = fake_workspace / 'documents'
    scope_root.mkdir()
    target = scope_root / 'notes.txt'
    assert resolver.check_access(target, scope_root, 'read') == 'allowed'


def test_outside_scope_needs_approval(resolver, fake_workspace):
    scope_root = fake_workspace / 'documents'
    scope_root.mkdir()
    other = fake_workspace / 'scripts'
    other.mkdir()
    target = other / 'run.py'
    assert resolver.check_access(target, scope_root, 'read') == 'needs_approval'


def test_forbidden_always_forbidden(resolver, fake_workspace):
    scope_root = fake_workspace / 'documents'
    scope_root.mkdir()
    target = Path.home() / '.ssh' / 'id_rsa'
    assert resolver.check_access(target, scope_root, 'read') == 'forbidden'


# ── assert_allowed ───────────────────────────────────────────────


def test_assert_allowed_does_not_raise_for_workspace_path(resolver, fake_workspace):
    target = fake_workspace / 'notes.txt'
    resolver.assert_allowed(target)  # must not raise


def test_assert_allowed_raises_for_forbidden_path(resolver, fake_workspace):
    from raoc.substrate.exceptions import ScopeViolationError
    target = Path.home() / '.ssh' / 'id_rsa'
    with pytest.raises(ScopeViolationError):
        resolver.assert_allowed(target)


def test_assert_allowed_raises_for_outside_workspace(resolver, fake_workspace):
    from raoc.substrate.exceptions import ScopeViolationError
    target = Path.home() / 'Documents' / 'secret.txt'
    with pytest.raises(ScopeViolationError):
        resolver.assert_allowed(target)
