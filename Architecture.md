# Architecture.md — System Architecture and Data Design
# Remote Autonomous OS Controller (RAOC)
# Version: Post-MVP (Phase 1 onward)
# Last updated: March 2026

---

## 1. System Overview

RAOC is an async agent pipeline. A message enters from Telegram, passes through
a series of specialised agents, and a report exits to Telegram. No agent calls
another agent directly. All state lives in the job record. The pipeline
coordinator reads job status and routes to the next agent.

The pipeline is currently transitioning from a fixed two-task MVP toward a
general-purpose operator. The coordinator handles four task types today and will
support open-ended goals via dynamic planning from Phase 3 onward.

```
Phone (Telegram)
    ↓ message
"Got it. Working on it..."     ← immediate hardcoded acknowledgement
Telegram Gateway
    ↓ raw text
Intake Agent          → classifies: run_script | rewrite_file | query | query_action
    ↓ TaskObject
[query path]          → QueryAgent → answer sent directly, no approval needed
[query_action path]   → QueryAgent.run_search_for_action → confirmation → pipeline
[action path]
Discovery Agent       → content-based file detection, extract_text_for_rewrite()
    ↓ ContextPackage  → includes detected_format, output_path, text_blocks, extraction_method
Policy Agent          → reviews every ActionObject, tags zone and policy decision  ← Phase 1
    ↓ PolicyResults
Planning Agent        → builds ActionObject list, calls Claude for content
    ↓ ActionObject[]
StatusNarrator        → "Found notes.txt (1.2KB). Building your rewrite plan..." (Haiku, async)
[WAIT: User approval via Telegram]
    ↓ approved
StatusNarrator        → "Backing up notes.txt..." (sync, before each action)
Execution Agent       → runs steps in order, handles file/web/MCP/script actions
    ↓ ExecutionSummary
Verification Agent    → checks output_path (not just target_path) for PDF rewrites
    ↓ VerificationResult
Reporter Agent        → formats evidence report, surfaces format changes and fallback notes
    ↓ report + screenshots
Phone (Telegram)
```

---

## 2. Folder Structure

