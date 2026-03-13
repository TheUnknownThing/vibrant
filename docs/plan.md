# Project Vibrant — Implementation Plan

> **Version**: 1.0.0
> **Date**: 2026-03-08
> **Spec Reference**: [spec.md](./spec.md) v1.1.1
> **Status**: APPROVED

---

## Current State Assessment

### What Exists (`codex_tui/`)

| Module | What It Does | Spec Coverage |
|---|---|---|
| `codex_client.py` | JSON-RPC client for `codex app-server` (spawn, request/response, notifications, stderr) | §8.1–8.2 partial |
| `models.py` | Pydantic models: JSON-RPC wire types, Thread/Turn/Item, SessionConfig, AppSettings | §4.3–4.4 partial |
| `history.py` | File-backed thread persistence (`~/.codex-tui/history/`) | §4.1 partial |
| `app.py` | Textual TUI: sidebar thread list + conversation view + input bar + settings | §7 partial (2-panel, not 4-panel) |
| `widgets/` | `ThreadList`, `ConversationView`, `InputBar`, `SettingsPanel` | §7 partial |
| `__main__.py` | CLI entry, codex binary check, app launch | Minimal |

### What Must Be Built

The current codebase is a **simple Codex chat TUI**. Vibrant requires transforming it into a **multi-agent orchestration control plane** with: Orchestrator state machine, Gatekeeper agent, Consensus Pool, Roadmap, Git worktree isolation, validation pipeline, 4-panel TUI, NDJSON logging, crash recovery, and merge conflict resolution.

---

## Phased Implementation

### Legend

- **[Spec §N]** — references spec section
- **Depends**: tasks that must complete before this one starts
- **Acceptance**: concrete pass/fail checks for the task

---

## [x] Phase 0 — Foundation & Project Restructure

> **Goal**: Establish the `vibrant` package structure, configuration, and data layer that every later phase depends on.

### [x] Task 0.1 — Package Restructure & Entry Point

**Depends**: None

Rename/restructure the project from `codex_tui` to `vibrant`. Create the package layout:

```
vibrant/
├── __init__.py
├── __main__.py            # CLI: `python -m vibrant` or `vibrant`
├── config.py              # vibrant.toml loader (§4.1)
├── models/
│   ├── __init__.py
│   ├── wire.py            # JSON-RPC models (from models.py)
│   ├── agent.py           # AgentRunRecord, AgentType, AgentStatus
│   ├── task.py            # TaskInfo, TaskStatus, TaskLifecycle
│   ├── consensus.py       # ConsensusPool model
│   ├── state.py           # OrchestratorState model
│   └── settings.py        # AppSettings, SessionConfig
├── providers/
│   ├── __init__.py
│   ├── base.py            # Abstract ProviderAdapter interface
│   └── codex/
│       ├── __init__.py
│       ├── client.py      # CodexClient (from codex_client.py)
│       └── adapter.py     # CodexProviderAdapter
├── orchestrator/
│   ├── __init__.py
│   ├── engine.py          # Orchestrator state machine
│   ├── task_dispatch.py   # Task queue & dispatch
│   └── git_manager.py     # Worktree create/cleanup/merge
├── gatekeeper/
│   ├── __init__.py
│   └── gatekeeper.py      # Gatekeeper spawn & prompt logic
├── consensus/
│   ├── __init__.py
│   ├── parser.py          # Parse consensus.md sections
│   └── writer.py          # Atomic write + versioning
├── logging/
│   ├── __init__.py
│   └── ndjson_logger.py   # Native & canonical NDJSON log writer
├── validation/
│   ├── __init__.py
│   └── pipeline.py        # Test agent spawn & result collection
├── tui/
│   ├── __init__.py
│   ├── app.py             # Main Textual App (4-panel)
│   └── widgets/
│       ├── __init__.py
│       ├── plan_tree.py       # Panel A
│       ├── agent_output.py    # Panel B
│       ├── consensus_view.py  # Panel C
│       ├── chat_panel.py      # Panel D
│       └── input_bar.py
└── skills/
    └── __init__.py
```

