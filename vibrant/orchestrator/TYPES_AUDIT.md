# Orchestrator Types Audit

This document records an audit of the larger objects in
[`vibrant/orchestrator/types.py`](/home/rogerw/project/vibrant/vibrant/orchestrator/types.py)
and outlines how to narrow or remove them safely.

Audit date: March 15, 2026

## Goal

The goal is to keep orchestrator types aligned with the actual architecture:

- durable records should store durable state only
- public read models should expose consumer-ready data, not provider internals
- policy helpers should depend on the smallest shape they actually need
- compatibility aliases should be removed once first-party callers stop using them

## Boundary Rules

Use these rules when deciding whether a field belongs in a type:

1. A store record may contain durable state and durable identifiers, but not live handles or transport objects.
2. A public read model may contain data that external or first-party consumers need to render or act, but not provider-specific recovery internals unless that is an explicit contract.
3. A policy helper should accept the narrowest data shape needed for the decision it makes.
4. A type named like a capability or snapshot should not carry live service objects.
5. If a field is written but not read, remove it or start using it intentionally.

## Findings By Type

### `WorkflowSnapshot`

Status: fixed in current tree

- Defined in [`types.py`](/home/rogerw/project/vibrant/vibrant/orchestrator/types.py#L303).
- Built in [`basic/artifacts/__init__.py`](/home/rogerw/project/vibrant/vibrant/orchestrator/basic/artifacts/__init__.py#L16).
- The previous `active_agent_ids` drift has already been removed from the type.
- The remaining shape matches the current builder.

Cleanup:

- Keep this shape aligned with its only builder.
- If agent-level workflow projection is needed later, add it intentionally with a real projection and tests.

### `BoundAgentCapabilities`

Status: replaced in current tree

- Built in [`basic/binding/service.py`](/home/rogerw/project/vibrant/vibrant/orchestrator/basic/binding/service.py#L85).
- Consumed in [`interface/mcp/fastmcp_host.py`](/home/rogerw/project/vibrant/vibrant/orchestrator/interface/mcp/fastmcp_host.py#L347) and [`policy/gatekeeper_loop/lifecycle.py`](/home/rogerw/project/vibrant/vibrant/orchestrator/policy/gatekeeper_loop/lifecycle.py#L146).
- Actual consumers only use `principal` and `access`.
- `mcp_server` is a live server object embedded in a dataclass named like a value object.
- `tool_names`, `resource_names`, and `provider_binding` are currently write-only.

Cleanup:

- Replaced by `AgentMCPBinding`, a two-field value object with:
  - `principal`
  - `access`
- `BindingPreset` still carries tool/resource visibility, where it belongs.
- The live server object and write-only provider mapping have been removed from the returned binding type.

### `AttemptExecutionSnapshot`

Status: split in current tree

- Built in [`policy/task_loop/sessions.py`](/home/rogerw/project/vibrant/vibrant/orchestrator/policy/task_loop/sessions.py#L80).
- Used for recovery in [`policy/task_loop/sessions.py`](/home/rogerw/project/vibrant/vibrant/orchestrator/policy/task_loop/sessions.py#L105) and [`policy/task_loop/execution.py`](/home/rogerw/project/vibrant/vibrant/orchestrator/policy/task_loop/execution.py#L120).
- Exposed through MCP in [`interface/mcp/resources.py`](/home/rogerw/project/vibrant/vibrant/orchestrator/interface/mcp/resources.py#L41).
- Recovery logic only needs `attempt_id`, `status`, `live`, and `workspace_path`.
- The object also carries provider thread path, resume cursor, input requests, and provider kind.

Boundary issue:

- Provider resume metadata is internal execution detail, but this snapshot is exposed on the read surface.

Cleanup:

- Split into:
  - `AttemptRecoveryState` for internal task-loop recovery
  - `AttemptExecutionView` for MCP and first-party inspection
- `workspace_path`, `provider_thread_path`, and `provider_resume_cursor` no longer leak onto the public read surface.

### `TaskResult`

Status: narrowed in current tree

- Defined in [`types.py`](/home/rogerw/project/vibrant/vibrant/orchestrator/types.py#L490).
- Constructed in [`policy/task_loop/loop.py`](/home/rogerw/project/vibrant/vibrant/orchestrator/policy/task_loop/loop.py#L144) and nearby call sites.
- Rendered in the TUI through [`tui/app.py`](/home/rogerw/project/vibrant/vibrant/tui/app.py#L900).
- Actual use is limited to:
  - `task_id`
  - `outcome`
  - `summary`
  - `error`
  - `worktree_path`
- The previously unused `task_status`, `gatekeeper_result`, `merge_result`, and `events` fields have been removed.

Cleanup:

- Keep `TaskResult` limited to the fields the task loop really returns.
- If richer per-step data is needed later, introduce a different result type instead of widening this one again.
- Add one test that asserts the returned shape, so the type cannot quietly widen again.

### `RuntimeExecutionResult`

Status: narrowed in current tree

- Built in [`basic/runtime/service.py`](/home/rogerw/project/vibrant/vibrant/orchestrator/basic/runtime/service.py#L138).
- Consumed by gatekeeper flow in [`policy/gatekeeper_loop/lifecycle.py`](/home/rogerw/project/vibrant/vibrant/orchestrator/policy/gatekeeper_loop/lifecycle.py#L243), task execution in [`policy/task_loop/execution.py`](/home/rogerw/project/vibrant/vibrant/orchestrator/policy/task_loop/execution.py#L212), and planning-completion detection in [`tui/app.py`](/home/rogerw/project/vibrant/vibrant/tui/app.py#L1280).
- First-party use sites rely on:
  - `run_id`
  - `role`
  - `status`
  - `summary`
  - `error`
  - `awaiting_input`
  - `provider_events_ref`
  - `provider_thread_id`
  - `input_requests`

Cleanup:

- The public wait result now excludes raw event replay, provider thread path/cursor data, and the embedded normalized provider result.
- Canonical event subscription remains available through the runtime/event services instead of being duplicated on the wait result.

### `QuestionRecord`

Status: split in current tree

- Defined in [`types.py`](/home/rogerw/project/vibrant/vibrant/orchestrator/types.py#L216).
- Persisted in [`basic/stores/questions.py`](/home/rogerw/project/vibrant/vibrant/orchestrator/basic/stores/questions.py#L56).
- Rendered in [`tui/widgets/chat_panel.py`](/home/rogerw/project/vibrant/vibrant/tui/widgets/chat_panel.py#L144).
- Used in request shaping in [`policy/gatekeeper_loop/requests.py`](/home/rogerw/project/vibrant/vibrant/orchestrator/policy/gatekeeper_loop/requests.py#L41).
- UI only needs a small subset: `question_id`, `text`, `status`, and `answer`.
- Policy mostly needs `question_id`, `text`, and `status`.
- Persistence owns the wider audit fields.
- `source_turn_id` appears to be write-only.

Boundary issue:

- The repo still has a legacy Pydantic `QuestionRecord` with different status values in [`models/state.py`](/home/rogerw/project/vibrant/vibrant/models/state.py#L49). That is old-state compatibility, but it also means there are two separate meanings for the same conceptual object.

Cleanup:

- The durable store still owns `QuestionRecord`.
- Public policy, facade, MCP, and TUI consumers now receive `QuestionView`.
- Source metadata and timestamps no longer cross the public read boundary.
- `source_turn_id` remains write-only on the durable record and should be removed next if no consumer appears.

### `AgentStreamEvent`

Status: cleaned up in current tree

- Core conversation contract in [`basic/conversation/stream.py`](/home/rogerw/project/vibrant/vibrant/orchestrator/basic/conversation/stream.py#L59).
- Replayed by the conversation store in [`basic/conversation/store.py`](/home/rogerw/project/vibrant/vibrant/orchestrator/basic/conversation/store.py#L75).
- Consumed by conversation rendering in [`tui/widgets/conversation_view.py`](/home/rogerw/project/vibrant/vibrant/tui/widgets/conversation_view.py#L257).
- The unused `task_id` field has been removed from the stream event type.
- Legacy persisted frames may still contain `task_id`, so the loader strips it during replay.

Cleanup:

- Keep the stream contract focused on conversation identity.
- If task identity is needed later, add it back intentionally with a real consumer.

### `AgentRunSnapshot`

Status: keep, but trim underused subfields later

- This is part of the stable read model in [`STABLE_API.md`](/home/rogerw/project/vibrant/vibrant/orchestrator/STABLE_API.md#L75).
- Projected in [`interface/basic.py`](/home/rogerw/project/vibrant/vibrant/orchestrator/interface/basic.py#L225).
- The snapshot is justified as a stable combined durable/live run view.
- Some subfields appear underused by first-party code:
  - `outcome.output`
  - `provider.native_event_log`
  - `provider.canonical_event_log`
  - possibly `provider.thread_path`
  - possibly `provider.resume_cursor`

Cleanup:

- Keep the top-level object stable.
- Deprecate and eventually remove underused subfields one by one, not the entire snapshot.
- Prefer adding a dedicated provider-debug view if log paths need to remain inspectable.

### Compatibility Aliases

Status: removed in current tree

- Defined in [`types.py`](/home/rogerw/project/vibrant/vibrant/orchestrator/types.py#L480).
- The unused aliases have been removed from the types module and package re-export surface.

Cleanup:

- Keep new code on explicit `AgentRun*` names only.

## Recommended Cleanup Order

### Phase 1: Fix incorrect or dead shape

Low-risk cleanups:

1. Fix `WorkflowSnapshot.active_agent_ids` by either removing it or projecting it.
2. Split `AttemptExecutionSnapshot` into internal recovery state and public execution view.
3. Split `RuntimeExecutionResult` into a public wait result and an internal provider-debug result.
4. Replace `BoundAgentCapabilities` with a narrower binding-registration object.

### Phase 2: Split public views from internal execution detail

Medium-risk cleanups:

1. Introduce `QuestionView` for UI and policy call sites.
2. Keep the durable question record only where persistence needs it.
3. Remove the remaining legacy `models/state.py` question model once migration compatibility is no longer needed.

### Phase 3: Tighten stable read models

Higher coordination cost:

1. Trim underused `AgentRunSnapshot` subfields behind a deliberate compatibility window.
2. Add shape tests for public read models so their boundaries stop drifting.

## Suggested Guardrails

To keep this from drifting again:

- Add tests that construct each public read model from its real builder and assert the exact expected fields.
- Do not expose provider resume cursors or live service objects through MCP resources unless the stable API says so explicitly.
- For new types, require a short docstring that states whether the type is:
  - durable store record
  - internal policy helper
  - public read model
  - compatibility alias

## Concrete Next Refactor

If doing this incrementally, start here:

1. Replace `BoundAgentCapabilities` with a two-field value object used only for MCP registration and provider invocation compilation.
2. Split `AttemptExecutionSnapshot` into an internal recovery shape and a public execution view.
3. Split `RuntimeExecutionResult` into a public wait result and an internal provider-debug result.
4. Introduce `QuestionView` for UI and policy callers.
