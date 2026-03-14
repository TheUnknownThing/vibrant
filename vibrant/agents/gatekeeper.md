# Gatekeeper Identity, Responsibilities, and Migration Guide

## Purpose

The Gatekeeper is a special agent role in Vibrant. Unlike a normal code agent, it is not primarily responsible for editing source code or completing a single implementation task. Its job is to supervise project execution, maintain planning continuity, evaluate outcomes, and drive orchestrator behavior through structured interactions.

In the current architecture direction, the Gatekeeper should be treated as:

- a long-lived project-level conversational agent
- a read-only agent from the filesystem perspective
- a decision-making and orchestration agent rather than a code-producing worker
- an agent that interacts with the Orchestrator through MCP and structured request/response flows

This document explains the Gatekeeper's identity, what makes it different from a normal code agent, and how to migrate it cleanly into the AgentBase and AgentRuntime architecture.

## Role Definition

The Gatekeeper is the planning and review authority for a Vibrant project.

Its responsibilities include:

- creating or refining the execution plan
- reviewing agent outcomes against acceptance criteria
- deciding whether work is accepted, retried, escalated, or replanned
- asking the user for high-level decisions when required
- maintaining continuity across multiple planning and review turns
- issuing structured orchestration intents to the host through MCP

The Gatekeeper is not a direct replacement for a code agent with a different prompt. It is a separate role with different lifecycle expectations and different side effects.

## How the Gatekeeper Differs from a Code Agent

### Code Agent

A code agent is usually:

- short-lived
- task-scoped
- expected to produce code changes
- tied to a task worktree or branch
- typically run once per task attempt

The main output of a code agent is implementation work plus a summary.

### Gatekeeper

The Gatekeeper is usually:

- long-lived
- project-scoped rather than task-scoped
- expected to preserve conversation context across triggers
- focused on decisions, planning, review, and escalation
- not expected to write project source files directly

The main output of the Gatekeeper is not code. Its output is a set of structured orchestration decisions and user-facing planning actions.

## Gatekeeper Operating Model

The intended operating model is:

- filesystem access remains read-only
- project state changes are driven through MCP calls to the Orchestrator
- the Gatekeeper preserves a durable provider thread and resumes it across multiple conversations
- the Gatekeeper relies on the Orchestrator as the source of truth for persisted state

This means the Gatekeeper should not directly own mutation of execution state. Instead, it should ask the Orchestrator to apply changes in a controlled, auditable, structured way.

## Why Read-Only is the Right Default

Keeping the Gatekeeper read-only simplifies the security and consistency model.

Benefits:

- the Gatekeeper cannot silently mutate repository state outside the approved orchestration surface
- all durable state changes remain centralized in the Orchestrator
- consensus and roadmap updates can be validated and versioned by one authority
- recovery behavior is easier to reason about because the host remains the source of truth

With this model, the Gatekeeper still has strong control over the system, but its control is expressed through MCP tools rather than arbitrary file writes.

## What the Gatekeeper Should Output

In the new model, the Gatekeeper should not be treated as a text parser target that emits loose transcript conventions such as plain-text verdict lines or file-based diffs that are later reinterpreted.

Instead, its meaningful output should be:

- MCP calls
- structured request/response exchanges with the host
- a final conversational summary for auditability and UI display

For example, values such as these should be host-derived from structured tool activity rather than transcript heuristics:

- verdict
- questions
- consensus_updated
- roadmap_updated
- plan_modified

The transcript still matters for observability and operator review, but it should no longer be the authoritative state source.

## Gatekeeper Lifecycle Expectations

The Gatekeeper should preserve context across multiple triggers, including:

- project start
- task completion review
- task failure review
- max retries exceeded
- user conversation

This makes the Gatekeeper fundamentally different from the usual one-task-one-run model used by code agents.

The practical implication is that provider thread persistence and thread resumption are especially important for the Gatekeeper.

## Should the Gatekeeper Use the Existing Agent Architecture?

Yes.

The existing AgentBase and AgentRuntime design is sufficient as the main architectural foundation. A separate Gatekeeper-specific runtime stack is not required.

