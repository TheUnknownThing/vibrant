# Phase 2: Role-Defined Results, Orchestrator-Defined Actions

This document describes the proposed **second phase** of the agent-system refactor after the current **role / agent-instance / run** model.

The goal of this phase is to make the runtime and storage model already present in the codebase more behaviorally coherent without collapsing all agent behavior into one artificial result schema.

## Summary

Phase 1 established the structural model:

- **Role** = policy and capabilities metadata
- **Agent instance** = stable logical actor identity
- **Run** = one execution of that actor

That structure is already implemented.

Phase 2 should establish the behavioral boundary:

- **agents and roles define what result they produce**
- **the orchestrator defines what to do based on that result**

This is the key shift.

The system should stop drifting toward either of these bad extremes:

- one fake "unified" business result for all roles
- service-layer orchestration that implicitly hardcodes role behavior everywhere

Instead, it should adopt:

- one shared **runtime result envelope**
- role-specific **typed result payloads**
- orchestrator-owned **workflow decisions and side effects**

## Problem Statement

The current codebase has a real structural role / instance / run architecture, but behavior is still partly concentrated in orchestrator services.

Today, the system still relies on service-specific logic for decisions such as:

- when to resume an existing thread
- how to interpret some run outcomes
- what follow-up stage should happen after a run
- which paths are Gatekeeper-specific versus task-agent-specific

This creates two problems:

1. role behavior is not yet fully expressed through the role layer
2. services still carry more role-specific branching than they should

At the same time, it would be a mistake to push all workflow decisions down into the role or agent classes. The orchestrator must remain the authority for persistence, transitions, coordination, and durable side effects.

## Core Principle

Phase 2 adopts this boundary:

### Agents / Roles

Agents and roles are responsible for:

- defining what context they need
- defining how they execute
- defining what structured result they produce
- defining the semantic meaning of that result

### Orchestrator

The orchestrator is responsible for:

- deciding what to do next based on that result
- validating and persisting state changes
- routing review, retry, pause, merge, testing, or escalation flows
- coordinating multiple agents and workflow stages
- remaining the source of truth for consensus, roadmap, questions, and workflow state

In short:

- **agent = producer of typed outcomes**
- **orchestrator = consumer and decision authority**

## Non-Goals

Phase 2 should explicitly avoid these goals:

### No single business result for all roles

There should not be one universal domain result schema shared by Gatekeeper, code, merge, and validation/test roles.

Those roles produce different kinds of outcomes.

Trying to force them into one business shape would create optional-field soup and hidden role branching.

### No role-owned persistence authority

Roles should not directly own:

- consensus persistence
- roadmap persistence
- workflow-state mutation
- question persistence
- merge application
- cross-agent coordination

Those remain host responsibilities.

### No prompt-driven workflow semantics

The orchestrator should not rely on loose transcript conventions to determine global actions.

Role outputs should become more structured, while workflow consequences remain orchestrator-owned.

## Target Model

Phase 2 should standardize the difference between:

1. the **runtime envelope** for any run
2. the **role payload** produced by that run

### 1. Shared Runtime Envelope

Every run should continue to have a generic host-facing envelope containing runtime facts such as:

- lifecycle state
- summary
- error
- canonical events
- pending input requests
- provider resume information
- timestamps

This envelope is not the business meaning of the run.

It is only the generic runtime wrapper that lets the orchestrator:

- wait for completion
- persist lifecycle
- recover provider threads
- expose status to the UI
- respond to provider requests generically

### 2. Role-Specific Result Payload

Inside or alongside that envelope, each role should produce its own typed result payload.

Examples:

- `CodeAgentPayload`
- `GatekeeperDecisionPayload`
- `MergeAgentPayload`
- `ValidationAgentPayload`

This payload is where the role expresses the semantic outcome of its run.

Examples of role-specific payload content:

- code agent: implementation summary, blockers, files touched, confidence, completion status
- gatekeeper: verdict, requested user decisions, roadmap deltas, consensus deltas, replanning intent
- merge agent: conflict status, resolution summary, unresolved blockers
- validation agent: command outcomes, failing checks, confidence in pass/fail conclusion

The orchestrator must not assume these payloads are interchangeable.

## Recommended Role Result Contract

The role layer should define a typed result payload contract per role.

Conceptually:

```text
RunResultEnvelope[
  payload: RoleResultPayload
]
```

Where:

- the envelope is generic
- the payload is role-specific

The payload should answer:

- what happened in role terms?
- is the role declaring success, blocked work, or need for input?
- what structured information should the orchestrator consider?

The payload should not answer:

- should the workflow pause?
- should the task retry?
- should work merge now?
- should the roadmap be rewritten?

Those are orchestrator decisions.

## Responsibilities by Layer

### Role Layer

The role layer should become responsible for:

- prompt and run-context preparation for that role
- result-payload definition for that role
- role-specific interpretation of provider output into payload form
- declaring runtime preferences such as resumability or request support

This may be implemented either through richer role metadata or through explicit behavior objects attached to a role spec.

### Agent Instance Layer

The instance layer should remain responsible for:

- stable logical actor identity
- provider defaults for that stable actor
- active/latest run association
- resumable thread continuity for that actor

The instance layer should stay role-neutral.

### Run Layer

The run layer should remain responsible for:

- one execution record
- runtime lifecycle state
- run-local prompt/worktree/provider metadata
- the resulting runtime envelope

