# Orchestrator Stable API

This document defines the stable public contract for the orchestrator redesign.

Related design notes:

- [`TYPES_AUDIT.md`](/home/rogerw/project/vibrant/vibrant/orchestrator/TYPES_AUDIT.md): audit of larger orchestrator types and cleanup plan
- [`BEHAVIOR_CONTRACT.md`](/home/rogerw/project/vibrant/vibrant/providers/BEHAVIOR_CONTRACT.md): provider adapter compatibility contract consumed by the runtime and orchestrator

It is written from the perspective of **external and first-party consumers**
that need a durable integration boundary while the implementation is organized
as layered `basic`, `policy`, and `interface` packages.

## Status

As of **March 15, 2026**, the stable contract is defined by:

- the **interface control-plane model** described here
- the **typed MCP resource/tool surface**
- the **workflow-session read model**, including the orchestrator-owned
  concurrency limit
- the **read models and conversation subscription semantics**
- the **compatibility constraints** required during migration

During the migration, first-party consumers may still use compatibility entry
points such as `OrchestratorFacade` and `OrchestratorMCPServer`, but those
entry points must behave according to the redesigned authority model.

## Design Rules

The stable contract is governed by the following rules:

1. The orchestrator owns all durable state under `.vibrant/`.
2. The Gatekeeper never mutates orchestrator state by writing files or by prose output.
3. The Gatekeeper mutates orchestrator state only through typed MCP tools.
4. The orchestrator never infers planning or review decisions from free-form text.
5. Conversation history shown to the TUI is an orchestrator-owned artifact, not a provider-log projection.
6. Workflow state is authoritative; consensus metadata may mirror it only as a one-way projection.
7. The task concurrency limit is orchestrator workflow-session state, not provider state.

## Stable Consumer Model

The stable integration model is:

1. bootstrap an orchestrator root for one project
2. submit workflow or user actions through the interface control plane or compatibility facade
3. read coherent state through snapshots and typed query adapters
4. subscribe to orchestrator-owned conversation streams
5. use MCP resources/tools for Gatekeeper-driven mutations

Consumers should not rely on:

- internal service composition
- store implementation details
- provider-native logs as primary history
- provider-specific event chunking, auxiliary events, or terminal-event ordering
- ad hoc status patching APIs
- Gatekeeper prose as an authority channel

## Provider Adapter Compatibility

Provider adapters are part of the stable runtime boundary. The normative
behavior rules live in
[`BEHAVIOR_CONTRACT.md`](/home/rogerw/project/vibrant/vibrant/providers/BEHAVIOR_CONTRACT.md).

The important orchestrator rule is that providers are accepted based on
normalized behavior, not based on matching Codex or Claude internals. A
provider is compatible if it:

- satisfies the `ProviderAdapter` lifecycle surface
- persists resumable thread metadata when available
- terminates turns through canonical `turn.completed` or `runtime.error`
- surfaces interactive pauses through canonical `request.*` events
- emits assistant transcript text through `content.delta`

Consumers must not require provider-specific event timing, payload layout, or
native protocol details beyond that contract.

## Stable Identity Model

The stable integration boundary uses these identifiers:

- `session_id`: one durable workflow session
- `submission_id`: one host-originated Gatekeeper submission
- `task_id`: one roadmap task definition
- `attempt_id`: one execution attempt for one task
- `workspace_id`: one isolated task or integration worktree record
- `role`: policy and capability identity
- `agent_id`: one stable logical actor instance
- `run_id`: one execution of that actor
- `conversation_id`: one orchestrator-owned conversation stream
- `question_id`: one durable user-decision record
- `ticket_id`: one durable review ticket
- `event_id`: one canonical orchestrator event

Provider-native ids such as `provider_thread_id`, `turn_id`, and `item_id` are
resume or trace handles, not orchestrator primary keys.

Unqualified `session_id` is reserved for workflow or Gatekeeper session state.
Run-scoped binding and runtime contracts should use `run_id` or another
explicitly scoped name instead.

## Stable Read Models

### `OrchestratorSnapshot`

`OrchestratorSnapshot` is the stable high-level read model for first-party UI
consumers.

Required semantic contents:

- workflow status
- Gatekeeper session snapshot
- roadmap view
- consensus view
- pending question views
- active review tickets or review summaries
- role summaries
- instance summaries
- run/runtime summaries
- active attempt summaries
- user-input banner or blocking-state projection when applicable

The exact implementation may evolve, but the snapshot must remain a coherent,
consumer-ready projection of orchestrator state.

### `WorkflowSessionSnapshot`

This read model is the stable durable view of the workflow session.

It must preserve the meaning of:

- `session_id`
- workflow `status`
- `resume_status`
- `concurrency_limit`
- Gatekeeper session projection
- total agent spawn count
- pending question ids
- active attempt ids

The important behavioral rule is that `concurrency_limit` belongs to the
orchestrator session. It may be seeded from `vibrant.toml`, but enforcement and
durable ownership remain in orchestrator workflow state.

### `AgentRunSnapshot`

This read model represents the orchestrator’s combined durable and live view of
one run.

