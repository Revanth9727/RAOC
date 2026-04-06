# Plan.md — Build Checklist
# Remote Autonomous OS Controller (RAOC)
# Last updated: March 2026
#
# HOW TO USE THIS FILE
# ────────────────────
# Tick items as you complete them: change [ ] to [x]
# Update the CURRENT STATUS section at the top after each phase.
# Do not move to the next phase until the current phase is 100% ticked.
#
# LEGEND
# [ ] = not started
# [~] = in progress
# [x] = complete
# [!] = blocked — needs attention before proceeding

---

## CURRENT STATUS

```
MVP (Phases 1–9):  [x] COMPLETE — 272 tests passing

Current phase:     [ ] Phase 1 — Policy Agent + Zone Model
Next phase:        [ ] Phase 2 — Web Browsing Substrate

Last completed:    MVP Phase 9 — Pipeline coordinator
Blocked on:        —
Notes:             Three community tools installed before starting Phase 1:
                   Superpowers (/brainstorming before each phase),
                   GSD (/gsd:new-milestone at start of each phase),
                   awesome-claude-code (browse before each phase)
```

---

## PRE-PHASE TOOL SETUP

- [ ] Superpowers installed: `/plugin marketplace add obra/superpowers-marketplace`
- [ ] Superpowers installed: `/plugin install superpowers@superpowers-marketplace`
- [ ] Superpowers verified: restart Claude Code, session-start message confirms active
- [ ] GSD installed: `npx get-shit-done-cc --claude --global`
- [ ] GSD verified: `/gsd:new-milestone` command available in Claude Code
- [ ] awesome-claude-code bookmarked: https://github.com/hesreallyhim/awesome-claude-code
- [ ] awesome-claude-code visual directory bookmarked: https://awesomeclaude.ai/awesome-claude-code

---

## MVP — COMPLETE

All MVP phases (Pre-setup through Phase 9) are complete.
272 tests passing. All phone tests passed.
See git history for phase-by-phase commits.

What was built in the MVP:
- [x] Job record and database (Phase 1)
- [x] Secrets and Telegram gateway (Phase 2)
- [x] Host state sampler and screenshots (Phase 3)
- [x] LLM client (Phase 4)
- [x] Intake agent — run_script, rewrite_file, query, query_action (Phase 4+)
- [x] Discovery agent — content-based file detection (Phase 5)
- [x] Planning agent (Phase 6)
- [x] Execution agent + command wrapper (Phase 7)
- [x] Verification and reporter agents (Phase 8)
- [x] Pipeline coordinator — async, with narration (Phase 9)
- [x] Query agent — metadata-first, content search fallback
- [x] Status narrator — Haiku, fire-and-forget, correct message ordering
- [x] File type support — PDF, DOCX, CSV, JSON, ZIP, binary detection
- [x] PDF rewrite — in-place via pymupdf, DOCX fallback via pdf2docx
- [x] PDF OCR — pytesseract fallback for image-only PDFs
- [x] Timestamped backups — tied to job.created_at
- [x] ZIP handling — lists contents, asks user, extracts named file, cleans up

---

## PHASE 1 — Policy Agent and Zone Model

**Goal:** Every planned action passes through a policy engine before execution.
**Done when:** All tests green AND all three phone test cases pass.
**Before starting:** /brainstorming → /gsd:new-milestone → browse awesome-claude-code

### Tool checklist
- [ ] /brainstorming run — design reviewed before coding
- [ ] /gsd:new-milestone run — spec and roadmap created
- [ ] awesome-claude-code browsed — security hooks checked (Parry)

### Preparation
- [ ] Prompt 1 (Preparation) run — codebase audited, no code changed
- [ ] Report reviewed — no conflicts found or resolved

### Zone Model
- [ ] raoc/models/policy.py created — ZoneType, PolicyDecision, PolicyResult
- [ ] raoc/substrate/zone_resolver.py created — ZoneResolver class
- [ ] ~/raoc/zone_config.yaml created — default zone assignments
- [ ] ZoneResolver loads config from config.ZONE_CONFIG
- [ ] WORKSPACE always resolves to safe_workspace regardless of config
- [ ] ~/.ssh always resolves to forbidden
- [ ] Most specific path match wins
- [ ] Missing config file uses safe defaults, not error

