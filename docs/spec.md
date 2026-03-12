# Project Vibrant — Specification Document
> **Version**: 1.1.1
> **Date**: 2026-03-08
> **Author**: Spec-Driven Development Architect
> **Status**: APPROVED
---
## Table of Contents
0. [Changelog](#0-changelog)
1. [Executive Summary](#1-executive-summary)
2. [Design Philosophy](#2-design-philosophy)
3. [System Architecture](#3-system-architecture)
4. [Data Model & Storage](#4-data-model--storage)
5. [Core Workflows](#5-core-workflows)
6. [Component Specifications](#6-component-specifications)
7. [TUI Specification](#7-tui-specification)
8. [Agent Integration — OpenAI Codex CLI](#8-agent-integration--openai-codex-cli)
9. [Gatekeeper Specification](#9-gatekeeper-specification)
10. [Consensus-Driven Workflow](#10-consensus-driven-workflow)
11. [Validation & Self-Correction](#11-validation--self-correction)
12. [Git Workflow & Isolation](#12-git-workflow--isolation)
13. [Error Handling & Resilience](#13-error-handling--resilience)
14. [v1 Scope & Non-Goals](#14-v1-scope--non-goals)
15. [Acceptance Criteria](#15-acceptance-criteria)
16. [Glossary](#16-glossary)
---
## 0. Changelog

- **1.1.1** (2026-03-08): Corrected the Codex app-server protocol details.
- **1.1.0** (2026-03-07): Updated the Codex integration design.
- **1.0.0** (2026-03-07): Initial draft.

---
## 1. Executive Summary
**Vibrant** is a terminal-based management control plane for orchestrating autonomous coding agents. It is *not* an IDE — it is an **Air Traffic Control** system for agentic software development.
Vibrant enables a single human operator to **propose a project**, collaborate with a Gatekeeper agent to **form a structured plan**, and then **delegate execution** to a fleet of Codex CLI agents that self-validate and self-correct. The Gatekeeper — itself an agent process — guards a **Consensus Pool** (a versioned Markdown document) that serves as the single source of truth for project state, decisions, and progress.
### Key Value Proposition
| Current Gap | Vibrant Solution |
|---|---|
| Single agents degrade after ~1 hour due to context limits | Multi-agent pipeline with just-in-time context loading; each agent is short-lived and scoped |
| No supervision → garbage code in long-running tasks | Gatekeeper validates every task output; modifies plan on failure |
| "Coarse-grained vibe coding" (manual human verification) | Agent-driven self-validation (unit tests + e2e test agents) |
| No structured plan → agents drift from objectives | Consensus Pool is the immutable contract; Gatekeeper enforces adherence |
### Target Platform
- Linux-like environments (Linux, WSL)
- Python ≥ 3.11 with [Rich](https://github.com/Textualize/rich) / [Textual](https://github.com/Textualize/textual) for TUI
---
## 2. Design Philosophy
### 2.1 Everything Is an Agent
Every non-trivial operation — coding, testing, merge-conflict resolution, e2e validation — is performed by a spawned agent process. The Vibrant core is an **orchestrator**, not an executor.
### 2.2 Consensus as Contract
The Consensus Pool is a structured Markdown document that records:
- Outline of project for new agents to read (What they need to know to get started)
- Design choices made by gatekeeper or user
- Active goals and assignments
No agent may deviate from the consensus. The Gatekeeper is the sole writer (except for User overrides via Gatekeeper mediation) of the pool, and the pool is kept short and readable by the Gatekeeper.
When a new agent is spawned, the consensus pool is always read as context.
### 2.3 Just-in-Time Context
Agents are loaded with only the context they need for their specific task. Skills (text files) and project files are injected at spawn time, not preloaded.
### 2.4 Human-in-the-Loop, Not Human-in-the-Way
The pipeline runs autonomously. The user is only interrupted for **high-level decisions** (product direction, architecture pivots). Technical questions are resolved by the Gatekeeper. The Gatekeeper decides what questions are important to the user (design choices, not implementation details).
### 2.5 Roadmap for Iterative Development
When the project is initiated, the Gatekeeper creates a structured plan called the Roadmap. The roadmap outlines the tasks needed to complete the project, and the Gatekeeper updates it as tasks are completed or if re-planning is needed. The active step guides the prompts given to the sub-agents.
---
## 3. System Architecture
### 3.1 High-Level Architecture Diagram
```
┌──────────────────────────────────────────────────────────┐
│                       VIBRANT TUI                        │
│  ┌─────────┐ ┌──────────────┐ ┌───────────┐ ┌────────┐   │
│  │  Plan   │ │ Agent Output │ │ Consensus │ │  Chat  │   │
│  │  Tree   │ │   Streams    │ │   Pool    │ │  Q&A   │   │
│  │  (A)    │ │    (B)       │ │   (C)     │ │  (D)   │   │
│  └─────────┘ └──────────────┘ └───────────┘ └────────┘   │
└────────────────────────┬─────────────────────────────────┘
                         │
              ┌──────────▼──────────┐
              │    Orchestrator     │
              │    (Python Core)    │
              │                     │
              │  ┌───────────────┐  │
              │  │  Gatekeeper   │  │  ← Spawned Codex CLI agent
              │  │  (Agent)      │  │
              │  └───────┬───────┘  │
              │          │          │
              │  ┌───────▼───────┐  │
              │  │ Task Dispatch │  │
              │  │   Engine      │  │
              │  └───────┬───────┘  │
              └──────────┼──────────┘
                         │
          ┌──────────────┼──────────────┐
          │              │              │
   ┌──────▼──────┐ ┌────▼─────┐ ┌──────▼──────┐
   │ Code Agent  │ │ Code     │ │ Test Agent  │
   │ (Codex CLI) │ │ Agent N  │ │ (Codex CLI) │
   │ worktree/1  │ │ wt/N     │ │ worktree/T  │
   └─────────────┘ └──────────┘ └─────────────┘
```
### 3.2 Component Overview
Note: In the first iteration we will only implement for codex cli, but please design with extensibility in mind for future agent types.
| Component | Type | Responsibility |
|---|---|---|
| **Orchestrator** | Python process (long-running) | Lifecycle management, TUI rendering, state persistence, provider session management, process spawning |
| **Gatekeeper** | Spawned Codex CLI agent | Plan management, agent output evaluation, consensus updates, user escalation |
| **Code Agent** | Spawned Codex CLI agent | Execute a single task from the plan in an isolated git worktree |
| **Test Agent** | Spawned Codex CLI agent | Run test suites (unit, e2e) against agent output; read-only access |
| **Merge Agent** | Spawned Codex CLI agent | Resolve merge conflicts when integrating agent branches |
| **Codex Provider Adapter** | Orchestrator subservice | Launches `codex app-server`, manages JSON-RPC session/thread lifecycle, and normalizes provider events |
| **Consensus Pool** | Markdown file (`.vibrant/consensus.md`) | Versioned source of truth for plan, decisions, and progress |
| **Provider Event Logs** | NDJSON files under `.vibrant/logs/providers/` | Native and canonical runtime event audit trail for debugging and recovery |
| **Skill Store** | Directory of text files (`.vibrant/skills/`) | Just-in-time loadable skill definitions |
### 3.3 Process Hierarchy
Here is an example of coordination between components during a project run:
```
vibrant (Python)
├── gatekeeper (codex CLI, spawned on-demand)
├── agent-task-001 (codex CLI, isolated worktree)
├── agent-task-002 (codex CLI, isolated worktree)
├── agent-test-001 (codex CLI, read-only)
└── agent-merge-001 (codex CLI, on conflict)
```
---
## 4. Data Model & Storage
### 4.1 Directory Structure
All Vibrant state lives in `.vibrant/` at the project root. This directory is committed to git, providing natural version control.
```
<project-root>/
├── .vibrant/
│   ├── .gitignore                # Ignore logs, imported conversations, and other generated artifacts
│   ├── vibrant.toml              # Project-level configuration
│   ├── roadmap.md                # Project-level roadmap
│   ├── consensus.md              # The Consensus Pool (plan + decisions + progress)
│   ├── consensus.history/        # Snapshot archive (auto-generated)
│   │   ├── consensus.2026-03-07T22-00-00.md
│   │   └── consensus.2026-03-07T23-15-00.md
│   ├── skills/                   # Skill definition files
│   │   ├── gui-design.md
│   │   ├── testing-strategy.md
│   │   └── ...
│   ├── agents/                   # Agent run records, including provider resume metadata
│   │   ├── agent-task-001.json
│   │   ├── agent-task-002.json
│   │   └── ...
│   ├── conversations/            # Imported conversation snapshots / optional fallback artifacts
│   │   ├── gatekeeper-session-001.jsonl
│   │   └── agent-task-001.jsonl
│   ├── prompts/                  # Generated prompt templates
│   │   └── ...
│   ├── logs/
│   │   └── providers/
│   │       ├── native/           # Raw provider events / stderr-oriented diagnostics
│   │       └── canonical/        # Normalized runtime events used by Vibrant
│   └── state.json                # Orchestrator runtime state (durable)
└── ... (project source code)
```
### 4.2 Consensus Pool Format (`consensus.md`)
The consensus pool is a structured Markdown file with machine-parseable sections delimited by HTML comments for reliable parsing.
```markdown
# Consensus Pool — Project {name}
<!-- META:START -->
- **Project**: {name}
- **Created**: {ISO 8601 timestamp}
- **Last Updated**: {ISO 8601 timestamp}
- **Version**: {integer, auto-incremented on every write}
- **Status**: PLANNING | EXECUTING | PAUSED | COMPLETED | FAILED
<!-- META:END -->
## Objectives
<!-- OBJECTIVES:START -->
{Free-form markdown describing the high-level project goals.}
<!-- OBJECTIVES:END -->
## Design Choices
<!-- DECISIONS:START -->
### Decision {n}: {title}
- **Date**: {ISO 8601}
- **Made By**: `gatekeeper` | `user`
- **Context**: {why this decision was needed}
- **Resolution**: {what was decided}
- **Impact**: {what tasks/plan items were affected}
<!-- DECISIONS:END -->
## Getting Started
Simple, straightforward instructions for a new agent to get up to speed and start contributing. This is the "onboarding doc" for agents. Prefer links instead of embedding large amounts of context.
```
### 4.3 Agent Record Format (`agents/agent-task-001.json`)
```json
{
  "agent_id": "agent-task-001",
  "task_id": "task-001",
  "type": "code | test | merge | gatekeeper",
  "status": "spawning | connecting | running | awaiting_input | completed | failed | killed",
  "pid": 12345,
  "branch": "vibrant/task-001",
  "worktree_path": "/tmp/vibrant-worktrees/task-001",
  "started_at": "2026-03-07T22:00:00Z",
  "finished_at": "2026-03-07T22:25:00Z",
  "exit_code": 0,
  "provider": {
    "kind": "codex",
    "transport": "app-server-json-rpc",
    "runtime_mode": "full-access",
    "provider_thread_id": "thread_abc123",
    "resume_cursor": {"threadId": "thread_abc123"},
    "native_event_log": ".vibrant/logs/providers/native/agent-task-001.ndjson",
    "canonical_event_log": ".vibrant/logs/providers/canonical/agent-task-001.ndjson"
  },
  "summary": "...(~500 words from agent)...",
  "prompt_used": "...",
  "skills_loaded": ["gui-design", "testing-strategy"],
  "retry_count": 0,
  "max_retries": 3,
  "error": null
}
```
### 4.4 Orchestrator State (`state.json`)
```json
{
  "session_id": "uuid",
  "started_at": "ISO8601",
  "status": "running | paused | completed",
  "active_agents": ["agent-task-001", "agent-task-003"],
  "gatekeeper_status": "idle | running | awaiting_user",
  "pending_questions": ["Q1", "Q3"],
  "last_consensus_version": 14,
  "concurrency_limit": 4,
  "provider_runtime": {
    "agent-task-001": {
      "status": "ready",
      "provider_thread_id": "thread_abc123"
    }
  },
  "completed_tasks": ["task-001", "task-002"],
  "failed_tasks": [],
  "total_agent_spawns": 7
}
```
---
## 5. Core Workflows
### 5.1 End-to-End Workflow (Happy Path)
```
┌──────────┐     ┌────────────┐     ┌──────────────┐     ┌──────────────┐
│  User    │     │ Gatekeeper │     │ Orchestrator │     │ Code Agents  │
│ (Human)  │     │ (Agent)    │     │ (Python)     │     │ (Codex)      │
└────┬─────┘     └─────┬──────┘     └──────┬───────┘     └──────┬───────┘
     │                 │                   │                    │
     │  1. Proposal    │                   │                    │
     │────────────────▶│                   │                    │
     │                 │                   │                    │
     │  2. Questions   │                   │                    │
     │◀────────────────│                   │                    │
     │                 │                   │                    │
     │  3. Answers     │                   │                    │
     │────────────────▶│                   │                    │
     │                 │                   │                    │
     │  4. Draft Plan  │                   │                    │
     │◀────────────────│                   │                    │
     │                 │                   │                    │
     │  5. Approve/Edit│                   │                    │
     │────────────────▶│                   │                    │
     │                 │                   │                    │
     │                 │  6. Write         │                    │
     │                 │  consensus.md     │                    │
     │                 │  roadmap.md       │                    │
     │                 │─────────────────▶ │                    │
     │                 │                   │                    │
     │                 │                   │  7. Spawn agents   │
     │                 │                   │───────────────────▶│
     │                 │                   │                    │
     │                 │                   │  8. Task complete  │
     │                 │                   │◀───────────────────│
     │                 │                   │   (+ 500w summary) │
     │                 │                   │                    │
     │                 │  9. Evaluate      │                    │
     │                 │◀─────────────────│                    │
     │                 │                   │                    │
     │                 │  10. Verdict +    │                    │
     │                 │  consensus update │                    │
     │                 │─────────────────▶│                    │
     │                 │                   │                    │
     │                 │                   │  11. Next task...  │
     │                 │                   │───────────────────▶│
     │                 │                   │                    │
```
### 5.2 Workflow States (Orchestrator State Machine)
```
                    ┌──────────┐
                    │  INIT    │
                    └────┬─────┘
                         │ user provides proposal
                         ▼
                    ┌──────────┐
              ┌─────│ PLANNING │◀────────────────────┐
              │     └────┬─────┘                     │
              │          │ plan approved              │
              │          ▼                            │
              │     ┌───────────┐                     │
              │     │ EXECUTING │──── gatekeeper      │
              │     └─────┬─────┘    deems re-plan ───┘
              │           │                  needed
              │           │ all tasks done
              │           ▼
              │     ┌───────────┐
              │     │ VALIDATING│
              │     └─────┬─────┘
              │           │
              │     ┌─────▼─────┐     ┌──────────┐
              │     │ COMPLETED │     │  PAUSED  │
              │     └───────────┘     └──────────┘
              │                            ▲
              └────── user pauses ─────────┘
```
### 5.3 Task Lifecycle
```
pending → queued → in-progress → completed → [gatekeeper: accepted]
                        │                         │
                        │ (failure)               │ (rejected)
                        ▼                         ▼
                      failed ──→ [gatekeeper prompts with lesson learnt from failure ] ──→ queued (retry)
                        │
                        │ (max retries exceeded)
                        ▼
                    escalated ──→ [user notified]
```
---
## 6. Component Specifications
### 6.1 Orchestrator (`vibrant/orchestrator.py`)
The Orchestrator is the core Python process. It is the only long-running process.
**Responsibilities:**
1. Parse and manage `consensus.md`
2. Maintain `state.json` (durable across restarts)
3. Spawn/kill agent subprocesses (Codex CLI)
4. Monitor agent lifecycle (PID tracking, exit codes)
5. Route completed task summaries to the Gatekeeper
6. Render the TUI via Textual
7. Handle user input (approval, Q&A responses, consensus review)
8. Enforce concurrency limits
9. Manage git worktrees (create/cleanup)
## 7. TUI Specification
See the tui redesign guide in `docs/tui.md`.
### 8.4 Canonical Runtime Event Model
Codex's stable app-server surface currently exposes notifications and requests such as:
- `sessionConfigured`
- `thread/started`
- `turn/started`
- `item/started`
- `item/completed`
- `item/agentMessage/delta`
- `turn/completed`
- `error`
- server-initiated JSON-RPC requests such as `item/tool/requestUserInput`, `item/commandExecution/requestApproval`, and `item/fileChange/requestApproval`

Codex may also emit supplementary native notifications under `codex/event/*`. Vibrant should capture those in the native provider log as best-effort diagnostics, but the canonical layer should normalize everything into a stable internal event vocabulary, including at minimum:
- `session.started`
- `session.state.changed`
- `thread.started`
- `turn.started`
- `content.delta`
- `request.opened`
- `request.resolved`
- `user-input.requested`
- `user-input.resolved`
- `task.progress`
- `task.completed`
- `turn.completed`
- `runtime.error`

Each canonical event should use the same dict-backed envelope regardless of backend:
- required: `type`, `timestamp`
- optional common routing fields: `origin`, `provider`, `agent_id`, `task_id`, `provider_thread_id`
- optional provider escape hatch: `provider_payload`

Known event-specific top-level fields should stay provider-neutral. In particular:
- `thread.started`: `resumed`, optional `thread_path`, optional `thread`
- `turn.started` / `turn.completed` / `task.completed`: `turn_id`, optional `turn_status`, optional `turn`
- `content.delta`: `item_id`, `turn_id`, `delta`
- `reasoning.summary.delta`: `item_id`, `turn_id`, `delta`, optional `summary_index`
- `task.progress`: `item`, optional `turn_id`, optional `item_type`, optional `text`
- `request.opened`: `request_id`, `request_kind`, `method`, optional `params`
- `request.resolved`: `request_id`, `request_kind`, `method`, optional `result`, optional `error`, optional `error_message`
- `user-input.requested` / `user-input.resolved`: the request fields above, plus orchestrator-generated question fields when applicable
- `runtime.error`: optional `error`, optional `error_code`, optional `error_message`

Backend-specific wire payloads should not introduce new top-level canonical keys unless they are intended to become part of the shared contract. Extra provider detail belongs in `provider_payload`.

These canonical events drive:
- Panel B live output rendering
- task progress updates in the TUI
- Gatekeeper evaluation inputs
- crash recovery / resume logic
- observability and audit logs

Raw stdout/stderr remains useful for debugging, but it is not the primary integration contract.
### 8.5 Session Persistence, Resume, and Requests
Vibrant should persist provider-session bindings durably:
- `provider_thread_id` (the primary Codex resume handle)
- `thread.path` / rollout-path metadata when available
- Vibrant-managed replay cursor or last-seen native/canonical sequence metadata, if maintained
- runtime mode and approval policy
- current status
- last known active turn / task metadata
- log file paths

On restart:
1. Reconstruct active agents from `state.json` and `.vibrant/agent-runs/*.json`.
2. Re-launch `codex app-server` for any recoverable in-flight agent.
3. Attempt `thread/resume` using `provider_thread_id` as the primary key.
4. If resume fails with a recoverable “missing thread / unknown thread” class of error, mark the session stale, fall back to a fresh session, and route the decision to the Gatekeeper.
5. Use rollout-path metadata or reconstructed history only as explicit fallback strategies when the provider supports them.

If Codex emits approval or user-input requests, Vibrant must do two things:
1. Record them as structured canonical request events.
2. Respond to the server with a JSON-RPC response payload; these are server-initiated requests, not fire-and-forget notifications.
### 8.6 Logging & Conversation Artifacts
Vibrant should retain two best-effort NDJSON log streams per agent:
1. **Native provider log** — close to Codex runtime JSON messages plus stderr diagnostics.
2. **Canonical provider log** — normalized events consumed by Vibrant.

Operational notes:
- Logs should live under `.vibrant/logs/providers/` and be safe to rotate or cap.
- The native log should preserve raw JSON-RPC requests, responses, notifications, server-initiated requests, parse failures, and plain stderr lines.
- Imported JSONL conversation history from `~/.codex/` is still useful as a fallback or debugging artifact, but it is no longer the primary source of truth for task outcomes.
- Final summaries should be extracted from canonical completion events or the final assistant message, then copied into the agent record.
---
## 9. Gatekeeper Specification
### 9.1 Nature
The Gatekeeper is **itself a Codex CLI agent process**, spawned by the Orchestrator. It is not a direct LLM API call — it is a full Codex session with file system access to the `.vibrant/` directory.
### 9.2 Gatekeeper Invocation Triggers
The Gatekeeper is invoked (spawned) when:
| Trigger | Context Provided |
|---|---|
| **Project start** | User proposal → Gatekeeper creates initial plan |
| **Task completion** | Agent summary + diff → Gatekeeper evaluates |
| **Task failure** | Error logs + agent output → Gatekeeper re-plans |
| **Max retries exceeded** | Failure history → Gatekeeper escalates or pivots |
| **User requests conversation** | User message → Gatekeeper responds and updates consensus |
### 9.3 Gatekeeper Prompt Template
```
You are the Gatekeeper for Project {name}. You are the sole authority over the project plan.
## Your Responsibilities
1. Evaluate agent output against the plan's acceptance criteria.
2. Update .vibrant/consensus.md when tasks are completed or when the plan needs adjustment.
3. If an agent failed, analyze the failure and modify the task's prompt or acceptance criteria.
4. If you encounter a high-level decision (product direction, UX, architecture), ask the user
   by adding a question to the Questions section of consensus.md with priority "blocking".
5. If the decision is purely technical, make it yourself and log it in the Decisions section.
## Current Consensus
{contents of .vibrant/consensus.md}
## Trigger
{trigger_type}: {trigger_description}
## Agent Summary (if applicable)
{agent_summary}
## Rules
1. Always update consensus.md directly — it is the source of truth.
2. Increment the version number in META on every update.
3. Never remove completed decisions from the log.
4. When re-planning a failed task, keep the failure history in Gatekeeper Notes.
5. You have read/write access to the .vibrant/ directory ONLY.
## Available Skills
The following skills are available for agents. Assign them to tasks as needed:
{list of skill names and descriptions from .vibrant/skills/}
```
---
## 10. Consensus-Driven Workflow
### 10.1 Consensus Update Rules
The Consensus Pool may be updated under these conditions:
| # | Trigger | Who Updates | How |
|---|---|---|---|
| 1 | Project start | Gatekeeper | Creates initial `consensus.md` from user proposal |
| 2 | Task completion | Gatekeeper | Updates task status, writes verdict, adjusts plan |
| 3 | Task failure | Gatekeeper | Modifies task prompt, updates retry count, logs decision |
| 4 | User intervention | Gatekeeper (after user input) | User tells Gatekeeper what to change; Gatekeeper writes |
| 5 | Re-planning | Gatekeeper | Adds/removes/reorders tasks based on emergent needs |
### 10.2 Consensus Versioning
1. Every write to `consensus.md` increments the `Version` field in META.
2. Before overwriting, the Orchestrator copies the current `consensus.md` to `consensus.history/consensus.{ISO8601}.md`.
3. `.vibrant/` itself should remain committed, but `.vibrant/.gitignore` should exclude imported conversations, provider logs, and other generated or sensitive artifacts from git history.
---
## 11. Validation & Self-Correction
### 11.1 Validation Pipeline
After a code agent completes a task:
```
Code Agent completes
        │
        ▼
Orchestrator collects canonical runtime events + final turn outcome
        │
        ├── `turn.completed` / `runtime.error` indicates immediate failure path when applicable
        │
        ▼
Spawn Test Agent (if required in roadmap.md, on same branch/worktree, read-only)
        │
        ├── Run unit tests (`pytest`, `npm test`, etc.)
        ├── Run linter / type checker (if configured)
        ├── (if GUI project) Spawn e2e test agent (browser/computer-use)
        │
        ▼
Test Agent reports results
        │
        ▼
Forward to Gatekeeper:
  - Code Agent summary / `task.completed` payload
  - Canonical provider event log excerpt
  - Test Agent results
  - Git diff
        │
        ▼
Gatekeeper evaluates → verdict
```
### 11.2 Test Agent Specifics
- In the first iteration, test agents are not allowed to work concurrently with code agents to eliminate the complexity of code agents writing to the working directory while test agents are running. 
- Test agents are spawned in the same worktree as the code agent, but with a read-only advisory prompt.
- Test agents use the same `codex app-server` provider path as code agents so validation benefits from the same structured runtime events and recovery semantics.
- Test agent prompt includes the project's test commands (from `vibrant.toml`).
### 11.3 Rollback
On task failure:
1. The agent's branch is reset to its starting commit (`git reset --hard`).
2. The worktree is cleaned.
3. The Gatekeeper logs the failure and its analysis in `Gatekeeper Notes`.
4. A new attempt uses a revised prompt written by the Gatekeeper.
---
## 12. Git Workflow & Isolation
### 12.1 Branch Strategy
```
main (protected — only merged into by Orchestrator)
├── vibrant/task-001  (worktree 1)
├── vibrant/task-002  (worktree 2)
├── vibrant/task-003  (worktree 3)
└── ...
```
### 12.2 Worktree Management
1. **Creation**: Before spawning a code agent, the Orchestrator creates a git worktree:
   ```bash
   git worktree add /tmp/vibrant-worktrees/task-001 -b vibrant/task-001
   ```
2. **Cleanup**: After a task is merged or abandoned:
   ```bash
   git worktree remove /tmp/vibrant-worktrees/task-001
   git branch -d vibrant/task-001
   ```
3. **Location**: Worktrees are created in a temporary directory (configurable, default: `/tmp/vibrant-worktrees/`).
### 12.3 Merge Process
1. After Gatekeeper accepts a task, the Orchestrator attempts `git merge vibrant/task-xxx` into `main`.
2. If conflicts occur:
   - A **Merge Agent** is spawned with the conflict markers as context.
   - The Merge Agent resolves conflicts and commits.
   - The Gatekeeper validates the merge resolution.
3. If the Merge Agent fails, the conflict is escalated to the user.
### 12.4 Ordering
Tasks with dependencies are merged in dependency order. Independent tasks can be merged in any order, with conflict resolution as needed.
---
## 13. Error Handling & Resilience
### 13.1 Process Crash Recovery
1. **Orchestrator crash**: On restart, the Orchestrator reads `state.json`, `.vibrant/agent-runs/*.json`, and `consensus.md` to reconstruct state. For in-flight agents with persisted `resume_cursor` metadata, it re-launches `codex app-server` and attempts `thread/resume` before deciding whether the task must be retried.
2. **Agent crash**: Treated as task failure unless the provider thread can be resumed cleanly. The Gatekeeper receives the crash details, canonical runtime log excerpt, and resume outcome before deciding whether to retry.
3. **Gatekeeper crash**: Orchestrator re-spawns the Gatekeeper with the same context and, if available, the same persisted provider-thread metadata.
### 13.2 State Durability
- `state.json` is written atomically (write to temp file, then `os.rename`).
- `consensus.md` is written atomically with the same pattern.
- Agent run updates in `.vibrant/agent-runs/` are written atomically so provider resume metadata is never partially persisted.
- Provider event logs are best-effort observability artifacts; they should flush frequently, but the canonical persisted state remains the source of truth.
- File locks prevent concurrent writes.
### 13.3 Timeout Handling
- Each agent has a configurable timeout (default: 25 minutes).
- If an agent exceeds its timeout, the Orchestrator sends `SIGTERM`, waits 10 seconds, then `SIGKILL`.
- The task is marked as `failed` with reason `timeout`.
---
## 14. v1 Scope & Non-Goals
### 14.1 In Scope (v1)
| Feature | Details |
|---|---|
| TUI with 4-panel layout | Textual-based, panels A/B/C/D as specified |
| Orchestrator core | Process management, state persistence, git worktree management |
| Codex CLI integration | `codex app-server` session lifecycle, JSON-RPC transport, canonical runtime event normalization, and optional conversation import |
| Gatekeeper (as Codex agent) | Plan creation, evaluation, re-planning, escalation |
| Consensus Pool | Structured Markdown, versioned, parseable |
| Self-validation | Unit test agent (configurable command) |
| Self-correction | Gatekeeper re-prompts on failure, up to max retries |
| Git isolation | Branch-per-task, worktrees, automated merge |
| Provider observability logs | Native + canonical NDJSON logs per agent |
| E2E testing with computer-use agent | Real E2E testing based on real interaction with computer |
| Merge conflict resolution agent | Agent-based conflict resolution |
| Durable state | Resume after crash/restart, including persisted provider thread metadata |
| Linux/WSL support | Primary target platform |
### 14.2 Non-Goals (v1)
| Feature | Rationale |
|---|---|
| Claude Code / other agent providers | v1 supports Codex CLI only; architecture allows future providers |
| Web UI / GUI | TUI only for v1 |
| Multi-user collaboration | Single operator for v1 |
| Sandboxing / isolation | Agents run with full user permissions for v1 |
| Remote agent execution | All agents run locally |
| Plugin system | Skills system covers extensibility for v1 |
| Cloud deployment | Local-only |
### 14.3 Future Considerations (Post-v1)
- Multi-provider support (Claude Code, custom agents)
- Web dashboard alongside TUI
- Distributed agent execution (SSH-spawned agents on remote machines)
- DAG-based task scheduling with critical path analysis
- Self-evolution mode (Vibrant modifying its own codebase)
---
## 15. Acceptance Criteria
### 15.1 Core Acceptance Tests
| # | Test | Pass Condition |
|---|---|---|
| AC-01 | **TUI launches** | `vibrant` command starts the TUI with all 4 panels visible |
| AC-02 | **Proposal → Plan** | User types a proposal; Gatekeeper produces a plan in `consensus.md` |
| AC-03 | **Plan approval** | User reviews and approves the plan; status transitions to EXECUTING |
| AC-04 | **Agent spawn** | Orchestrator launches a `codex app-server` session for an agent in an isolated worktree and opens or resumes a provider thread |
| AC-05 | **Real-time output** | Canonical provider events and assistant text deltas stream in Panel B in real-time |
| AC-06 | **Task completion** | A `task.completed` / final assistant outcome is captured, summarized, and forwarded to Gatekeeper |
| AC-07 | **Gatekeeper evaluation** | Gatekeeper reads the summary, validation results, and relevant runtime events, then writes a verdict to consensus |
| AC-08 | **Self-correction** | On task failure, Gatekeeper modifies prompt and re-queues |
| AC-09 | **User escalation** | Gatekeeper asks a blocking question; TUI alerts user in Panel D |
| AC-10 | **Merge** | Completed task branch is merged into main |
| AC-11 | **Merge conflict** | Conflict triggers Merge Agent; resolution is validated by Gatekeeper |
| AC-12 | **Consensus versioning** | Each consensus update increments version; history is preserved |
| AC-13 | **Crash recovery** | Kill Orchestrator; restart; active sessions resume via stored provider-thread metadata when possible, otherwise they are safely re-queued |
| AC-14 | **Conversation viewing** | User can switch between agent conversation histories and provider event logs |
| AC-15 | **User ↔ Gatekeeper chat** | User can converse with Gatekeeper to adjust plan |
| AC-16 | **Provider observability** | Native and canonical NDJSON logs are written per agent and remain inspectable after completion |
---
## 16. Glossary
| Term | Definition |
|---|---|
| **Agent** | A spawned Codex CLI process that performs a specific task autonomously |
| **Canonical Runtime Event** | A normalized provider event emitted by Vibrant regardless of provider-specific wire format |
| **Code Agent** | An agent that writes or modifies code for a specific task |
| **Consensus Pool** | The `.vibrant/consensus.md` file; single source of truth for project state |
| **Gatekeeper** | A specialized agent that manages the plan, evaluates agent output, and mediates user communication |
| **Merge Agent** | An agent spawned specifically to resolve git merge conflicts |
| **Orchestrator** | The core Python process that manages the lifecycle of all agents, provider sessions, and the TUI |
| **Provider Thread** | The durable Codex thread identifier stored so a session can be resumed after restart |
| **Plan** | The ordered list of tasks in the roadmap.md file, each with prompts and acceptance criteria |
| **Task** | A discrete unit of work in the plan, assigned to a single agent |
| **Test Agent** | An agent that runs validation (tests, linting) against code agent output |
| **Verdict** | The Gatekeeper's judgment on whether a task's output meets its acceptance criteria |
| **Worktree** | A git worktree providing an isolated working directory for an agent |
---
> **End of Specification Document**
>
> This document is the source of truth for Project Vibrant v1. All implementation work
> should reference this spec. Changes to the spec require incrementing the version number
> and noting the change in a changelog section.