Update `pyproject.toml`: rename to `vibrant`, add entry point `vibrant = "vibrant.__main__:main"`.

**Acceptance**:
- `python -m vibrant --help` prints usage
- All existing tests updated to import from `vibrant`
- `pytest` passes with no import errors

---

### [x] Task 0.2 — Configuration System (`vibrant.toml`)

**Depends**: 0.1  
**[Spec §4.1, §8.2]**

Create `vibrant/config.py`:
- Load `vibrant.toml` from project root (`.vibrant/vibrant.toml`)
- Schema: codex binary path, model, model-provider, approval policy, reasoning effort/summary, sandbox mode, concurrency limit, agent timeout, worktree directory, test commands
- Fallback to defaults if file missing
- Pydantic model `VibrantConfig`

**Acceptance**:
- Unit test: parse a sample `vibrant.toml` → `VibrantConfig` with all fields
- Unit test: missing file → defaults applied
- Unit test: invalid TOML → clear error message

---

### [x] Task 0.3 — Data Models

**Depends**: 0.1  
**[Spec §4.2, §4.3, §4.4]**

Create Pydantic models for:
- `AgentRunRecord` matching §4.3 JSON schema (agent_id, task_id, type, status, pid, branch, provider metadata, etc.)
- `OrchestratorState` matching §4.4 (session_id, active_agents, gatekeeper_status, pending_questions, etc.)
- `TaskInfo` (id, title, acceptance_criteria, status per §5.3 lifecycle, branch, retry_count, max_retries, prompt, skills, dependencies)
- `ConsensusDocument` (parsed representation of consensus.md sections)

**Acceptance**:
- Unit test: round-trip serialize/deserialize each model
- Unit test: `AgentRunRecord` status transitions validated
- Unit test: `TaskInfo` lifecycle state machine (pending→queued→in-progress→completed)

---

### [x] Task 0.4 — `.vibrant/` Directory Initialization

**Depends**: 0.2, 0.3  
**[Spec §4.1]**

Create `vibrant init` CLI subcommand:
- Creates `.vibrant/` directory structure (consensus.md, roadmap.md, vibrant.toml, skills/, agents/, conversations/, prompts/, logs/providers/native/, logs/providers/canonical/, consensus.history/, .gitignore)
- `.gitignore` excludes: `logs/`, `conversations/`, `agents/*.json` runtime state
- Generates initial empty `consensus.md` with META section (version 0, status INIT)
- Generates empty `state.json`

**Acceptance**:
- Run `vibrant init` in a temp dir → all directories and files exist
- `consensus.md` has valid META section parseable by Task 0.3 models
- `.gitignore` contains expected exclusions
- Running `vibrant init` twice is idempotent (no errors, no duplicates)

---

## [x] Phase 1 — Orchestrator Core & State Machine

> **Goal**: Implement the Orchestrator engine that manages lifecycle, state persistence, and task dispatch.

### [x] Task 1.1 — Orchestrator State Machine

**Depends**: 0.3, 0.4  
**[Spec §5.2]**

Implement `vibrant/orchestrator/engine.py`:
- States: `INIT → PLANNING → EXECUTING → VALIDATING → COMPLETED` plus `PAUSED`
- Transitions per §5.2 diagram (user proposal → PLANNING, plan approved → EXECUTING, etc.)
- Durable state: write `state.json` atomically on every transition (temp file + `os.rename`)
- Recovery: on startup, read `state.json` + `agents/*.json` + `consensus.md` to reconstruct state

**Acceptance**:
- Unit test: all valid transitions succeed, invalid transitions raise
- Unit test: state persisted to disk after each transition, re-loadable
- Unit test: simulated crash → restart → state recovered correctly
- Unit test: PAUSED state reachable from PLANNING and EXECUTING

---

### [x] Task 1.2 — Task Dispatch Engine

