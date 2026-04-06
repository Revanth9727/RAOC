# CLAUDE.md
# Remote Autonomous OS Controller (RAOC)
# Read this file at the start of every session before doing anything.
# Last updated: March 2026

---

## What this project is

A governed autonomous agent that receives natural language instructions
via Telegram, interprets them, builds an execution plan, shows the plan for
human approval, executes on a real Mac only after approval, verifies outcomes,
and sends back an evidence report with screenshots and before/after file state.

This is not a chatbot. It is a secure remote operator with a policy engine,
scoped authority, dynamic planning, and a full audit trail.

The system is evolving from a two-task MVP toward a general-purpose autonomous
operator capable of handling any computer task: file operations, web browsing,
email, calendar, media processing, code tasks, and more.

---

## Current task types

1. **run_script** — Run an existing .py or .sh script, OR write and run a script based on instruction.
2. **rewrite_file** — Read any supported file, rewrite it based on a natural language instruction, back up the original, save the new version.
3. **query** — Answer information questions about workspace contents. No approval needed. Read-only.
4. **query_action** — Find a file by content or metadata, confirm with user, then act on it.

**Next phases will add open-ended goals handled by a dynamic planner and tool registry.**
**Do not add features outside the current phase scope without explicit instruction.**

---

## Tech stack

- Language: Python 3.12
- Package manager: uv
- LLM: Anthropic Claude via `anthropic` SDK
  - Planning/intake model: `claude-sonnet-4-5-20251001` (config.LLM_MODEL)
  - Narration model: `claude-haiku-4-5-20251001` (config.NARRATOR_MODEL)
- Database: SQLite via SQLAlchemy Core — file at `~/raoc/data/raoc.db`
- Schema validation: Pydantic v2
- Messaging gateway: `python-telegram-bot` (async)
- Process inspection: `psutil`
- Screenshots: `pyautogui` + `Pillow`
- Secrets: macOS Keychain via `keyring`
- PDF (text): `pdfplumber`, `pymupdf`, `pdf2docx`
- PDF (OCR): `pytesseract`
- DOCX: `python-docx`
- Encoding detection: `chardet`

---

## Folder structure

```
~/raoc/                        ← project root (always run Claude Code from here)
├── CLAUDE.md                  ← this file
├── PRD.md                     ← what the product does and acceptance criteria
├── Architecture.md            ← folder structure, schemas, data flow
├── AI_rules.md                ← rules for Claude Code and for the developer
├── Plan.md                    ← build checklist, tick as you go
├── zone_config.yaml           ← zone model config (Phase 1 onward)
├── pyproject.toml
│
├── raoc/
│   ├── __init__.py
│   ├── config.py              ← ALL paths and constants — import from here always
│   ├── coordinator.py         ← async pipeline state router
│   ├── main.py                ← entry point
│   │
│   ├── models/
│   │   ├── job.py             ← JobRecord, JobStatus (includes SEARCHING, CONFIRMING, PAUSED)
│   │   ├── action.py          ← ActionObject, ActionType (includes web + MCP types)
│   │   ├── task.py            ← TaskObject (includes query, query_action types)
│   │   ├── policy.py          ← PolicyDecision, ZoneType, PolicyResult
│   │   ├── web.py             ← WebPageContent, WebExtractionResult
│   │   └── mcp.py             ← McpServer, McpToolResult
│   │
│   ├── db/
│   │   ├── schema.py          ← SQLAlchemy table definitions + create_tables()
│   │   └── queries.py         ← all database read/write functions
│   │
│   ├── agents/
│   │   ├── intake.py          ← IntakeAgent (classifies run_script, rewrite_file, query, query_action)
│   │   ├── discovery.py       ← DiscoveryAgent (content-based file type detection)
│   │   ├── planning.py        ← PlanningAgent
│   │   ├── execution.py       ← ExecutionAgent
│   │   ├── verification.py    ← VerificationAgent
│   │   ├── reporter.py        ← ReporterAgent
│   │   ├── query_agent.py     ← QueryAgent (metadata-first, content search fallback)
│   │   ├── policy_agent.py    ← PolicyAgent (Phase 1 onward)
│   │   ├── tool_registry.py   ← ToolRegistry (Phase 3 onward)
│   │   ├── goal_interpreter.py ← GoalInterpreter (Phase 3 onward)
│   │   └── dynamic_planner.py ← DynamicPlanner (Phase 3 onward)
│   │
│   ├── substrate/
│   │   ├── command_wrapper.py ← safe shell execution
│   │   ├── host_sampler.py    ← file system and process state, content-based extraction
│   │   ├── screenshot.py      ← screenshot capture
│   │   ├── secret_broker.py   ← macOS Keychain access
│   │   ├── vault.py           ← extended per-service credential vault (Phase 6 onward)
│   │   ├── llm_client.py      ← Anthropic API wrapper (supports async, MCP servers, narrator model)
│   │   ├── status_narrator.py ← Haiku-based pipeline narration
│   │   ├── zone_resolver.py   ← resolves paths to zone types (Phase 1 onward)
│   │   ├── browser_agent.py   ← Playwright web browsing (Phase 2 onward)
│   │   ├── pdf_grpc_client.py ← Go PDF microservice gRPC client (Phase 3 onward)
│   │   ├── mcp_client.py      ← MCP server config and result parsing (Phase 4 onward)
│   │   └── exceptions.py      ← all custom exceptions
│   │
│   └── gateway/
│       └── telegram_bot.py    ← TelegramGateway (async, send_status, send_confirmation)
│
├── pdf_service/               ← Go gRPC PDF microservice (Phase 3 onward)
│   ├── proto/pdf.proto
│   ├── main.go
│   ├── pdf_ops.go
│   └── Makefile
│
├── tests/                     ← mirrors raoc/ structure exactly
└── data/                      ← runtime data, gitignored
    ├── raoc.db
    └── raoc.log
```

