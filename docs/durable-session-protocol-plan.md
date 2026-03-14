# Durable State And Resume Protocol Plan

> Date: 2026-03-14
> Status: proposed implementation plan
> Scope: unify orchestrator durability and resume behavior around transparent session protocols without flattening all stores into one generic abstraction

## Goal

Make the orchestrator easier to reason about by tightening three things:

1. first-party callers should have stable `get(...)` reads for durable state
2. state mutation should flow through explicit `update(...)` commands instead of ad hoc patches
3. resumable runtime-backed resources should expose `resume(...)` through one consistent session contract

The key constraint is that not every durable artifact is a resumable session.
`roadmap.md`, `consensus.md`, questions, and review tickets should stay normal
durable artifacts. Gatekeeper runtime state and task execution state should be
treated as sessions.

## Recommended Direction

Adopt a two-tier contract model:

- artifact repositories
  - `get(...)`
  - `list(...)` when needed
  - `update(...)` or domain verbs such as `resolve(...)`
  - no `resume(...)`
- session resources
  - `get(session_id)`
  - `update(session_id, command)`
  - `resume(session_id)`

In practice, this means:

- do not force every store in `basic/stores/` behind one generic
  `update/get/resume` interface
- do introduce a shared session protocol for resources that own runtime state,
  conversation continuity, and provider resume handles
- keep policy as the owner of workflow meaning while basic remains the owner of
  storage, projection, and generic runtime mechanics

## Current Constraints

The existing tree already has a strong separation of concerns:

- durable stores are wired in `vibrant/orchestrator/bootstrap.py`
- generic runtime and conversation mechanics live in `basic/`
- workflow decisions live in `policy/`
- first-party reads and writes are forwarded through `interface/`

Important facts from the current implementation:

- workflow resume is status restoration only
  - `vibrant/orchestrator/policy/shared/workflow.py`
  - `vibrant/orchestrator/policy/gatekeeper_loop/transitions.py`
- Gatekeeper resume is a real provider-thread resume
  - `vibrant/orchestrator/policy/gatekeeper_loop/lifecycle.py`
  - `vibrant/orchestrator/basic/runtime/service.py`
  - `vibrant/orchestrator/basic/stores/agent_runs.py`
- conversation resume is replay and rebind, not runtime continuation
  - `vibrant/orchestrator/basic/conversation/store.py`
  - `vibrant/orchestrator/basic/conversation/stream.py`
- task attempts are durable but not resumable
  - `vibrant/orchestrator/policy/task_loop/execution.py`
  - `vibrant/orchestrator/basic/stores/attempts.py`

The main problems to fix are:

1. resume authority is split across workflow state, run records, and
   conversation manifests
2. conversation routing still depends on stable `agent_id` instead of concrete
   `run_id`
3. task attempts persist enough data to resume later, but there is no actual
   attempt runtime resume path
4. active snapshots derived from durable stores can outlive live runtime
   handles after restart
5. workflow resume logic is duplicated in two policy locations

## Boundary Rules

Use these rules for every change in this plan:

- `basic/` may persist, project, bind, replay, and generically resume runtime
  handles
- `basic/` must not decide workflow meaning, review semantics, or question
  routing
- `policy/` owns session commands and workflow interpretation
- `run_id` is the authoritative execution identifier for runtime recovery and
  conversation provenance
- provider resume metadata must have one authoritative durable source
- `state.json` should keep session summary facts, not duplicate recoverable
  provider details unless they are truly the primary source of truth

## Target Contract Model

### 1. Artifact Repositories

These remain specialized and non-resumable:

- workflow state store
- roadmap store
- consensus store
- question store
- review ticket store
- agent instance store
- agent run store
- conversation frame store

The unification work here is modest:

- normalize read naming where practical
- prefer `update(...)` plus typed commands over open-ended field patches
- keep domain verbs where they carry meaning
- avoid pretending these are all the same storage shape

### 2. Session Resources