```
~/raoc/                          ← project root
│
├── CLAUDE.md                    ← Claude Code context (read every session)
├── PRD.md                       ← Product requirements
├── Architecture.md              ← This file
├── AI_rules.md                  ← Rules for Claude Code and for you
├── Plan.md                      ← Build checklist
├── zone_config.yaml             ← Zone model (Phase 1 onward)
├── pyproject.toml
│
├── raoc/
│   ├── __init__.py
│   ├── config.py                ← ALL paths, constants, model names, thresholds
│   ├── coordinator.py           ← async pipeline state router
│   ├── main.py                  ← entry point, wires everything together
│   │
│   ├── models/
│   │   ├── job.py               ← JobRecord, JobStatus (SEARCHING, CONFIRMING, PAUSED added)
│   │   ├── action.py            ← ActionObject, ActionType (web + MCP types added)
│   │   ├── task.py              ← TaskObject (query, query_action added)
│   │   ├── policy.py            ← PolicyDecision, ZoneType, PolicyResult  [Phase 1]
│   │   ├── web.py               ← WebPageContent, WebExtractionResult  [Phase 2]
│   │   ├── mcp.py               ← McpServer, McpToolResult  [Phase 4]
│   │   └── subjob.py            ← SubJob, SubJobStatus  [Phase 7]
│   │
│   ├── db/
│   │   ├── schema.py            ← SQLAlchemy table definitions + create_tables()
│   │   └── queries.py           ← all read/write functions
│   │
│   ├── agents/
│   │   ├── intake.py            ← IntakeAgent
│   │   ├── discovery.py         ← DiscoveryAgent
│   │   ├── planning.py          ← PlanningAgent
│   │   ├── execution.py         ← ExecutionAgent
│   │   ├── verification.py      ← VerificationAgent
│   │   ├── reporter.py          ← ReporterAgent
│   │   ├── query_agent.py       ← QueryAgent
│   │   ├── policy_agent.py      ← PolicyAgent  [Phase 1]
│   │   ├── tool_registry.py     ← ToolRegistry  [Phase 3]
│   │   ├── goal_interpreter.py  ← GoalInterpreter  [Phase 3]
│   │   ├── dynamic_planner.py   ← DynamicPlanner  [Phase 3]
│   │   └── meta_coordinator.py  ← MetaCoordinator  [Phase 7]
│   │
│   ├── substrate/
│   │   ├── command_wrapper.py   ← safe shell execution
│   │   ├── host_sampler.py      ← file system state, content-based extraction pipeline
│   │   ├── screenshot.py        ← screenshot capture
│   │   ├── secret_broker.py     ← macOS Keychain access
│   │   ├── vault.py             ← per-service credential vault  [Phase 6]
│   │   ├── llm_client.py        ← Anthropic API wrapper (async, MCP, narrator model)
│   │   ├── status_narrator.py   ← Haiku narration, fire-and-forget
│   │   ├── zone_resolver.py     ← path → zone type resolution  [Phase 1]
│   │   ├── browser_agent.py     ← Playwright substrate  [Phase 2]
│   │   ├── pdf_grpc_client.py   ← Go PDF microservice client  [Phase 3]
│   │   ├── mcp_client.py        ← MCP server config and result parsing  [Phase 4]
│   │   └── exceptions.py        ← all custom exceptions
│   │
│   └── gateway/
│       ├── __init__.py
│       └── telegram_bot.py      ← async TelegramGateway
│
├── pdf_service/                 ← Go gRPC microservice  [Phase 3]
│   ├── proto/pdf.proto
│   ├── main.go
│   ├── pdf_ops.go
│   └── Makefile
│
├── tests/
│   ├── conftest.py              ← autouse fixture zeroing narration delays
│   ├── test_models.py
│   ├── test_db.py
│   ├── test_secrets.py
│   ├── test_gateway.py
│   ├── test_sampler.py
│   ├── test_screenshot.py
│   ├── test_llm_client.py
│   ├── test_narrator.py
│   ├── test_intake.py
│   ├── test_discovery.py
│   ├── test_planning.py
│   ├── test_command_wrapper.py
│   ├── test_execution.py
│   ├── test_verification.py
│   ├── test_reporter.py
│   ├── test_coordinator.py
│   ├── test_query_agent.py
│   └── test_end_to_end.py
│
└── data/
    ├── raoc.db
    └── raoc.log

~/raoc_workspace/
    ├── .backups/    ← timestamped backups: filename_YYYYMMDD_HHMMSS.ext.bak
    ├── scripts/     ← scripts written by the system
    └── screenshots/ ← all job screenshots
```

---

## 3. Config.py — Single Source of Truth