### Policy Agent
- [ ] raoc/agents/policy_agent.py created — PolicyAgent class
- [ ] __init__(self, db, zone_resolver: ZoneResolver, llm=None)
- [ ] review_plan() reviews every ActionObject, returns list[PolicyResult]
- [ ] _evaluate_action() dispatches per zone and action type
- [ ] forbidden zone → blocked
- [ ] safe_workspace → auto_approved
- [ ] read_only zone + read action → auto_approved
- [ ] read_only zone + write action → blocked
- [ ] restricted zone → approval_required
- [ ] edge cases that cannot be cleanly resolved → judgment_zone
- [ ] blocked job sets BLOCKED status and audit entry immediately
- [ ] judgment_zone items collected for user review in plan preview
- [ ] every policy decision written to audit log

### Coordinator integration
- [ ] Prompt 3 (Policy Agent + Coordinator integration) run
- [ ] coordinator.py calls policy_agent.review_plan() after planning, before preview
- [ ] blocked result sends clear message to user naming the blocked path and reason
- [ ] judgment_zone items listed separately in plan preview
- [ ] policy_decision and policy_reason columns added to actions table
- [ ] save_action and _row_to_action updated in queries.py

### Tests
- [ ] tests/test_zone_resolver.py — 5 tests covering key cases
- [ ] tests/test_policy_agent.py — 8 tests covering all decision paths
- [ ] All tests use tmp_path — never real workspace or ~/.ssh
- [ ] uv run pytest tests/ -v — all 272+ tests green

### Phone test cases
- [ ] Test 1 (Easy): Rewrite notes.txt → plan shows auto_approved, executes normally
- [ ] Test 2 (Medium): Rewrite file in ~/Documents → plan shows approval_required, executes after approval
- [ ] Test 3 (Complex): Rewrite SSH config → BLOCKED before plan preview, nothing executes

### Phase 1 Complete
- [ ] All tests green
- [ ] All three phone tests pass
- [ ] CLAUDE.md updated: Phase 1 COMPLETE
- [ ] Git tagged: v1.0.0

---

## PHASE 2 — Web Browsing Substrate

**Goal:** RAOC can navigate the web, extract content, and screenshot pages.
**Done when:** All tests green AND all three phone test cases pass.
**Before starting:** /brainstorming → /gsd:new-milestone → browse awesome-claude-code

### Tool checklist
- [ ] /brainstorming run
- [ ] /gsd:new-milestone run
- [ ] awesome-claude-code browsed

### Preparation
- [ ] Prompt 1 (Preparation) run — no existing network code conflicts found
- [ ] playwright installed: `uv add playwright`
- [ ] chromium installed: `playwright install chromium`

### Browser Substrate
- [ ] raoc/substrate/browser_agent.py created — BrowserAgent class
- [ ] raoc/models/web.py created — WebPageContent, WebExtractionResult
- [ ] BrowserError added to exceptions.py
- [ ] navigate() returns WebPageContent with title, url, text_content, links, screenshot_path
- [ ] screenshot() saves to SCREENSHOTS_DIR/job_id/label.png
- [ ] fill_form() never logs injected values
- [ ] click() uses accessibility selector first
- [ ] browser always stopped in finally block
- [ ] timeout enforced from config.BROWSER_TIMEOUT

### Execution Integration
- [ ] WEB_NAVIGATE, WEB_EXTRACT, WEB_SCREENSHOT, WEB_FILL_FORM, WEB_CLICK added to ActionType
- [ ] execution.py handlers added for all web action types
- [ ] WEB_FILL_FORM and WEB_CLICK always require approval in policy
- [ ] main.py instantiates BrowserAgent, starts before gateway.run()

### Tests
- [ ] tests/test_browser_agent.py — 5 tests (mocked Playwright)
- [ ] tests/test_execution.py updated — 4 new web action tests
- [ ] uv run pytest tests/ -v — all tests green

