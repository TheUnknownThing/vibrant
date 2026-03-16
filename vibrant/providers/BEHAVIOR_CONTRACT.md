# Provider Behavior Contract

This document defines the behavior requirements a provider adapter must satisfy
for the Vibrant orchestrator and runtime to work correctly.

It is intentionally narrower than any provider's native protocol. A backend can
have very different native APIs, event ordering, chunking, resume metadata, or
tool semantics and still be compatible if it normalizes those differences into
this contract.

The typed adapter surface lives in
[`base.py`](/home/rogerw/project/vibrant/vibrant/providers/base.py).

## Scope

This contract applies to provider adapters that plug into `AgentBase`,
`BaseAgentRuntime`, and the orchestrator runtime service.

It does not require all providers to share:

- the same launch flags or transport
- the same thread or turn identifiers
- the same native payload shapes
- identical chunk sizes or event ordering for non-terminal progress events
- identical logging internals

## Required Adapter Surface

A compatible provider adapter must implement the `ProviderAdapter` methods:

- `start_session`
- `stop_session`
- `start_thread`
- `resume_thread`
- `start_turn`
- `interrupt_turn`
- `send_request`
- `respond_to_request`
- `on_canonical_event`

Unsupported optional capabilities must fail fast with an explicit exception such
as `NotImplementedError` or `RuntimeError`. Providers must not silently ignore
required controls like `runtime_mode`, `approval_policy`, or request-response
handling.

## Lifecycle Guarantees

### Session

- `start_session()` must leave the adapter ready for thread operations.
- A session should emit `session.started` once before any terminal session
  shutdown.
- `stop_session()` should be safe after success, failure, or interruption.
- If the adapter can emit a terminal session state, it should emit
  `session.state.changed` with `state="stopped"` on shutdown.

### Thread and Resume

- `start_thread()` opens a fresh provider conversation.
- `resume_thread(provider_thread_id)` re-attaches to an existing provider
  conversation using durable provider metadata.
- Resumable providers must surface a durable provider thread id as soon as it is
  known.
- The provider thread id may appear in the return value, in `thread.started`, or
  in a later canonical event if the native backend does not expose it
  immediately.
- If an `agent_record` is supplied, the adapter must persist resume metadata onto
  `agent_record.provider` once the thread id is known. At minimum this means:
  `kind`, `transport`, `provider_thread_id`, and a resumable
  `ProviderResumeHandle`.

### Turn

- `start_turn()` starts one provider turn using provider-neutral `input_items`,
  `runtime_mode`, and `approval_policy`.
- Each started turn must eventually resolve by surfacing either
  `turn.completed` or `runtime.error`.
- `interrupt_turn()` must not leave the runtime hanging indefinitely. It should
  cause the active turn to resolve promptly or stop the session cleanly.

## Canonical Event Requirements

The orchestrator depends on the canonical event stream, not on provider-native
logs. Providers may emit extra canonical events, but the following semantics are
required.

### Required terminal semantics

- `turn.completed` means the active turn has ended.
- `runtime.error` means the turn or session failed in a way the runtime should
  treat as terminal unless a later provider-specific recovery flow restarts the
  run.

### Assistant output

- Assistant text that should contribute to the runtime transcript or summary must
  be emitted through `content.delta`.
- `assistant.message.delta` and `assistant.message.completed` are optional extra
  events for richer consumers, not substitutes for `content.delta`.

### Requests and user input

- If a provider needs host interaction, it must surface that through
  `request.opened`.
- `request.opened` must carry a stable `request_id`, `request_kind`, and
  provider method name.
- After `respond_to_request()` succeeds, the adapter must emit
  `request.resolved` or surface a terminal `runtime.error`.
- Providers may also emit `user-input.requested` / `user-input.resolved` as
  convenience mirrors, but the generic `request.*` events are the minimum
  runtime contract.

### Thread identity

- If a canonical event includes a known provider thread id, it should populate
  `provider_thread_id`.
- A resumable provider should emit `thread.started` once the thread identity is
  known, but that event does not have to be immediate if the native backend
  reveals the session id later.

## Runtime Controls

Providers must either enforce or explicitly reject the provider-neutral runtime
controls passed by Vibrant:

- `runtime_mode`
- `approval_policy`
- provider-neutral `input_items`

If a backend cannot support a requested mode or approval policy, it must raise
an explicit error instead of silently downgrading behavior.

## Allowed Variation

The orchestrator must tolerate and providers may vary in:

- event chunking granularity
- auxiliary `task.progress` events
- presence or absence of `task.completed`
- whether `thread.started` happens immediately or later in the turn
- provider payload shape under `provider_payload`
- whether a failure emits only `runtime.error` or both `runtime.error` and a
  later `turn.completed`

Consumers must not depend on those details.

## Compatibility Checklist

When adding a new provider, verify all of the following:

1. `AgentBase.run()` can complete using only the adapter methods and canonical
   events the provider emits.
2. A successful run emits `content.delta` and terminates with `turn.completed`.
3. A failed run terminates with `runtime.error`.
4. If the provider is resumable, `agent_record.provider.resume_handle` is
   populated before the run finishes.
5. If the provider can ask for approval or input, `request.opened` and
   `request.resolved` are emitted and `respond_to_request()` unblocks the turn.
6. Unsupported runtime controls fail explicitly instead of being ignored.
