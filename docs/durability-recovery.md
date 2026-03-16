# Durability And Recovery

> **Date**: 2026-03-16
> **Status**: Draft

This document describes:

- the current durability and recovery design in Vibrant
- what the system can and cannot recover today
- the proposed future checkpoint-based recovery model
- the gap between the current system and that future model

The goal is not to claim exact arbitrary-kill recovery. The practical target is:

- durable host-owned checkpoints
- best-effort provider resume
- explicit operator-controlled continuation after restart

## 1. Summary

Vibrant already has a meaningful durability layer. Workflow state, task attempts,
questions, review tickets, workspaces, agent runs, agent instances, provider
event logs, and processed conversations are persisted under `.vibrant/`.

That gives the backend good recovery for:

- workflow status
- review-pending and accepted task state
- worker attempt identity and phase
- provider resume handles when they were persisted
- processed conversation history
- durable lookup of most execution artifacts by id

However, the current system does not provide seamless or exact recovery from
arbitrary interrupts or kills.

The main limitation is architectural:

- the orchestrator persists durable state
- providers may expose resumable thread handles
- but the orchestrator does not yet own fine-grained recovery checkpoints for
  in-flight execution boundaries

The future design should make the orchestrator the source of truth for recovery
precision. Providers should be treated as optional resume helpers, not as the
authority for exact recovery.

The same principle applies to handle identity:

- the host should own the stable logical execution handle
- live in-memory handles should be an implementation detail
- provider thread/session ids should remain resumability metadata, not the
  primary durable identity

## 2. Current Design

### 2.1 Durable State

The orchestrator persists these durable artifacts under `.vibrant/`:

- `state.json`
  workflow session state, pause/resume state, Gatekeeper session projection
- `attempts.json`
  task-attempt records and durable attempt status
- `questions.json`
  user-question records
- `reviews.json`
  review tickets and resolutions
- `workspaces.json`
  workspace metadata
- `agent-runs/*.json`
  one record per agent run, including provider resume metadata
- `agent-instances/*.json`
  stable logical agent identities
- `conversations/index.json`
  conversation manifests
- `conversations/frames/*.jsonl`
  processed conversation frames
- `logs/providers/native/*.ndjson`
  native provider diagnostics
- `logs/providers/canonical/*.ndjson`
  canonical provider event stream

### 2.2 Boot And Restart Flow

On orchestrator startup:

1. `.vibrant/` files are loaded.
2. Stores and services are reconstructed.
3. Runtime state is repaired against the fact that no live in-memory handles
   exist yet.
4. Active attempt sessions are reconciled.

Important current behavior:

- startup repairs stale in-memory state
- startup does not automatically restart worker runs
- startup does not automatically restart an interrupted Gatekeeper turn
- later commands may resume work from persisted metadata

### 2.3 Gatekeeper Recovery Today

The Gatekeeper has a durable session projection:

- `agent_id`
- `run_id`
- `conversation_id`
- `lifecycle_state`
- derived `provider_thread_id`
- `active_turn_id`
- `resumable`
- `last_error`

When the Gatekeeper is started again, the lifecycle service may reuse the latest
provider resume handle and start a new run against that provider thread.

What this means in practice:

- the Gatekeeper conversation can often continue on the same provider thread
- the host can preserve planning and review history
- a new Gatekeeper submission can resume prior provider context

What it does not mean:

- exact reattachment to an in-flight interrupted handle
- exact recovery of pending provider requests after process death
- seamless continuation of a half-completed turn

If the process dies while the Gatekeeper is `starting` or `running`, bootstrap
repairs that session to `idle` when no live runtime handle exists.

### 2.4 Worker Attempt Recovery Today

Worker execution is attempt-centric.

Each attempt durably records:

- `attempt_id`
- `task_id`
- `status`
- `workspace_id`
- `code_run_id`
- validation and merge run ids
- `conversation_id`
- timestamps

On the next task-loop tick, the system can:

- detect that a prior run already reached a durable terminal state and consume it
- detect that an attempt is recoverable and launch a new worker run
- reuse a provider resume handle if one exists
- otherwise restart from persisted task prompt and workspace metadata

This is real recovery, but it is not seamless:

- the attempt survives
- the run usually changes
- some in-flight output since the last durable write may be lost