It must preserve the meaning of:

- stable instance identity
- concrete run identity
- runtime/lifecycle state
- provider resume metadata
- workspace context
- best-known summary/error/output projection

### `AgentInstanceSnapshot`

This read model represents one stable logical actor instance.

It must preserve the meaning of:

- stable `agent_id`
- role identity
- scope and provider defaults
- latest-run and active-run linkage

### `RoleSnapshot`

This read model represents one policy role and its currently observed
instance/run footprint.

### Conversation Views

The stable conversation contract is centered on:

- `AgentStreamEvent`
- `AgentConversationEntry`
- `AgentConversationView`
- replayable conversation subscriptions

The TUI contract is the processed conversation stream, not raw canonical
provider events and not imported provider transcript artifacts.

### Question And Attempt Views

The public question and attempt inspection surface is intentionally narrower
than the durable store or recovery layer:

- `QuestionView` is the public question shape for facade, control-plane, MCP,
  and TUI consumers.
- `AttemptExecutionView` is the public attempt-execution summary shape for
  both active and completed attempts.
- Durable question audit fields and attempt recovery-only fields stay on
  internal record/recovery types.
- Provider resume cursors, provider thread paths, and workspace paths are not
  part of the public attempt-inspection contract.

## Interface Control Plane Contract

The layered orchestrator is built around `basic` capabilities, `policy` loops,
and an `interface` control plane with the following stable semantics:

```python
@dataclass
class GatekeeperSubmission:
    submission_id: str
    session: GatekeeperSessionSnapshot
    conversation_id: str
    agent_id: str | None
    run_id: str | None
    accepted: bool
    active_turn_id: str | None
    error: str | None = None

class InterfaceControlPlane:
    async def submit_user_input(self, text: str, question_id: str | None = None) -> GatekeeperSubmission: ...
    async def wait_for_gatekeeper_submission(self, submission: GatekeeperSubmission) -> RuntimeExecutionResult: ...
    async def respond_to_gatekeeper_request(
        self,
        run_id: str,
        request_id: int | str,
        *,
        result: object | None = None,
        error: dict[str, object] | None = None,
    ) -> RuntimeHandleSnapshot: ...
    def start_execution(self) -> WorkflowSnapshot: ...
    def pause_workflow(self) -> WorkflowSnapshot: ...
    def resume_workflow(self) -> WorkflowSnapshot: ...
    async def restart_gatekeeper(self, reason: str | None = None) -> GatekeeperLoopState: ...
    async def stop_gatekeeper(self) -> GatekeeperLoopState: ...
    async def run_next_task(self) -> TaskResult | None: ...
    async def run_until_blocked(self) -> list[TaskResult]: ...
    def conversation(self, conversation_id: str) -> AgentConversationView | None: ...
    def subscribe_conversation(self, conversation_id: str, callback, *, replay: bool = False): ...
    def workflow_snapshot(self) -> WorkflowSnapshot: ...
    def workflow_session(self) -> WorkflowSessionSnapshot: ...
    def gatekeeper_state(self) -> GatekeeperLoopState: ...
    def task_loop_state(self) -> TaskLoopSnapshot: ...
    def list_roles(self) -> list[RoleSnapshot]: ...
    def get_role(self, role: str) -> RoleSnapshot | None: ...
    def list_instances(self) -> list[AgentInstanceSnapshot]: ...
    def get_instance(self, agent_id: str) -> AgentInstanceSnapshot | None: ...
    def list_runs(self) -> list[AgentRunSnapshot]: ...
    def list_active_runs(self) -> list[AgentRunSnapshot]: ...
    def get_run(self, run_id: str) -> AgentRunSnapshot | None: ...
    def get_question(self, question_id: str) -> QuestionView | None: ...
    def list_attempt_executions(
        self,
        *,
        task_id: str | None = None,
        status: AttemptStatus | None = None,
    ) -> list[AttemptExecutionView]: ...
    def list_review_tickets(
        self,
        *,
        task_id: str | None = None,
        status: ReviewTicketStatus | None = None,
    ) -> list[ReviewTicket]: ...
```

The stable behavioral rule is that public consumers receive a **submission
receipt plus explicit wait/query methods**, not raw lifecycle or runtime
services as their primary integration surface.

`RuntimeExecutionResult` is the narrow wait result for those flows. It does not
double as a raw event transcript or provider-debug bundle.

The control plane currently exposes the workflow-session projection as a stable
read surface. The concurrency limit is therefore observable through the public
API even though there is not yet a dedicated stable write method for changing it.

## Compatibility Facade

`OrchestratorFacade` remains the compatibility surface for first-party app code
while the redesign is being integrated.

Compatibility commitments:

- it must expose coherent snapshot reads
- it may preserve selected legacy entry points temporarily
- it must route mutations through the interface control plane
- it must not preserve legacy authority behavior that contradicts the redesign

Allowed temporary compatibility examples:

- convenience reads such as roadmap or consensus accessors
- async user-message helpers that internally translate into control-plane submissions
- stable task/run projection helpers used by the current TUI