```python
# raoc/config.py

from pathlib import Path
from datetime import datetime

HOME            = Path.home()
WORKSPACE       = HOME / 'raoc_workspace'
BACKUPS_DIR     = WORKSPACE / '.backups'
SCRIPTS_DIR     = WORKSPACE / 'scripts'
SCREENSHOTS_DIR = WORKSPACE / 'screenshots'

PROJECT_ROOT    = Path(__file__).parent.parent
DATA_DIR        = PROJECT_ROOT / 'data'
DB_PATH         = DATA_DIR / 'raoc.db'
LOG_PATH        = DATA_DIR / 'raoc.log'
VAULT_REGISTRY  = DATA_DIR / 'vault_registry.json'
ZONE_CONFIG     = PROJECT_ROOT / 'zone_config.yaml'

ZONE_CONFIG     = PROJECT_ROOT / 'zone_config.yaml'

MAX_FILE_SIZE_CHARS         = 50_000
MAX_COMMAND_TIMEOUT         = 30
MAX_OUTPUT_CHARS            = 10_240
MAX_BINARY_NONPRINTABLE_RATIO = 0.10
PDF_REWRITE_LENGTH_TOLERANCE  = 0.15
PDF_OUTPUT_EXTENSION          = '.docx'

BLOCKED_PATTERNS = [
    'rm -rf', 'sudo', 'chmod', 'chown',
    'mkfs', 'dd if=', '> /dev/', 'kill -9',
]

LLM_MODEL        = 'claude-sonnet-4-5-20251001'
NARRATOR_MODEL   = 'claude-haiku-4-5-20251001'
LLM_MAX_TOKENS   = 2048

NARRATION_DELAY_BEFORE_PLAN      = 1.5   # seconds — zeroed in tests
NARRATION_DELAY_BEFORE_EXECUTION = 1.0   # seconds — zeroed in tests

KEYCHAIN_SERVICE          = 'raoc'
KEYCHAIN_ANTHROPIC_KEY    = 'anthropic_api_key'
KEYCHAIN_TELEGRAM_TOKEN   = 'telegram_bot_token'
KEYCHAIN_TELEGRAM_USER_ID = 'telegram_user_id'

# Phase 2 onward
BROWSER_TIMEOUT    = 30
BROWSER_HEADLESS   = True

# Phase 3 onward — Go PDF microservice
PDF_GRPC_HOST = 'localhost'
PDF_GRPC_PORT = 50051

# Phase 3 onward — local transcription
# uv add openai-whisper (local, no cloud, no API key)

# Phase 4 onward
MCP_SERVERS = {
    'gmail': 'https://gmail.mcp.claude.com/mcp',
    'gcal':  'https://gcal.mcp.claude.com/mcp',
}

def make_timestamped_stem(original_name: str, created_at: datetime) -> str:
    """Return filename stem with job timestamp appended.
    Example: make_timestamped_stem('notes.txt', dt) → 'notes_20260324_143022'
    """
    ts = created_at.strftime('%Y%m%d_%H%M%S')
    stem = Path(original_name).stem
    return f"{stem}_{ts}"
```

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

Path matching uses resolved absolute paths. Most specific match wins.
WORKSPACE always resolves to safe_workspace regardless of this file.
Missing file uses safe defaults — not an error.
This file is read-only at runtime — agents never write to it.

---

## 4. Database Schema

**Database:** SQLite. File at config.DB_PATH.
**ORM:** SQLAlchemy Core. Direct table definitions.
**All datetimes stored as ISO 8601 strings (TEXT). All booleans as INTEGER (0/1).**

### 4.1 jobs table

```
Column                Type     Notes
──────────────────────────────────────────────────────────────────────
job_id                TEXT     PRIMARY KEY. UUID4.
raw_request           TEXT     NOT NULL. Original message text.
task_type             TEXT     run_script | rewrite_file | query | query_action
target_path           TEXT     Resolved absolute path. Set by discovery.
output_path           TEXT     Write destination (differs from target for PDF→DOCX)
status                TEXT     NOT NULL. See JobStatus enum.
created_at            TEXT     NOT NULL. ISO 8601 UTC.
updated_at            TEXT     NOT NULL. ISO 8601 UTC.
error_message         TEXT     Set on failure. Human readable.
approval_granted      INTEGER  NULL = not yet asked. 1 = approved. 0 = denied.
clarification_question TEXT    Set when job is AWAITING_APPROVAL for a question.
query_intent          TEXT     For query/query_action: extracted search goal.
zip_source_path       TEXT     Set when ZIP detected awaiting file selection.
found_file_path       TEXT     Set after query_action search confirms a file.
implied_task_type     TEXT     For query_action: rewrite_file | run_script.
action_instruction    TEXT     For query_action: the action part of the request.
goal_spec             TEXT     JSON-serialised GoalSpec (Phase 3 onward).
pause_reason          TEXT     Set when job is PAUSED (Phase 5 onward).
resume_token          TEXT     UUID validated on resume (Phase 5 onward).
last_completed_step   INTEGER  Written after every execution step (Phase 5 onward).
parent_job_id         TEXT     For sub-jobs spawned by MetaCoordinator (Phase 7 onward).
```

