# TUI Rework Migration Instructions

Checked against these branches on March 14, 2026:

- Current orchestrator branch: `refactor/orchestor-rebirth`
- Incoming TUI branch: `refactor/tui-rework`

## Goal

Merge the TUI work from `refactor/tui-rework` without reintroducing the old orchestrator architecture.

The migration target is the orchestrator in `refactor/orchestor-rebirth`. The old orchestrator in `refactor/tui-rework` should be used only as reference material for understanding previous TUI expectations.

## Decision

Keep the orchestrator from `refactor/orchestor-rebirth` and migrate the TUI onto its contracts.

Do not merge the old orchestrator implementation from `refactor/tui-rework`.

## Why

The current branch already establishes the new authority model:

- orchestrator-owned durable stores under `.vibrant/`
- a control plane for host actions
- orchestrator-owned conversation streams
- typed question records and review tickets
- a compatibility facade layered on top of the new model

The old branch is built around a different model:

- `StateStore`, `PlanningService`, `QuestionService`, `ReviewService`, `TaskExecutionService`, and `GatekeeperRuntimeService`
- a synthetic Gatekeeper thread in the TUI
- raw canonical event callbacks as the primary chat/history integration
- task execution that runs through Gatekeeper review and merge before returning final results

Those models are not equivalent. A direct merge will create semantic conflicts even where method names look similar.

## High-Level Rule Set

1. `vibrant/orchestrator/**` comes from `refactor/orchestor-rebirth`.
2. TUI presentation work from `refactor/tui-rework` may be ported, but only after adapting it to the new orchestrator contracts.
3. Chat/history must use orchestrator-owned conversation streams, not the old synthetic Gatekeeper thread model.
4. Question handling must use `QuestionRecord` and question ids, not plain question strings as the primary control surface.
5. Task execution must respect the new review-ticket flow. Do not restore the old "run task -> Gatekeeper review -> merge -> final accepted/retried result" path.

## Current Target Contracts

The merge should target these surfaces from `refactor/orchestor-rebirth`:

- `Orchestrator`
- `OrchestratorFacade`
- `OrchestratorControlPlane`
- `AgentRuntimeService`
- `ConversationStreamService`
- `QuestionRecord`
- `GatekeeperSubmission`
- `AgentConversationView`
- `AgentStreamEvent`
- review ticket APIs exposed via the facade and orchestrator root

In practice, the TUI already relies on:

- `Orchestrator.run_until_blocked()`
- `Orchestrator.run_next_task()`
- `Orchestrator.snapshot()`
- `Orchestrator.list_recent_events()`
- `Orchestrator.runtime_service.wait_for_run(agent_id)`
- `Orchestrator.runtime_service.subscribe_canonical_events(callback)`
- `Orchestrator.control_plane.submit_user_message(text)`
- `Orchestrator.control_plane.answer_user_decision(question_id, answer)`
- `Orchestrator.control_plane.conversation(conversation_id)`
- `Orchestrator.control_plane.subscribe_conversation(conversation_id, callback)`
- `OrchestratorFacade.snapshot()`
- `OrchestratorFacade.list_question_records()`
- `OrchestratorFacade.list_pending_question_records()`
- `OrchestratorFacade.get_workflow_status()`
- `OrchestratorFacade.get_consensus_document()`
- `OrchestratorFacade.write_consensus_document(document)`
- `OrchestratorFacade.get_task_summaries()`
- `OrchestratorFacade.pause_workflow()`
- `OrchestratorFacade.resume_workflow()`
- `OrchestratorFacade.transition_workflow_state(next_status)`

## Old TUI Assumptions That Must Be Migrated

The old `refactor/tui-rework` TUI assumed the following behaviors:

- `create_orchestrator(project_root, on_canonical_event=callback)` would drive most UI updates through a raw event callback.
- Gatekeeper chat was stored as a synthetic `ThreadInfo` and persisted through `HistoryStore`.
- `submit_gatekeeper_message()` and `answer_pending_question()` returned Gatekeeper-style result objects with transcript/verdict semantics.
- `list_pending_questions()` and `get_current_pending_question()` were sufficient for user-input flow.
- `run_next_task()` often returned final outcomes such as `accepted`, `retried`, or `escalated`.
- consensus and workflow views were refreshed around the old `refresh()` behavior and old state projections.

These assumptions should be treated as migration items, not preserved as architecture.

## API Mapping

