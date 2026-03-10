# Orchestrator Stable API

This document defines the **current stable public API** for the orchestrator package.

## Status

As of March 10, 2026, the stable API for external consumers is:

- `OrchestratorFacade`
- `OrchestratorMCPServer`
- `OrchestratorSnapshot`
- `CodeAgentLifecycleResult`

`OrchestratorFacade` and `OrchestratorSnapshot` are the intended long-term
integration surface for the TUI, MCP-facing code, and future orchestrator
refactors.

`CodeAgentLifecycleResult` remains exported for legacy execution-control flows,
but it should be treated as a compatibility type rather than the preferred
app-facing contract.

## Design Goal

External components should depend on a **small, stable facade** instead of reaching into orchestrator internals such as:

- `OrchestratorEngine`
- engine state layout
- service composition
- consensus persistence details
- question service wiring

This allows the orchestrator internals to change without forcing matching changes in every caller.

## Stable Read API

### `OrchestratorSnapshot`

`OrchestratorSnapshot` is the stable read model returned by `OrchestratorFacade.snapshot()`.

Fields:

- `status: OrchestratorStatus`
- `pending_questions: tuple[str, ...]`
- `question_records: tuple[QuestionRecord, ...]`
- `roadmap: RoadmapDocument | None`
- `consensus: ConsensusDocument | None`
- `consensus_path: Path | None`
- `agent_records: tuple[AgentRecord, ...]`
- `execution_mode: RoadmapExecutionMode | None`
- `user_input_banner: str`
- `notification_bell_enabled: bool`

Use the snapshot when a caller wants a coherent, read-only view of orchestrator-backed state.

### `OrchestratorFacade` read methods

Stable read methods:

- `snapshot()`
- `workflow_status()`
- `consensus_document()`
- `roadmap()`
- `task(task_id)`
- `consensus_source_path()`
- `agent_records()`
- `task_summaries()`
- `pending_questions()`
- `question_records()`
- `pending_question_records()`
- `current_pending_question()`
- `user_input_banner()`
- `notification_bell_enabled()`
- `roadmap_document`
- `execution_mode`

These methods should remain available even if the engine or service layout changes internally.

## Stable Action API

Stable action methods on `OrchestratorFacade`:

- `submit_gatekeeper_message(text)`
- `answer_pending_question(answer, *, question=None)`
- `update_consensus(...)`
- `add_task(task, *, index=None)`
- `update_task(task_id, **updates)`
- `reorder_tasks(ordered_task_ids)`
- `ask_question(text, ...)`
- `resolve_question(question_id, *, answer=None)`
- `pause_workflow()`
- `resume_workflow()`

These methods are the preferred stable write or workflow-intent entrypoints for
external integrations.

## Compatibility Action API

The following `OrchestratorFacade` methods remain available for legacy callers,
but they are compatibility entrypoints and should not be used as the preferred
long-term contract for new integrations:

- `reload_from_disk()`
- `execute_until_blocked()`
- `execute_next_task()`
- `can_transition_to(next_status)`
- `transition_workflow_state(next_status)`

These methods primarily expose persistence or runtime-driving behavior that may
continue to change as the orchestrator refactor converges on a service-backed
control plane.

## Compatibility Surface

`OrchestratorFacade.engine` currently exists for **backward compatibility**.

Rules:

- If a real lifecycle engine exists, `facade.engine` returns that underlying engine.
- If no engine exists, the facade may return a compatibility wrapper.
- New code should **not** treat `facade.engine` as the stable API.

`LegacyOrchestratorEngineView` and `LegacyOrchestratorStateView` are compatibility helpers only. They are not the long-term contract.

Likewise, `CodeAgentLifecycleResult` remains export-stable for compatibility,
but new code should avoid depending on detailed execution internals such as raw
Gatekeeper results, merge results, runtime events, or worktree paths unless
that data is explicitly promoted into a future stable read model.

## MCP Surface

`OrchestratorMCPServer` is the typed in-process MCP registry for the document
and workflow control plane. It uses the shared scope definitions in
`vibrant/mcp/authz.py` rather than a separate orchestrator-local auth model.

Current scope-gated resources and tools cover:

- consensus reads and updates
- roadmap reads and task mutations
- structured question reads and resolution
- workflow pause/resume

## Not Stable

The following are **not** stable external APIs and may change during future refactors:

- `OrchestratorEngine`
- direct access to `engine.state`
- direct access to `engine.agents`
- direct access to `engine.consensus`
- direct access to `engine.consensus_path`
- orchestrator service classes under `vibrant/orchestrator/services/`
- internal fallback behavior used only to preserve legacy callers

If an external component needs one of these, prefer adding a facade method instead of reaching through to engine internals.

## Current Caveat

The TUI still contains direct `engine` access in `vibrant/tui/app.py` for legacy behavior preservation.
That means the **stable API exists now**, but the whole app has not yet been fully migrated to depend on it exclusively.

So the current state is:

- stable API is present
- compatibility layer lives in `vibrant/orchestrator/`
- full decoupling is not complete yet

## Guidance For Future Refactors

When refactoring the orchestrator system:

1. Preserve `OrchestratorFacade` method names and behavior.
2. Preserve `OrchestratorSnapshot` field meanings.
3. Prefer semantic intent methods over generic runtime-driver methods.
4. Avoid introducing new external dependencies on engine internals.
5. Add new public needs to the facade first.
6. Treat `facade.engine` and `execute_*` helpers as legacy compatibility, not as the preferred integration path.

## Minimal Example

```python
from vibrant.orchestrator import OrchestratorFacade

facade = OrchestratorFacade(lifecycle)

snapshot = facade.snapshot()
status = facade.workflow_status()
questions = facade.pending_questions()

facade.pause_workflow()
facade.resume_workflow()
```

## Regression Coverage

Compatibility expectations for this surface are currently covered by:

- `tests/test_orchestrator_facade.py`

That file should be extended whenever the facade contract changes.