### Phone test cases
- [ ] Test 1 (Easy): "Go to news.ycombinator.com and tell me the top 5 stories"
- [ ] Test 2 (Medium): "Go to github.com/anthropics/anthropic-sdk-python and tell me the latest release"
- [ ] Test 3 (Complex): Research and save comparison to workspace file

### Phase 2 Complete
- [ ] All tests green
- [ ] All three phone tests pass
- [ ] CLAUDE.md updated: Phase 2 COMPLETE
- [ ] Git tagged: v2.0.0

---

## PHASE 3 — Dynamic Planning + Tool Registry + Go PDF Microservice

**Goal:** Any goal handled. Go PDF delivers high-fidelity in-place editing.
**Done when:** All tests green AND all three phone test cases pass.
**Before starting:** /brainstorming → /gsd:new-milestone → browse awesome-claude-code

### Tool checklist
- [ ] /brainstorming run
- [ ] /gsd:new-milestone run
- [ ] awesome-claude-code browsed

### Go PDF Microservice (Workstream A)
- [ ] Go module initialised in pdf_service/
- [ ] pdf_service/proto/pdf.proto written — ExtractText, ReplaceText, ExportPdf
- [ ] make proto — Go and Python stubs generated
- [ ] pdf_service/main.go — gRPC server on localhost:50051
- [ ] pdf_service/pdf_ops.go — unipdf operations
- [ ] make build — binary compiles to pdf_service/bin/pdf_service
- [ ] make test — Go tests pass
- [ ] raoc/substrate/pdf_grpc_client.py — PdfGrpcClient class
- [ ] PdfServiceError added to exceptions.py
- [ ] main.py starts pdf_service binary, waits for gRPC port ready
- [ ] tests/test_pdf_grpc_client.py — 4 tests (mocked gRPC)

### Tool Registry (Workstream B)
- [ ] raoc/agents/tool_registry.py created — Tool model, ToolRegistry class
- [ ] All existing action types registered as tools
- [ ] Web action types registered (Phase 2)
- [ ] PDF action types registered (pdf_extract, pdf_replace, pdf_export)
- [ ] describe_all() returns formatted string for LLM consumption
- [ ] tests/test_tool_registry.py — 4 tests

### Dynamic Planning (Workstream B)
- [ ] raoc/agents/goal_interpreter.py — GoalInterpreter, GoalSpec model
- [ ] raoc/agents/dynamic_planner.py — DynamicPlanner
- [ ] coordinator.py routes open-ended goals to GoalInterpreter + DynamicPlanner
- [ ] existing task types (run_script, rewrite_file, query, query_action) still use original pipeline
- [ ] goal_spec column added to jobs table
- [ ] backward compatibility: all 272+ existing tests still pass
- [ ] tests/test_goal_interpreter.py — 4 tests
- [ ] tests/test_dynamic_planner.py — 6 tests including backward compat

### Integration
- [ ] uv run pytest tests/ -v — ALL tests green (no regressions)

### Phone test cases
- [ ] Test 1 (Easy): "Go to xe.com and tell me the current USD to EUR exchange rate"
- [ ] Test 2 (Medium): Analyse Python files in workspace for function count
- [ ] Test 3 (Complex): Replace company name in PDF using Go service, layout preserved

### Phase 3 Complete
- [ ] All tests green, no regressions
- [ ] All three phone tests pass
- [ ] CLAUDE.md updated: Phase 3 COMPLETE
- [ ] Git tagged: v3.0.0

---

## PHASE 4 — MCP Connectors

**Goal:** Gmail, Calendar, and any MCP service accessible.
**Done when:** All tests green AND all three phone test cases pass.

- [ ] /brainstorming → /gsd:new-milestone → browse awesome-claude-code
- [ ] Preparation prompt run
- [ ] raoc/models/mcp.py created — McpServer, McpToolResult
- [ ] raoc/substrate/mcp_client.py created — McpClient
- [ ] llm_client.py updated — mcp_servers parameter added to call()
- [ ] MCP_SERVERS added to config.py
- [ ] MCP_QUERY, MCP_WRITE added to ActionType enum
- [ ] execution.py handlers for MCP actions
- [ ] network_allowed capability check before any MCP call
- [ ] tests/test_mcp_client.py — 5 tests
- [ ] tests/test_execution.py updated — MCP action tests
- [ ] uv run pytest tests/ -v — all tests green
- [ ] Phone tests: calendar query, Gmail search, Gmail + file write
- [ ] CLAUDE.md updated: Phase 4 COMPLETE
- [ ] Git tagged: v4.0.0