Not allowed as compatibility behavior:

- direct Gatekeeper file-writing semantics
- review decision inference from task text or status diffs
- text-based pending-question reconciliation as the authoritative model

## MCP Stable Contract

`OrchestratorMCPServer` is a stable first-party integration surface, but the
stable part is the **resource/tool contract**, not any specific internal
handler layering.

The active transport model is an orchestrator-owned loopback FastMCP HTTP host
with per-run binding registration and server-side filtering. The compatibility
import path under `vibrant.orchestrator.mcp` should re-export that active
implementation rather than define a second authority path.

### Stable Read Resources

The Gatekeeper-facing stable resource set is:

- `get_consensus()`
- `get_roadmap()`
- `get_workflow_session()`
- `get_task(task_id)`
- `get_workflow_status()`
- `get_question(question_id)`
- `list_pending_questions()`
- `list_active_runs()`
- `list_active_attempts()`
- `list_attempt_executions(task_id=None, status=None)`
- `get_review_ticket(ticket_id)`
- `list_review_tickets(task_id=None, status=None)`
- `list_pending_review_tickets()`
- `list_recent_events(limit=20)`

### Stable Write Tools

The semantic write tool set is:

- `update_consensus(...)`
- `add_task(...)`
- `update_task_definition(...)`
- `reorder_tasks(task_ids)`
- `request_user_decision(...)`
- `withdraw_question(question_id, reason=None)`
- `respond_to_gatekeeper_request(run_id, request_id, result=None, error=None)`
- `end_planning_phase()`
- `pause_workflow()`
- `resume_workflow()`
- `accept_review_ticket(ticket_id)`
- `retry_review_ticket(ticket_id, failure_reason, prompt_patch=None, acceptance_patch=None)`
- `escalate_review_ticket(ticket_id, reason)`

The stable rule is that these tools express **semantic control-plane
commands**, not file patches and not free-form text deltas.

### MCP Transport Semantics

The transport-level behavior that consumers may rely on is:

- the MCP endpoint is loopback HTTP
- per-run visibility is enforced server-side from a registered binding
- the binding identity is carried via `X-Vibrant-Binding`
- provider-specific launch arguments are compiled from a provider-neutral
  access descriptor instead of being authored directly in policy code

Consumers should not rely on:

- a shared global MCP profile
- provider-specific flag shapes as part of the orchestrator contract
- transport auth schemes from older experimental docs

## Durable Store Contract

The stable architecture assumes the following durable stores exist and remain
orchestrator-owned:

- workflow state store
- roadmap store
- consensus store
- question store
- attempt store
- workspace store
- review ticket store
- agent instance store
- agent run store
- conversation store

Store file layouts may evolve, but the authority boundaries must not.

## Runtime and Conversation Contract

The generic runtime surface must remain role-agnostic and support:

- start
- resume
- wait
- interrupt
- kill
- canonical event subscription

Canonical events must include stable identity and replay ordering:

- `event_id`
- `sequence`
- `role`
- `agent_id`
- `run_id`

Optional routing fields may include:

- `conversation_id`
- `attempt_id`
- `provider_thread_id`

Task identity belongs to attempts, workspaces, and review tickets. Generic
runtime events should not rely on `task_id` as a surrogate run identifier.

The stable conversation contract requires:

- assistant message lifecycle events
- tool-call lifecycle events
- request lifecycle events
- no raw hidden reasoning in stored history

## Compatibility Constraints

The redesign requires an explicit migration path. The following constraints are
stable requirements during that migration:

1. First-party consumers must have a migration path before stable consumer APIs change.
2. Legacy semantic aliases are not part of the stable contract and should be removed as consumers migrate.
3. Legacy authority paths are deprecated and must not define the durable model.
4. Provider logs may remain exposed for debugging, but they cannot be treated as the primary UI history.
5. Workflow status and consensus metadata must not form a two-way synchronization loop.

## Non-Stable Internals

The following may remain importable during refactoring, but they are not
stable contracts unless later documented here:

- concrete bootstrap wiring
- internal service constructors
- store helper classes not exposed through the facade or MCP
- provider adapter implementation details
- workspace implementation internals

In particular, callers should not couple themselves to private or internal
orchestrator packages purely because they exist on disk.

## Migration Guidance

When migrating a first-party consumer:

1. Prefer snapshot reads over store peeking.
2. Prefer control-plane or semantic facade actions over status-patching helpers.
3. Prefer conversation subscriptions over provider-log polling for chat history.
4. Prefer review-ticket resolution commands over task-status mutations.
5. Treat compatibility aliases as transitional, not as the long-term design.

## Example Integration Shape

```python
from vibrant.orchestrator import OrchestratorFacade, create_orchestrator

orchestrator = create_orchestrator(project_root)
facade = OrchestratorFacade(orchestrator)

snapshot = facade.snapshot()

submission = await facade.submit_user_message("Build the CLI and the TUI.")
conversation = facade.conversation(submission.conversation_id)
```

The exact consumer API may include compatibility wrappers, but it must preserve
the redesigned semantics described above.
