# Orchestrator Refactor: Central MCP for Vibrant

> Historical note: this document describes the pre-bootstrap orchestrator
> structure and migration plan. Current code now boots through `bootstrap.py`
> and no longer includes `engine.py` or `lifecycle.py`.

## Goal

Refactor `vibrant.orchestrator` into the central MCP-backed control plane for Vibrant.

The orchestrator should become the single agent-facing surface for:

- Gatekeeper powers in the current slice: manage tasks, update plan state, ask user questions, and pause/resume workflow.
- Sub-agent powers in the current slice: read consensus, read roadmap/task context, and consume typed orchestration state through MCP.
- Vibrant-specific features in the current slice: structured access to consensus, roadmap, workflow state, and questions.

For now, agent spawn, agent handles, and agent result/reporting flows are intentionally deferred. That work is being reshaped by the separate unified agent base / handle / result refactor, and this document focuses on the orchestrator-owned control-plane pieces that can move independently.

The important shift is from:

- prompt instructions + direct `.vibrant/` file edits

to:

- typed orchestrator services exposed through an MCP interface

The markdown files remain the durable human-readable artifacts, but agents should interact with them through orchestrator APIs instead of editing them directly.

## Current State

The current code is split across three main responsibilities:

- `engine.py`: durable state and workflow projection
- `lifecycle.py`: task execution, gatekeeper routing, roadmap merging, workflow coordination
- `gatekeeper.py`: gatekeeper prompt/run wrapper with direct authority to update `consensus.md` and `roadmap.md`

This means the effective orchestration logic is distributed. `OrchestratorEngine` is mostly a state store, while `CodeAgentLifecycle` is the real coordinator. Agent-facing capability is still implicit:

- Gatekeeper gets power from prompt text plus filesystem access
- Code agents get power from injected prompt context plus worktree access
- Consensus, roadmap, and question flows are not expressed as a typed interface yet
- Lifecycle execution and result handling are mixed into the current coordinator boundary

## Target Architecture

### Design Principle

The orchestrator should be the single authority for agent-visible project state and orchestration actions.

Agents should not be trusted with direct mutation of orchestrator-owned artifacts. Instead:

- agents call orchestrator MCP tools
- orchestrator validates and applies the request
- orchestrator persists changes to markdown/state artifacts
- TUI and recovery logic consume the same orchestrator state

### Layering

Recommended layering:

1. Durable models and persistence
2. Orchestrator domain services
3. Orchestrator facade
4. MCP server surface
5. Provider adapter wiring for agent sessions

### Durable Models and Persistence

Keep and extend the existing durable artifacts:

- `.vibrant/state.json`
- `.vibrant/consensus.md`
- `.vibrant/roadmap.md`
- `.vibrant/agents/*.json`
- provider event logs

Persistence should continue to flow through existing structured helpers where possible:

- `vibrant.consensus.parser`
- `vibrant.consensus.writer`
- `vibrant.consensus.roadmap`


## New Orchestrator Boundary

The orchestrator package should own a typed internal API, not just workflow helpers.

### Core Services

Recommended internal services for the current slice:

- `StateStore`: load, persist, and project orchestrator state
- `ConsensusService`: read/update consensus through structured operations
- `RoadmapService`: read/update roadmap and task definitions
- `QuestionService`: create, answer, and resolve user-facing questions
- `WorkflowService`: pause, resume, complete, and reconcile workflow status

Deferred follow-up services after the agent refactor lands:

- `AgentRegistry`: register agents, persist runtime metadata, expose status/results
- `TaskExecutionService`: dispatch tasks, spawn execution agents, collect results
- `ReviewService`: route finished work to gatekeeper and apply verdicts

### Facade

Add one high-level entry point for the rest of the app:

- `OrchestratorFacade`

This facade should be the API used by:

- TUI
- Gatekeeper integration
- code/test/merge agents
- future external automation

This removes the current split where the UI reaches into both lifecycle and engine internals.

## MCP Surface

The MCP layer should sit on top of orchestrator services and expose role-scoped tools and resources.

`OrchestratorFacade` is a useful stable surface for many MCP handlers, but it is
not the only allowed dependency. First-party MCP wiring may call internal
services directly when that gives a cleaner implementation or exposes runtime
capabilities that have not been promoted into the facade yet.

For shared transport and enforcement, the role-based HTTP MCP authorization layer should be treated as its own internal library. See [mcp_http_authz.md](/home/rogerw/project/vibrant/docs/mcp_http_authz.md).

### MCP TODO / Status

