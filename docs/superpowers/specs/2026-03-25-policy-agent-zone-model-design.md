# Design: Policy Agent + Zone Model
# RAOC Phase 1
# Date: 2026-03-25

---

## 1. Goal

Every planned action passes through a policy engine before execution. No action
reaches the approval flow — or the user — without a policy decision attached.
The zone model defines what areas of the machine RAOC can touch and under what
conditions. The policy agent enforces those rules consistently before any plan
preview is sent.

---

## 2. New Files

```
raoc/models/policy.py          — ZoneType, PolicyDecision, PolicyResult
raoc/substrate/zone_resolver.py — ZoneResolver
raoc/agents/policy_agent.py    — PolicyAgent
zone_config.yaml               — zone assignments (project root)
tests/test_zone_resolver.py
tests/test_policy_agent.py
```

`raoc/substrate/exceptions.py` gains one new exception:

```python
class AmbiguousZoneError(RaocError): ...   # path matches two zones at equal depth
```

---

## 3. Data Models (`raoc/models/policy.py`)

### ZoneType (str enum)
```
safe_workspace    — ~/raoc_workspace and subdirs. Full automation allowed.
read_only         — Reads auto-approved, writes blocked.
restricted        — Any action requires explicit user approval.
forbidden         — No action permitted. Ever.
```

### PolicyDecision (str enum)
```
auto_approved      — Policy cleared this action. No user sign-off needed.
approval_required  — Policy knows exactly what this action does. Needs user sign-off.
blocked            — Action is not permitted. Job stops here.
judgment_zone      — Policy cannot determine zone or full effect. Escalates to user.
```

### PolicyResult (Pydantic model)
```
action_id:  str
decision:   PolicyDecision
zone:       ZoneType
reason:     str   — plain English, includes path. Shown to user if blocked or judgment_zone.
```

---

## 4. ActionObject Changes (`raoc/models/action.py`)

Three new optional fields added to `ActionObject`:

```python
policy_decision: Optional[str] = None
policy_reason:   Optional[str] = None
target_zone:     Optional[str] = None
```

`queries.save_action()` and `queries._row_to_action()` updated to persist and
load all three fields from the `actions` table (columns already exist in schema).

---

## 5. ZoneResolver (`raoc/substrate/zone_resolver.py`)

```python
class ZoneResolver:
    def __init__(self, config_path: Path): ...
    def resolve(self, path: Path) -> ZoneType: ...
```

### Loading
- Reads `zone_config.yaml` at `config.ZONE_CONFIG` on init.
- If the file is missing: logs a warning explicitly, then falls back to `restricted`
  for all unmatched paths.
- Config is read once and cached. Never re-read at runtime.
- This file is read-only at runtime — agents never write to it.

### Resolving
1. Resolve input to absolute path.
2. Apply hard-coded overrides (cannot be overridden by config):
   - Any path under `config.WORKSPACE` → `safe_workspace`
   - Any path under `~/.ssh` → `forbidden`
   - Any path under `~/Library/Keychains` → `forbidden`
   - Any path under `~/.aws` → `forbidden`
   - Any path under `~/.config` → `forbidden`
3. Find longest matching prefix across all config entries. Most specific match wins.
4. If two entries match at equal depth (tie) → caller receives signal to use
   `judgment_zone`. ZoneResolver raises `AmbiguousZoneError` for the coordinator
   and PolicyAgent to handle.
5. Unmatched paths → `restricted`.

### zone_config.yaml defaults
```yaml
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

---

## 6. PolicyAgent (`raoc/agents/policy_agent.py`)

```python
class PolicyAgent:
    def __init__(self, db, zone_resolver: ZoneResolver, llm=None): ...
    def review_plan(self, job_id: str) -> list[PolicyResult]: ...
    def _evaluate_action(self, action: ActionObject) -> PolicyResult: ...
```

### review_plan()
1. Reads all `ActionObject`s for the job from the database.
2. Calls `_evaluate_action()` on each.
3. Stamps `policy_decision`, `policy_reason`, and `target_zone` onto each action
   via `queries.update_action_policy(action_id, decision, reason, zone)` — an
   UPDATE on the existing row, not an INSERT. PlanningAgent already inserted the
   action; PolicyAgent only writes these three fields.
4. Writes one audit entry per decision: `'policy_decision'`.
5. Returns the full `list[PolicyResult]`.

State changes (BLOCKED status, gateway messages) happen in the coordinator, not here.

### _evaluate_action() — decision order

Evaluation is strict and sequential. First match wins.

**Step 1 — Forbidden check (always first)**
If `zone_resolver.resolve(action.target_path)` returns `forbidden`:
→ return `blocked`
Reason includes path: e.g. `"~/.ssh/config is in the forbidden zone. SSH config
files cannot be automated. This is a permanent restriction."`

**Step 2 — Capability override**
If `action.action_type == ActionType.CMD_EXECUTE`:
→ return `approval_required`
Reason: `"Script execution always requires approval regardless of location."`
(Zone is still resolved and recorded, but does not affect the decision.)

