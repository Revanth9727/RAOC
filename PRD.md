# PRD.md — Product Requirements Document
# Remote Autonomous OS Controller (RAOC)
# Version: Post-MVP
# Last updated: March 2026

---

## 1. Purpose

This document defines what RAOC does, for whom, and what success looks like.
It is the single source of truth for product scope decisions.
When there is disagreement between this document and any other document,
this document wins for product scope questions.
The engineering spec (Architecture.md) wins for implementation questions.

---

## 2. Problem Statement

There is no safe, transparent way to delegate real computer tasks to an AI from a phone.

Existing tools either:
- Require full unrestricted access to the machine (unsafe)
- Only work when you are physically at the computer (not remote)
- Give you no visibility into what the AI is about to do (no control)
- Have no proof of what actually happened (no audit)

The result: you either do the task yourself, or you give an AI tool
more trust than it has earned.

---

## 3. User

**Who:** Single user. You.

**Profile:**
- Technically capable. Comfortable with terminal and Python.
- Has a Mac at home on WiFi.
- Uses a phone remotely on mobile data.
- Wants to delegate real computer tasks without losing control.
- Willing to approve a plan before anything executes.
- Expects to be told exactly what happened after execution.

There is no second user. There is no public signup. There is no onboarding flow.

---

## 4. Core Value Proposition

> Send a natural language instruction from your phone.
> See a plan. Approve it. Get proof it worked.
> Nothing touches your machine without your say.

---

## 5. Current Scope (Post-MVP, Pre-Phase 1)

### 5.1 What the System Does Today

The system handles four task types:

#### Task Type A — Run a Script
Run an existing .py or .sh script, or write and run a new one from a natural
language description. System reviews script for safety, shows plan, executes
after approval, returns stdout/stderr and screenshots.

#### Task Type B — Rewrite a File
Read a file of any supported format, rewrite based on natural language
instruction, back up the original with a timestamp, save new version after
approval. Returns before/after state and screenshots.

**Supported formats (detected by content, not extension):**
Plain text, Markdown, CSV, JSON, Python, Shell, DOCX, PDF (text), PDF (OCR), ZIP (asks which file).

**PDF rewrite strategy:** in-place if rewritten text within 15% of original
length per block; falls back to DOCX conversion if length exceeds tolerance
or if PDF was image-only (OCR). User notified of format change before approval.

**Backup naming:** `filename_YYYYMMDD_HHMMSS.ext.bak` — tied to job timestamp.
Multiple jobs on the same file never overwrite each other's backups.

#### Task Type C — Query
Answer information questions about workspace contents. Metadata scan first,
content search only if metadata cannot answer. No approval needed. Read-only.
Answer sent directly to phone.

Examples: "What files do I have?", "What is the most recent file?",
"Which file contains my resume?", "Find the file with the Q3 numbers."

#### Task Type D — Query + Action
Find a file by content or metadata search, confirm what was found with the
user, then proceed to the action pipeline (rewrite or run script) with the
discovered file already resolved. Confirmation always required before acting.

Examples: "Find my cover letter and rewrite it for a senior engineering role.",
"Find the file with Q3 data and run an analysis script on it."

### 5.2 The Core Loop

Every action job follows this sequence:

```
Phone message
    → "Got it. Working on it..." (immediate)
    → Intake (classify the request)
    → Discovery (find and read the target)
    → Narration ("Found notes.txt. Building plan...")
    → Planning (build step-by-step action plan)
    → [Policy check — Phase 1 onward]
    → Plan preview (sent to phone with Approve / Deny)
    → [pipeline stops — waits for user]
    → Approval
    → Execution (with narration before each action)
    → Verification
    → Evidence report
```

If the user denies: nothing executes. Zero side effects.
If execution fails: honest failure report sent. Original restored from backup.

For query jobs: no plan preview, no approval, direct answer.

### 5.3 Workspace Boundary (Current)

All file operations are within `~/raoc_workspace/`.
Phase 1 will introduce a zone model expanding access to the whole machine
with defined safety boundaries per folder.

