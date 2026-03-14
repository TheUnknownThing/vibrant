# Project Vibrant — Specification Document
> **Version**: 1.2.0
> **Date**: 2026-03-13
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

- **1.2.0** (2026-03-13): Replaced the legacy result-parsing orchestrator model with the command-driven redesign. The orchestrator now owns all durable state under `.vibrant/`, the Gatekeeper mutates state only through typed MCP tools, execution is attempt-centric, processed conversation history is orchestrator-owned, and workflow state is authoritative over consensus metadata.
- **1.1.1** (2026-03-08): Corrected the Codex app-server protocol details.
- **1.1.0** (2026-03-07): Updated the Codex integration design.
- **1.0.0** (2026-03-07): Initial draft.

---

## 1. Executive Summary

**Vibrant** is a terminal-based control plane for orchestrating autonomous coding agents. It is not an IDE. It is an orchestrator that manages agent lifecycle, durable project state, workflow execution, review, and operator interaction.

Vibrant enables one human operator to propose a project, collaborate with a **Gatekeeper** agent to produce or revise a roadmap, and then delegate implementation to scoped worker agents. The central architectural rule is that the **orchestrator owns every durable artifact under `.vibrant/`**. The Gatekeeper owns planning and review decisions, but it expresses those decisions only through **typed MCP commands** issued to the orchestrator.

The design intentionally separates:

- **authority**: who decides
- **persistence**: who writes files
- **runtime control**: who spawns, resumes, interrupts, and stops agents
- **conversation history**: what the TUI reads and what provider logs are used for

This turns the orchestrator from a result-parsing runtime into a **command-driven control plane**.

### Key Value Proposition

| Current Gap | Vibrant Solution |
|---|---|
| Single long-lived agents degrade under context pressure | Multi-agent execution with short-lived, scoped workers |
| Runtime output is ambiguous or lossy | Typed orchestrator state plus canonical runtime events and durable conversation frames |
| Planning and review drift without strong authority boundaries | Gatekeeper owns decisions; orchestrator owns durable state and workflow transitions |
| Provider logs are hard to use as UI history | Orchestrator-owned conversation stream and history store back the TUI directly |

### Target Platform

- Linux-like environments including Linux and WSL
- Python >= 3.11
- Textual / Rich for the TUI
- Codex CLI as the initial provider backend

---

## 2. Design Philosophy

### 2.1 Everything Is an Agent

All non-trivial work is performed by agent processes. Code execution, validation, merge conflict resolution, and Gatekeeper planning/review are agent-driven. The Python process remains an orchestrator, not an executor of project tasks.

### 2.2 Orchestrator-Owned Durable State

The orchestrator is the sole writer for durable project state under `.vibrant/`, including:

- workflow session state
- roadmap persistence
- consensus persistence
- question records
- attempt records
- review tickets
- agent records
- conversation history

The Gatekeeper does not mutate files directly and does not update state by prose output. It issues commands. The orchestrator validates, persists, and publishes those changes.

### 2.3 Typed Mutation Path

The Gatekeeper controls planning, review, pause/resume, and user-question requests only through typed MCP tools. The orchestrator must not infer authoritative decisions from:

- free-form Gatekeeper text
- roadmap diffs
- task summaries
- provider-native transcripts

Typed commands are the authority path.

### 2.4 Consensus as Contract, Workflow as Authority

The consensus pool remains the shared project contract for new agents: goals, decisions, current context, and onboarding guidance. However, **workflow state is authoritative in orchestrator state**, not in `consensus.md`. If workflow status is mirrored into consensus metadata, that value is a **one-way projection** from the workflow state machine.

### 2.5 Just-in-Time Context

Agents receive only the project files, skills, and roadmap context needed for the current task. The orchestrator assembles prompts and capability bindings at spawn time.

### 2.6 Human-in-the-Loop, Not Human-in-the-Way

The user is interrupted only for high-level product, UX, or architecture decisions. Technical decisions are Gatekeeper-owned unless they require escalation. User answers are host-owned: the orchestrator records, routes, and resolves them.

---

## 3. System Architecture