Add a shared session protocol for runtime-backed resources:

```python
class SessionResource(Protocol[SnapshotT, CommandT]):
    def get(self, session_id: str) -> SnapshotT: ...
    def update(self, session_id: str, command: CommandT) -> SnapshotT: ...
    async def resume(self, session_id: str) -> SnapshotT: ...
```

Recommended concrete session resources:

- `WorkflowSession`
  - wraps workflow status, pause/resume target status, and concurrency policy
- `GatekeeperSession`
  - wraps the current `GatekeeperSessionSnapshot` plus conversation continuity
    and provider resume handle lookup
- `AttemptExecutionSession`
  - new resource for one active task attempt and its resumable runtime state

Conversation should stay adjacent but separate:

- `ConversationSession`
  - `get(conversation_id)`
  - `replay(conversation_id)`
  - `subscribe(conversation_id, ...)`
  - no `resume(...)` because replay is not runtime continuation

## Workstream 1: Make Resume Metadata Authoritative

Objective: remove the current split-brain between workflow session state,
per-run provider metadata, and conversation manifests.

### Changes

- make `AgentRunRecord.provider.resume_handle` the authoritative provider resume
  source for resumable runs
- keep `GatekeeperSessionSnapshot` focused on:
  - `agent_id`
  - `run_id`
  - `conversation_id`
  - lifecycle state
  - active turn
  - last error
- stop treating `state.json.gatekeeper_session.provider_thread_id` as an
  independent authority once the run record already exists
- keep a lightweight resumable flag in workflow state only if needed for quick
  UI projection

### Critical Files

- `vibrant/orchestrator/basic/stores/workflow_state.py`
- `vibrant/models/agent.py`
- `vibrant/orchestrator/policy/gatekeeper_loop/lifecycle.py`
- `vibrant/orchestrator/basic/stores/agent_runs.py`

### Acceptance Criteria

- one Gatekeeper resume path can reconstruct its provider handle from the run
  record without relying on duplicated thread metadata in multiple stores
- workflow state still exposes enough information for the UI without becoming a
  second provider metadata store

## Workstream 2: Make Conversations Run-Aware

Objective: make conversation replay and future resume semantics traceable to the
run that produced the events.

This overlaps directly with the run-aware direction already captured in
`docs/identity-consistency-cleanup-plan.md` and should be implemented in the
same style.

### Changes

- make `run_id -> conversation_id` the primary routing index in the conversation
  stream
- persist participating `run_id` values in the conversation manifest
- keep `agent_id -> conversation_id` only as an optional convenience lookup
- update bind APIs so policy can register both stable actor identity and the
  concrete run that is about to emit events
- resolve canonical runtime events by `run_id` first

### Critical Files

- `vibrant/orchestrator/basic/conversation/store.py`
- `vibrant/orchestrator/basic/conversation/stream.py`
- `vibrant/orchestrator/types.py`
- `vibrant/orchestrator/policy/gatekeeper_loop/lifecycle.py`
- `vibrant/orchestrator/policy/task_loop/execution.py`

### Acceptance Criteria

- conversation replay no longer depends on one active conversation per stable
  `agent_id`
- Gatekeeper continuity still works across multiple runs
- task retries and future attempt resume flows preserve exact run provenance

## Workstream 3: Introduce Attempt Execution Sessions

Objective: close the biggest protocol gap by giving task attempts the same
session semantics that the Gatekeeper already has.

### Changes

- add a typed `AttemptExecutionSnapshot` that combines:
  - `attempt_id`
  - `task_id`
  - `run_id`
  - `conversation_id`
  - status
  - provider resume handle
  - workspace reference
- introduce an `AttemptExecutionSession` policy resource with:
  - `get(attempt_id)`
  - `update(attempt_id, command)`
  - `resume(attempt_id)`
- teach `ExecutionCoordinator` to resume a task run when:
  - the attempt is still resumable
  - the run record has a resume handle
  - the workspace is still valid