### 4.2 actions table

```
Column              Type     Notes
──────────────────────────────────────────────────────────────────────
action_id           TEXT     PRIMARY KEY. UUID4.
job_id              TEXT     NOT NULL. Foreign key → jobs.job_id.
step_index          INTEGER  NOT NULL. 0-based.
action_type         TEXT     NOT NULL. See ActionType enum below.
risk_level          TEXT     'low', 'medium', or 'high'.
target_path         TEXT     File or resource this action targets.
intent              TEXT     Plain English description of this step.
command             TEXT     Shell command, file content, URL, or selector.
status              TEXT     pending | running | succeeded | failed | skipped.
execution_output    TEXT     Stdout + stderr, truncated to MAX_OUTPUT_CHARS.
verification_result TEXT     'passed', 'failed', or 'warned'.
policy_decision     TEXT     auto_approved | approval_required | blocked | judgment_zone
policy_reason       TEXT     Plain English reason for policy decision.
created_at          TEXT     NOT NULL. ISO 8601 UTC.
completed_at        TEXT     Set when status leaves running.
```

### 4.3 audit_log table

```
Column     Type     Notes
──────────────────────────────────────────────────────
id         INTEGER  PRIMARY KEY AUTOINCREMENT.
job_id     TEXT     NOT NULL.
event      TEXT     NOT NULL. Snake_case event name.
detail     TEXT     Optional. Extra context.
created_at TEXT     NOT NULL. ISO 8601 UTC.
```

### 4.4 sub_jobs table (Phase 7 onward)

```
Column         Type     Notes
──────────────────────────────────────────────────────
subjob_id      TEXT     PRIMARY KEY. UUID4.
parent_job_id  TEXT     NOT NULL.
goal           TEXT     What this sub-job achieves.
tools_required TEXT     JSON array of tool names.
status         TEXT     pending | running | completed | failed.
result         TEXT     Result text.
error          TEXT     Error message if failed.
depends_on     TEXT     JSON array of subjob_ids.
created_at     TEXT     NOT NULL.
completed_at   TEXT
```

---

## 5. ActionType Enum

```
FILE_READ       FILE_WRITE      FILE_BACKUP     FILE_DELETE
DIR_LIST        DIR_CREATE
CMD_INSPECT     CMD_EXECUTE
WEB_NAVIGATE    WEB_EXTRACT     WEB_SCREENSHOT  WEB_FILL_FORM  WEB_CLICK
MCP_QUERY       MCP_WRITE
SCREENSHOT
```

---

## 6. JobStatus Enum

```
RECEIVED → UNDERSTANDING → DISCOVERING → PLANNING → AWAITING_APPROVAL
         ↘ (query path)  → REPORTING → COMPLETED
         ↘ (query_action) → SEARCHING → CONFIRMING → DISCOVERING → ...

AWAITING_APPROVAL → EXECUTING → VERIFYING → REPORTING → COMPLETED
                                                        → FAILED
                 → CANCELLED

EXECUTING → PAUSED (Phase 5 — 2FA or critical risk)
PAUSED → EXECUTING (on resume) | CANCELLED (on deny)

BLOCKED   (policy blocked job before execution)
```

---

## 7. Agent Responsibilities

```
Agent               Reads from              Writes to               Calls
──────────────────────────────────────────────────────────────────────────────
IntakeAgent         jobs (raw_request)      jobs (task_type,        Claude API
                                            query_intent,           (tool_use)
                                            action_instruction)

QueryAgent          jobs                    jobs (status)           HostSampler
                                            gateway (answer)        Claude API

DiscoveryAgent      jobs                    jobs (target_path,      HostSampler
                                            output_path,            extract_text_for_rewrite()
                                            format_change)

PolicyAgent         jobs, actions           actions (policy_        ZoneResolver
                    zone_config.yaml        decision, reason)

PlanningAgent       jobs, ContextPackage    actions (all steps)     Claude API
                                            jobs (status)

ExecutionAgent      jobs, actions           actions (status,        CommandWrapper
                                            output)                 BrowserAgent
                                            jobs (last_completed    ScreenshotCapture
                                            _step)                  filesystem

VerificationAgent   jobs, actions           jobs (status)           HostSampler
                    output_path                                      filesystem (read)

ReporterAgent       jobs, actions,          jobs (status)           TelegramGateway
                    audit_log

StatusNarrator      stage + context dict    gateway.send_status()   Claude API (Haiku)
                                                                     fire-and-forget
```