### 5.4 What the User Sees on Their Phone

**Immediate:** "Got it. Working on it..." within 1 second of sending.

**During processing:** Narration messages as each stage completes:
- "Found [filename] ([size], modified [time]). Building your rewrite plan..."
- "Backing up [filename] before making any changes..."
- "Rewriting [filename]..."

**Plan preview (before execution):**
- Task type and target file
- Step list
- Format change note if PDF→DOCX applies
- Approve and Deny buttons

**Evidence report (after execution):**
- Success or failure status
- Before/after file state (sizes, paths, backup name)
- Script output (first 20 lines of stdout) if applicable
- Format change note if applicable
- Post-execution screenshot
- Plain-language failure explanation if anything went wrong

---

## 6. Roadmap — Phases to General-Purpose Operator

The system is evolving toward a general-purpose autonomous operator. The goal:
send any computer task from your phone, the system figures out what to do,
does it, stops at the right moments to ask, reports back with proof.

| Phase | What It Adds | Key Unlocks |
|---|---|---|
| 1 | Policy agent + zone model | Safe access to whole machine |
| 2 | Web browsing substrate (Playwright) | Web research, portal access |
| 3 | Dynamic planning + tool registry + Go PDF | Any task type, high-fidelity PDF editing |
| 4 | MCP connectors (Gmail, Calendar, Slack) | External services without custom integrations |
| 5 | Interrupt and resume (2FA, critical handoff) | Banking, booking, connection drop recovery |
| 6 | Extended secret vault | Per-service credentials, scoped, time-limited |
| 7 | Dynamic agent spawning | Complex multi-step autonomous tasks |
| 8 | Memory agent | Preference learning, session context |
| 9 | Semantic search (local embeddings) | Fast discovery across large workspaces |
| 10 | GUI automation substrate | Desktop apps with no CLI or API |
| 11 | Hardening (approval expiry, rehearsal, launchd) | Production-ready autonomous operator |

---

## 7. Functional Requirements

### FR-1: Message Reception
MUST receive messages via Telegram.
MUST reject messages from non-whitelisted user IDs.
MUST reject messages older than 60 seconds.
MUST send acknowledgement within 1 second of receipt.

### FR-2: Task Interpretation
MUST classify requests into: run_script, rewrite_file, query, query_action.
MUST extract target file or instruction from message.
MUST ask one clarifying question if task type cannot be determined.
MUST refuse the job if still unclear after one clarification.

### FR-3: File Discovery
MUST detect file type by content inspection, not by extension.
MUST locate target file within approved zone.
MUST report clearly if file not found.
MUST detect and report if file is locked.
MUST refuse rewrite of binary files with plain-language explanation.
MUST refuse rewrite of files over MAX_FILE_SIZE_CHARS.
MUST handle ZIP files by listing contents and asking which file to extract.

### FR-4: Planning
MUST build an action plan for each task.
MUST review script content for blocked patterns before including in plan.
MUST refuse to plan execution of scripts containing blocked patterns.
MUST save all planned actions to database before sending preview.
MUST include format change note in plan intent for PDF→DOCX rewrites.
MUST include timestamped backup path in plan intent.

### FR-5: Approval Flow
MUST send plan preview before executing any action.
MUST stop pipeline after sending plan preview and wait for approval.
MUST execute zero actions if user denies.
MUST send cancellation confirmation if denied.

### FR-6: Execution
MUST execute actions in step_index order.
MUST back up any file before rewriting it (timestamped).
MUST capture screenshot before and after execution.
MUST stop if any step fails.
MUST restore from timestamped backup if file write fails.
MUST narrate before FILE_BACKUP, FILE_WRITE, and CMD_EXECUTE actions.
MUST write last_completed_step to job record after every step.
MUST NOT execute without approval_granted == True (enforced in code).
MUST NOT execute paths outside approved zone.
MUST NOT execute commands containing blocked patterns.

### FR-7: Verification
MUST verify outcomes against expected state, not exit codes.
MUST check output_path (not just target_path) for PDF rewrites.
MUST check file existence, backup existence, and file size after rewrite.
MUST check exit code and stdout presence after script execution.