- keep fresh-start behavior as the explicit fallback when no resumable runtime
  exists

### Critical Files

- `vibrant/orchestrator/policy/task_loop/execution.py`
- `vibrant/orchestrator/basic/stores/attempts.py`
- `vibrant/orchestrator/basic/stores/agent_runs.py`
- `vibrant/orchestrator/basic/runtime/service.py`
- `vibrant/orchestrator/types.py`

### Acceptance Criteria

- a suspended or awaiting-input task attempt can be resumed through policy
  rather than restarted from scratch
- attempt durability means both review continuity and runtime continuity when
  the provider supports it

## Workstream 4: Collapse Workflow Resume Into One Session Path

Objective: stop treating workflow pause/resume as a separate one-off policy
helper.

### Changes

- define one workflow session command path for:
  - set status
  - pause
  - resume
  - update concurrency
- remove the duplicated resume helper behavior now split between:
  - `policy/shared/workflow.py`
  - `policy/gatekeeper_loop/transitions.py`
- keep consensus status projection as a side effect of workflow session update,
  not as an alternate authority path

### Critical Files

- `vibrant/orchestrator/policy/shared/workflow.py`
- `vibrant/orchestrator/policy/gatekeeper_loop/transitions.py`
- `vibrant/orchestrator/basic/stores/workflow_state.py`
- `vibrant/orchestrator/interface/policy.py`

### Acceptance Criteria

- there is exactly one implementation of workflow resume semantics
- callers still use the same high-level control-plane verbs
- the UI can still infer paused-return status cleanly

## Workstream 5: Expose Session-Centric Reads At The Interface Layer

Objective: make the transparent protocol visible to first-party callers without
breaking the current facade/control-plane model.

### Changes

- add explicit control-plane reads for:
  - workflow session snapshot
  - Gatekeeper session snapshot
  - attempt execution snapshot
  - conversation snapshot
- keep current compatibility helpers while first-party callers migrate
- route TUI and MCP surfaces through session-centric reads where that improves
  clarity
- avoid exposing raw persistence objects as the preferred public surface when a
  session snapshot is the real product contract

### Critical Files

- `vibrant/orchestrator/interface/basic.py`
- `vibrant/orchestrator/interface/control_plane.py`
- `vibrant/orchestrator/interface/policy.py`
- `vibrant/orchestrator/interface/mcp/resources.py`
- `vibrant/orchestrator/facade.py`
- `vibrant/tui/app.py`

### Acceptance Criteria

- first-party code can fetch session state without reaching into raw stores
- conversation bind/replay paths read like session recovery, not ad hoc store
  plumbing
- MCP resources expose the same session-centric view as the control plane

## Workstream 6: Reconcile Durable "Active" State With Live Runtime State

Objective: stop stale durable statuses from masquerading as live runs after
restart.

### Changes

- define startup recovery rules for runs and attempts that were persisted as
  active before process exit
- on bootstrap, reconcile:
  - durable run status
  - live runtime handles
  - resumability
- project orphaned active runs into an explicit recoverable state instead of
  silently treating them as live forever
- update slot accounting so crashed-or-stale active records do not block the
  workflow indefinitely

### Critical Files

- `vibrant/orchestrator/bootstrap.py`
- `vibrant/orchestrator/basic/artifacts/__init__.py`
- `vibrant/orchestrator/basic/runtime/service.py`
- `vibrant/orchestrator/basic/stores/attempts.py`
- `vibrant/orchestrator/basic/stores/agent_runs.py`
- `vibrant/orchestrator/policy/task_loop/loop.py`

### Acceptance Criteria

- a restart does not leave stale active attempts consuming execution slots
- active snapshots reflect either a live handle or a clearly recoverable
  suspended state

## Current Store Migration Plan

This section turns the workstreams above into a concrete migration plan for the
existing `.vibrant/` stores. The goal is to migrate authority cleanly without a
flag-day state reset.

### Migration Rules