---

## 8. File Extraction Pipeline (host_sampler.py)

```python
extract_text_for_rewrite(path: Path) -> tuple[str, str, Path, list[dict], str]
# Returns: (text, detected_format, output_path, text_blocks, extraction_method)
# detected_format: 'pdf' | 'pdf_ocr' | 'docx' | 'text' | 'unknown'
# output_path: same as input except PDF → .docx
# text_blocks: list of {page, bbox, original_text, font_size} — PDF only
# extraction_method: 'pdf_native' | 'pdf_ocr' | 'text'

Detection order (by content, never by extension):
  1. Read first 8 bytes for magic bytes
     %PDF → PDF path (pdfplumber → pytesseract OCR fallback)
     PK\x03\x04 + word/ zip entry → DOCX path (python-docx)
     PK\x03\x04, no word/ entry → ZipFileDetectedError(path, contents)
  2. UTF-8 decode → plain text path
  3. chardet if UnicodeDecodeError
  4. Binary ratio check → ExtractionError if >10% non-printable
```

---

## 9. PDF Write Strategy (planning.py → execution.py)

```
For PDF input, planning determines write_strategy:
  'pdf_ocr' extraction method → always 'pdf_to_docx'
  All text blocks within PDF_REWRITE_LENGTH_TOLERANCE (15%) → 'pdf_inplace'
  Any block exceeds tolerance → 'pdf_to_docx'

pdf_inplace (execution):
  pymupdf: white rect over original bbox, render replacement at same position
  with same font metrics. On overflow → fall back to pdf_to_docx, log fallback.

pdf_to_docx (execution):
  pdf2docx conversion → python-docx write.
  Output is timestamped .docx at output_path.
```

---

## 10. Narration Architecture

```python
# StatusNarrator uses Haiku (config.NARRATOR_MODEL)
# All narration is fire-and-forget except execution step narration

# Coordinator calls (fire-and-forget):
await self._narrate('message_received', {'raw_request': ...})
await asyncio.sleep(0)  # yield to event loop

# Before plan preview (allows narration to arrive first):
await asyncio.sleep(config.NARRATION_DELAY_BEFORE_PLAN)
# send plan preview

# Execution step narration (synchronous — awaited before action runs):
await self._narrate_execution_step_sync(action)
# then run the action

# Active narration stages: message_received, discovery_complete,
# execution_step (FILE_BACKUP, FILE_WRITE, CMD_EXECUTE only), job_failed
# Removed stages: intake_complete, discovery_started, planning_started,
# planning_complete, verification_complete, execution_complete
```

---

## 11. ZIP Handling Flow

```
discovery.py detects ZIP (PK magic, no word/ entry)
  → raises ZipFileDetectedError(path, contents)
  → coordinator sets job AWAITING_APPROVAL
  → sends file list to user: "Which file to extract?"

User replies with filename
  → coordinator.handle_clarification() validates filename
  → extracts named file to WORKSPACE using zipfile module
  → updates job.target_path to extracted file
  → re-runs discovery on extracted file
  → pipeline continues normally

After job completes:
  → coordinator deletes extracted temp file
  → backup and output remain
```

---

## 12. Custom Exceptions

