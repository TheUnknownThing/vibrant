# Identity Consistency Cleanup Plan

> Date: 2026-03-14
> Status: implemented in the current tree
> Scope: remove the remaining identity-layer mismatches after the role / instance / run refactor

## Implementation Status

The cleanup items in this document now have a first implementation pass in the
current tree:

- Gatekeeper runs no longer fabricate roadmap `task_id` values.
- canonical events now carry `role`, `agent_id`, and `run_id`.
- conversation binding is run-aware and conversation manifests persist
  participating `run_id` values.
- the first-party facade/control-plane/TUI path now reads `roles`,
  `instances`, and `runs` explicitly.
- MCP binding descriptors now use `run_id` instead of an overloaded
  run-scoped `session_id`.

## Why This Exists

The current architecture is directionally correct, but a few seams still mix
layers or carry transitional names:

1. some non-task actors still fabricate `task_id` values
2. conversation routing is keyed by stable `agent_id` instead of the concrete
   `run_id` that produced the events
3. public read surfaces still expose run records behind legacy `agent*` names
4. `role` is the intended primary concept, but `type` still leaks through the
   model and call surface
5. `session_id` means different things in different layers

This plan fixes those problems without undoing the current role / instance /
run model.

## Boundary Rules

These rules should guide every change in this plan:

- `task_id` is a roadmap work-item identifier, not a generic placeholder for
  any run in the system.
- `agent_id` is the stable instance identifier.
- `run_id` is the authoritative execution identifier.
- `conversation_id` is an orchestrator-owned history stream identifier.
- provider ids such as `provider_thread_id`, `turn_id`, and `item_id` are
  provider handles, not orchestrator primary keys.
- new cleanup should delete transitional aliases after callers are moved;
  long-lived compatibility shims are out of scope.

## Target Identity Model

The intended model after cleanup is:

| Identifier | Meaning | Required on |
|---|---|---|
| `role` | policy/capability identity | instance, run, events, public filters |
| `agent_id` | stable logical actor instance | instance, run, runtime handle |
| `run_id` | one execution of one instance | run, runtime, canonical events |
| `task_id` | roadmap task identity | roadmap tasks, attempts, review tickets, task-aware projections |
| `attempt_id` | one concrete execution attempt for one task | attempts, review tickets |
| `conversation_id` | orchestrator-owned conversation stream | conversation manifests, attempts, Gatekeeper session |
| `question_id` | user decision record | question store |
| `ticket_id` | review decision record | review store |

Two consequences follow from that model:

- generic run records must not carry `task_id`; task-to-run association belongs
  to attempts and other task-owned projections
- conversation projection must resolve events by `run_id`, not by the stable
  `agent_id`.

## Non-Goals

- preserve older local state formats indefinitely
- keep both `role` and `type` as equal first-class concepts
- grow a more abstract identity layer than the current system needs

If a local `.vibrant/` artifact becomes invalid because of these changes, the
preferred response is a one-step migration or state reset, not permanent dual
paths.

## Workstream 1: Decouple Run Identity From Task Identity

### Problem

`AgentRunIdentity` currently carries `task_id`, which forces the generic run
layer to know task execution details and pushes non-task actors toward synthetic
task ids. That leaks task-loop concerns into provider/runtime contracts and
blurs the distinction between roadmap work and generic agent execution.

### Changes

- remove `AgentRunIdentity.task_id` from the generic run model
- persist task-to-run association in attempt records and task-aware
  orchestrator projections
- stop creating synthetic Gatekeeper task ids; Gatekeeper run bindings should
  carry no task id
- remove task-based helpers from the generic run store
- update runtime event normalization to avoid synthesizing `task_id`; higher
  layers may project task context back in from attempts when needed

### Acceptance Criteria

- no new generic run record contains `task_id`
- no new Gatekeeper run record or runtime event contains a fabricated `task_id`
- no UI or policy path detects Gatekeeper by checking `task_id.startswith(...)`
- task attempts remain the source of truth for the real roadmap `task_id` and
  the participating `run_id` values

## Workstream 2: Make Conversation Binding Run-Aware

### Problem

Conversation projection currently resolves canonical events through an
`agent_id -> conversation_id` map. That only works because the runtime allows
one active run per stable instance. The conversation layer should not depend on
that side effect.

### Changes

- make `run_id -> conversation_id` the primary conversation binding index
- extend conversation binding APIs to register the producing `run_id`
- persist run membership in the conversation manifest
- resolve incoming canonical events by `run_id` first
- keep any `agent_id -> conversation_id` index, if still needed, as a
  convenience lookup only
- keep task attempts mapped to `attempt-{attempt_id}` conversations and bind
  each new task run explicitly