- land compatibility readers before changing any writer
- at each phase, one fact has one authoritative store
- prefer lazy in-place normalization on read/upsert over bulk rewriting all
  local state
- do not rewrite append-only conversation frame logs unless replay is actually
  blocked
- bootstrap recovery should be the only place that reconciles stale "active"
  durable state against the live runtime table

### Store Inventory

The migration work only needs real schema or authority changes in these stores:

- `.vibrant/state.json`
- `.vibrant/agent-runs/*.json`
- `.vibrant/attempts.json`
- `.vibrant/conversations/index.json`
- `.vibrant/agent-instances/*.json`

These stores should remain specialized and need no session-schema migration:

- `.vibrant/roadmap.md`
- `.vibrant/consensus.md`
- `.vibrant/questions.json`
- `.vibrant/reviews.json`

### 1. Migrate `state.json` From Authority To Session Summary

Current shape in `WorkflowStateStore`:

- `gatekeeper_session` persists `agent_id`, `run_id`, `conversation_id`,
  `lifecycle_state`, `provider_thread_id`, `active_turn_id`, `resumable`,
  `last_error`, and `updated_at`
- the loader still accepts older root-level Gatekeeper fields and
  `provider_runtime.gatekeeper.provider_thread_id`

Target shape:

- `gatekeeper_session.run_id` and `conversation_id` stay durable
- provider resume authority moves to the matching run record in
  `.vibrant/agent-runs/<run_id>.json`
- `provider_thread_id` and `resumable` become derived session summary fields at
  most, not independent authorities

Implementation steps:

1. add a projection helper that can derive Gatekeeper resume summary from
   `GatekeeperSessionSnapshot + AgentRunRecord`
2. change Gatekeeper resume lookup order to:
   - `gatekeeper_session.run_id -> agent_run_store.get(run_id)`
   - latest resumable run for the Gatekeeper `agent_id` as a temporary fallback
   - `state.json.gatekeeper_session.provider_thread_id` only as the final
     migration fallback
3. keep `WorkflowStateStore._parse_gatekeeper_session(...)` tolerant of the
   current legacy layouts until the writer cutover is complete
4. once all resume callers use run-owned metadata, trim `WorkflowStateStore.save()`
   so `provider_thread_id` is either omitted or written only as a derived cache
5. opportunistically rewrite `state.json` on the next successful Gatekeeper
   session update so old root-level Gatekeeper fields disappear

Cutover condition:

- Gatekeeper resume still works when `state.json` contains only `run_id`,
  `conversation_id`, lifecycle facts, and no authoritative provider thread
  metadata

### 2. Normalize `agent-runs/*.json` As The Resume Source Of Truth

Current shape in `AgentRunStore` and `AgentProviderMetadata`:

- each run record already has a `provider.resume_handle`
- model validators already migrate older `provider_thread_id`, `resume_cursor`,
  `provider_name`, and related legacy keys into the normalized provider model
- policy still has an agent-scoped helper,
  `AgentRunStore.provider_thread_handle(agent_id)`, which hides the run that
  actually owns the resume handle

Target shape:

- every resumable run stores its provider continuation only in
  `provider.resume_handle`
- resume recovery is addressed by `run_id` first
- agent-scoped lookups become convenience helpers, not the main policy path

Implementation steps:

1. add explicit run-oriented helpers such as:
   - `resume_handle_for_run(run_id)`
   - `latest_resumable_run(agent_id)`
   - `list_resumable_active()` if startup recovery needs it
2. move Gatekeeper and attempt recovery code to these run-oriented helpers
3. keep the model validators as the read-time migration path for older run
   files
4. ensure every new upsert writes the normalized provider schema so older
   `provider_thread_id` / `resume_cursor` fields stop reappearing
5. add a bootstrap normalization sweep that reloads and rewrites legacy run
   files only when the parsed normalized form differs from the on-disk payload
6. remove policy dependence on `provider_thread_handle(agent_id)` after all
   callers are moved