```python
class RaocError(Exception): ...
class ScopeViolationError(RaocError): ...      # path outside approved zone
class CommandBlockedError(RaocError): ...      # blocked pattern in command
class FileTooLargeError(RaocError): ...        # exceeds MAX_FILE_SIZE_CHARS
class FileNotFoundInWorkspaceError(RaocError): ...
class FileLockedError(RaocError): ...
class UnsupportedFileTypeError(RaocError): ...
class ExtractionError(RaocError): ...          # cannot extract text from file
class ZipFileDetectedError(RaocError): ...     # carries path and contents list
class LLMError(RaocError): ...
class IntakeError(RaocError): ...
class BrowserError(RaocError): ...             # Phase 2 onward
class PdfServiceError(RaocError): ...          # Phase 3 onward
```

---

## 13. Security Boundaries

### Workspace boundary (current)
```python
def _assert_in_workspace(path: Path) -> None:
    if not str(path.resolve()).startswith(str(config.WORKSPACE.resolve())):
        raise ScopeViolationError(f"Path outside workspace: {path}")
```

### Zone boundary (Phase 1 onward)
ZoneResolver reads zone_config.yaml. Every action's target_path is resolved
to a zone before PolicyAgent makes its decision. Unmatched paths default to
restricted. Paths inside WORKSPACE always resolve to safe_workspace.

### Command boundary
CommandWrapper checks BLOCKED_PATTERNS before execution.
Minimal PATH environment. No parent process environment passthrough.
stdin closed (no interactive input).

### Approval boundary
ExecutionAgent checks `job.approval_granted == True` before any step.
coordinator.advance() returns immediately after sending plan preview —
execution does not start until handle_approval(approved=True) is called.

### Secret boundary
SecretBroker is the only component touching the Keychain.
Secret values never stored in database, logged, or passed as arguments.
Phase 6 vault: per-service scope enforcement, 30-second injection lifetime.

### Narration boundary
Status messages are generated by Haiku from real agent output data.
No hardcoded status strings in coordinator.py.
Narration failures are swallowed — never stop the pipeline.

---

## 14. How Components Are Wired in main.py

```python
# Current wiring (async)

broker     = SecretBroker()
llm        = LLMClient(broker)
narrator   = StatusNarrator(llm)
sampler    = HostSampler()
cmd        = CommandWrapper()
screenshot = ScreenshotCapture()
gateway    = TelegramGateway(broker)

coordinator = PipelineCoordinator(
    db=queries,
    llm=llm,
    narrator=narrator,
    sampler=sampler,
    command_wrapper=cmd,
    screenshot_capture=screenshot,
    gateway=gateway,
    query_agent=QueryAgent(queries, sampler, llm),
)

gateway.on_message       = coordinator.handle_new_message
gateway.on_approval      = coordinator.handle_approval
gateway.on_clarification = coordinator.handle_clarification

await gateway.run()  # blocks here
```

---

## 15. Testing Architecture

```
Test file              What it tests              Key mocks / notes
──────────────────────────────────────────────────────────────────────────────
conftest.py            Shared fixtures            Zeros narration delays
test_models.py         Pydantic models
test_db.py             SQLite schema + queries    tmp_path for db
test_secrets.py        SecretBroker               keyring module
test_gateway.py        TelegramGateway            telegram.Application
test_sampler.py        HostSampler, extraction    tmp_path, real files (no mocks on extractors)
test_screenshot.py     ScreenshotCapture          pyautogui
test_llm_client.py     LLMClient                  anthropic.Anthropic
test_narrator.py       StatusNarrator             LLMClient
test_intake.py         IntakeAgent                LLMClient, db
test_discovery.py      DiscoveryAgent             HostSampler, db, tmp_path
test_planning.py       PlanningAgent              LLMClient, db
test_command_wrapper.py CommandWrapper            subprocess.run
test_execution.py      ExecutionAgent             CommandWrapper, tmp_path
test_verification.py   VerificationAgent          HostSampler, db, tmp_path
test_reporter.py       ReporterAgent              TelegramGateway, db
test_coordinator.py    PipelineCoordinator        all agents mocked, AsyncMock for gateway
test_query_agent.py    QueryAgent                 HostSampler, LLMClient
test_end_to_end.py     Full pipeline              LLMClient, Telegram, tmp_path

272 tests passing as of March 2026.
```