---

## PHASE 5 — Interrupt and Resume

**Goal:** 2FA, critical risk handoffs, connection drops handled without losing state.

- [ ] /brainstorming → /gsd:new-milestone → browse awesome-claude-code
- [ ] Preparation prompt run
- [ ] PAUSED added to JobStatus enum
- [ ] pause_reason, resume_token, last_completed_step added to jobs table
- [ ] execution.py writes last_completed_step after every step
- [ ] critical_risk action pauses job, sends screenshot, waits
- [ ] coordinator.handle_resume() validates resume_token
- [ ] coordinator.handle_connection_restore() sends notice, does not auto-resume
- [ ] browser_agent.py detects 2FA prompts (OTP input, text indicators)
- [ ] coordinator.handle_2fa_code() routes numeric message to 2FA handler
- [ ] tests/test_coordinator.py updated — pause/resume tests
- [ ] uv run pytest tests/ -v — all tests green
- [ ] Phone tests: 2FA banking login, flight search, flight booking with payment stop
- [ ] CLAUDE.md updated: Phase 5 COMPLETE
- [ ] Git tagged: v5.0.0

---

## PHASE 6 — Extended Secret Vault

**Goal:** Per-service credentials stored securely with scope enforcement.

- [ ] /brainstorming → /gsd:new-milestone → browse awesome-claude-code
- [ ] Preparation prompt run
- [ ] raoc/substrate/vault.py created — Vault class extending SecretBroker
- [ ] raoc/cli/vault_cli.py created — add, list, delete commands
- [ ] VaultEntry model with name, category, service_scope, inject_as
- [ ] vault_registry.json at config.VAULT_REGISTRY (no values)
- [ ] Scope enforcement: retrieve() raises ScopeViolationError on mismatch
- [ ] Every retrieve() written to audit log (name not value)
- [ ] 30-second injection lifetime via threading.Timer
- [ ] tests/test_vault.py — 6 tests
- [ ] uv run pytest tests/ -v — all tests green
- [ ] Phone tests: add token, browse with credentials, booking with travel profile
- [ ] CLAUDE.md updated: Phase 6 COMPLETE
- [ ] Git tagged: v6.0.0

---

## PHASE 7 — Dynamic Agent Spawning

**Goal:** Complex goals spawn specialised sub-agents. Parallel execution where possible.

- [ ] /brainstorming → /gsd:new-milestone → browse awesome-claude-code
- [ ] Preparation prompt run
- [ ] raoc/models/subjob.py created — SubJob, SubJobStatus
- [ ] sub_jobs table added to schema.py and queries.py
- [ ] raoc/agents/meta_coordinator.py created — MetaCoordinator
- [ ] MetaCoordinator decomposes goals into sub-jobs
- [ ] Independent sub-jobs run concurrently (max 3 simultaneous)
- [ ] Dependent sub-jobs wait for predecessors
- [ ] reporter.py handles multi-sub-job evidence reports
- [ ] coordinator routes complex multi-domain goals to MetaCoordinator
- [ ] tests/test_meta_coordinator.py — 5 tests
- [ ] uv run pytest tests/ -v — all tests green
- [ ] Phone tests: email+calendar parallel, invoice reconciliation, content pipeline
- [ ] CLAUDE.md updated: Phase 7 COMPLETE
- [ ] Git tagged: v7.0.0

---

## PHASE 8 — Memory Agent

**Goal:** Preference memory learned from behaviour. Policy memory explicit only.