### 3.1 High-Level Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                          VIBRANT TUI                         │
│  roadmap │ conversations │ consensus │ review/questions │ logs │
└───────────────────────────────┬──────────────────────────────┘
                                │
                    ┌───────────▼───────────┐
                    │  Orchestrator Control │
                    │        Plane          │
                    └───────────┬───────────┘
                                │
         ┌──────────────────────┼──────────────────────┐
         │                      │                      │
 ┌───────▼────────┐   ┌─────────▼──────────┐   ┌──────▼──────────┐
 │ Gatekeeper     │   │ Workflow / Review  │   │ Conversation /  │
 │ Lifecycle      │   │ / Execution Policy │   │ Runtime Event   │
 │ + MCP Binding  │   │ + Workspace        │   │ Projection      │
 └───────┬────────┘   └─────────┬──────────┘   └──────┬──────────┘
         │                      │                      │
   ┌─────▼─────┐         ┌──────▼──────┐       ┌──────▼──────┐
   │ Gatekeeper│         │ Worker/Test │       │ Provider     │
   │ Agent     │         │ /Merge      │       │ Adapters     │
   └───────────┘         └─────────────┘       └──────────────┘
```

### 3.2 Component Overview

| Component | Type | Responsibility |
|---|---|---|
| **Orchestrator Control Plane** | Python service | Global workflow coordination, user routing, conversation recording, event publication, snapshots and subscriptions |
| **Agent Session Binding** | Python service | Assign role-scoped MCP capabilities to Gatekeeper, workers, validators, and merge agents |
| **Gatekeeper Lifecycle** | Python service | Spawn, resume, interrupt, stop, and restart the Gatekeeper using generic runtime handles |
| **Workflow Policy** | Python service | Task eligibility, leases, task-state transitions, blocking rules, completion detection |
| **Execution Coordinator** | Python service | Attempt creation, workspace prep, prompt assembly, worker runtime, validation orchestration, artifact collection |
| **Review Control** | Python service | Review ticket creation, resolution, merge follow-up, retry/escalation routing |
| **Runtime Service** | Python service | Generic start/resume/wait/interrupt/kill for all agents plus canonical runtime event publication |
| **Conversation Stream** | Python service | TUI-facing conversation frames, durable history, subscriptions, replay, recovery |
| **Workspace Service** | Python service | Worktree creation, reset, diff collection, merge, discard |
| **Gatekeeper** | Agent process | Planning and review decisions issued through typed MCP calls |
| **Workers / Validators / Merge Agents** | Agent processes | Task execution, validation, merge conflict resolution with no control-plane authority |

### 3.3 Authority Model

| Role | Owns Decisions? | Writes `.vibrant/` State? | Controls Workflow? |
|---|---|---|---|
| **Orchestrator** | No product decisions, yes system transitions | Yes | Yes |
| **Gatekeeper** | Yes, for planning/review/escalation | No, only through MCP tools | Indirectly, through typed commands |
| **Workers** | No | No | No |

---

## 4. Data Model & Storage

### 4.1 Directory Structure

All orchestrator-owned state lives under `.vibrant/` at the project root.

```
<project-root>/
├── .vibrant/
│   ├── .gitignore
│   ├── vibrant.toml
│   ├── roadmap.md
│   ├── consensus.md
│   ├── consensus.history/
│   ├── attempts.json
│   ├── questions.json
│   ├── reviews.json
│   ├── state.json
│   ├── skills/
│   ├── agents/
│   │   └── *.json
│   ├── conversations/
│   │   ├── manifests/
│   │   └── frames/
│   ├── prompts/
│   └── logs/
│       └── providers/
│           ├── native/
│           └── canonical/
└── ...
```

### 4.2 Workflow State (`state.json`)

`state.json` stores only non-derivable workflow/session facts. It must not be used as a grab-bag for projections that can be reconstructed elsewhere.

Persisted facts:

- `session_id`
- `started_at`
- `workflow_status`
- `concurrency_limit`
- `gatekeeper_session`
- `total_agent_spawns`

Do not persist:

- active agent lists
- pending questions
- derived conversation history
- consensus version mirrors
- provider-runtime maps that can be recovered from agent records

### 4.3 Roadmap and Task State

`roadmap.md` remains the durable, human-readable task plan. The roadmap store owns:

- task creation
- task definition updates
- definition versions
- task ordering
- task state projection

Task lifecycle authority lives in workflow policy, not in arbitrary callers patching statuses.

### 4.4 Consensus Store

`consensus.md` remains human-readable and versioned. The orchestrator writes it in response to typed MCP commands such as consensus updates or appended decisions. The Gatekeeper never writes the file directly.

### 4.5 Question Store (`questions.json`)

Question records are stable, durable objects with:

- stable `question_id`
- routing metadata
- source conversation and turn references
- blocking scope
- resolution or withdrawal status

Question resolution is host-owned.

### 4.6 Attempt Store (`attempts.json`)

Execution is **attempt-centric**. Each worker attempt persists:

- attempt identity
- task identity
- frozen task-definition version
- workspace id
- worker / validator / merge agent ids
- conversation id
- lifecycle status
- timestamps

Attempt state is separate from roadmap task state.

### 4.7 Review Ticket Store (`reviews.json`)

Review tickets are attempt-scoped, not task-singletons. Accept/retry/escalate decisions resolve the ticket and then drive workflow transitions or merge follow-up.

### 4.8 Agent Records (`agents/*.json`)

Agent records remain the durable source of truth for per-agent lifecycle, provider resume metadata, log paths, and summary/error data.

### 4.9 Conversation Store

The TUI must read orchestrator-owned conversation frames, not provider-native transcripts. Conversation history is durable and replayable. Provider logs remain fallback/debug artifacts only.

---

## 5. Core Workflows

### 5.1 Planning Flow

1. The user submits a proposal.
2. The control plane records the host message into the Gatekeeper conversation history.
3. The Gatekeeper lifecycle service starts or resumes the Gatekeeper session.
4. The Gatekeeper reads roadmap, consensus, questions, and workflow state through MCP resources.
5. The Gatekeeper issues typed MCP commands to update roadmap, consensus, or open/withdraw questions.
6. Stores persist changes immediately.
7. The control plane updates snapshots and TUI subscriptions.
8. Planning ends only when the Gatekeeper explicitly calls the semantic planning completion command.

### 5.2 Execution Flow

1. Workflow policy selects ready tasks and leases them.
2. Execution coordinator freezes the task-definition version and prepares a workspace.
3. A code agent is spawned for one attempt.
4. Runtime canonical events are published and projected into conversation frames.
5. Validation runs, if required, before review begins.
6. Execution coordinator returns an `AttemptCompletion`.
7. Workflow policy moves the task into `review_pending`.
8. Review control creates an attempt-scoped review ticket.
9. The Gatekeeper reads the review ticket through MCP and explicitly accepts, retries, or escalates it.
10. Review control applies the decision and drives merge follow-up when needed.

### 5.3 User-Question Flow

1. The Gatekeeper requests a user decision through a typed MCP tool.
2. The question store persists a stable record with routing metadata.
3. Workflow policy blocks the affected path.
4. The user answers through the host UI.
5. The control plane records the answer in conversation history, resolves the question, and forwards the answer into the active Gatekeeper session.

### 5.4 Recovery Flow

1. Stores load durable facts on startup.
2. Conversation history is rebuilt from stored frames.
3. Agent records supply resume metadata and log paths.
4. The orchestrator attempts provider-thread resume where possible.
5. Missing or stale provider threads are treated as runtime recovery failures, not as implicit control-plane decisions.

---

## 6. Component Specifications

### 6.1 Orchestrator Control Plane

Responsibilities:

1. Own the workflow state machine.
2. Route user chat, user answers, and workflow commands.
3. Record host-originated conversation entries before agent submission.
4. Publish canonical runtime events to subscribers.
5. Coordinate Gatekeeper lifecycle, workflow policy, execution, review, and completion detection.

It must not parse markdown directly, manage worktrees directly, or infer decisions from text output.

### 6.2 Agent Session Binding

This service binds role-scoped MCP capability sets to agent sessions. It keeps authorization and provider binding metadata out of Gatekeeper lifecycle and worker execution code.

### 6.3 Gatekeeper Lifecycle

This service owns only Gatekeeper runtime lifecycle:

- start / resume
- submit message
- interrupt active turn
- stop or restart session
- publish session snapshots

It does not apply workflow transitions or persist roadmap/consensus/question mutations directly.

### 6.4 MCP Control Surface

The MCP layer is the authoritative mutation path for the Gatekeeper.

Required read resources:

- consensus
- roadmap
- task
- workflow status
- pending questions
- active agents
- active attempts
- review ticket lookup
- recent domain events

Required write tools:

- update consensus
- add task
- update task definition
- reorder tasks
- request user decision
- withdraw question
- end planning phase
- pause workflow
- resume workflow
- accept review ticket
- retry review ticket
- escalate review ticket

### 6.5 Workflow Policy

Workflow policy owns:

- dispatch eligibility
- dependency blocking
- task-state transitions
- task acceptance/requeue/escalation
- workflow completion detection

Task state and attempt state remain separate.

### 6.6 Execution Coordinator

Execution coordinator owns:

- workspace preparation
- prompt/context assembly
- attempt creation
- worker runtime
- validation orchestration
- artifact collection

It does not decide retry, escalation, or acceptance.

### 6.7 Review Control

Review control owns asynchronous review ticket lifecycle and resolution. It is the single authority that applies accept/retry/escalate review decisions and coordinates merge follow-up.

### 6.8 Runtime

Runtime is the generic agent mechanism shared by Gatekeeper and workers. It must expose:

- start
- resume
- wait
- interrupt
- kill
- canonical event subscriptions

Runtime publishes canonical events only. It does not shape TUI conversation history.

### 6.9 Conversation Stream

Conversation stream owns:

- durable TUI-facing conversation frames
- replay and rebuild
- live subscriptions
- canonical-event to conversation-frame projection

Provider logs are not the primary conversation-history source.

### 6.10 Compatibility Constraints

The redesign requires a migration layer while first-party consumers move to the new model.

Rules:

1. Public facade and MCP compatibility must be resolved before removing first-party entry points.
2. Compatibility aliases may exist temporarily, but they must route into the new semantic command handlers.
3. The redesign must not reintroduce legacy authority paths such as free-form review inference or direct Gatekeeper file writes.

---

## 7. TUI Specification

See `docs/tui.md` for the UI layout and interaction design.

Contractual notes for the redesign:

- The TUI consumes orchestrator snapshots, review/question projections, and processed conversation frames.
- The TUI must not treat provider-native logs as its primary chat history.
- The TUI may expose provider logs and canonical logs as observability/debug views.

---

## 8. Agent Integration — OpenAI Codex CLI

### 8.1 Provider Role

Codex CLI remains the initial agent backend. The orchestrator owns session and thread lifecycle and records normalized canonical events regardless of provider-native wire format.

### 8.2 Canonical Runtime Event Contract

Every canonical event must carry:

- `event_id`
- `sequence`
- `type`
- `timestamp`

Routing fields may include:

- `origin`
- `provider`
- `agent_id`
- `task_id`
- `provider_thread_id`

Required lifecycle coverage:

- assistant message delta + completion
- assistant thinking summary delta + completion
- tool call started + delta + completion
- request opened + resolved
- turn started + completed
- runtime error

Raw hidden reasoning must not enter canonical events or stored conversation history. Only user-facing or summary-level reasoning is allowed.

### 8.3 Provider Logs

Provider logs remain useful for debugging and recovery fallback, but they are not the TUI contract and they are not the control-plane source of truth.

---

## 9. Gatekeeper Specification

### 9.1 Nature

The Gatekeeper is a long-lived Codex agent process managed by the orchestrator. It is a planning and review authority, not a file-writing authority.

### 9.2 Allowed Responsibilities

The Gatekeeper may:

- create or revise the roadmap
- update consensus context and decisions
- request or withdraw user questions
- resolve review tickets by explicit accept/retry/escalate commands
- request workflow pause/resume transitions

The Gatekeeper may not:

- write `.vibrant/` files directly
- mutate workflow state by prose output
- resolve user answers itself
- control worker lifecycle directly

### 9.3 Prompt Expectations

The Gatekeeper prompt must instruct the agent to:

1. read project state through MCP resources
2. express durable changes through typed MCP tools
3. keep natural-language output informational only
4. request user intervention only for high-level decisions

---

## 10. Consensus-Driven Workflow

### 10.1 Consensus Role

The consensus pool is the durable project contract for new agents. It should remain short, readable, and onboarding-oriented.

It records:

- objectives
- design decisions
- current context
- active plan guidance

### 10.2 Consensus Update Rules

1. The orchestrator writes `consensus.md`.
2. The Gatekeeper updates consensus only by typed MCP commands.
3. Every write increments the consensus version and snapshots the prior file into `consensus.history/`.
4. Workflow status in consensus metadata, if retained, is a one-way projection from workflow state.
5. There must be no two-way auto-sync loop between consensus status and workflow state.

---

## 11. Validation & Self-Correction

### 11.1 Validation Pipeline

Validation is part of execution orchestration, not of review inference.

Pipeline:

1. Code agent attempt completes.
2. Validation agents run when required.
3. Execution coordinator returns an `AttemptCompletion` with validation evidence.
4. Review control opens a ticket.
5. Gatekeeper explicitly resolves the ticket through typed review tools.

### 11.2 Retry and Escalation

Retry is review-driven and attempt-scoped. A retry creates a new attempt against a versioned task definition. Escalation blocks the task until user or Gatekeeper action resolves it.

---

## 12. Git Workflow & Isolation

### 12.1 Workspace Model

The workspace service owns:

- prepare task workspace
- collect review diff
- reset workspace
- merge task result
- discard workspace

### 12.2 Merge Process

1. Accepted work is merged by the orchestrator.
2. Merge conflicts create merge follow-up handling through review control and merge agents.
3. Merge failure is modeled as a review/control-plane event, not as an implicit text verdict.

---

## 13. Error Handling & Resilience

### 13.1 Recovery

On restart, the orchestrator reconstructs durable state from:

- workflow state store
- roadmap and consensus stores
- attempt store
- question store
- review ticket store
- agent records
- conversation manifests and frames

Provider logs may be replayed only as explicit fallback when conversation history is incomplete.

### 13.2 Durability Rules

- durable stores must be written atomically
- agent records must preserve provider resume metadata safely
- provider logs are best-effort observability artifacts
- control-plane facts must not be derived from lossy provider output

---

## 14. v1 Scope & Non-Goals

### 14.1 In Scope (v1)

| Feature | Details |
|---|---|
| Orchestrator control plane | Workflow coordination, durable state ownership, agent lifecycle |
| Codex CLI integration | Runtime/session management, canonical event normalization |
| Gatekeeper MCP control | Typed planning/review/user-question mutation path |
| Attempt-centric execution | Task leasing, frozen task definitions, validation before review |
| Durable conversation history | TUI-facing conversation frames owned by orchestrator |
| Review tickets | Attempt-scoped accept/retry/escalate decisions |
| Workspace isolation | Worktrees, diff collection, merge/discard/reset |
| TUI integration | Snapshot reads, conversation subscriptions, observability panels |

### 14.2 Non-Goals (v1)

| Feature | Rationale |
|---|---|
| Multi-provider support | Future work; Codex CLI only in v1 |
| Gatekeeper direct file writes | Explicitly removed by design |
| Provider-native logs as chat history | Explicitly removed by design |
| Free-form control inference | Explicitly removed by design |
| Multi-user collaboration | Single operator in v1 |

---

## 15. Acceptance Criteria

| # | Test | Pass Condition |
|---|---|---|
| AC-01 | **Durable state authority** | All durable orchestrator state under `.vibrant/` is written by the orchestrator, not by agent file edits |
| AC-02 | **Typed Gatekeeper mutation path** | Gatekeeper changes roadmap, consensus, review state, and questions only through typed MCP tools |
| AC-03 | **Planning flow** | User proposal produces roadmap and consensus updates through the control plane and persisted stores |
| AC-04 | **Attempt-centric execution** | Each execution attempt persists its own attempt record with frozen task-definition version |
| AC-05 | **Validation before review** | Review tickets are created only after execution and validation evidence are available |
| AC-06 | **Review authority** | Accept/retry/escalate decisions are explicit review commands, not inferred from text output |
| AC-07 | **Conversation ownership** | TUI conversation history is rebuilt from orchestrator-owned frames rather than provider logs |
| AC-08 | **Workflow authority split** | Workflow state remains authoritative even if consensus metadata includes projected status |
| AC-09 | **Crash recovery** | Restart reconstructs workflow, questions, attempts, review tickets, and conversation history from durable stores |
| AC-10 | **Compatibility path** | First-party facade/MCP consumers can migrate without relying on legacy text-based authority paths |

---

## 16. Glossary

| Term | Definition |
|---|---|
| **Attempt** | One concrete execution attempt for a task, with its own workspace, conversation, and validation evidence |
| **Canonical Runtime Event** | A provider-neutral runtime event with stable identity and replay-safe ordering |
| **Consensus Pool** | The orchestrator-owned `consensus.md` artifact that summarizes project context and decisions |
| **Control Plane** | The orchestrator subsystem that coordinates workflow, routing, and durable state transitions |
| **Conversation Frame** | A processed, durable, TUI-facing conversation event derived from host input or canonical runtime events |
| **Gatekeeper** | The planning/review agent that issues typed MCP commands instead of writing files directly |
| **Review Ticket** | An attempt-scoped review object that the Gatekeeper resolves explicitly |
| **Workflow State** | The authoritative orchestrator lifecycle state persisted separately from consensus metadata |
| **Workspace Service** | The subsystem that prepares, resets, diffs, merges, and discards task workspaces |

---

> **End of Specification Document**
>
> This document is the source of truth for the redesigned orchestrator architecture.