**Step 3 — Zone table**

| Zone           | Action type                                          | Decision           |
|----------------|------------------------------------------------------|--------------------|
| `safe_workspace` | any                                                | `auto_approved`    |
| `read_only`    | `FILE_READ`, `CMD_INSPECT`, `SCREENSHOT`             | `auto_approved`    |
| `read_only`    | `FILE_WRITE`, `FILE_BACKUP`, `FILE_DELETE`, `DIR_CREATE` | `blocked`      |
| `restricted`   | any                                                  | `approval_required`|

**Step 4 — Judgment zone (exactly two triggers)**
- (a) Path matches entries in two different zones at the same depth — tie that
  most-specific-wins cannot resolve. `ZoneResolver` raises `AmbiguousZoneError`.
- (b) Action targets a zip or archive file where the full extraction destination
  is unknown at policy evaluation time.

Reason includes specific description: e.g. `"~/Documents/project/ matches both
'restricted' and an inner path at equal depth. Cannot determine which zone applies."`

---

## 7. Coordinator Integration (`coordinator.py`)

Policy check slots into `advance()` inside the `DISCOVERING` branch, after
`planning.run()` and before the plan preview is sent.

```python
# After planning.run(job_id, context):
policy_results = self.policy_agent.review_plan(job_id)

blocked = [r for r in policy_results if r.decision == 'blocked']
if blocked:
    bullet_lines = "\n".join(f"• {r.reason}" for r in blocked)
    message = f"Job blocked by policy. Nothing will execute.\n\n{bullet_lines}"
    queries.update_job_status(job_id, JobStatus.BLOCKED, engine=self.db)
    queries.write_audit(job_id, 'job_blocked', detail=message, engine=self.db)
    _fire(self.gateway.send_message(text=message))
    return  # no plan preview sent, pipeline stops

# Otherwise: build plan preview (with judgment_zone section if present)
# then send approval request as normal
```

`PipelineCoordinator.__init__()` gains a `policy_agent` parameter.
`main.py` instantiates `ZoneResolver(config.ZONE_CONFIG)` and
`PolicyAgent(db, zone_resolver)` and passes them in.

---

## 8. Plan Preview Changes (`_build_plan_preview()`)

When `judgment_zone` results are present, a separate section is appended before
the approve/deny line:

```
Task: rewrite_file
Target: ~/Documents/project/notes.txt
Steps: 3

  1. Back up notes.txt
  2. Rewrite notes.txt
  3. Verify output

⚠️ Needs your judgment (2 items):
  • Step 1 — ~/Documents/project/ matches both 'restricted' and an inner path
    at equal depth. Cannot determine which zone applies.
  • Step 3 — target is inside a zip archive; extraction destination is unknown.

Approve to execute or Deny to cancel.
```

All steps appear in the main numbered list (the user needs to see the full
execution plan). Judgment_zone items additionally appear in the flagged section
with their specific reason. The user approves or denies the entire plan once —
no per-step interruptions.

---

## 9. Tests

### tests/test_zone_resolver.py (5 tests)
1. Path under `config.WORKSPACE` → `safe_workspace` regardless of config
2. Path under `~/.ssh` → `forbidden` regardless of config
3. Path under `~/Library/Keychains`, `~/.aws`, `~/.config` → `forbidden`
4. Most-specific-match: `~/Documents/Reference/report.pdf` → `read_only`,
   not `restricted` (~/Documents)
5. Missing `zone_config.yaml` → logs warning, returns `restricted` for unmatched path

### tests/test_policy_agent.py (8 tests)
1. `safe_workspace` path + `FILE_WRITE` → `auto_approved`
2. `forbidden` path + any action → `blocked`
3. `read_only` path + `FILE_WRITE` → `blocked`
4. `read_only` path + `FILE_READ` → `auto_approved`
5. `restricted` path + any non-CMD_EXECUTE action → `approval_required`
6. `CMD_EXECUTE` in any zone (including `safe_workspace`) → `approval_required`
7. Tie at equal depth → `judgment_zone`
8. `review_plan()` stamps all three fields on every action and returns full list

All tests use `tmp_path`. Never touch real workspace or `~/.ssh`.

---

## 10. Phone Test Cases (from Plan.md)

**Test 1 (Easy):** Rewrite `~/raoc_workspace/notes.txt`
→ All actions `auto_approved`. Plan preview shows no judgment section. Executes normally.

**Test 2 (Medium):** Rewrite file in `~/Documents`
→ Actions show `approval_required` (restricted zone). Plan preview shows this.
User approves. Executes.

**Test 3 (Complex):** Rewrite `~/.ssh/config`
→ `forbidden` zone. Job blocked before plan preview. User receives:
`"Job blocked by policy. Nothing will execute.\n\n• ~/.ssh/config is in the
forbidden zone. SSH config files cannot be automated. This is a permanent
restriction that cannot be bypassed."`
Nothing executes.
