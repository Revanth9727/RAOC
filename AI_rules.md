# AI_rules.md — Rules for Claude Code and for You
# Remote Autonomous OS Controller (RAOC)
# Version: Post-MVP
# Last updated: March 2026

---

## Purpose

This file has two sections.

**Section 1** is addressed to Claude Code.
It defines non-negotiable rules for how code is written in this project.

**Section 2** is addressed to you — the developer.
It defines how to work with Claude Code, GSD, and Superpowers effectively
during the build.

---

---

# SECTION 1: RULES FOR CLAUDE CODE

---

## RULE 1: Read before writing

Before writing any code, read CLAUDE.md, Architecture.md, and PRD.md.
Every decision flows from those documents. Do not assume structure, naming,
or scope. The CLAUDE.md file lists every agent, model, and file in the current
build. Read it carefully — the structure has grown significantly beyond the MVP.

---

## RULE 2: One component at a time

Never write more than one new file per prompt unless explicitly told to.
If a prompt asks for a component, write that component and its test file.
Nothing else.

---

## RULE 3: Always follow the agent pattern

Every agent class must follow this exact structure. No exceptions.

```python
class AgentName:
    def __init__(self, db, llm=None):
        self.db = db
        self.llm = llm

    def run(self, job_id: str) -> ResultType:
        """One-line docstring describing what this agent does."""
        try:
            # 1. Read job from db
            # 2. Update status to current stage
            # 3. Write audit: 'stage_started'
            # 4. Do the work
            # 5. Write results back to db
            # 6. Update status to next stage
            # 7. Write audit: 'stage_complete'
            # 8. Return result
        except ScopeViolationError as e:
            queries.update_job_status(job_id, JobStatus.BLOCKED, str(e))
            queries.write_audit(job_id, 'job_blocked', str(e))
            raise
        except Exception as e:
            queries.update_job_status(job_id, JobStatus.FAILED, str(e))
            queries.write_audit(job_id, 'job_failed', str(e))
            raise
```

**Agents never send messages to the gateway. Only coordinator sends messages.**

---

## RULE 4: Always import from config

Never hardcode a path, constant, model name, or limit.
Every value comes from raoc.config.

```python
# WRONG
model = 'claude-sonnet-4-5-20251001'
workspace = Path.home() / 'raoc_workspace'
narrator_model = 'claude-haiku-4-5-20251001'

# CORRECT
from raoc import config
model = config.LLM_MODEL
workspace = config.WORKSPACE
narrator_model = config.NARRATOR_MODEL
```

---

## RULE 5: Always use pathlib.Path for paths

Never use raw strings for file paths. Never use os.path.
Always use pathlib.Path.

---

## RULE 6: Always validate paths against approved zone

Any function accepting a file path must validate it is within the approved zone.
In the current build: use _assert_in_workspace().
Phase 1 onward: use ZoneResolver.resolve() via PolicyAgent.
A path outside the zone is a ScopeViolationError, not a FileNotFoundError.

---

## RULE 7: Never log secret values

Only log that a secret was accessed, never what the value is.

```python
# WRONG
logger.info(f"Using API key: {api_key}")

# CORRECT
logger.info("Secret accessed: anthropic_api_key")
```

---

## RULE 8: Use Python logging, never print()

All output goes through the Python logging module. No print() anywhere
in production code. print() is acceptable only inside test files for debugging.

---

## RULE 9: All Claude API calls use tool_use

Never ask Claude to return free-text JSON. Always use tool_use for structured
outputs. Always validate the response with a Pydantic model before using it.

---

## RULE 10: Coordinator is async — respect it

coordinator.py, handle_new_message(), handle_approval(), handle_clarification(),
and advance() are all `async def`. All coordinator methods must be awaited.

Narration calls use fire-and-forget:
```python
self._fire(self._narrate_and_send(stage, context))
await asyncio.sleep(0)  # yield to event loop
```

Execution step narration is synchronous (awaited before the action):
```python
await self._narrate_execution_step_sync(action)
# then run the action
```

Never use time.sleep() in coordinator.py. Always await asyncio.sleep().

