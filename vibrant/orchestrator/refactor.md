# Orchestrator Refactor: Central MCP for Vibrant

## Goal

Refactor `vibrant.orchestrator` into the central MCP-backed control plane for Vibrant.

The orchestrator should become the single agent-facing surface for:

- Gatekeeper powers: manage tasks, update plan state, ask user questions, spawn agents, inspect results, pause/resume workflow.
- Sub-agent powers: read consensus, read roadmap/task context, report progress, return results, raise blockers, request review.
- Vibrant-specific features: structured access to consensus, roadmap, runtime state, event history, and agent lifecycle actions.

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
- Agent-specialized Vibrant features are not expressed as a typed interface

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

A small shared helper for machine-managed Markdown sections should be introduced as a support layer for consensus, roadmap, and future orchestrator-owned docs. See [docs/structured_markdown.md](/home/rogerw/project/vibrant/docs/structured_markdown.md).

## New Orchestrator Boundary

The orchestrator package should own a typed internal API, not just workflow helpers.

### Core Services

Recommended internal services:

- `StateStore`: load, persist, and project orchestrator state
- `ConsensusService`: read/update consensus through structured operations
- `RoadmapService`: read/update roadmap and task definitions
- `QuestionService`: create, answer, and resolve user-facing questions
- `AgentRegistry`: register agents, persist runtime metadata, expose status/results
- `TaskExecutionService`: dispatch tasks, spawn execution agents, collect results
- `ReviewService`: route finished work to gatekeeper and apply verdicts
- `WorkflowService`: pause, resume, complete, and reconcile workflow status

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

For shared transport and enforcement, the role-based HTTP MCP authorization layer should be treated as its own internal library. See [mcp_http_authz.md](/home/rogerw/project/vibrant/docs/mcp_http_authz.md).

### Resources

Recommended read surfaces:

- `consensus.current`
- `roadmap.current`
- `task.by_id`
- `task.assigned`
- `agent.status`
- `workflow.status`
- `events.recent`
- `questions.pending`

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
- `agent_spawn`
- `agent_list`
- `agent_result_get`
- `workflow_pause`
- `workflow_resume`

### Sub-Agent Tools

Sub-agents should get a narrower surface:

- `consensus_get`
- `roadmap_get`
- `task_get`
- `task_report_progress`
- `task_report_result`
- `task_report_blocker`
- `review_request`

Sub-agents should not directly mutate consensus or roadmap structure.

### Capability Enforcement

Tools must be role-scoped:

- gatekeeper: plan and workflow authority
- execution agents: task-scoped reporting only
- validation/merge agents: result/report tools only

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

Task results should be represented explicitly rather than inferred from prompt text and markdown diffing alone.

Recommended additions:

- task result records
- blocker records
- gatekeeper review records
- agent assignment metadata

## Recommended Module Structure

Suggested package additions under `vibrant/orchestrator/`:

```text
orchestrator/
├── engine.py                # state store / workflow projection
├── facade.py                # public orchestrator API for app and agents
├── services/
│   ├── consensus.py
│   ├── roadmap.py
│   ├── questions.py
│   ├── agents.py
│   ├── execution.py
│   └── workflow.py
├── mcp/
│   ├── server.py
│   ├── resources.py
│   ├── tools_gatekeeper.py
│   ├── tools_agents.py
│   └── authz.py
├── task_dispatch.py
├── git_manager.py
└── lifecycle.py             # temporary compatibility layer during migration
```

## Migration Plan

### Phase 1: Internal Boundary Cleanup

Refactor `lifecycle.py` so it delegates to smaller services.

Targets:

- move consensus/roadmap mutation out of lifecycle
- move question handling out of engine
- keep `engine.py` focused on durable state and workflow status projection
- introduce `OrchestratorFacade` and route TUI calls through it

### Phase 2: Structured Question and Result Models

Add explicit records for:

- user questions
- task results
- blockers
- reviews

Update state persistence and UI projections to use these structured records.

### Phase 3: MCP Server Introduction

Add the orchestrator MCP server with:

- read resources
- gatekeeper tools
- sub-agent tools
- role-based authorization

At this phase, the MCP surface can coexist with the current prompt-driven filesystem flow.

### Phase 4: Gatekeeper Migration

Migrate gatekeeper from direct `.vibrant/` writes to orchestrator MCP calls.

Changes:

- prompt updated to use tools rather than directly editing markdown
- consensus and roadmap writes routed through orchestrator services
- question creation routed through `QuestionService`

Gatekeeper remains the policy authority, but persistence authority moves to orchestrator.

### Phase 5: Sub-Agent Migration

Migrate code/test/merge agents to orchestrator MCP access for:

- reading assigned task context
- reading consensus
- reporting results and blockers
- requesting review

Sub-agents keep filesystem access for project code changes in their worktrees, but not for orchestrator-owned control-plane state.

### Phase 6: Permission Tightening

Once the MCP path is stable:

- remove direct agent write access to `.vibrant/consensus.md`
- remove direct agent write access to `.vibrant/roadmap.md`
- restrict agent-facing Vibrant operations to MCP tools only

## Compatibility Strategy

This refactor should be incremental.

Recommended compatibility approach:

- keep `CodeAgentLifecycle` as a compatibility shell during migration
- keep markdown artifacts as source-of-truth outputs
- preserve current TUI behavior while changing the underlying service boundary
- migrate one agent role at a time, starting with gatekeeper

## Why This Is the Right Split

This design preserves what already works:

- durable markdown artifacts
- roadmap parsing/writing
- provider adapter architecture
- agent records and workflow state

But it fixes the current architectural problem:

- orchestrator logic is currently spread across prompts, lifecycle code, and file mutations

After the refactor:

- orchestrator is the control plane
- MCP is the agent-facing protocol
- markdown is the persisted representation
- TUI, recovery, and agents all operate on the same typed orchestration layer

## First Implementation Slice

The best first slice is:

1. Add `OrchestratorFacade`
2. Extract `ConsensusService`, `RoadmapService`, and `QuestionService`
3. Change TUI to use the facade instead of reaching into lifecycle/engine internals
4. Introduce structured question records
5. Add a minimal MCP server with read resources and question/report tools

This gives immediate architectural benefit without requiring a full agent prompt rewrite on day one.