### 2.5 Handle Identity Today

The current backend already has some transparent read-side projections, but not
yet transparent execution handles.

Today, the practical meaning of ids is:

- `attempt_id`
  stable logical task-attempt identity
- Gatekeeper session state
  stable logical session projection
- `run_id`
  concrete execution instance used for runtime lookup, provider bindings, and
  per-run persistence
- provider thread/session id
  external resumability anchor that may survive across multiple `run_id` values

This means the current implementation generally treats:

- one provider thread/session as resumable context
- one `run_id` as one execution incarnation

That is why a recovered worker attempt or Gatekeeper submission often resumes
against the same provider thread while creating a fresh `run_id`.

### 2.6 Conversation Durability Today

Vibrant does not use raw provider logs as the UI conversation model.
Instead it projects canonical runtime events into orchestrator-owned conversation
frames.

This is a strong design choice because it makes the host the owner of what the
UI can replay.

Current guarantees:

- durable conversation manifests
- durable run-to-conversation binding
- replayable processed conversation frames

Current limitation:

- frame appends are not yet tail-corruption tolerant after a hard kill

### 2.7 Provider Resume Today

The provider contract requires:

- session start/stop
- thread start/resume
- turn start
- interrupt
- request-response handling
- canonical event emission
- durable provider thread metadata when resumable

The provider contract does not require:

- exact mid-turn replay
- exact restoration of hidden model state
- arbitrary token-precise restart

This is a critical distinction.

Today the correct model is:

- providers may support resumable conversation threads
- providers do not define exact recovery precision
- the orchestrator must own recovery precision if it needs more than thread
  continuity

## 3. Current Guarantees

The current backend can reasonably claim:

- durable workflow reload after restart
- durable task-attempt, review-ticket, and question history
- durable worker recovery on a later execution tick
- durable provider thread handles when the adapter persisted them in time
- durable replay of most processed conversation history
- explicit non-support for worker interactive approval continuation

The current backend should not claim:

- seamless arbitrary-kill recovery
- exact in-flight Gatekeeper continuation
- exact mid-turn worker continuation
- lossless replay of every last event after a hard kill
- filesystem rollback to a prior exact workspace state
- fully transparent live-vs-durable execution handles for control operations

## 4. Known Gaps

### 4.1 No Fine-Grained Recovery Checkpoints

The host does not yet persist an explicit checkpoint object for:

- run high-water mark
- open provider requests
- exact recovery event boundary
- active phase and actor in one authoritative recovery record

Instead, recovery is assembled from multiple stores and projections.

This works, but it is not precise enough for strong crash-recovery guarantees.

### 4.2 Gatekeeper Awaiting-Input Is Not Durably Recoverable

If the Gatekeeper is waiting on a live provider request and the process dies:

- the run record may survive
- the provider thread id may survive
- the conversation survives

But the live request handle and in-memory request list do not.

That means restart can preserve context, but not necessarily resume the exact
pending request boundary.

### 4.3 Agent Run Writes Are Not Uniformly Crash-Safe

Many mapping stores use atomic JSON file replacement.

Agent run records do not. They are written directly as one file per run.

That creates a failure mode where:

- the process dies during a run-record write
- the run file becomes truncated or malformed
- the resume handle is lost or unreadable

Since provider resume metadata lives on run records, this is a real recovery
gap.

### 4.4 Conversation Tail Writes Are Not Yet Corruption-Tolerant

Conversation frames are appended as JSONL, but the reader assumes every line is
valid JSON.

After a hard kill during append, the final line may be partial. That can break
rebuild of the conversation until the tail is manually repaired.

### 4.5 Workspace Recovery Is Implicit, Not Checkpointed

Today workspace state is expected to survive because the filesystem survives.

That is acceptable for preserving files, but it does not define:

- what exact workspace state a checkpoint refers to
- how to revert to a prior known-good filesystem boundary
- how to distinguish "resume from current workspace" from "reset to checkpoint"

Without an explicit workspace checkpoint model, the system cannot honestly claim
"revert to last checkpoint" for file contents.

### 4.6 Transparent Handle Semantics Are Only Partial

The current backend does a reasonable job of projecting read models from either
durable state or a live in-memory runtime handle.

It does not yet provide the same transparency for control operations.

Today:

- read paths can often resolve state from durable records and overlay live
  details when present
- control paths such as interrupt, wait, kill, and request-response still need
  a live runtime handle behind the id

This means the backend is currently:

- mostly transparent for observation
- not yet transparent for execution control

### 4.7 Current `run_id` Semantics Do Not Match The Desired Transparent Model

The current codebase largely uses `run_id` as the concrete execution identity.

That is not inherently wrong, but it mixes together several concerns:

- stable logical handle
- live execution incarnation
- user-facing lookup identity

The future model should separate those concerns explicitly.

The discussed direction is:

- keep `run_id` as the stable host-owned logical execution handle
- add a separate per-incarnation id for each live materialization or recovery
  epoch
- keep provider thread/session ids as resumability metadata attached to the
  logical handle or incarnation, not as the primary identity

This preserves the existing widespread reliance on `run_id` as the main lookup
id while making the per-incarnation boundary explicit where needed.

## 5. Future Direction

The future design should use fine-grained host-owned checkpoints with
best-effort provider continuation.

The target contract is:

- the host owns authoritative recovery boundaries
- the provider contributes a resumable thread handle when available
- restart never silently continues execution
- restart surfaces a recoverable checkpoint and waits for operator intent

### 5.1 Recovery Principle

Recovery should mean:

- load the last durable host checkpoint
- classify the interrupted work as `resumable`, `restartable`, or `damaged`
- let the operator decide whether to continue

This is intentionally different from automatic background restart.

### 5.2 Handle And State Model

The future model should expose a transparent host-owned handle that works the
same way whether the backing state is live in memory or only durably recovered.

The top-level handle state should be:

- `running`
  there is an active live execution incarnation
- `stopped`
  there is no live execution incarnation, but the logical handle is still
  continue-able
- `finished`
  the logical handle is terminal and will not execute again

Each handle should also carry a single structured machine-readable `reason`
field to explain why it is in its current state.

Examples:

- `running` + `reason=active`
- `stopped` + `reason=process_died`
- `stopped` + `reason=user_stopped`
- `finished` + `reason=completed`
- `finished` + `reason=failed`
- `finished` + `reason=cancelled`

This keeps the application contract simple:

- live-vs-durable is hidden behind the handle
- continue-ability is encoded by state
- callers do not need separate flags like `continue_allowed`

### 5.3 Stable Run Id And Per-Incarnation Id

The discussed preferred direction is:

- `run_id` should become the stable logical execution handle
- a new per-incarnation id should identify each live materialization of that run
- resume after restart may create a new incarnation without changing `run_id`

This gives the product a stable public identity while preserving precise
internal boundaries for:

- subscriptions and live runtime tables
- event sequencing across resume borders
- binding lifetimes
- recovery and auditing

In this model:

- `run_id` is host-owned and stable
- `incarnation_id` is host-owned and changes across resume/materialization
  borders
- provider thread/session id may remain stable across multiple incarnations of
  one `run_id`

This intentionally avoids using provider identity as the primary meaning of a
run.

### 5.4 Checkpoint Types

#### Run checkpoint

Each run should have a durable checkpoint that records:

- `run_id`
- `incarnation_id`
- `agent_id`
- `role`
- `attempt_id` or Gatekeeper session binding if applicable
- `phase`
- `provider_resume_handle`
- `provider_session_id`
- `conversation_id`
- `event_high_watermark`
- `request_state`
- `workspace_checkpoint_ref`
- `state`
- `reason`
- `updated_at`

This is the authoritative recovery record for one logical execution handle and
its current incarnation boundary.

#### Conversation checkpoint

The conversation layer should expose a precise durable high-water mark:

- monotonically increasing event sequence
- append-only log
- tail corruption tolerance
- optional checksum or length validation per record

The goal is exact host-visible replay through sequence `N`, even if the provider
cannot recover the exact internal generation boundary after `N`.

#### Attempt checkpoint

Task attempts should gain an explicit execution checkpoint, not just a status
value spread across projections.

The checkpoint should answer:

- which task attempt is active
- which phase it is in
- which agent/run currently owns it
- which incarnation currently owns it if one is live
- whether it is currently `running`, `stopped`, or `finished`

Example phases:

- `leased`
- `code_running`
- `code_awaiting_input`
- `validating`
- `review_pending`
- `merge_pending`
- `completed`
- `failed`

#### Workspace checkpoint

The future model should not require git commits for content checkpoints.
If workspace precision is desired without git, the system needs an explicit
filesystem checkpoint primitive.

Possible implementations:

- reflink or copy-on-write snapshot if supported
- full tree copy for checkpoint boundaries
- provider-specific or platform-specific snapshot adapters later

What matters is not the implementation detail. What matters is that a checkpoint
has a durable workspace reference that means:

- "resume from current filesystem state"
- or "reset to checkpointed filesystem state"

### 5.5 Provider Role In The Future Model

Providers should be treated as best-effort continuation helpers.

The host may assume:

- a provider thread can sometimes be resumed
- resuming that thread usually preserves enough conversational continuity to be
  useful

The host should not assume:

- exact model-state restoration
- exact replay from an arbitrary event inside one turn
- guaranteed graceful shutdown before death

This means the durable contract should be described as:

- checkpointed host recovery with best-effort provider continuation

not:

- seamless exact resume

## 6. Gap Analysis

### 6.1 Current State

Current state is already good enough for:

- durable workflow and review progression
- preserving planning and execution history
- recovering worker attempts with acceptable loss of in-flight tail data
- preserving provider thread handles often enough to resume useful context

Current state is not good enough for:

- strong arbitrary-kill guarantees
- precise in-flight recovery boundaries
- workspace rollback to a known checkpoint
- exact Gatekeeper awaiting-input recovery
- transparent control operations against one stable logical handle

### 6.2 Required Changes

To close the gap, the backend needs:

- atomic and fsynced run-record persistence
- tail-tolerant conversation log reading
- explicit recovery checkpoint records for runs and attempts
- a stable-handle API with separate per-incarnation identity
- durable request-state persistence for interactive runs
- explicit workspace checkpoint semantics
- restart UX that surfaces recovery choices instead of auto-continuing

### 6.3 What Does Not Need To Change

The following current design choices are still correct:

- orchestrator-owned durable state under `.vibrant/`
- attempt-centric task execution
- provider-neutral runtime contract
- orchestrator-owned conversation projection
- best-effort provider resume handles
- not auto-restarting execution on boot

The following current design choice should change:

- `run_id` currently behaves mostly like an execution incarnation id; the future
  model should make it the stable logical handle and introduce a separate
  incarnation id

## 7. Recommended Product Contract

The recommended external contract for Vibrant durability and recovery is:

- Vibrant durably stores workflow, attempts, questions, reviews, runs,
  conversations, and provider resume metadata under `.vibrant/`.
- After restart, Vibrant restores the latest durable checkpointed state.
- Vibrant exposes one stable host-owned handle per logical execution.
- That handle reports semantic state as `running`, `stopped`, or `finished`.
- If a provider resume handle exists, Vibrant may continue from that provider
  thread.
- If no provider resume handle exists, Vibrant may restart work from the latest
  durable host checkpoint.
- Vibrant does not guarantee exact lossless recovery of in-flight output after
  an arbitrary kill.
- Vibrant does not automatically continue execution after restart; the operator
  explicitly chooses whether to continue.
- Live in-memory state and durably recovered state are an implementation detail,
  not part of the application-level handle contract.

## 8. Implementation Priorities

Suggested order:

1. Make run-record persistence atomic.
2. Make conversation frame reading tolerate torn final lines.
3. Introduce a stable logical run handle plus a separate per-incarnation id.
4. Add typed run checkpoints with event high-water marks.
5. Add typed attempt checkpoints that explicitly record phase, active run, and
   active incarnation.
6. Add durable request-state persistence for interactive Gatekeeper runs.
7. Add workspace checkpoint semantics.
8. Add recovery UI and control-plane commands for:
   `continue`, `restart_from_checkpoint`, `abandon`

## 9. Bottom Line

The current backend is a reasonable foundation for durable recovery, but it is
not yet a complete crash-recovery system.

The right future model is:

- fine-grained host-owned checkpoints
- stable host-owned execution handles with separate per-incarnation ids
- best-effort provider continuation
- explicit user-controlled recovery

That model matches the realities of general providers such as Codex while still
giving Vibrant precise, understandable, and operable recovery behavior.