- [ ] /brainstorming → /gsd:new-milestone → browse awesome-claude-code
- [ ] Preparation prompt run
- [ ] MemoryRecord model — tier, pattern_description, scope, expires_at
- [ ] memory_records table added to schema
- [ ] Preference memory: inferred after 2+ consistent observations
- [ ] Habit: promoted to user after 5+ observations, 80%+ consistency
- [ ] Standing rule: explicit user command only, never inferred
- [ ] Preferences surfaced as suggestions, never applied silently
- [ ] Drift detection: weekly check, scope warnings
- [ ] tests/test_memory_agent.py — tests covering all three tiers
- [ ] uv run pytest tests/ -v — all tests green
- [ ] Phone tests: remembered folder, Heathrow preference, morning email routine
- [ ] CLAUDE.md updated: Phase 8 COMPLETE
- [ ] Git tagged: v8.0.0

---

## PHASE 9 — Semantic Search

**Goal:** Local embedding model. Fast, relevant file discovery at scale.

- [ ] /brainstorming → /gsd:new-milestone → browse awesome-claude-code
- [ ] Preparation prompt run
- [ ] sentence-transformers installed: `uv add sentence-transformers`
- [ ] chromadb installed: `uv add chromadb`
- [ ] Workspace files chunked and indexed locally
- [ ] Query matched by cosine similarity
- [ ] Discovery uses embeddings for content search in QueryAgent
- [ ] No cloud, fully local
- [ ] tests/test_semantic_search.py — key search quality tests
- [ ] uv run pytest tests/ -v — all tests green
- [ ] Phone tests: board meeting file, Henderson account search, auth code search
- [ ] CLAUDE.md updated: Phase 9 COMPLETE
- [ ] Git tagged: v9.0.0

---

## PHASE 10 — GUI Automation Substrate

**Goal:** Desktop apps with no CLI or API accessible via Accessibility API.

- [ ] /brainstorming → /gsd:new-milestone → browse awesome-claude-code
- [ ] Preparation prompt run
- [ ] macOS Accessibility API substrate built
- [ ] Image matching fallback (pyautogui, threshold 0.92)
- [ ] Strict preference order: accessibility API → image match → coordinates (last resort)
- [ ] Pre-conditions checked before every interaction
- [ ] Sequences >5 steps screenshot mid-sequence
- [ ] If accessibility target not found: halt and escalate, never fall back to coordinates
- [ ] tests/test_gui_agent.py — pre-condition and failure tests
- [ ] uv run pytest tests/ -v — all tests green
- [ ] Phone tests: Xcode screenshot, Final Cut export, accounting software CSV export
- [ ] CLAUDE.md updated: Phase 10 COMPLETE
- [ ] Git tagged: v10.0.0

---

## PHASE 11 — Hardening

**Goal:** Approval expiry, second factor, rehearsal, launchd background service.

- [ ] /brainstorming → /gsd:new-milestone → browse awesome-claude-code
- [ ] Preparation prompt run

### Approval expiry and second factor
- [ ] Medium risk: approval expires 15 minutes after grant
- [ ] High risk: 4-digit PIN confirmation, expires 5 minutes after PIN
- [ ] Critical risk: typed phrase, expires 2 minutes, non-transferable
- [ ] Scope hash on ApprovalRecord — action change invalidates approval
- [ ] Expired approval re-requests without executing

### Rehearsal layer
- [ ] Low risk: execute directly, no rehearsal
- [ ] Medium risk: dry run describes every action without executing
- [ ] High risk: scoped container, real files, contained writes
- [ ] Rehearsal results always communicated as "logic check passed", not guarantee

### launchd
- [ ] raoc.plist created at ~/Library/LaunchAgents/
- [ ] RAOC starts automatically on login
- [ ] Restarts on crash
- [ ] Logs to data/raoc.log

### Tests
- [ ] tests/test_approval_expiry.py — expiry and hash invalidation tests
- [ ] tests/test_rehearsal.py — dry run and container tests
- [ ] uv run pytest tests/ -v — all tests green
- [ ] Phone tests: expired approval re-request, high-risk PIN, hosts file critical phrase

### Phase 11 Complete — MVP Done
- [ ] All tests green
- [ ] All phone tests pass
- [ ] CLAUDE.md updated: Phase 11 COMPLETE — GENERAL-PURPOSE OPERATOR DONE
- [ ] Git tagged: v11.0.0
- [ ] launchd confirmed working: RAOC starts on login