---

## Workspace paths

```
~/raoc_workspace/              ← safe workspace zone
~/raoc_workspace/scripts/      ← scripts written by the system
~/raoc_workspace/.backups/     ← timestamped originals before rewrite
~/raoc_workspace/screenshots/  ← all job screenshots
```

**All paths come from `raoc.config`. Never hardcode a path string.**
**Phase 1 onward: zone_config.yaml defines access to paths outside workspace.**

---

## Agent pattern — every agent follows this exactly

```python
class AgentName:
    def __init__(self, db, llm=None):
        self.db = db
        self.llm = llm  # None for agents that don't call Claude

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

**Agents never call gateway methods directly. Only coordinator sends messages.**

---

## Coordinator is async

coordinator.py, handle_new_message(), handle_approval(), handle_clarification(),
and advance() are all `async def`. All coordinator methods must be awaited.
Narration calls use asyncio.create_task() — fire-and-forget, never blocking.
Execution step narration is synchronous (awaited) before each FILE_BACKUP,
FILE_WRITE, and CMD_EXECUTE action to guarantee message ordering.

---

## Coding conventions

- All paths are `pathlib.Path` objects — never raw strings
- All Claude API calls use `tool_use` for structured output — never free-text JSON
- All database writes use transactions — `with engine.begin() as conn:`
- All errors are caught, logged to audit table, and re-raised as typed exceptions
- No `print()` statements — use Python `logging` module only
- Every function and class has a docstring
- Every agent has a corresponding test file in `tests/`
- Import all paths and constants from `raoc.config` — never hardcode
- Timestamps on all backup and output filenames — derived from `job.created_at`
  Format: `filename_YYYYMMDD_HHMMSS.ext.bak` — never `datetime.now()`

---

## File type support

Detection is by file content (magic bytes), never by extension.
Extension is used only as a hint for write-back strategy.

| Format | Detection | Read | Write |
|---|---|---|---|
| Plain text, Markdown, CSV, JSON, .py, .sh | UTF-8 decode | Yes | Yes |
| DOCX | PK magic + word/ zip entry | python-docx | python-docx |
| PDF (text layer) | %PDF magic | pdfplumber | pymupdf in-place or pdf2docx |
| PDF (image-only) | %PDF magic, empty pdfplumber | pytesseract OCR | DOCX output |
| ZIP (non-DOCX) | PK magic, no word/ entry | List contents, ask user | N/A |
| Binary | >10% non-printable bytes | ExtractionError | N/A |

---

## Security rules — never violate these

- Never execute any path outside approved zone (safe_workspace in MVP, zone model in Phase 1+)
- Never pass secrets as command-line arguments
- Never store secret values in the database, logs, screenshots, or any file
- Never log a secret value — only log that the secret was accessed
- Never run commands containing: `rm -rf`, `sudo`, `chmod`, `chown`, `mkfs`, `dd if=`, `kill -9`
- Never execute any action without `job.approval_granted == True`
- Always back up a file before rewriting it (timestamped backup)
- Always validate paths with `_assert_in_workspace()` before any file operation

---

## How to run tests

```bash
cd ~/raoc
uv run pytest tests/ -v                        # full suite
uv run pytest tests/test_intake.py -v          # single file
```

All tests must pass before reporting a phase done.
All tests use `tmp_path` for file operations — never the real workspace.
Never mock away security checks in tests.
NARRATION_DELAY_BEFORE_PLAN and NARRATION_DELAY_BEFORE_EXECUTION
are zeroed in tests via conftest.py autouse fixture.

---

## Current build phase

Update this line every time you complete a phase.

```
MVP (Phases 1–9): COMPLETE — 272 tests passing
Phase 1 (Policy Agent + Zone Model): NOT STARTED — 293 tests passing
Phase 2 (Web Browsing Substrate): NOT STARTED
```

---

## Key reference documents

- **PRD.md** — what the product does, acceptance criteria, scope
- **Architecture.md** — full schemas, data models, folder structure, security boundaries
- **AI_rules.md** — rules for how code is written and how to use Claude Code
- **Plan.md** — phase-by-phase checklist, tick as you go
- **Build Guide (RAOC_Build_Guide_v2.docx)** — Claude Code prompts and test cases for all 11 phases
