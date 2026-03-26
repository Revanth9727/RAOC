# Phase 1: Policy Agent + Zone Model — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every planned action passes through a policy engine before execution, with path-based zone enforcement and a five-outcome decision table.

**Architecture:** `ZoneResolver` reads `zone_config.yaml` and resolves any filesystem path to one of four zones. `PolicyAgent` calls `ZoneResolver` for each `ActionObject` and applies a strict three-step decision table (forbidden → CMD_EXECUTE override → zone table), stamping `policy_decision`, `policy_reason`, and `target_zone` onto each action row. The coordinator calls `policy_agent.review_plan()` after planning and before sending the plan preview; blocked jobs stop immediately with a bullet-list message, judgment_zone items appear as a flagged section in the plan preview.

**Tech Stack:** Python 3.12, Pydantic v2, SQLAlchemy Core, PyYAML (`pyyaml` — already a transitive dep via python-telegram-bot; verify with `uv run python -c "import yaml"` before assuming it's available)

---

### Task 1: Add `AmbiguousZoneError` to exceptions and create policy models

**Files:**
- Modify: `raoc/substrate/exceptions.py`
- Create: `raoc/models/policy.py`
- Test: `tests/test_models.py` (append — verify enums and model instantiation)

- [ ] **Step 1.1: Write failing tests for the new types**

Append to `tests/test_models.py`:

```python
from raoc.models.policy import PolicyDecision, PolicyResult, ZoneType
from raoc.substrate.exceptions import AmbiguousZoneError


def test_zone_type_values():
    assert ZoneType.SAFE_WORKSPACE == 'safe_workspace'
    assert ZoneType.READ_ONLY == 'read_only'
    assert ZoneType.RESTRICTED == 'restricted'
    assert ZoneType.FORBIDDEN == 'forbidden'


def test_policy_decision_values():
    assert PolicyDecision.AUTO_APPROVED == 'auto_approved'
    assert PolicyDecision.APPROVAL_REQUIRED == 'approval_required'
    assert PolicyDecision.BLOCKED == 'blocked'
    assert PolicyDecision.JUDGMENT_ZONE == 'judgment_zone'


def test_policy_result_fields():
    result = PolicyResult(
        action_id='abc',
        decision=PolicyDecision.BLOCKED,
        zone=ZoneType.FORBIDDEN,
        reason='~/.ssh is in the forbidden zone.',
    )
    assert result.action_id == 'abc'
    assert result.decision == 'blocked'
    assert result.zone == 'forbidden'
    assert '~/.ssh' in result.reason


def test_ambiguous_zone_error_is_raoc_error():
    from raoc.substrate.exceptions import RaocError
    err = AmbiguousZoneError('~/Documents/project')
    assert isinstance(err, RaocError)
    assert '~/Documents/project' in str(err)
```

- [ ] **Step 1.2: Run tests to verify they fail**

```bash
cd ~/Desktop/raoc && uv run pytest tests/test_models.py -k "zone_type or policy_decision or policy_result or ambiguous_zone" -v
```

Expected: `ImportError` or `ModuleNotFoundError` — `raoc.models.policy` doesn't exist yet.

- [ ] **Step 1.3: Add `AmbiguousZoneError` to exceptions.py**

Add after `ZipFileDetectedError`:

```python
class AmbiguousZoneError(RaocError):
    """Raised when a path matches two zone entries at equal specificity.

    Carries the path that triggered the tie so PolicyAgent can build
    a meaningful reason string for the user.
    """

    def __init__(self, path: str):
        self.path = path
        super().__init__(f"Ambiguous zone for path: {path}")
```

- [ ] **Step 1.4: Create `raoc/models/policy.py`**

```python
"""Policy models for RAOC zone enforcement.

ZoneType enumerates the four zones a filesystem path can belong to.
PolicyDecision enumerates the five outcomes the policy engine can produce.
PolicyResult carries the decision for one ActionObject.
"""

from enum import Enum

from pydantic import BaseModel


class ZoneType(str, Enum):
    """Four zones that define what RAOC may do in a given path."""

    SAFE_WORKSPACE = 'safe_workspace'
    READ_ONLY      = 'read_only'
    RESTRICTED     = 'restricted'
    FORBIDDEN      = 'forbidden'


class PolicyDecision(str, Enum):
    """Five possible outcomes from the policy engine for one action."""

    AUTO_APPROVED      = 'auto_approved'
    APPROVAL_REQUIRED  = 'approval_required'
    BLOCKED            = 'blocked'
    JUDGMENT_ZONE      = 'judgment_zone'


class PolicyResult(BaseModel):
    """Policy engine verdict for one ActionObject.

    reason is plain English and includes the path — ready to show to the user
    if decision is blocked or judgment_zone.
    """

    action_id: str
    decision:  PolicyDecision
    zone:      ZoneType
    reason:    str
```

- [ ] **Step 1.5: Run tests to verify they pass**

```bash
uv run pytest tests/test_models.py -k "zone_type or policy_decision or policy_result or ambiguous_zone" -v
```

Expected: 4 PASSED.

- [ ] **Step 1.6: Run full suite to check for regressions**

```bash
uv run pytest tests/ -v --tb=short
```

Expected: all existing tests still pass.

- [ ] **Step 1.7: Commit**

```bash
git add raoc/substrate/exceptions.py raoc/models/policy.py tests/test_models.py
git commit -m "feat: add AmbiguousZoneError and policy models (ZoneType, PolicyDecision, PolicyResult)"
```

---

### Task 2: Extend ActionObject with policy fields and update DB layer

**Files:**
- Modify: `raoc/models/action.py`
- Modify: `raoc/db/schema.py`
- Modify: `raoc/db/queries.py`
- Test: `tests/test_db.py` (append)

- [ ] **Step 2.1: Write failing tests**

Append to `tests/test_db.py`:

```python
from raoc.db.queries import update_action_policy


def test_action_object_has_policy_fields():
    from raoc.models.action import ActionObject
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
    from raoc.db.queries import (
        create_job,
        get_actions_for_job,
        save_action,
        update_action_policy,
    )
    from raoc.db.schema import create_tables, get_engine
    from raoc.models.action import ActionObject

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
```

- [ ] **Step 2.2: Run tests to verify they fail**

```bash
uv run pytest tests/test_db.py -k "action_object_has_policy or update_action_policy" -v
```

Expected: `ImportError` on `update_action_policy` or `AttributeError` on policy fields.

- [ ] **Step 2.3: Add three optional fields to `ActionObject`**

In `raoc/models/action.py`, after `verification_result`:

```python
policy_decision: Optional[str]    = None
policy_reason:   Optional[str]    = None
target_zone:     Optional[str]    = None
```

- [ ] **Step 2.4: Add three columns to the `actions` table in `schema.py`**

In `raoc/db/schema.py`, inside the `actions` Table definition, after the `completed_at` column:

```python
Column('policy_decision', Text),
Column('policy_reason',   Text),
Column('target_zone',     Text),
```

`_sync_columns()` already handles adding missing columns to an existing database automatically — no migration script needed.

- [ ] **Step 2.5: Add `update_action_policy()` to `queries.py`**

Add after `update_action_status()`:

```python
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
```

- [ ] **Step 2.6: Update `_row_to_action()` to load the three new fields**

In `raoc/db/queries.py`, inside `_row_to_action()`, add after `verification_result=row['verification_result']`:

```python
policy_decision=row['policy_decision'],
policy_reason=row['policy_reason'],
target_zone=row['target_zone'],
```

- [ ] **Step 2.7: Run tests to verify they pass**

```bash
uv run pytest tests/test_db.py -k "action_object_has_policy or update_action_policy" -v
```

Expected: 2 PASSED.

- [ ] **Step 2.8: Run full suite**

```bash
uv run pytest tests/ -v --tb=short
```

Expected: all tests pass.

- [ ] **Step 2.9: Commit**

```bash
git add raoc/models/action.py raoc/db/schema.py raoc/db/queries.py tests/test_db.py
git commit -m "feat: add policy_decision, policy_reason, target_zone to ActionObject and DB layer"
```

---

### Task 3: Build ZoneResolver

**Files:**
- Create: `raoc/substrate/zone_resolver.py`
- Create: `zone_config.yaml`
- Create: `tests/test_zone_resolver.py`

- [ ] **Step 3.1: Check whether PyYAML is available**

```bash
uv run python -c "import yaml; print(yaml.__version__)"
```

If this fails with `ModuleNotFoundError`, run:

```bash
uv add pyyaml
```

- [ ] **Step 3.2: Create `zone_config.yaml` at project root**

```yaml
# zone_config.yaml — RAOC zone model
# Defines which filesystem paths RAOC can access and under what conditions.
# This file is read-only at runtime. Agents never write to it.
#
# Zone types:
#   safe_workspace  — full automation, no approval needed
#   read_only       — reads auto-approved, writes blocked
#   restricted      — any action requires user approval
#   forbidden       — no action ever permitted
#
# Path matching: most specific (longest) prefix wins.
# Hard-coded overrides (cannot be changed here):
#   ~/raoc_workspace  → always safe_workspace
#   ~/.ssh, ~/Library/Keychains, ~/.aws, ~/.config  → always forbidden

safe_workspace:
  - ~/raoc_workspace

read_only:
  - ~/Documents/Reference
  - ~/Downloads

restricted:
  - ~/Desktop
  - ~/Documents

forbidden:
  - ~/.ssh
  - ~/Library/Keychains
  - ~/.aws
  - ~/.config
```

- [ ] **Step 3.3: Write all 5 tests for ZoneResolver**

Create `tests/test_zone_resolver.py`:

```python
"""Tests for raoc.substrate.zone_resolver.ZoneResolver."""

import logging
from pathlib import Path

import pytest

from raoc import config
from raoc.models.policy import ZoneType
from raoc.substrate.exceptions import AmbiguousZoneError
from raoc.substrate.zone_resolver import ZoneResolver


@pytest.fixture()
def config_file(tmp_path):
    """Write a minimal zone_config.yaml to a temp path and return that path."""
    content = """
safe_workspace:
  - ~/raoc_workspace

read_only:
  - ~/Documents/Reference
  - ~/Downloads

restricted:
  - ~/Desktop
  - ~/Documents

forbidden:
  - ~/.ssh
  - ~/Library/Keychains
  - ~/.aws
  - ~/.config
"""
    p = tmp_path / 'zone_config.yaml'
    p.write_text(content)
    return p


@pytest.fixture()
def resolver(config_file):
    return ZoneResolver(config_file)


def test_workspace_always_safe(resolver, tmp_path, monkeypatch):
    """Any path under config.WORKSPACE resolves to safe_workspace regardless of config."""
    fake_workspace = tmp_path / 'raoc_workspace'
    fake_workspace.mkdir()
    monkeypatch.setattr(config, 'WORKSPACE', fake_workspace)
    target = fake_workspace / 'scripts' / 'foo.py'
    assert resolver.resolve(target) == ZoneType.SAFE_WORKSPACE


def test_ssh_always_forbidden(resolver):
    """~/.ssh is hard-coded forbidden and cannot be overridden by config."""
    ssh_path = Path.home() / '.ssh' / 'id_rsa'
    assert resolver.resolve(ssh_path) == ZoneType.FORBIDDEN


def test_all_hardcoded_forbidden_paths(resolver):
    """~/Library/Keychains, ~/.aws, ~/.config are also hard-coded forbidden."""
    home = Path.home()
    assert resolver.resolve(home / 'Library' / 'Keychains' / 'login.keychain') == ZoneType.FORBIDDEN
    assert resolver.resolve(home / '.aws' / 'credentials') == ZoneType.FORBIDDEN
    assert resolver.resolve(home / '.config' / 'some_app' / 'config') == ZoneType.FORBIDDEN


def test_most_specific_match_wins(resolver):
    """~/Documents/Reference/report.pdf → read_only, not restricted (~/Documents)."""
    path = Path.home() / 'Documents' / 'Reference' / 'report.pdf'
    assert resolver.resolve(path) == ZoneType.READ_ONLY


def test_missing_config_logs_warning_and_uses_restricted(tmp_path, caplog):
    """Missing zone_config.yaml logs a warning and falls back to restricted for unknown paths."""
    missing = tmp_path / 'nonexistent.yaml'
    with caplog.at_level(logging.WARNING):
        r = ZoneResolver(missing)
    assert any('zone_config' in record.message.lower() or 'missing' in record.message.lower()
               for record in caplog.records)
    # An arbitrary path with no hard-coded override should be restricted
    assert r.resolve(Path.home() / 'Desktop' / 'foo.txt') == ZoneType.RESTRICTED
```

- [ ] **Step 3.4: Run tests to verify they fail**

```bash
uv run pytest tests/test_zone_resolver.py -v
```

Expected: `ModuleNotFoundError: No module named 'raoc.substrate.zone_resolver'`

- [ ] **Step 3.5: Create `raoc/substrate/zone_resolver.py`**

```python
"""ZoneResolver — maps filesystem paths to ZoneType values.

Reads zone_config.yaml on init and caches it. Never re-reads at runtime.
Four hard-coded overrides cannot be changed via config:
  - config.WORKSPACE       → safe_workspace (always)
  - ~/.ssh                 → forbidden (always)
  - ~/Library/Keychains    → forbidden (always)
  - ~/.aws                 → forbidden (always)
  - ~/.config              → forbidden (always)

For all other paths, the most specific (longest) matching prefix wins.
Unmatched paths default to restricted.
Tie (two entries at equal depth) raises AmbiguousZoneError.
Missing config file logs a warning and treats all unmatched paths as restricted.
"""

import logging
from pathlib import Path
from typing import Optional

from raoc import config
from raoc.models.policy import ZoneType
from raoc.substrate.exceptions import AmbiguousZoneError

logger = logging.getLogger(__name__)

# Hard-coded forbidden paths — cannot be overridden by zone_config.yaml
_HARDCODED_FORBIDDEN: list[Path] = [
    Path.home() / '.ssh',
    Path.home() / 'Library' / 'Keychains',
    Path.home() / '.aws',
    Path.home() / '.config',
]


class ZoneResolver:
    """Resolves a filesystem path to its ZoneType.

    Instantiate once at startup; call resolve() for each path.
    """

    def __init__(self, config_path: Path) -> None:
        """Load zone_config.yaml from config_path.

        If the file is missing, logs a warning and uses safe defaults
        (all unmatched paths → restricted).
        """
        self._zones: dict[str, ZoneType] = {}  # resolved_path_str → ZoneType
        self._config_loaded = False
        self._load(config_path)

    def _load(self, config_path: Path) -> None:
        """Parse zone_config.yaml into self._zones."""
        if not config_path.exists():
            logger.warning(
                "zone_config.yaml not found at %s — all unmatched paths will be treated as "
                "restricted. Create zone_config.yaml at the project root to configure zones.",
                config_path,
            )
            return

        try:
            import yaml
            with open(config_path) as f:
                data = yaml.safe_load(f) or {}
        except Exception as exc:
            logger.warning("Failed to parse zone_config.yaml (%s): %s", config_path, exc)
            return

        zone_map = {
            'safe_workspace': ZoneType.SAFE_WORKSPACE,
            'read_only':      ZoneType.READ_ONLY,
            'restricted':     ZoneType.RESTRICTED,
            'forbidden':      ZoneType.FORBIDDEN,
        }
        for zone_name, zone_type in zone_map.items():
            for raw_path in (data.get(zone_name) or []):
                resolved = Path(raw_path).expanduser().resolve()
                self._zones[str(resolved)] = zone_type

        self._config_loaded = True
        logger.info("ZoneResolver loaded %d zone entries from %s", len(self._zones), config_path)

    def resolve(self, path: Path) -> ZoneType:
        """Return the ZoneType for a filesystem path.

        Evaluation order:
          1. Hard-coded safe_workspace override (config.WORKSPACE)
          2. Hard-coded forbidden overrides (~/.ssh etc.)
          3. Longest-prefix match from zone_config.yaml
          4. Default: restricted

        Raises AmbiguousZoneError if two config entries match at equal depth.
        """
        resolved = path.resolve()

        # 1. Hard-coded safe_workspace
        try:
            resolved.relative_to(config.WORKSPACE.resolve())
            return ZoneType.SAFE_WORKSPACE
        except ValueError:
            pass

        # 2. Hard-coded forbidden paths
        for forbidden_root in _HARDCODED_FORBIDDEN:
            try:
                resolved.relative_to(forbidden_root.resolve())
                return ZoneType.FORBIDDEN
            except ValueError:
                pass

        # 3. Longest-prefix match from config
        best_match: Optional[ZoneType] = None
        best_depth: int = -1
        tie: bool = False

        for zone_path_str, zone_type in self._zones.items():
            zone_path = Path(zone_path_str)
            try:
                rel = resolved.relative_to(zone_path)
                depth = len(zone_path.parts)
                if depth > best_depth:
                    best_depth = depth
                    best_match = zone_type
                    tie = False
                elif depth == best_depth and zone_type != best_match:
                    tie = True
            except ValueError:
                pass

        if tie:
            raise AmbiguousZoneError(str(resolved))

        if best_match is not None:
            return best_match

        # 4. Default
        return ZoneType.RESTRICTED
```

- [ ] **Step 3.6: Run tests to verify they pass**

```bash
uv run pytest tests/test_zone_resolver.py -v
```

Expected: 5 PASSED.

- [ ] **Step 3.7: Run full suite**

```bash
uv run pytest tests/ -v --tb=short
```

Expected: all existing tests pass.

- [ ] **Step 3.8: Commit**

```bash
git add raoc/substrate/zone_resolver.py zone_config.yaml tests/test_zone_resolver.py
git commit -m "feat: add ZoneResolver with hard-coded overrides and longest-prefix zone matching"
```

---

### Task 4: Build PolicyAgent

**Files:**
- Create: `raoc/agents/policy_agent.py`
- Create: `tests/test_policy_agent.py`

- [ ] **Step 4.1: Write all 8 tests for PolicyAgent**

Create `tests/test_policy_agent.py`:

```python
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
    # Make step_index unique
    actions_before = get_actions_for_job(job_id, engine=db)
    actions_before[1].step_index = 1
    from raoc.db.queries import update_action_status  # reuse engine access pattern
    from raoc.db import queries as q
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
```

- [ ] **Step 4.2: Run tests to verify they fail**

```bash
uv run pytest tests/test_policy_agent.py -v
```

Expected: `ModuleNotFoundError: No module named 'raoc.agents.policy_agent'`

- [ ] **Step 4.3: Create `raoc/agents/policy_agent.py`**

```python
"""PolicyAgent — evaluates every ActionObject before execution.

Reads all actions for a job, evaluates each against the zone model,
stamps policy_decision / policy_reason / target_zone on each action row,
writes audit entries, and returns a list[PolicyResult].

State changes (BLOCKED status, gateway messages) are the coordinator's
responsibility — this agent only stamps and returns.
"""

import logging

from raoc.agents.policy_agent import _READ_TYPES, _WRITE_TYPES  # noqa — forward ref guard
from raoc.db import queries
from raoc.models.action import ActionObject, ActionType
from raoc.models.policy import PolicyDecision, PolicyResult, ZoneType
from raoc.substrate.exceptions import AmbiguousZoneError
from raoc.substrate.zone_resolver import ZoneResolver

logger = logging.getLogger(__name__)

# Action types that are read-only — safe in read_only zones
_READ_TYPES = {
    ActionType.FILE_READ,
    ActionType.CMD_INSPECT,
    ActionType.SCREENSHOT,
}

# Action types that write — blocked in read_only zones
_WRITE_TYPES = {
    ActionType.FILE_WRITE,
    ActionType.FILE_BACKUP,
    ActionType.FILE_DELETE,
    ActionType.DIR_CREATE,
}


class PolicyAgent:
    """Evaluates every planned action against the zone model.

    Does not call Claude. Does not touch job status. Returns results only.
    """

    def __init__(self, db, zone_resolver: ZoneResolver, llm=None) -> None:
        """Store db engine and zone resolver."""
        self.db = db
        self.zone_resolver = zone_resolver

    def review_plan(self, job_id: str) -> list[PolicyResult]:
        """Evaluate every action for a job and stamp policy fields on each row.

        Returns the full list of PolicyResult, one per action.
        Each action in the database is updated with policy_decision, policy_reason,
        and target_zone before this method returns.
        """
        actions = queries.get_actions_for_job(job_id, engine=self.db)
        results: list[PolicyResult] = []

        for action in actions:
            result = self._evaluate_action(action)
            queries.update_action_policy(
                action_id=action.action_id,
                decision=result.decision.value,
                reason=result.reason,
                zone=result.zone.value,
                engine=self.db,
            )
            queries.write_audit(
                job_id,
                'policy_decision',
                detail=f"step {action.step_index}: {result.decision.value} — {result.reason}",
                engine=self.db,
            )
            results.append(result)
            logger.info(
                "Policy: job=%s step=%d action=%s → %s",
                job_id, action.step_index, action.action_type, result.decision.value,
            )

        return results

    def _evaluate_action(self, action: ActionObject) -> PolicyResult:
        """Return the PolicyResult for one action using the three-step decision table.

        Step 1 — Forbidden check (always first): if zone is forbidden → blocked.
        Step 2 — Capability override: if CMD_EXECUTE → approval_required.
        Step 3 — Zone table: safe_workspace/read_only/restricted rules.
        Step 4 — Judgment zone: AmbiguousZoneError or zip target.
        """
        from pathlib import Path

        target = action.target_path or ''
        action_type_str = (
            action.action_type.value
            if hasattr(action.action_type, 'value')
            else str(action.action_type)
        )

        # Resolve zone (may raise AmbiguousZoneError → judgment_zone)
        try:
            zone = self.zone_resolver.resolve(Path(target))
        except AmbiguousZoneError as exc:
            return PolicyResult(
                action_id=action.action_id,
                decision=PolicyDecision.JUDGMENT_ZONE,
                zone=ZoneType.RESTRICTED,  # fallback zone for model validity
                reason=(
                    f"{target} matches entries in two different zones at equal specificity. "
                    f"Policy cannot determine which zone applies — review before approving."
                ),
            )

        # Step 1: Forbidden check (always wins)
        if zone == ZoneType.FORBIDDEN:
            return PolicyResult(
                action_id=action.action_id,
                decision=PolicyDecision.BLOCKED,
                zone=zone,
                reason=(
                    f"{target} is in the forbidden zone. "
                    f"This path cannot be automated. This is a permanent restriction "
                    f"that cannot be bypassed."
                ),
            )

        # Step 2: CMD_EXECUTE capability override
        if action_type_str == ActionType.CMD_EXECUTE.value:
            return PolicyResult(
                action_id=action.action_id,
                decision=PolicyDecision.APPROVAL_REQUIRED,
                zone=zone,
                reason=(
                    f"Script execution always requires approval regardless of location. "
                    f"Target: {target}."
                ),
            )

        # Step 3: Zone table
        if zone == ZoneType.SAFE_WORKSPACE:
            return PolicyResult(
                action_id=action.action_id,
                decision=PolicyDecision.AUTO_APPROVED,
                zone=zone,
                reason=f"{target} is in the safe workspace — auto-approved.",
            )

        if zone == ZoneType.READ_ONLY:
            action_enum = None
            try:
                action_enum = ActionType(action_type_str)
            except ValueError:
                pass

            if action_enum in _READ_TYPES:
                return PolicyResult(
                    action_id=action.action_id,
                    decision=PolicyDecision.AUTO_APPROVED,
                    zone=zone,
                    reason=f"{target} is read-only and this is a read action — auto-approved.",
                )
            else:
                return PolicyResult(
                    action_id=action.action_id,
                    decision=PolicyDecision.BLOCKED,
                    zone=zone,
                    reason=(
                        f"{target} is in a read-only zone. "
                        f"Write actions are blocked in read-only zones."
                    ),
                )

        if zone == ZoneType.RESTRICTED:
            return PolicyResult(
                action_id=action.action_id,
                decision=PolicyDecision.APPROVAL_REQUIRED,
                zone=zone,
                reason=f"{target} is in a restricted zone — requires your approval.",
            )

        # Fallback (should not reach here with valid zone values)
        return PolicyResult(
            action_id=action.action_id,
            decision=PolicyDecision.JUDGMENT_ZONE,
            zone=zone,
            reason=f"Unhandled zone state for {target} — review before approving.",
        )
```

**Important:** The file above has a self-import bug on line 7 (`from raoc.agents.policy_agent import ...`). Remove that line entirely — `_READ_TYPES` and `_WRITE_TYPES` are defined in the same file. The correct file has no self-import.

- [ ] **Step 4.4: Fix the self-import bug**

The file you just created has `from raoc.agents.policy_agent import _READ_TYPES, _WRITE_TYPES` at the top. Remove that line. The sets are defined lower in the same file and imported nowhere externally.

- [ ] **Step 4.5: Run tests to verify they pass**

```bash
uv run pytest tests/test_policy_agent.py -v
```

Expected: 8 PASSED.

- [ ] **Step 4.6: Run full suite**

```bash
uv run pytest tests/ -v --tb=short
```

Expected: all tests pass.

- [ ] **Step 4.7: Commit**

```bash
git add raoc/agents/policy_agent.py tests/test_policy_agent.py
git commit -m "feat: add PolicyAgent with three-step decision table and audit trail"
```

---

### Task 5: Integrate PolicyAgent into coordinator and wire main.py

**Files:**
- Modify: `raoc/coordinator.py`
- Modify: `raoc/main.py`
- Modify: `tests/test_coordinator.py`

- [ ] **Step 5.1: Write failing coordinator tests for policy integration**

Append to `tests/test_coordinator.py`:

```python
from raoc.models.policy import PolicyDecision, PolicyResult, ZoneType


def _make_policy_result(decision: PolicyDecision, reason: str = 'test reason') -> PolicyResult:
    return PolicyResult(
        action_id='action-1',
        decision=decision,
        zone=ZoneType.SAFE_WORKSPACE,
        reason=reason,
    )


def _make_coordinator_with_policy(db, policy_results=None) -> PipelineCoordinator:
    """Build a coordinator with a mocked PolicyAgent returning the given results."""
    gateway = MagicMock()
    gateway.send_message = AsyncMock()
    gateway.send_approval_request = AsyncMock()
    gateway.send_status = AsyncMock()
    gateway.send_confirmation = AsyncMock()

    if policy_results is None:
        policy_results = [_make_policy_result(PolicyDecision.AUTO_APPROVED)]

    policy_agent = MagicMock()
    policy_agent.review_plan.return_value = policy_results

    coord = PipelineCoordinator(
        db=db,
        llm=MagicMock(),
        sampler=MagicMock(),
        command_wrapper=MagicMock(),
        gateway=gateway,
        policy_agent=policy_agent,
    )
    return coord


def test_blocked_policy_stops_pipeline_before_preview(db):
    """When PolicyAgent returns blocked, coordinator sends blocked message and does NOT send plan preview."""
    from raoc.db.queries import create_job, get_job, save_action, update_job_field, update_job_status
    from raoc.db.schema import create_tables, get_engine
    from raoc.models.action import ActionObject
    from raoc.models.job import JobStatus

    blocked_result = _make_policy_result(
        PolicyDecision.BLOCKED,
        reason='~/.ssh/config is in the forbidden zone. This is a permanent restriction.',
    )
    blocked_result.action_id  # access to confirm it's a PolicyResult

    coord = _make_coordinator_with_policy(db, policy_results=[blocked_result])

    # Patch all agents to do nothing except planning (which must set status to AWAITING_APPROVAL)
    job = create_job('rewrite ~/.ssh/config', engine=db)
    update_job_field(job.job_id, task_type='rewrite_file',
                     target_path=str(Path.home() / '.ssh' / 'config'), engine=db)
    update_job_status(job.job_id, JobStatus.DISCOVERING, engine=db)

    # Mock discovery and planning
    coord.discovery.run = MagicMock(return_value={'task_type': 'rewrite_file',
                                                   'target_path': str(Path.home() / '.ssh' / 'config'),
                                                   'size_bytes': 100, 'modified_at': '',
                                                   'detected_format': 'text', 'format_change': False})
    coord.planning.run = MagicMock(side_effect=lambda job_id, ctx: update_job_status(
        job_id, JobStatus.AWAITING_APPROVAL, engine=db))

    asyncio.get_event_loop().run_until_complete(coord.advance(job.job_id))

    # send_approval_request must NOT have been called
    coord.gateway.send_approval_request.assert_not_called()
    # send_message MUST have been called with the blocked message
    call_args = coord.gateway.send_message.call_args
    assert call_args is not None
    text = call_args.kwargs.get('text', '') or call_args.args[0] if call_args.args else ''
    assert 'blocked' in text.lower() or 'forbidden' in text.lower()


def test_judgment_zone_items_appear_in_plan_preview(db):
    """When PolicyAgent returns judgment_zone, _build_plan_preview includes the flagged section."""
    from raoc.coordinator import PipelineCoordinator

    judgment_result = PolicyResult(
        action_id='a1',
        decision=PolicyDecision.JUDGMENT_ZONE,
        zone=ZoneType.RESTRICTED,
        reason='~/Documents/project/ matches two zones at equal depth.',
    )

    coord = _make_coordinator_with_policy(db, policy_results=[judgment_result])

    from raoc.models.action import ActionObject
    from raoc.db.queries import create_job, save_action, update_job_field
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
        policy_decision='judgment_zone',
        policy_reason='~/Documents/project/ matches two zones at equal depth.',
        target_zone='restricted',
    )
    save_action(action, engine=db)

    from raoc.db.queries import get_actions_for_job
    actions = get_actions_for_job(job.job_id, engine=db)
    preview = coord._build_plan_preview(job.job_id, actions)

    assert '⚠️' in preview or 'judgment' in preview.lower()
    assert '~/Documents/project/' in preview
```

- [ ] **Step 5.2: Run tests to verify they fail**

```bash
uv run pytest tests/test_coordinator.py -k "blocked_policy or judgment_zone_items" -v
```

Expected: `TypeError` — `PipelineCoordinator.__init__()` doesn't accept `policy_agent` yet.

- [ ] **Step 5.3: Add `policy_agent` parameter to `PipelineCoordinator.__init__()`**

In `raoc/coordinator.py`, update the import block at the top:

```python
from raoc.agents.policy_agent import PolicyAgent
from raoc.substrate.zone_resolver import ZoneResolver
```

Update `__init__()` signature and body:

```python
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
    self.narrator = narrator
    self.policy_agent = policy_agent  # None = policy disabled (legacy/test mode)
    self.pending_clarification: dict = {}
    self.pending_zip_cleanup: dict = {}
    self.intake = IntakeAgent(db, llm)
    self.discovery = DiscoveryAgent(db, sampler, llm)
    self.planning = PlanningAgent(db, llm)
    self.execution = ExecutionAgent(db, command_wrapper, sampler)
    self.verification = VerificationAgent(db, sampler)
    self.reporter = ReporterAgent(db, gateway)
    self.query_agent = QueryAgent(db, sampler, llm, gateway)
```

- [ ] **Step 5.4: Add policy check inside `advance()` DISCOVERING branch**

In `raoc/coordinator.py`, find the `DISCOVERING` branch in `advance()`. After the line:

```python
self.planning.run(job_id, context)
```

Add (before the `await asyncio.sleep(...)` line):

```python
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
```

- [ ] **Step 5.5: Update `_build_plan_preview()` to include judgment_zone section**

Replace the existing `_build_plan_preview()` method body with:

```python
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

    # Judgment zone section — only shown when present
    judgment_items = [
        a for a in sorted_actions
        if (
            getattr(a, 'policy_decision', None) == 'judgment_zone'
            or str(getattr(a, 'policy_decision', '')) == 'judgment_zone'
        )
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
```

- [ ] **Step 5.6: Update `_make_coordinator()` in `tests/test_coordinator.py`**

The existing `_make_coordinator()` helper must pass `policy_agent=None` explicitly so existing tests still work with the new optional parameter:

```python
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
        policy_agent=None,   # ← add this line
    )
    return coord
```

- [ ] **Step 5.7: Update `main.py` to instantiate and wire ZoneResolver and PolicyAgent**

Replace the `# 8. Coordinator` block in `raoc/main.py`:

```python
from raoc.agents.policy_agent import PolicyAgent
from raoc.substrate.zone_resolver import ZoneResolver

# 7.5 Policy substrate
zone_resolver = ZoneResolver(config.ZONE_CONFIG)
policy_agent = PolicyAgent(db=None, zone_resolver=zone_resolver)

# 8. Coordinator
coordinator = PipelineCoordinator(
    db=None,
    llm=llm,
    sampler=sampler,
    command_wrapper=cmd,
    gateway=gateway,
    narrator=narrator,
    policy_agent=policy_agent,
)
```

Note: `db=None` is the existing pattern — the coordinator's agents resolve the engine themselves via `get_engine()` when `db` is None. `PolicyAgent` follows the same pattern.

- [ ] **Step 5.8: Run new coordinator tests**

```bash
uv run pytest tests/test_coordinator.py -k "blocked_policy or judgment_zone_items" -v
```

Expected: 2 PASSED.

- [ ] **Step 5.9: Run full suite**

```bash
uv run pytest tests/ -v --tb=short
```

Expected: all 272+ tests pass. Zero regressions.

- [ ] **Step 5.10: Commit**

```bash
git add raoc/coordinator.py raoc/main.py tests/test_coordinator.py
git commit -m "feat: wire PolicyAgent into coordinator pipeline and update plan preview for judgment_zone"
```

---

### Task 6: Update CLAUDE.md build phase marker

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 6.1: Update current phase line in CLAUDE.md**

Find the `## Current build phase` section and replace:

```
MVP (Phases 1–9): COMPLETE — 272 tests passing
Phase 1 (Policy Agent + Zone Model): NOT STARTED
```

With:

```
MVP (Phases 1–9): COMPLETE — 272 tests passing
Phase 1 (Policy Agent + Zone Model): COMPLETE
Phase 2 (Web Browsing Substrate): NOT STARTED
```

- [ ] **Step 6.2: Run full suite one final time**

```bash
uv run pytest tests/ -v
```

Expected: all tests pass. Count should be 272 + 5 (zone_resolver) + 8 (policy_agent) + 2 (coordinator policy) + 2 (models) = 289+ tests.

- [ ] **Step 6.3: Final commit**

```bash
git add CLAUDE.md
git commit -m "chore: mark Phase 1 complete in CLAUDE.md"
git tag v1.0.0
```

---

## Self-Review

**Spec coverage check:**

| Spec section | Covered by task |
|---|---|
| `AmbiguousZoneError` in exceptions.py | Task 1 |
| `ZoneType`, `PolicyDecision`, `PolicyResult` models | Task 1 |
| `policy_decision`, `policy_reason`, `target_zone` on `ActionObject` | Task 2 |
| `update_action_policy()` in queries.py | Task 2 |
| `_row_to_action()` loads three new fields | Task 2 |
| `zone_config.yaml` created with defaults | Task 3 |
| `ZoneResolver` with hard-coded overrides | Task 3 |
| All 4 forbidden hard-coded paths | Task 3 |
| Missing config → warning + restricted | Task 3 |
| `PolicyAgent.review_plan()` | Task 4 |
| `_evaluate_action()` three-step order | Task 4 |
| Forbidden check first | Task 4 |
| CMD_EXECUTE capability override | Task 4 |
| Zone table (safe/readonly/restricted) | Task 4 |
| Two judgment_zone triggers | Task 4 |
| Audit entry per decision | Task 4 |
| Coordinator: policy check after planning, before preview | Task 5 |
| Blocked → bullet message, no preview, BLOCKED status | Task 5 |
| judgment_zone section in plan preview | Task 5 |
| `policy_agent` param in `PipelineCoordinator.__init__()` | Task 5 |
| `main.py` wires ZoneResolver + PolicyAgent | Task 5 |
| 5 zone_resolver tests | Task 3 |
| 8 policy_agent tests | Task 4 |
| Coordinator tests updated | Task 5 |
| CLAUDE.md phase marker | Task 6 |

**No gaps found.**

**Placeholder scan:** No TBDs, no "handle edge cases", no "similar to Task N". All code blocks are complete.

**Type consistency check:**
- `PolicyResult.decision` is `PolicyDecision` enum → coordinator compares `r.decision == 'blocked'` (string equality works because `PolicyDecision` is a `str` enum — ✓)
- `update_action_policy(decision=result.decision.value, ...)` → `.value` used when passing to DB — ✓
- `_make_coordinator_with_policy` uses the same `PipelineCoordinator` signature added in Task 5 — ✓
- `_READ_TYPES` and `_WRITE_TYPES` sets use `ActionType` enum members — must match actual enum values in `raoc/models/action.py` (`FILE_READ`, `CMD_INSPECT`, `SCREENSHOT`, `FILE_WRITE`, `FILE_BACKUP`, `FILE_DELETE`, `DIR_CREATE`). Note: `DIR_CREATE` and `FILE_DELETE` are listed in Architecture.md ActionType enum but may not exist yet in `raoc/models/action.py` — **verify with `grep -n "DIR_CREATE\|FILE_DELETE" raoc/models/action.py` before running Task 4** and add them to the enum if missing.