Current implementation status for this slice:

- [x] `OrchestratorMCPServer` exists as an in-process typed registry over `OrchestratorFacade`
- [x] Shared scope-based authorization is enforced via `vibrant.mcp.authz`
- [x] Read resources implemented: `consensus.current`, `roadmap.current`, `task.by_id`, `workflow.status`, `questions.pending`
- [x] Gatekeeper tools implemented: `consensus_get`, `consensus_update`, `roadmap_get`, `roadmap_add_task`, `roadmap_update_task`, `roadmap_reorder_tasks`, `question_ask_user`, `question_resolve`, `workflow_pause`, `workflow_resume`
- [x] Sub-agent read tools implemented: `consensus_get`, `roadmap_get`, `task_get`
- [ ] Wire MCP into live Gatekeeper and sub-agent runtime sessions as the primary control-plane path
- [ ] Add deferred read surfaces: `task.assigned`, `agent.status`, `events.recent`
- [ ] Add deferred reporting/spawn tools after the unified agent result model lands

### Resources

Recommended read surfaces for the current slice:

- `consensus.current`
- `roadmap.current`
- `task.by_id`
- `workflow.status`
- `questions.pending`

Deferred read surfaces:

- `task.assigned`
- `agent.status`
- `events.recent`

### Gatekeeper Tools

The gatekeeper should have privileged tools such as:

- `consensus_get`
- `consensus_update`
- `roadmap_get`
- `roadmap_add_task`
- `roadmap_update_task`
- `roadmap_reorder_tasks`
- `question_ask_user`
- `question_resolve`
- `workflow_pause`
- `workflow_resume`

Deferred gatekeeper tools:

- `agent_spawn`
- `agent_list`
- `agent_result_get`

### Sub-Agent Tools

Sub-agents should get a narrower surface in the current slice:

- `consensus_get`
- `roadmap_get`
- `task_get`

Deferred sub-agent tools:

- `task_report_progress`
- `task_report_result`
- `task_report_blocker`
- `review_request`

Sub-agents should not directly mutate consensus or roadmap structure.

Even with the new agent-base hierarchy in place, these deferred tools still need
orchestrator support for durable agent handles, result/reporting records,
awaiting-input state, and role-scoped execution wiring.

### Capability Enforcement

Tools must be role-scoped:

- gatekeeper: plan and workflow authority
- execution agents in the current slice: read-only task context
- validation/merge agents: deferred with the unified result model

The MCP layer should enforce this instead of relying on prompt wording.

## Data Model Changes

The current state model is too thin for a real agent-facing control plane.

### Questions

Replace the current `pending_questions: list[str]` shape with structured records, for example:

- `question_id`
- `source_agent_id`
- `source_role`
- `text`
- `priority`
- `status`
- `answer`
- `created_at`
- `resolved_at`

This is necessary for:

- reliable MCP tool calls
- TUI correlation
- replay/recovery
- auditability

### Task and Result Records

This remains important, but it is not part of the first orchestrator cleanup slice.

Task results should be represented explicitly rather than inferred from prompt text and markdown diffing alone.

Recommended additions:

- task result records
- blocker records
- gatekeeper review records
- agent assignment metadata

### Agent Handle and Request State

The agent-base refactor gives us a reusable single-run primitive, but the
orchestrator still needs explicit durable state for agent handles and
interactive/runtime coordination.

Recommended additions:

- durable provider-thread handle fields surfaced through the facade
- pending request metadata for `request.opened` / `AWAITING_INPUT` flows
- first-registration spawn accounting semantics for `total_agent_spawns`
- normalized agent run/result snapshots for UI, recovery, and MCP callers

## Recommended Module Structure

Suggested package shape under `vibrant/orchestrator/` for the current slice:

```text
orchestrator/
├── engine.py                # state store / workflow projection
├── facade.py                # public orchestrator API for app and agents
├── agents/
│   ├── manager.py
│   ├── registry.py
│   ├── runtime.py
│   └── store.py
├── artifacts/
│   ├── consensus.py
│   ├── planning.py
│   ├── questions.py
│   ├── roadmap.py
│   └── workflow.py
├── execution/
│   ├── git_workspace.py
│   ├── prompts.py
│   ├── retry_policy.py
│   ├── review.py
│   └── service.py
├── state/
│   ├── backend.py
│   ├── projection.py
│   └── store.py
├── mcp/
│   ├── server.py
│   ├── resources.py
│   ├── tools_gatekeeper.py
│   ├── tools_agents.py
│   └── authz.py
└── lifecycle.py             # retained temporarily until the agent refactor lands
```