The run layer may carry the role-specific payload, but it should not decide global workflow actions.

### Service Layer

The service layer should remain responsible for:

- persistence and validation
- workflow transitions
- durable artifacts
- coordination between roles
- side effects such as merge, question creation, or retry scheduling

This means services should stay **specific to orchestrator authority**, but become **less specific to role semantics**.

## What Should Move Out of Services

Phase 2 should reduce service-owned role branching in these categories:

- special-case role outcome interpretation
- ad hoc follow-up-stage selection
- Gatekeeper-specific run policy that should really be role policy
- role-dependent prompt or resume decisions that are not host authority concerns

Examples of behavior that should become more role-driven:

- whether a role usually resumes prior thread context
- whether a role supports interactive provider requests
- how raw provider output becomes a typed role payload
- whether the role reports `completed`, `blocked`, `needs_input`, `replan_requested`, or similar semantic states

## What Must Stay in Services

These behaviors should remain orchestrator-owned even after Phase 2:

- persisting roadmap and consensus changes
- validating task-state transitions
- deciding retry versus escalation versus pause
- deciding whether to merge accepted work
- deciding whether to run validation after code execution
- deciding whether to route a result to Gatekeeper review
- creating and resolving user-facing question records

This is the main architectural safeguard of the phase.

## Role Hooks vs Orchestrator Decisions

Phase 2 is often easiest to imagine as "before run" and "after run" hooks, but the real target is slightly richer.

The role side should provide something conceptually like:

- `prepare_run(...)`
- `interpret_run(...) -> RoleResultPayload`
- `runtime_preferences(...)`

The orchestrator side should provide something conceptually like:

- `decide_next_action(payload, envelope, workflow_state)`
- `apply_decision(...)`

This keeps the role responsible for meaning and the orchestrator responsible for action.

## Example: Code Agent

### Code Role Produces

A code run may produce a payload such as:

- semantic status: `completed`, `blocked`, or `needs_input`
- implementation summary
- declared blockers
- optional changed-file summary
- optional confidence or self-check notes

### Orchestrator Decides

Based on that payload, the orchestrator decides whether to:

- mark the attempt as failed
- route to Gatekeeper review
- queue validation
- ask the user for missing information
- schedule a retry

The code role should not directly decide those workflow consequences.

## Example: Gatekeeper

### Gatekeeper Role Produces

A Gatekeeper run may produce a payload such as:

- semantic status: `accepted`, `retry_requested`, `needs_user_decision`, `replan_requested`, `escalated`
- task verdicts
- requested question content
- desired roadmap or consensus mutations in structured form

### Orchestrator Decides

The orchestrator then decides whether to:

- persist those roadmap or consensus changes
- create question records
- move the workflow into paused or executing state
- reschedule a task
- merge accepted work

The Gatekeeper role provides the decision payload; the orchestrator applies it.

## Why This Is Better Than a Unified Business Result

This model avoids two common failure modes.

### Failure Mode 1: Over-Unification

If every role must share one business result schema, the result becomes vague and overloaded.

### Failure Mode 2: Service Sprawl

If services alone infer role meaning from transcripts or per-role branches, the role system becomes structurally real but behaviorally weak.

Phase 2 avoids both by combining:

- shared runtime mechanics
- role-specific meaning
- orchestrator-owned action

## Suggested Interface Direction

The codebase does not need to adopt these exact names, but the design should move toward this shape.

### Role Definition

Each role should have:

- metadata
- runtime builder
- result payload type
- behavior helpers for run preparation and result interpretation

### Runtime Output

The runtime should continue to return a generic run envelope and should allow role-specific payload attachment.

### Orchestrator Consumption

Workflow services should consume:

- the generic envelope for lifecycle facts
- the role payload for meaning

Then those services should select the next action through orchestrator policy.

## Migration Plan

Phase 2 can be introduced incrementally.

### Step 1: Keep the Existing Runtime Envelope

Do not replace the existing runtime-level result wrapper.

Keep the generic envelope used for:

- waiting
- persistence
- recovery
- event streaming
- input request handling

### Step 2: Add Role-Specific Payload Extraction

Add a role-owned path that maps raw run output into a typed role payload.

This should happen close to the role or role-bound behavior layer, not inside broad workflow services.

### Step 3: Make Services Consume Payloads Instead of Ad Hoc Role Inference

Update services to branch on typed role payloads and workflow policy rather than on transcript heuristics or bespoke role checks.

### Step 4: Keep All Durable Side Effects Orchestrator-Owned

Do not move persistence authority into roles.

Instead, services should validate and apply the actions suggested by role payloads.

### Step 5: Remove Transitional Role-Specific Branching Where the Payload Now Covers It

As payload contracts become stable, simplify service logic that currently hardcodes special behavior for one role.

## Acceptance Criteria

Phase 2 should be considered complete when:

- every major built-in role produces a typed role-specific payload
- the runtime still exposes one generic execution envelope
- workflow services consume role payloads instead of relying on transcript-driven meaning
- workflow consequences remain orchestrator-owned
- no role directly owns consensus, roadmap, question, or workflow persistence
- Gatekeeper, code, merge, and validation roles can differ in result semantics without forcing one business schema

## Final Position

Phase 2 should not make the orchestrator "less important."

It should make the boundary cleaner:

- roles define **meaningful results**
- the orchestrator defines **meaningful actions**

That is the right next step after the current role / instance / run architecture.