The Gatekeeper should be represented as a specialized AgentBase subclass and then exposed through the same runtime protocol used for other agents.

This keeps the system coherent:

- AgentBase manages the provider session, thread, turn, and event lifecycle
- AgentRuntime exposes the run as an AgentHandle and NormalizedRunResult
- the Orchestrator remains responsible for persistence and workflow coordination

## What the Existing Architecture Already Gives You

The current architecture already provides the key primitives needed for the Gatekeeper migration:

- a provider adapter lifecycle in AgentBase
- canonical event forwarding
- provider thread persistence and resume support
- a runtime handle that can observe pending provider requests
- a host-side respond_to_request control path

This means the migration should focus on behavior and integration details rather than inventing a new abstraction hierarchy.

## What Must Change for the Gatekeeper to Work Correctly

Even though the architecture is sufficient, several behavioral differences matter.

### 1. The Gatekeeper must not auto-reject host-facing requests

AgentBase currently assumes autonomous agents should reject interactive provider requests by default.

That is acceptable for ordinary code agents, but not for the Gatekeeper.

The Gatekeeper must be able to participate in request/response flows because its orchestration authority is expressed through MCP and host-mediated requests.

Implication:

- the Gatekeeper subclass should override the default auto-reject behavior

### 2. The Gatekeeper should remain read-only at the runtime level

The Gatekeeper should use read-only runtime modes for both thread and turn execution.

Implication:

- it should inherit or emulate the read-only behavior used for non-mutating agents
- project mutation should move to MCP tool execution handled by the Orchestrator

### 3. The Gatekeeper should be treated as a long-lived conversational identity

The provider thread for the Gatekeeper is not just a recovery handle. It is a continuity mechanism.

Implication:

- the latest Gatekeeper thread should usually be resumed rather than replaced
- user follow-up responses should continue the same thread whenever possible

### 4. The host remains the source of truth

The Gatekeeper may preserve conversational memory, but the Orchestrator should remain authoritative for durable state.

Implication:

- if the Gatekeeper conversation and the host state disagree, the host state wins
- each Gatekeeper invocation should still receive fresh structured state from the Orchestrator

## MCP and Request Handling Model

The Gatekeeper does not need a dedicated MCP-specific runtime state machine. MCP should be treated as one kind of provider-mediated host interaction.

That means:

- the runtime does not need a special Gatekeeper-only MCP state
- the runtime does need to handle provider requests that require host participation

This is the important distinction.

If Codex performs something entirely inside its own execution environment, that is effectively a black-box tool invocation and the runtime does not need to care.

If Codex sends a server request to the host and cannot continue until the host responds, then the runtime does need to care. In that situation, the runtime must surface the pending request and allow the Orchestrator to resolve it.

## Relationship Between MCP Calls and Runtime State

You do not need a special MCP state, but you do need a general external-response state.

Today, the runtime exposes this concept as:

- request.opened canonical events
- InputRequest on the AgentHandle
- an awaiting-input state on the handle

That is enough as a general mechanism, provided you accept that the name awaiting_input is broader than just literal human text input.

In other words:

- no separate MCP lifecycle layer is required
- no separate Gatekeeper-only request model is required
- one generic request/response model is enough

## Verdicts, Questions, and Plan Updates in the New Model

In the previous Gatekeeper implementation, values such as verdict and questions were inferred after the run by parsing transcripts and comparing documents.

In the new model, these values should instead be derived from structured host actions.

Recommended rule:

- if the Gatekeeper wants to accept work, it calls an MCP tool expressing that decision
- if it wants to request user clarification, it calls a dedicated MCP tool for that
- if it wants to update roadmap or consensus state, it asks the host to do so through structured parameters

The host then becomes responsible for:

- validation
- persistence
- versioning
- event emission
- durable state synchronization

## Recommended MCP Surface

The MCP surface should be narrow, explicit, and high-level.

Good examples:

- end_planning_phase
- request_user_decision
- withdraw_question
- update_consensus
- add_task
- update_task_definition
- reorder_tasks
- accept_review_ticket
- retry_review_ticket
- escalate_review_ticket