**Depends**: 1.1  
**[Spec §5.3, §6.1]**

Implement `vibrant/orchestrator/task_dispatch.py`:
- Task queue (priority + dependency ordering)
- Task lifecycle: `pending → queued → in-progress → completed → [accepted]` with failure/retry/escalation paths
- Concurrency limiter (from config, default 4)
- Dependency resolution: tasks with `depends_on` only dispatch after dependencies complete
- Retry logic: on failure, re-queue with incremented retry_count up to max_retries

**Acceptance**:
- Unit test: tasks dispatched in dependency order
- Unit test: concurrency limit respected (never exceed N simultaneous)
- Unit test: failed task retried up to max_retries, then escalated
- Unit test: task status transitions match §5.3 lifecycle diagram

---

### [x] Task 1.3 — Git Worktree Manager

**Depends**: 0.2  
**[Spec §12.1, §12.2, §12.3]**

Implement `vibrant/orchestrator/git_manager.py`:
- `create_worktree(task_id)` → creates branch `vibrant/{task_id}`, worktree in configured temp dir
- `remove_worktree(task_id)` → removes worktree + deletes branch
- `merge_task(task_id)` → `git merge vibrant/{task_id}` into main; detect conflicts
- `reset_worktree(task_id)` → `git reset --hard` to starting commit (for rollback §11.3)
- `list_worktrees()` → active worktrees

**Acceptance**:
- Integration test (real git repo): create worktree → verify branch exists, files accessible
- Integration test: merge clean branch → main updated
- Integration test: merge conflicting branch → conflict detected, returns conflict info
- Integration test: reset worktree → working directory clean
- Integration test: remove worktree → directory and branch gone

---

## [x] Phase 2 — Provider Abstraction & Codex Adapter

> **Goal**: Wrap the existing CodexClient behind a provider-neutral interface; add canonical event normalization and NDJSON logging.

### [x] Task 2.1 — Provider Adapter Interface

**Depends**: 0.1, 0.3  
**[Spec §3.2, §8.1]**

Create `vibrant/providers/base.py`:
- Abstract `ProviderAdapter` class with methods: `start_session()`, `stop_session()`, `start_thread()`, `resume_thread()`, `start_turn()`, `interrupt_turn()`, `respond_to_request()`
- Abstract `on_canonical_event` callback
- Runtime mode enum: `read_only`, `workspace_write`, `full_access` mapping to Codex sandbox modes

**Acceptance**:
- Abstract class defined with all methods
- Cannot instantiate directly (ABC enforcement)
- Runtime mode mapping documented and tested

---

### [x] Task 2.2 — Codex Provider Adapter

**Depends**: 2.1  
**[Spec §8.2, §8.3, §8.4, §8.5]**

Implement `vibrant/providers/codex/adapter.py`:
- Wraps existing `CodexClient`
- Session handshake: `initialize` → `initialized` → `thread/start` or `thread/resume`
- `experimentalApi = true` in capabilities
- Turn execution: `turn/start` with structured input array, `sandboxPolicy` object, `approvalPolicy`
- Translate Codex notifications into canonical events (§8.4 mapping)
- Handle server-initiated requests (approval, user input) → respond with JSON-RPC response
- Persist `provider_thread_id`, thread metadata in agent record

**Acceptance**:
- Integration test: spawn real `codex app-server`, complete handshake, start thread
- Unit test (mocked): notification `item/agentMessage/delta` → canonical `content.delta`
- Unit test: `turn/completed` → canonical `turn.completed`
- Unit test: server request → canonical `request.opened`, response sent back
- Unit test: resume with `provider_thread_id` sends `thread/resume`

---

### [x] Task 2.3 — NDJSON Dual-Log System

**Depends**: 2.2  
**[Spec §8.6]**