Cutover condition:

- no resume path needs provider metadata from workflow state or provider-specific
  fallback fields once the run record exists

### 3. Keep `attempts.json` Lean And Project Sessions From It

Current shape in `AttemptStore`:

- attempt records own `attempt_id`, `task_id`, `workspace_id`, `status`,
  `code_run_id`, `validation_run_ids`, `merge_run_id`, `conversation_id`, and
  timestamps
- there is no explicit attempt-runtime resume API and no provider handle copied
  into the attempt record

Target shape:

- `attempts.json` remains the attempt-owned index of task/workspace/run
  relationships
- `AttemptExecutionSession` is projected from `AttemptRecord + AgentRunRecord +
  workspace state`
- provider resume metadata stays in the referenced run records, not duplicated
  into the attempt store

Implementation steps:

1. add a projector that derives the active execution run from the current
   attempt status:
   - coding / awaiting-input statuses use `code_run_id`
   - validation statuses use the latest relevant `validation_run_id`
   - merge statuses use `merge_run_id`
2. implement `AttemptExecutionSnapshot` as a joined view before changing the
   on-disk attempt schema
3. update `ExecutionCoordinator` to ask the projector for the active run and
   try resume before starting a fresh run
4. only add a new durable `active_run_id` field if the joined view proves too
   ambiguous in practice; if added, backfill it deterministically from the
   existing phase-specific run ids
5. do not add `provider_thread_id` or `resume_handle` to `attempts.json`
6. rewrite attempt records lazily on update once any new phase/index field
   exists

Cutover condition:

- attempt recovery no longer infers resumability from conversation state or
  provider logs; it resolves it from the attempt's bound run ids

### 4. Finish Normalizing The Conversation Store Around `run_id`

Current shape in `ConversationStore`:

- manifests already persist `run_ids`, `active_turn_id`, `updated_at`, and
  `next_sequence`
- `_manifest_from_raw(...)` still accepts older `binding_ids` payloads
- frame logs already persist `run_id` when the canonical event had one

Target shape:

- `run_ids` are the only durable binding membership field in the manifest
- replay depends on `run_id -> conversation_id`, not on stable `agent_id`
- frame logs remain append-only

Implementation steps:

1. keep `_manifest_from_raw(...)` compatibility until all active test fixtures
   and local projects have been rewritten
2. on the first successful `bind_run(...)`, `append_frame(...)`, or explicit
   normalization pass, rewrite manifests using only `run_ids`
3. do not bulk rewrite historical `frames/*.jsonl` files just to inject missing
   `run_id` values
4. for older conversations with empty `run_ids`, backfill manifest membership
   from:
   - frame `run_id` values already present
   - Gatekeeper `gatekeeper_session.run_id`
   - attempt `code_run_id`, `validation_run_ids`, and `merge_run_id`
5. remove any remaining durable assumptions that one stable `agent_id` maps to
   one active conversation

Cutover condition:

- removing `binding_ids` support does not change conversation replay or runtime
  event routing

### 5. Reconcile `agent-instances/*.json` With The New Session Model

Current shape in `AgentInstanceStore`:

- instance records keep `latest_run_id` and `active_run_id`
- those pointers can survive process restart even when no live runtime handle
  still exists

Target shape:

- instance records still track latest and active run pointers
- bootstrap reconciliation clears stale active pointers instead of letting them
  masquerade as live runtime ownership

Implementation steps:

1. during bootstrap, compare each `active_run_id` against:
   - the run record lifecycle status
   - the live runtime handle table
2. if `active_run_id` points at a terminal or missing run, clear it
3. if the run is non-terminal but only recoverable from durable resume metadata,
   do not claim it is live; let the new session snapshot surface it as
   recoverable instead
4. rewrite only the instance files whose active pointers changed

Cutover condition:

- instance reads no longer imply that `active_run_id` means a live in-memory
  handle exists

### 6. Add A Bootstrap Normalization Pass