| Old TUI expectation | New target | Migration rule |
|---|---|---|
| Synthetic Gatekeeper `ThreadInfo` | `AgentConversationView` + `AgentStreamEvent` | Replace, do not emulate as source of truth |
| Raw `on_canonical_event` chat flow | `control_plane.conversation()` + `subscribe_conversation()` | Use conversation stream for chat/history |
| Plain pending question strings | `QuestionRecord` | Use question ids for answer submission |
| `submit_gatekeeper_message()` returning a Gatekeeper result | `submit_user_message()` returning `GatekeeperSubmission`, then wait on runtime and stream conversation | Update TUI flow to two-step submission + stream |
| `answer_pending_question(answer, question=...)` | `answer_user_decision(question_id, answer)` | Resolve by id, not text |
| `run_next_task()` final review/merge result | `run_next_task()` returning `review_pending` or `awaiting_user` | Surface review tickets explicitly |
| Review inferred from Gatekeeper result | typed review ticket resolution | Use accept/retry/escalate review APIs |
| Persistent chat via `HistoryStore` | persistent orchestrator conversation store | If persistence is needed, persist `conversation_id` or replay store-owned history |

## Files To Keep From `refactor/orchestor-rebirth`

Keep the current versions of these areas:

- `vibrant/orchestrator/**`
- `vibrant/tui/widgets/chat_panel.py`
- `vibrant/tui/widgets/conversation_view.py`
- `vibrant/tui/app.py` as the integration base
- `vibrant/tui/widgets/agent_output.py`
- `vibrant/tui/widgets/consensus_view.py` as the behavioral base for orchestrator-owned consensus state

These files already understand the new control-plane and conversation-stream model, even if they still need cleanup or refinement.

## Files To Treat As Reference Only

Do not take these old implementations wholesale from `refactor/tui-rework`:

- `vibrant/orchestrator/bootstrap.py`
- `vibrant/orchestrator/facade.py`
- `vibrant/orchestrator/gatekeeper_runtime.py`
- `vibrant/orchestrator/artifacts/**`
- `vibrant/orchestrator/agents/**`
- `vibrant/orchestrator/state/**`
- `vibrant/tui/widgets/chat_panel.py`
- `vibrant/tui/widgets/conversation_view.py`
- `vibrant/tui/app.py`

They are tightly coupled to the old orchestrator model.

## Files That Need Manual Porting

These files are the most likely to need deliberate hand-merge work:

- `vibrant/tui/app.py`
- `vibrant/tui/widgets/chat_panel.py`
- `vibrant/tui/widgets/conversation_view.py`
- `vibrant/tui/widgets/consensus_view.py`
- `vibrant/tui/widgets/agent_output.py`

Reason:

- they sit directly on orchestrator contracts
- they encode the Gatekeeper UX model
- they reflect the biggest semantic changes between branches

## Safe Merge Order

1. Start from `refactor/orchestor-rebirth`.
2. Keep `vibrant/orchestrator/**` from the current branch unchanged.
3. Merge or cherry-pick low-risk TUI presentation changes from `refactor/tui-rework` first.
4. Manually port integration-heavy TUI files afterward.
5. Run tests after each integration-heavy file or small file group.

Low-risk here means visual or layout changes that do not alter orchestrator imports or the data model. If a TUI file imports old thread/history models or old orchestrator services, it is not low risk.

## Recommended Migration Steps

### 1. Freeze the Orchestrator Boundary

Before merging TUI code, define the orchestrator boundary the TUI is allowed to use.

Recommended allowed surface:

- facade reads and workflow transitions
- task execution entry points
- runtime canonical-event subscription for agent logs
- control-plane Gatekeeper submission and conversation subscription
- review ticket listing and resolution

Recommended disallowed surface:

- direct store mutation from the TUI
- provider-native log parsing for chat history
- reintroducing old `ThreadInfo` Gatekeeper conversation persistence as the primary chat model

### 2. Add a Thin TUI Adapter

Create a small TUI-facing adapter around the current orchestrator so the app code no longer reaches into multiple internals ad hoc.

The adapter should expose:

- workflow snapshot and current status
- roadmap execution entry points
- question record reads
- Gatekeeper message submit and answer submit
- Gatekeeper conversation bind and subscribe
- runtime event subscribe for agent logs
- review ticket reads and actions

This adapter is optional for correctness, but strongly recommended before merging more TUI work. It reduces future branch conflicts and makes the allowed API explicit.

### 3. Keep the New Conversation Model

Use:

- `control_plane.conversation(conversation_id)`
- `control_plane.subscribe_conversation(conversation_id, callback)`
- `AgentConversationView`
- `AgentStreamEvent`

Do not restore:

- synthetic Gatekeeper `ThreadInfo` assembly as the main chat source
- `HistoryStore` as the authoritative Gatekeeper history store

If persistent Gatekeeper history across app restarts is still needed:

- persist the active `conversation_id`
- replay from the orchestrator conversation store
- or add a dedicated orchestrator-level replay helper

### 4. Migrate Questions to `QuestionRecord`

The merged TUI should use `QuestionRecord` as the real model.

Use:

- `list_question_records()`
- `list_pending_question_records()`
- `answer_user_decision(question_id, answer)`

Avoid designing new logic around:

- plain string question matching
- `get_current_pending_question()` as the only source of truth

String helpers can remain temporarily as compatibility sugar, but the control flow should use ids.

### 5. Rework "Run Task" Around Review Tickets

This is the largest semantic change.

Old behavior:

- execute task
- Gatekeeper reviews it immediately
- merge happens before returning final task outcome

New behavior:

- execute task
- orchestrator returns `review_pending` or `awaiting_user`
- review ticket is created
- review is resolved through typed actions

Migration rule:

- keep the new review-ticket model
- update the TUI to surface pending review tickets and their resolution options
- do not force the new orchestrator back into the old synchronous review pipeline

The TUI should support:

- showing that a task is now waiting for review
- showing review ticket details
- applying accept/retry/escalate decisions explicitly

### 6. Preserve Event-Driven Agent Logs

Keep the current agent-log model:

- bootstrap from `list_recent_events()`
- continue with `runtime_service.subscribe_canonical_events()`

Do not restore the old native-log-tail-centric behavior as the main integration mechanism.

Raw logs may still be useful for debugging, but canonical events should remain the primary TUI input for operational agent status.

### 7. Keep Current Consensus Semantics

The consensus editor/viewer should continue to treat consensus as orchestrator-owned state.

Allowed behavior:

- render current consensus document
- edit the markdown body
- save through `write_consensus_document(document)`

Do not couple the merged consensus widget back to old branch-specific assumptions about state refresh or historical storage models.

### 8. Port Visual Improvements Separately From Data-Model Changes

When resolving conflicts in TUI files:

- port layout, CSS, copy, and interaction polish first
- then rewire the behavior to the new orchestrator contracts

Do not accept a large old file replacement just because the UI looks newer. The integration logic matters more than the markup.

## Temporary Compatibility Shims

These are acceptable temporary shims if they reduce migration cost without reviving the old architecture:

- add `OrchestratorFacade.roadmap_document` as a temporary alias for `get_roadmap()` or `snapshot().roadmap`
- add `OrchestratorFacade.is_notification_bell_enabled()` as a temporary alias for `snapshot().notification_bell_enabled`
- keep helper methods like `list_pending_questions()` as convenience wrappers while the TUI migrates to `QuestionRecord`

These are not acceptable shims:

- reintroducing the old orchestrator service graph
- reintroducing review inference from free-form Gatekeeper text
- reintroducing synthetic chat history as the primary source of truth

## Explicit "Do Not Do" List

Do not:

- merge the old `vibrant/orchestrator/**` tree into the current branch
- restore `StateStore` as the main orchestrator state authority
- restore `GatekeeperRuntimeService` as the primary Gatekeeper host boundary
- restore old `HistoryStore`-based Gatekeeper chat as the main TUI contract
- assume `run_next_task()` should return final accepted/retried outcomes
- infer review outcomes from old Gatekeeper transcripts
- add compatibility layers that mutate files or state outside the new command/store model

## Testing Checklist

The merge should not be considered complete until the following are verified:

### Gatekeeper Conversation

- user messages create or reuse the correct Gatekeeper conversation id
- answering a question resolves the correct `question_id`
- conversation replay works after rebind
- live conversation subscription updates the chat panel incrementally

### Questions

- pending questions render from `QuestionRecord`
- resolved questions show both question and answer
- withdrawn questions are handled cleanly
- new pending questions trigger the correct banner/flash behavior

### Task Execution

- `run_next_task()` can return `review_pending`
- `run_next_task()` can return `awaiting_user`
- automatic mode stops when review or user input blocks the workflow

### Review Flow

- pending review tickets are visible
- accept action resolves ticket and updates roadmap state
- retry action resolves ticket, reapplies prompt or acceptance changes if needed, and requeues correctly
- escalate action resolves ticket and blocks appropriately

### Agent Logs

- bootstrap from `list_recent_events()` works
- live runtime event subscription updates the agent log panel
- agent switching and follow mode still work

### Consensus

- consensus renders when present
- save writes through orchestrator-owned state
- refresh does not discard unsaved user edits unexpectedly

## Suggested Acceptance Condition

The migration is done when all of the following are true:

1. The merged TUI runs entirely on the current orchestrator contracts.
2. No old orchestrator service graph is reintroduced.
3. Gatekeeper chat/history uses orchestrator conversations.
4. User input flow is driven by `QuestionRecord` and question ids.
5. Task review is handled through review tickets rather than old synchronous Gatekeeper review/merge behavior.
6. Temporary compatibility shims are small, explicit, and removable.

## Short Version

If time is tight, follow this rule:

- keep `vibrant/orchestrator/**` from `refactor/orchestor-rebirth`
- keep current `vibrant/tui/app.py` as the integration base
- manually replay `refactor/tui-rework` UI changes onto that base
- do not revive the old synthetic Gatekeeper thread or old synchronous review pipeline