Implement `vibrant/logging/ndjson_logger.py`:
- `NativeLogger`: writes raw JSON-RPC messages + stderr to `.vibrant/logs/providers/native/{agent_id}.ndjson`
- `CanonicalLogger`: writes normalized events to `.vibrant/logs/providers/canonical/{agent_id}.ndjson`
- Each line: `{"timestamp": "...", "event": "...", "data": {...}}`
- Flush after every write
- Wire into CodexProviderAdapter

**Acceptance**:
- Unit test: log 10 events → file has 10 lines, each valid JSON
- Unit test: native log captures raw JSON-RPC + stderr lines
- Unit test: canonical log captures normalized events only
- Integration test: real agent run → both log files populated

---

## [x] Phase 3 — Consensus Pool & Roadmap

> **Goal**: Implement the machine-parseable Consensus Pool and Roadmap system.

### [x] Task 3.1 — Consensus Pool Parser & Writer

**Depends**: 0.3  
**[Spec §4.2, §10.1, §10.2]**

Implement `vibrant/consensus/parser.py` and `writer.py`:
- Parse `consensus.md` HTML comment delimiters (`<!-- META:START -->` etc.)
- Extract: META (project, status, version), OBJECTIVES, DECISIONS
- Write: atomic file write (temp + rename), auto-increment version, snapshot to `consensus.history/`
- Validate: ensure version monotonically increases

**Acceptance**:
- Unit test: parse sample consensus.md → all sections extracted correctly
- Unit test: write updates → version incremented, snapshot created
- Unit test: concurrent write attempt → file lock prevents corruption
- Unit test: round-trip parse→modify→write→parse preserves data

---

### [x] Task 3.2 — Roadmap Parser

**Depends**: 0.3  
**[Spec §2.5, §5.1]**

Implement roadmap parsing in `vibrant/consensus/`:
- Parse `roadmap.md` into ordered `TaskInfo` list
- Each task has: id, title, acceptance_criteria (checklist), dependencies, skills, priority
- Support task status updates (Gatekeeper writes status back)
- Generate prompts from task + consensus context (§8.3 template)

**Acceptance**:
- Unit test: parse sample roadmap → correct TaskInfo list with dependencies
- Unit test: dependency graph is a valid DAG (no cycles)
- Unit test: prompt generation includes all template fields from §8.3

---

## [x] Phase 4 — Gatekeeper Agent

> **Goal**: Implement the Gatekeeper as a Codex CLI agent that manages planning, evaluation, and user escalation.

### [x] Task 4.1 — Gatekeeper Spawn & Prompt System

**Depends**: 2.2, 3.1, 3.2  
**[Spec §9.1, §9.2, §9.3]**

Implement `vibrant/gatekeeper/gatekeeper.py`:
- Spawn Gatekeeper as `codex app-server` with cwd=project root, `full_access` mode to `.vibrant/`
- Prompt template from §9.3 (responsibilities, current consensus, trigger, agent summary, rules, available skills)
- Trigger types: project_start, task_completion, task_failure, max_retries_exceeded, user_conversation
- Parse Gatekeeper output: detect consensus.md writes, question escalations, plan modifications

**Acceptance**:
- Integration test: spawn Gatekeeper with "project_start" trigger + proposal → consensus.md created with plan
- Integration test: spawn with "task_completion" trigger → verdict written to consensus
- Unit test: prompt template renders correctly for each trigger type
- Unit test: Gatekeeper response parsing extracts verdict, questions, plan updates

---

### [x] Task 4.2 — User Escalation Flow

**Depends**: 4.1  
**[Spec §9.2, §7.4]**

Implement escalation pipeline:
- Gatekeeper adds blocking question to consensus.md → Orchestrator detects `pending_questions`
- Orchestrator emits `user-input.requested` canonical event
- User answers in TUI → answer forwarded to Gatekeeper session → Gatekeeper updates consensus
- Terminal bell + banner notification (configurable)

**Acceptance**:
- Integration test: Gatekeeper asks question → appears in pending_questions → user answers → Gatekeeper receives answer
- Unit test: notification triggers terminal bell and banner text
- Unit test: question persisted in state.json across restart