Deferred additions after the agent refactor stabilizes:

- spawn/result-oriented MCP tools and resources
- thinner lifecycle compatibility shims as execution moves behind the runtime boundary
- any review/runtime/dispatch modules that still depend on the legacy execution path

## Migration Plan

### Phase 1: Document and Workflow Boundary Cleanup

Refactor the orchestrator boundary around document and workflow services first.

Targets:

- move consensus and roadmap mutation behind service-owned APIs
- move question handling out of engine
- keep `engine.py` focused on durable state and workflow status projection
- introduce `OrchestratorFacade` and route TUI calls through it
- remove direct caller dependence on `facade.engine`

### Phase 2: Structured Question Model

Add explicit records for:

- user questions

Update state persistence and UI projections to use these structured records.

### Phase 3: MCP Server Introduction

Add the orchestrator MCP server with:

- read resources
- gatekeeper tools
- sub-agent read tools
- role-based authorization

At this phase, the MCP surface can coexist with the current prompt-driven filesystem flow.

### Phase 4: Gatekeeper Migration

Migrate gatekeeper from direct `.vibrant/` writes to orchestrator MCP calls for the document and workflow surface.

Changes:

- prompt updated to use tools rather than directly editing markdown
- consensus and roadmap writes routed through orchestrator services
- question creation routed through `QuestionService`

Gatekeeper remains the policy authority, but persistence authority moves to orchestrator.

### Phase 5: Sub-Agent Context Migration

Migrate code/test/merge agents to orchestrator MCP access for:

- reading assigned task context
- reading consensus

At this stage, the orchestrator should also stop treating the shared agent base
as sufficient by itself and explicitly add:

- `MergeAgent` wiring in the merge-conflict path
- a concrete read-only `TestAgent` or validation-agent execution path
- resume/awaiting-input support needed for durable agent handles

Result reporting, blockers, review requests, and spawn/result lifecycle calls remain deferred until the unified agent result system is in place.

Sub-agents keep filesystem access for project code changes in their worktrees, but not for orchestrator-owned control-plane state.

### Deferred Follow-up: Agent Runtime and Result Migration

After the unified agent base / handle / result refactor stabilizes, add:

- `AgentBase` adoption inside orchestrator runtime services so the lifecycle is implemented once
- spawn/result-oriented MCP tools
- typed agent/runtime/result records
- explicit awaiting-input / request-opened handling that can pause and resume runs cleanly
- registry-owned spawn accounting and agent-record factory rules
- review and blocker reporting surfaces
- permission tightening around the old lifecycle execution path

### Phase 6: Permission Tightening

Once the MCP path is stable:

- remove direct agent write access to `.vibrant/consensus.md`
- remove direct agent write access to `.vibrant/roadmap.md`
- restrict document-oriented agent-facing Vibrant operations to MCP tools only

## Compatibility Strategy

This refactor should be incremental.

Recommended compatibility approach:

- keep `CodeAgentLifecycle` as a compatibility shell during this slice
- keep markdown artifacts as source-of-truth outputs
- preserve current TUI behavior while changing the underlying service boundary
- migrate one document/control-plane surface at a time, starting with gatekeeper-facing roadmap, consensus, and questions
- revisit lifecycle removal only after the separate agent-side refactor lands

## Why This Is the Right Split

This design preserves what already works:

- durable markdown artifacts
- roadmap parsing/writing
- provider adapter architecture
- workflow state

But it fixes the current architectural problem:

- orchestrator logic for roadmap, consensus, and questions is currently spread across prompts, lifecycle code, and file mutations

After the refactor:

- orchestrator owns the document and workflow control plane
- MCP is the agent-facing protocol for those surfaces
- markdown is the persisted representation
- TUI, recovery, and agents can read the same typed roadmap/consensus/question/workflow layer
- agent spawn/result mechanics can migrate later without blocking this cleanup

## First Implementation Slice

The best first slice is:

1. Make `OrchestratorFacade` the stable entrypoint for roadmap, consensus, questions, and workflow
2. Extract or finish `ConsensusService`, `RoadmapService`, and `QuestionService` as the only document/control-plane owners
3. Change TUI and gatekeeper integration to use the facade instead of reaching into lifecycle/engine internals
4. Introduce structured question records
5. Add a minimal MCP server with read resources plus roadmap/consensus/question/workflow tools

This gives immediate architectural benefit without requiring a full agent prompt rewrite on day one.