---

## RULE 11: Timestamps on all backup and output filenames

All backup filenames and PDF→DOCX output filenames use job.created_at,
never datetime.now() at execution time.

```python
# CORRECT
from raoc import config
stem = config.make_timestamped_stem(src.name, job.created_at)
backup_path = config.BACKUPS_DIR / f"{stem}{src.suffix}.bak"
```

---

## RULE 12: File type detection by content, never by extension

host_sampler.extract_text_for_rewrite() is the only correct way to read
a file for rewriting. It detects file type by magic bytes and content.
Never gate on extension anywhere in the codebase.

---

## RULE 13: output_path is not always target_path

For PDF rewrites the output is written to output_path (.docx), not target_path
(.pdf). Verification, reporter, and any code that checks the written file
must use output_path from the job record or context_package.

---

## RULE 14: Always write tests in the same prompt

When writing a component, always write the test file for it.
Tests go in tests/test_{component}.py.
Tests must use tmp_path for any file system operations.
Tests must mock subprocess.run — never run real shell commands in tests.
Tests must mock anthropic.Anthropic — never make real API calls in tests.
Tests must NOT mock file type extractors — create real files in tmp_path
so the extraction logic is actually exercised.
Narration delays must be zeroed in tests via conftest.py autouse fixture.

---

## RULE 15: Run tests before reporting done

After writing code and tests:
```
uv run pytest tests/ -v
```
Only report "done" if all tests pass. If any test fails, fix it first.
The test count is a baseline — every prompt should add tests, never lose them.

---

## RULE 16: Error handling pattern

Every agent method must follow this pattern. Never swallow exceptions.
Always update job status before re-raising.

```python
def run(self, job_id: str):
    try:
        ...
    except ScopeViolationError as e:
        queries.update_job_status(job_id, JobStatus.BLOCKED, str(e))
        queries.write_audit(job_id, 'job_blocked', str(e))
        raise
    except Exception as e:
        queries.update_job_status(job_id, JobStatus.FAILED, str(e))
        queries.write_audit(job_id, 'job_failed', str(e))
        raise
```

---

## RULE 17: Database writes use transactions

```python
# CORRECT
with engine.begin() as conn:
    conn.execute(jobs.update().where(...).values(...))
```

Never use conn.execute() without engine.begin() or conn.begin().

---

## RULE 18: Docstrings on every function

Every function, method, and class must have a docstring.
Keep them short and factual. One to three sentences.

---

## RULE 19: Backward compatibility is non-negotiable

Every new phase must preserve all existing tests. The current baseline is
272 passing tests. That number must never go down when adding new code.
If a change breaks an existing test, fix the test or fix the code.
Never delete a passing test to make a build succeed.

---

## RULE 20: Do not add out-of-scope features

Each phase has a defined scope listed in PRD.md Section 10 and the Build Guide.
Do not build Phase 2 features during Phase 1, and so on.
If a prompt asks for something outside the current phase, refuse politely and
explain which document defines the scope boundary.

---

---

# SECTION 2: RULES FOR YOU — THE DEVELOPER

---

## RULE A: Use GSD for every phase

GSD (Get Shit Done) is the outer planning wrapper for every phase.

```bash
# Start of every phase:
/gsd:new-milestone
# Feed it the phase goal from the Build Guide document
# Answer GSD's questions based on "What Gets Built" section
# GSD creates PLAN.md and roadmap — review it before running any prompt
```

Use `/gsd:quick --full` for the preparation prompt of each phase.
Use `/gsd:status` if you lose track of where you are.
GSD commits atomically after each task. Let it.

---

## RULE B: Use Superpowers for every session

Superpowers is installed as a Claude Code plugin. It activates automatically.

Run `/brainstorming` at the start of every phase before running any prompt.
This refines the design before planning begins and catches assumptions early.

When a test fails, type `/debug` instead of guessing. Superpowers will follow
a four-phase debugging methodology before touching any fix.

Do not interrupt Superpowers when it spawns subagents — it is running code
review. Let it finish.

---

## RULE C: Check awesome-claude-code before each phase