---

## Phase 5 — Agent Lifecycle & Execution

> **Goal**: Wire together Orchestrator + Provider + Gatekeeper for the full task execution loop.

### [x] Task 5.1 — Code Agent Lifecycle

**Depends**: 1.1, 1.2, 1.3, 2.2, 4.1  
**[Spec §5.1, §8.2, §8.3]**

Implement end-to-end code agent lifecycle:
1. Orchestrator picks task from queue
2. Create git worktree (Task 1.3)
3. Spawn CodexProviderAdapter in worktree (Task 2.2)
4. Send task prompt via `turn/start` (§8.3 template)
5. Stream canonical events during execution
6. On `turn/completed`: extract summary, update agent record
7. Forward to Gatekeeper for evaluation (Task 4.1)
8. On accepted: merge branch (Task 1.3)
9. On rejected: rollback + retry or escalate

**Acceptance**:
- E2E test: submit a task → agent executes → files modified in worktree → Gatekeeper accepts → merged to main
- E2E test: agent fails → Gatekeeper re-prompts → retry succeeds
- E2E test: max retries exceeded → task escalated to user
- Agent record written with all fields from §4.3

---

### Task 5.2 — Test Agent & Validation Pipeline

**Depends**: 5.1  
**[Spec §11.1, §11.2]**

Implement `vibrant/validation/pipeline.py`:
- After code agent completes, spawn test agent in same worktree (read-only mode)
- Test agent runs configured test commands (`pytest`, `npm test`, etc. from `vibrant.toml`)
- Collect test results as structured canonical events
- Forward: code agent summary + test results + git diff → Gatekeeper
- Test agents sequential with code agents (§11.2)

**Acceptance**:
- Integration test: code agent writes code + tests → test agent runs tests → results forwarded to Gatekeeper
- Unit test: test agent spawned with read-only sandbox mode
- Unit test: test failure → Gatekeeper receives failure details

---

### Task 5.3 — Merge Agent

**Depends**: 5.1, 1.3  
**[Spec §12.3]**

Implement merge conflict resolution:
- On merge conflict from `git_manager.merge_task()`, spawn Merge Agent
- Merge Agent receives conflict markers as context
- Merge Agent resolves and commits
- Gatekeeper validates resolution
- If Merge Agent fails → escalate to user

**Acceptance**:
- Integration test: two conflicting branches → merge agent resolves → Gatekeeper validates
- Integration test: unresolvable conflict → escalated to user
- Unit test: merge agent prompt includes conflict markers

---

## [x] Phase 6 — 4-Panel TUI

> **Goal**: Replace the current 2-panel layout with the spec's 4-panel layout.

### [x] Task 6.1 — Panel A: Plan/Task Tree

**Depends**: 3.2  
**[Spec §7.3 Panel A]**

Implement `vibrant/tui/widgets/plan_tree.py`:
- Tree view from roadmap.md tasks
- Status icons: ✓ completed, ⟳ running, ○ pending, ✗ failed
- Color by priority: critical=red, high=orange, medium=yellow, low=default
- Click task → overlay with details (acceptance criteria, prompt, agent summary)
- Live updates as tasks progress

**Acceptance**:
- Visual test: tree displays with correct icons and colors
- Test: selecting task shows detail overlay
- Test: live update when task status changes

---

### [x] Task 6.2 — Panel B: Agent Output Streams

**Depends**: 2.3  
**[Spec §7.3 Panel B]**

Implement `vibrant/tui/widgets/agent_output.py`:
- Stream canonical events + text deltas in real-time
- Ring buffer (10,000 lines per agent)
- Tab/F5 switching between active agents
- Auto-follow with scroll lock toggle (S key)
- Secondary debug view for raw stderr/native events

**Acceptance**:
- Visual test: streaming text appears in real-time
- Test: F5 cycles between agents
- Test: S key toggles scroll lock
- Test: buffer capped at 10,000 lines (old lines evicted)

---