- keep Gatekeeper continuity by binding each new Gatekeeper run to the existing
  Gatekeeper `conversation_id`

### Acceptance Criteria

- conversation projection does not require stable-agent uniqueness to work
- a task-scoped stable instance can safely accumulate multiple historical runs
  without losing conversation provenance
- the durable conversation manifest can answer which runs participated in a
  conversation

## Workstream 3: Promote Roles / Instances / Runs To The Public Surface

### Problem

The docs say the public model is `roles`, `instances`, and `runs`, but the
current control plane and facade still expose transitional `agent_records`,
`get_agent_record()`, and `list_active_agents()` aliases backed by run records.

### Changes

- add explicit public read surfaces for:
  - `roles`
  - `instances`
  - `runs`
  - `attempts`
  - `conversations`
- replace `OrchestratorSnapshot.agent_records` with explicit run/instance
  projections
- introduce stable public snapshot types such as:
  - `RoleSnapshot`
  - `AgentInstanceSnapshot`
  - `AgentRunSnapshot`
- stop exposing raw `AgentRunRecord` persistence objects as the preferred
  facade type
- delete compatibility aliases after first-party callers move

### Acceptance Criteria

- no first-party TUI path depends on `list_agent_records()` or
  `get_agent_record()`
- public code reads instances by `agent_id` and runs by `run_id`
- the stable surface no longer implies that “agent” and “run” are synonyms

## Workstream 4: Collapse The `role` / `type` Split

### Problem

The architecture says `role` is the top-layer concept, but the model still
carries legacy `type`, and some facade filters still accept `agent_type`.

### Changes

- define built-in role descriptors as the first-class policy catalog
- carry `role` through all public filters, snapshots, and canonical events
- remove `type` from new public contracts
- migrate first-party filtering and display logic from `agent_type` to `role`
- update TUI/runtime event classification to use `role` or stable `agent_id`,
  never synthetic `task_id`

### Acceptance Criteria

- no public method prefers `agent_type` over `role`
- canonical events contain enough identity metadata to classify Gatekeeper and
  worker activity without fabricated task ids
- the role catalog is policy-owned and externally queryable

## Workstream 5: Qualify Session Terminology

### Problem

`session_id` currently refers to at least two different concepts:

- the workflow session id in workflow state
- the per-binding identifier passed through MCP access descriptors, which is
  currently run-scoped

This makes traces and logs harder to reason about.

### Changes

- reserve unqualified `session` language for actual workflow or Gatekeeper
  session state only
- rename the MCP binding field from generic `session_id` to an explicit scoped
  name
- prefer explicit names in cross-layer contracts:
  - `workflow_id` or `workflow_session_id`
  - `run_id`
  - `provider_thread_id`
  - `submission_id`
- update debug metadata, logs, and docs to use the qualified names

### Acceptance Criteria

- no cross-layer contract uses bare `session_id` when it really means `run_id`
- workflow session, provider thread, and binding scope can be distinguished
  from logs without inference

## Recommended Execution Order

1. implement Workstream 1 and stop minting fake Gatekeeper `task_id` values
2. in the same branch, add `role` to the event path where needed so callers can
   stop keying off fake task ids
3. implement Workstream 2 so conversation routing is anchored to `run_id`
4. migrate TUI and facade consumers to the explicit role/instance/run surface
5. delete transitional aliases and `type`-first call shapes
6. finish session terminology cleanup once the public contracts are narrowed

This order keeps the riskiest semantic cleanup first and avoids rebuilding new
API surfaces on top of the current identity leakage.

## Test Plan

The following tests should be updated or expanded as the safety net:

- `tests/test_orchestrator_gatekeeper_loop.py`
- `tests/test_orchestrator_task_loop.py`
- `tests/test_orchestrator_conversation.py`
- `tests/test_chat_panel.py`
- `tests/test_orchestrator_architecture.py`
- `tests/test_orchestrator_bootstrap.py`
- `tests/test_orchestrator_mcp_surface.py`

Key assertions:

- Gatekeeper runs do not fabricate roadmap task ids
- conversation projection resolves events by `run_id`
- task attempts preserve the real `task_id`, `attempt_id`, `workspace_id`, and
  `conversation_id` relationships
- public first-party reads no longer depend on legacy `agent_record` aliases
- event classification uses `role` / `agent_id`, not synthetic `task_id`

## Completion Criteria

This cleanup is complete when all of the following are true:

- no fabricated Gatekeeper `task_id` values remain
- run identity and task identity are no longer conflated in the core model
- conversation provenance is run-aware
- public first-party APIs speak in terms of `roles`, `instances`, and `runs`
- `role` fully replaces `type` as the primary actor classification concept
- ambiguous `session_id` usage has been eliminated from cross-layer contracts