Bad examples:

- arbitrary shell execution
- raw state mutation by path
- unrestricted file write tools
- generic orchestrator.execute(action, payload) endpoints

The Gatekeeper should be powerful, but only through well-scoped operations.

## Why a Narrow MCP Surface Matters

A narrow MCP surface gives you:

- clearer auditability
- better recovery behavior
- easier validation
- better UI semantics
- lower risk of destructive or incoherent state mutations

This is especially important because the Gatekeeper is a privileged planning actor even if it is filesystem read-only.

## Context Preservation Guidance

The Gatekeeper should preserve provider thread context across turns, but that does not mean the thread history alone becomes the source of truth.

Use this rule:

- preserve the thread for continuity
- re-inject current project state on each new trigger
- trust the Orchestrator's structured state over any stale conversational memory

This prevents the Gatekeeper from drifting into outdated assumptions while still benefiting from long-running memory.

## Migration Strategy

The safest migration path is incremental.

### Phase 1: Move the Gatekeeper onto AgentBase

Introduce a Gatekeeper agent implementation as an AgentBase subclass.

It should:

- return AgentType.GATEKEEPER
- use read-only runtime modes
- disable automatic rejection of provider requests
- expose Gatekeeper-specific prompt construction
- preserve provider thread continuity across runs

### Phase 2: Run it through BaseAgentRuntime

Wrap the Gatekeeper agent in BaseAgentRuntime just like other agents.

This gives you:

- AgentHandle
- provider thread resumption
- pending request tracking
- wait and interrupt support

At this stage, you should not need a separate runtime stack just for the Gatekeeper.

### Phase 3: Replace transcript-driven semantics with MCP-driven semantics

Gradually remove logic that infers system meaning from transcript text or direct file mutation.

Move to:

- host-executed structured actions
- structured action results
- transcript as secondary audit output only

### Phase 4: Keep compatibility shims only where needed

Temporary fallbacks such as transcript sentinels can remain during migration, but they should not remain the main control path.

If an MCP tool exists, that tool should be the source of truth.

## What Should Stay Outside the Runtime Layer

The runtime should not become Gatekeeper-specific workflow logic.

The runtime layer should continue to do only these generic things:

- launch the agent
- observe canonical events
- track pending provider requests
- expose a handle
- allow the host to respond to requests
- normalize the run result

The runtime should not directly own:

- task review policy
- roadmap semantics
- consensus semantics
- escalation policy
- planning completion semantics

Those belong above the runtime boundary.

## Recommended Division of Responsibilities

### Gatekeeper Agent

Owns:

- prompt construction
- read-only execution identity
- conversational continuity
- participating in request/response flows

### Runtime

Owns:

- provider lifecycle
- event bridging
- pending request exposure
- handle-based control
- run normalization

### Orchestrator

Owns:

- state persistence
- MCP tool implementation
- consensus and roadmap durability
- status transitions
- user escalation and workflow coordination

## Practical Rules

When implementing the migrated Gatekeeper, use the following rules.

1. Treat the Gatekeeper as a supervisor, not a code worker.
2. Keep the Gatekeeper read-only.
3. Keep the Gatekeeper on a long-lived provider thread whenever possible.
4. Use MCP for side effects rather than direct file mutation.
5. Keep the Orchestrator as the source of truth.
6. Avoid adding a Gatekeeper-specific runtime layer unless the generic runtime proves insufficient.
7. Do not introduce MCP-specific states unless generic request/response handling proves inadequate.
8. Prefer structured host actions over transcript parsing.

## Final Recommendation

The correct migration is not to design a separate Gatekeeper execution stack.

The correct migration is to reuse the existing agent architecture and adapt behavior at the right seams:

- Gatekeeper becomes a specialized AgentBase subclass
- BaseAgentRuntime remains the runtime wrapper
- the Orchestrator exposes a carefully designed MCP surface
- provider requests are handled through the existing request/response path
- durable project state remains host-owned

This keeps the architecture coherent while still honoring the Gatekeeper's special role as a long-lived, read-only, orchestration-oriented agent.