### [x] Task 6.3 — Panel C: Consensus Pool View

**Depends**: 3.1  
**[Spec §7.3 Panel C]**

Implement `vibrant/tui/widgets/consensus_view.py`:
- Summary: status, version, task progress (completed/total), recent 3 decisions, pending questions count
- Pending questions highlighted when > 0
- F3 opens full consensus markdown in scrollable overlay

**Acceptance**:
- Visual test: summary shows correct counts
- Test: pending questions > 0 → highlight visible
- Test: F3 opens full markdown view

---
### [x] Task 6.4 — Panel D: Chat/Q&A Panel

**Depends**: 4.2  
**[Spec §7.3 Panel D]**

Implement `vibrant/tui/widgets/chat_panel.py`:
- During PLANNING: user ↔ Gatekeeper dialogue
- During EXECUTING: Gatekeeper escalation questions
- Input field at bottom
- Switchable between conversation threads
- Scrollable history

**Acceptance**:
- Visual test: messages display with sender labels
- Test: user types answer → forwarded to Gatekeeper
- Test: thread switching works
- Test: question notification flashes panel

---

### [x] Task 6.5 — 4-Panel Layout Assembly

**Depends**: 6.1, 6.2, 6.3, 6.4  
**[Spec §7.2]**

Assemble `vibrant/tui/app.py`:
- 4-panel grid layout matching §7.2 diagram
- Key bindings: F1=Help, F2=Pause, F3=Consensus, F5=Switch Agent, F10=Quit
- Status bar with notification banner
- Responsive: panels resize with terminal

**Acceptance**:
- AC-01: `vibrant` command starts TUI with all 4 panels visible
- All key bindings functional
- Terminal resize → panels adjust
- Notification banner appears on Gatekeeper escalation

---

## Phase 7 — Crash Recovery & Resilience

> **Goal**: Make the system durable across crashes and restarts.

### Task 7.1 — Orchestrator Crash Recovery

**Depends**: 5.1  
**[Spec §13.1]**

Implement recovery on startup:
1. Read `state.json` + `agents/*.json` + `consensus.md`
2. Re-launch `codex app-server` for recoverable in-flight agents
3. Attempt `thread/resume` with `provider_thread_id`
4. If resume fails → mark stale, fresh session, route to Gatekeeper
5. Atomic writes for all state files (temp + rename pattern)

**Acceptance**:
- AC-13: kill Orchestrator → restart → active sessions resume
- Test: resume succeeds → agent continues from where it left off
- Test: resume fails → task re-queued with Gatekeeper notification
- Test: state.json always valid JSON (no partial writes)

---

### Task 7.2 — Timeout & Process Monitoring

**Depends**: 5.1  
**[Spec §13.3]**

Implement:
- Configurable agent timeout (default 25 min)
- On timeout: SIGTERM → wait 10s → SIGKILL
- Task marked `failed` with reason `timeout`
- PID tracking for all child processes

**Acceptance**:
- Unit test: agent exceeding timeout → SIGTERM sent → SIGKILL after grace period
- Unit test: task status → failed with `timeout` reason
- Unit test: timeout configurable via vibrant.toml

---

## Phase 8 — End-to-End Integration & Polish

> **Goal**: Wire everything together and validate against spec acceptance criteria.

### Task 8.1 — E2E Happy Path

**Depends**: All previous phases  
**[Spec §5.1, §15.1]**

Full integration test:
1. `vibrant init` → `.vibrant/` created
2. User types proposal in Panel D
3. Gatekeeper creates plan → consensus.md + roadmap.md written
4. User approves → state transitions to EXECUTING
5. Code agents spawn in worktrees, execute tasks
6. Test agents validate
7. Gatekeeper evaluates → accepts/rejects
8. Accepted tasks merge to main
9. All tasks complete → state COMPLETED