Spend 5 minutes at: https://github.com/hesreallyhim/awesome-claude-code
or: https://awesomeclaude.ai/awesome-claude-code

Look for: hooks relevant to the phase, CLAUDE.md patterns, agent orchestration
examples (Phases 7+). Pick one useful thing per phase. Do not install
everything blindly — test in isolation first.

---

## RULE D: Always start a Claude Code session from the project root

```bash
cd ~/raoc
claude
```

If you start from a subfolder, Claude Code may not find CLAUDE.md.
Verify Claude Code knows the project: "What project is this? What phase are we on?"

---

## RULE E: One task per prompt

Resist asking for multiple things at once. One component per prompt.
The Build Guide has numbered prompts for a reason — run them in order.

---

## RULE F: Read every file before accepting it

Claude Code writes fast. Check every file it writes:
- Does it import from config.py, not hardcoded values?
- Does it follow the agent pattern from CLAUDE.md?
- Is coordinator still async? Are all coordinator methods async def?
- Does it use output_path (not target_path) where a PDF rewrite is involved?
- Did it add timestamps to backup filenames?
- Does the test file cover edge cases, not just the happy path?
- Did it add anything outside current phase scope?

---

## RULE G: Run tests yourself — do not trust Claude Code's claim

```bash
uv run pytest tests/ -v
```

If you see red, do not move to the next phase. Paste the failing test output
back to Claude Code and ask it to fix. GSD will track this automatically.

---

## RULE H: Update CLAUDE.md after every completed phase

After a phase passes all tests and phone tests:

```
## Current build phase
Phase X: [Name] — COMPLETE
Phase X+1: [Name] — NOT STARTED
```

Also update the folder structure section if new files were added.

---

## RULE I: Commit after each completed phase

GSD handles atomic commits per task. After a full phase passes all tests
and phone tests, tag it:

```bash
git tag -a vX.X.0 -m "Phase X complete: [component name]"
git push --tags
```

---

## RULE J: Never let Claude Code touch real files during testing

All tests use tmp_path. Never run execution agent against real files in
~/raoc_workspace/ until manual testing of that specific phase.
When doing manual tests, use throwaway files. Never test on a file you care about.

---

## RULE K: Test from your phone before calling a phase done

Unit tests prove the logic. A real message from your phone proves the wiring.
Run all three phone test cases from the Build Guide for each phase.
If phone tests fail but unit tests pass, the problem is in the wiring.
Check coordinator.py and main.py.

---

## RULE L: When Claude Code goes off-track, reset

Signs it has gone off-track:
- Adding features outside current phase scope
- Writing code that does not match the agent pattern
- Creating files in the wrong folders
- Not using async/await correctly in coordinator
- Making time.sleep() calls instead of asyncio.sleep()
- Forgetting to add timestamps to backup filenames

When this happens:
1. /clear to reset session context
2. "Read CLAUDE.md and Architecture.md. We are on Phase X. I need Y."
3. Do not try to salvage a session that has lost context

---

## RULE M: Keep prompts specific and bounded

**Good prompt:**
"Phase 1, Prompt 2: Build raoc/substrate/zone_resolver.py.
Read CLAUDE.md and Architecture.md first.
Build ZoneResolver class that loads zone_config.yaml and resolves any path
to a ZoneType. See Architecture.md section 3 for config.ZONE_CONFIG path.
Write tests in tests/test_zone_resolver.py using tmp_path.
Run tests before reporting done."

**Bad prompt:**
"Write the zone resolver"

---

## RULE N: Monitor token usage

After finishing a phase, check:
```
/cost
```

Fresh sessions with a good CLAUDE.md are often better than long sessions
where context degrades. If a session is getting expensive, finish the phase,
commit, and start fresh for the next phase.

---

## RULE O: Never store secrets anywhere except the Keychain

No .env files. No config files with secrets. No hardcoded keys.
The Keychain and (Phase 6 onward) the vault are the only places secrets live.

If you accidentally commit a secret:
1. Immediately revoke the key
2. Remove from git history using git filter-branch or BFG
3. Generate a new key and store it in the Keychain