### FR-8: Reporting
MUST send evidence report after every job (success or failure).
MUST send post-execution screenshot.
MUST include before/after file state.
MUST surface format change note prominently for PDF→DOCX rewrites.
MUST surface any execution fallback note (e.g. PDF in-place → DOCX).
MUST write failure reason in plain language, never a stack trace.
MUST update job status to COMPLETED or FAILED after reporting.

### FR-9: Audit
MUST write audit entry for every state transition.
MUST write audit entry for every action attempted.
MUST write audit entry for every approval granted or denied.
MUST write audit entry for every policy decision (Phase 1 onward).
MUST retain all job records and audit logs permanently.

### FR-10: Narration
MUST send immediate hardcoded acknowledgement before any processing.
MUST narrate discovery completion with real filename and size.
MUST narrate before FILE_BACKUP and FILE_WRITE execution steps.
MUST NOT send narration from inside agents — only coordinator via gateway.
Narration failures MUST NOT stop the pipeline.

---

## 8. Non-Functional Requirements

### NFR-1: Safety
No action executes without explicit user approval.
No file is modified without a timestamped backup.
No path outside approved zone is touched.
Approval gate is enforced in code, not by convention.

### NFR-2: Honesty
Every failure produces a failure report. No silent failures.
No success is reported unless verification passes.
Format changes are communicated before approval, not just in the report.

### NFR-3: Speed
Full loop (message to report) under 60 seconds for a simple task on a file
under 5,000 characters. First acknowledgement within 1 second always.

### NFR-4: Reliability
Job record reflects exact state at point of failure. Recovery possible.
last_completed_step written after every execution step, not just on failure.

### NFR-5: Privacy
No file content stored outside the database or transmitted outside the system
except via the Telegram report. No secrets logged or stored in database.

---

## 9. Acceptance Criteria — MVP (All Passing)

- [x] Run existing script from phone → plan preview → approve → stdout in report → screenshot.
- [x] Write and run new script from description → script shown in plan → executes → output in report.
- [x] Rewrite plain text file → approve → rewritten → timestamped backup exists → before/after in report.
- [x] Rewrite DOCX → approve → rewritten in place as DOCX → backup exists.
- [x] Rewrite PDF (text layer) → plan shows format change note → approve → DOCX output created → original PDF backed up.
- [x] Rewrite PDF (image-only) → OCR extracted → plan shows OCR note → approve → DOCX output.
- [x] Send ZIP → file list shown → reply with filename → file extracted → pipeline continues.
- [x] Deny any plan → nothing executes → cancellation message.
- [x] Query "what files do I have?" → direct answer → no approval.
- [x] Query "find my resume" → direct answer naming file → no approval.
- [x] "Find my cover letter and rewrite it" → search → confirmation → plan → approve → rewritten.
- [x] Point at file outside workspace → job refused with explanation.
- [x] Script with 'rm -rf' → blocked before execution.
- [x] File write fails → timestamped backup restored → failure report.
- [x] Two rewrites on same file → two separate timestamped backups, neither overwrites.
- [x] Acknowledgement arrives within 1 second of message.
- [x] Narration arrives before plan preview every time.
- [x] uv run pytest tests/ -v → 272 tests, 100% green.

---

## 10. What Is Explicitly Out of Scope Until Its Phase

| Feature | Phase |
|---|---|
| Access to paths outside workspace without approval | Phase 1 |
| Web browsing | Phase 2 |
| Open-ended goals beyond four task types | Phase 3 |
| Gmail, Calendar, Slack integration | Phase 4 |
| 2FA handling, booking tasks, connection drop resume | Phase 5 |
| Per-service credential vault | Phase 6 |
| Multi-agent spawning for complex tasks | Phase 7 |
| Memory and preference learning | Phase 8 |
| Semantic search across large workspaces | Phase 9 |
| GUI automation | Phase 10 |
| Approval expiry, PIN confirmation, launchd daemon | Phase 11 |