The store migrations above should be tied together by one startup pass in
`bootstrap.py`, not by scattered repair logic in policy code.

Bootstrap order:

1. load and index all run records by `run_id` and `agent_id`
2. load workflow state and normalize the Gatekeeper session against the run
   index
3. load attempts and compute active execution-session projections
4. load conversation manifests and backfill missing `run_ids` where they can be
   derived safely
5. reconcile agent-instance `active_run_id` pointers
6. rewrite only the files whose normalized form differs from the on-disk data

Why this order:

- run records must be normalized first because they become the resume authority
- workflow state and attempts both depend on the run index
- conversations can backfill membership from both run records and attempts
- instance pointers are safest to reconcile after run normalization

### 7. Rollout Sequence

Use this implementation order to avoid dual-authority windows:

1. land compatibility readers and joined projection helpers
2. land new session-centric read APIs while old writers still work
3. cut Gatekeeper and attempt resume over to run-owned metadata
4. trim `state.json` writes so provider metadata is no longer authoritative
5. add bootstrap normalization and selective rewrite
6. update tests and fixtures to the normalized shapes
7. delete the remaining legacy fallbacks such as `binding_ids` parsing and
   agent-scoped resume lookup

### 8. Migration Test Matrix

The test plan above should be expanded with explicit old-state fixtures for:

1. `state.json` that only has `provider_thread_id` under `gatekeeper_session`
2. `state.json` that still uses root-level legacy Gatekeeper keys
3. run records that only have legacy `provider_thread_id` / `resume_cursor`
   fields and no explicit `resume_handle`
4. conversation manifests that only have `binding_ids`
5. attempts that have phase-specific run ids but no future `active_run_id`
6. agent instances whose `active_run_id` points to a stale non-live run after
   restart

Success means the orchestrator can load, normalize, and resume correctly from
all of those states without manual local cleanup.

## Recommended Execution Order

1. Workstream 2: run-aware conversation binding
2. Workstream 1: authoritative resume metadata
3. Workstream 4: unified workflow session path
4. Workstream 3: attempt execution sessions and runtime resume
5. Workstream 6: startup reconciliation of active state
6. Workstream 5: session-centric interface cleanup and compatibility removal

This order keeps the identity and provenance boundary correct before building
new session APIs on top of it.

## Test Plan

Use the current focused orchestrator tests as the safety net:

- `tests/test_orchestrator_bootstrap.py`
- `tests/test_orchestrator_gatekeeper_loop.py`
- `tests/test_orchestrator_task_loop.py`
- `tests/test_orchestrator_conversation.py`
- `tests/test_orchestrator_workflow.py`
- `tests/test_orchestrator_mcp_surface.py`
- `tests/test_orchestrator_mcp_transport.py`
- `tests/test_orchestrator_architecture.py`

Run:

```bash
uv run pytest \
  tests/test_orchestrator_bootstrap.py \
  tests/test_orchestrator_gatekeeper_loop.py \
  tests/test_orchestrator_task_loop.py \
  tests/test_orchestrator_conversation.py \
  tests/test_orchestrator_workflow.py \
  tests/test_orchestrator_mcp_surface.py \
  tests/test_orchestrator_mcp_transport.py \
  tests/test_orchestrator_architecture.py
```

Add or tighten end-to-end assertions for:

1. workflow pause/resume restores the correct status through one code path
2. Gatekeeper conversation replay survives orchestrator restart
3. Gatekeeper provider-thread resume uses run-owned resume metadata
4. conversation projection resolves events by `run_id`
5. task attempts can resume provider-backed execution when supported
6. stale active attempts are reconciled on startup instead of blocking forever

## Completion Criteria

This plan is complete when:

- resumable resources share one session-shaped protocol
- non-resumable artifacts remain specialized repositories
- `run_id` is the main identity for resume and conversation provenance
- task attempts support real runtime resume instead of restart-only durability
- first-party control-plane and MCP surfaces expose the new model clearly