**Acceptance — maps to spec AC-01 through AC-16**:
- AC-01: TUI launches with 4 panels
- AC-02: Proposal → plan in consensus.md
- AC-03: Plan approval → EXECUTING state
- AC-04: Agent spawns in isolated worktree with provider thread
- AC-05: Canonical events stream in Panel B
- AC-06: Task completion captured and forwarded
- AC-07: Gatekeeper writes verdict
- AC-08: Failed task → re-prompt → retry
- AC-09: Blocking question → Panel D alert
- AC-10: Task branch merged to main
- AC-11: Conflict → Merge Agent → validated
- AC-12: Consensus version increments, history preserved
- AC-13: Crash → restart → resume
- AC-14: Switch between agent conversation histories
- AC-15: User ↔ Gatekeeper chat works
- AC-16: NDJSON logs written per agent

---

### Task 8.2 — Skills System

**Depends**: 0.4, 4.1  
**[Spec §2.3, §3.2]**

Implement skills loading:
- Read `.vibrant/skills/*.md` files
- Gatekeeper assigns skills to tasks
- Agent prompts include skill file contents (just-in-time context)

**Acceptance**:
- Unit test: skill files loaded and injected into prompt
- Unit test: Gatekeeper can reference available skills
- Integration test: agent prompt contains skill content

---

## Dependency Graph

```
Phase 0: [0.1] → [0.2] → [0.4]
              → [0.3] → [0.4]

Phase 1: [0.3, 0.4] → [1.1] → [1.2]
          [0.2]      → [1.3]

Phase 2: [0.1, 0.3]  → [2.1] → [2.2] → [2.3]

Phase 3: [0.3] → [3.1]
          [0.3] → [3.2]

Phase 4: [2.2, 3.1, 3.2] → [4.1] → [4.2]

Phase 5: [1.1, 1.2, 1.3, 2.2, 4.1] → [5.1] → [5.2]
                                             → [5.3]

Phase 6: [3.2]  → [6.1]─┐
          [2.3]  → [6.2]─┤
          [3.1]  → [6.3]─┼→ [6.5]
          [4.2]  → [6.4]─┘

Phase 7: [5.1] → [7.1]
          [5.1] → [7.2]

Phase 8: [ALL]  → [8.1]
          [0.4, 4.1] → [8.2]
```

### Parallelism Opportunities

These task groups can execute concurrently:
- Phase 1 (1.1–1.3) ∥ Phase 2 (2.1–2.3) ∥ Phase 3 (3.1–3.2)
- Phase 6 panels (6.1 ∥ 6.2 ∥ 6.3 ∥ 6.4) after respective deps
- Task 7.1 ∥ 7.2
- Task 8.1 ∥ 8.2 (8.2 has fewer deps)

---

## Verification Strategy

### Per-Task Verification

Every task includes unit tests and/or integration tests as specified in its Acceptance section. Tests are committed alongside implementation code.

### Phase Gate Verification

| After Phase | E2E Checkpoint |
|---|---|
| Phase 0 | `vibrant init` creates valid directory structure; `vibrant --help` works |
| Phase 1 | State machine transitions work; worktrees create/destroy; task queue dispatches correctly |
| Phase 2 | Can spawn `codex app-server`, complete handshake, start/resume thread, receive events |
| Phase 3 | Can parse and write consensus.md; roadmap produces correct task list |
| Phase 4 | Gatekeeper creates plan from proposal; evaluates mock task output |
| Phase 5 | Full agent lifecycle: spawn → execute → validate → evaluate → merge |
| Phase 6 | TUI displays all 4 panels with live data |
| Phase 7 | Kill + restart → state recovered; timeouts enforced |
| Phase 8 | All 16 acceptance criteria (AC-01 through AC-16) pass |

### Test Infrastructure

- **Unit tests**: `pytest` with `pytest-asyncio` for async code
- **Integration tests**: real `codex app-server` (already configured on this machine), real git repos
- **TUI tests**: Textual's built-in `pilot` testing framework for widget assertions
- **CI**: all unit tests run on every commit; integration tests will be added in the future

---

> **End of Implementation Plan**
