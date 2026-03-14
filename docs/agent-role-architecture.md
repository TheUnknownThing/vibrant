# Role / Agent / Run Architecture

> Date: 2026-03-14
> Status: current implementation reference plus next API direction

## Summary

Vibrant now has a real **role / agent-instance / run** architecture.

The core model is:

- **role** = policy and capabilities
- **agent instance** = stable logical actor identity
- **run** = one execution of that actor

That structural split is already implemented in persistence, runtime, task
execution, and the managed Gatekeeper path.

The next rule is behavioral:

- roles define the typed result they produce
- the orchestrator decides what to do because of that result

## Current Status

What is already true in the codebase:

- stable agent instances are persisted as `AgentInstanceRecord`
- individual executions are persisted as `AgentRunRecord`
- instance state and run history are stored separately
- task execution resolves a stable task-scoped instance, then creates a fresh
  run under it
- the managed Gatekeeper path resolves a stable project-scoped instance, then
  creates a fresh run per interaction
- provider-thread reuse is anchored to the stable agent identity, not to one
  long-lived run record
- facade reads are now grouped around `roles`, `instances`, and `runs`

What is still transitional:

- some runtime and facade reads still expose run-centric compatibility naming
- Gatekeeper still has some bespoke lifecycle behavior
- not every role metadata field is yet a fully generic policy input

## Primary Code Locations

The main implementation lives in these files:

- `vibrant/models/agent.py`
  - `AgentInstanceRecord`
  - `AgentRunRecord`
- `vibrant/orchestrator/basic/stores/agent_instances.py`
- `vibrant/orchestrator/basic/stores/agent_runs.py`
- `vibrant/orchestrator/policy/gatekeeper_loop/roles.py`
- `vibrant/orchestrator/policy/task_loop/roles.py`
- `vibrant/orchestrator/basic/runtime/service.py`
- `vibrant/orchestrator/policy/gatekeeper_loop/lifecycle.py`
- `vibrant/orchestrator/policy/task_loop/execution.py`
- `vibrant/orchestrator/facade.py`
- `vibrant/orchestrator/STABLE_API.md`

## Layered Model

### 1. Role layer

The role layer carries policy and capability metadata for each built-in role.

In practice it answers:

- what is this actor for?
- what scope should resolve to the stable actor id?
- which provider/runtime defaults belong to that role?
- should the role prefer a persistent thread?
- which capability preset and prompt shape should apply?

This data belongs in `policy/`, not `basic/`, because concrete roles are part
of workflow policy.

### 2. Agent-instance layer

The stable actor layer is represented by `AgentInstanceRecord`.

An agent instance answers:

- who is the logical actor?
- what role does it implement?
- what scope does it belong to?
- what provider defaults belong to this actor?
- what was its latest run?
- what run is active right now?

Current built-in scope patterns are:

- task-scoped workers
- a project-scoped Gatekeeper

### 3. Run layer

The execution layer is represented by `AgentRunRecord`.

A run answers:

- what happened this time?
- which prompt/workspace/provider metadata was used?
- what lifecycle state did this execution reach?
- what summary or error belongs to this execution?
- which provider thread or logs belong to this execution?

Stable actor identity and run identity are intentionally different:

- stable agent id: `agent-task-001`
- run id: `run-agent-task-001-3f2a9b7c`

That split is the foundation for retries, resume, and durable history.

## Runtime and Persistence Semantics

Important current rules:

- stable instances are stored in `.vibrant/agent-instances`
- run records are stored in `.vibrant/agent-runs`
- provider-thread continuity is anchored to the stable `agent_id`
- a stable actor may accumulate many runs over time
- the runtime currently allows at most one active live run per stable agent
  instance

## Current Workflow Integration

### Task execution

When a task runs through the task loop:

1. policy selects an eligible task
2. a workspace and attempt are prepared
3. the stable task-scoped worker instance is resolved
4. a fresh run is created beneath that stable instance
5. runtime starts the run
6. the run is projected into orchestrator-owned conversation and runtime state
7. policy decides review, retry, escalation, or completion

Important consequence:

> Re-running the same task role creates a new run for the same stable actor.

### Gatekeeper interaction

When Gatekeeper runs through the managed runtime path:

1. policy shapes a `GatekeeperRequest`
2. the stable project-scoped Gatekeeper instance is resolved
3. the latest resumable provider thread may be reused for that stable instance
4. a new run record is created for this submission
5. runtime starts or resumes the run
6. typed MCP actions update orchestrator state
7. policy decides the workflow implications

Important consequence:

> Gatekeeper continuity is modeled as one stable project actor with many runs.

### Restart and recovery

On startup, instance/run relationships can be rebuilt from persisted data:

- load run records from `.vibrant/agent-runs`
- load stable instances from `.vibrant/agent-instances`
- reconcile missing instance records from existing runs
- rebuild derived orchestrator state from the resulting run set

## Behavioral Boundary

The structural model is already in place. The main design rule now is the
boundary between role semantics and orchestrator authority.

### Roles and agents define

Roles and agent implementations are responsible for:

- defining what context they need
- defining how they execute
- defining what typed result payload they produce
- declaring role-local semantics such as success, blocked, needs-input, or
  replan-requested

### The orchestrator defines

The orchestrator remains responsible for:

- deciding what to do next because of that payload
- validating and persisting durable state changes
- routing review, retry, merge, pause, escalation, or question flows
- coordinating multiple runs and workflow stages
- remaining the source of truth for roadmap, consensus, questions, attempts,
  and workflow state

In short:

- agent = producer of typed outcomes
- orchestrator = consumer and decision authority

### Run envelope vs role payload

Every run should be understood as two layers:

1. a shared runtime envelope
   - lifecycle state
   - summary
   - error
   - timestamps
   - canonical events
   - pending input requests
   - provider resume information
2. a role-specific payload
   - code-agent implementation outcome
   - Gatekeeper planning/review decision
   - merge outcome
   - validation/test outcome

The envelope is host/runtime state. The payload is role meaning. Those should
not be flattened back into one fake universal business result.

## Public API Shape Today

The public agent-facing orchestrator API now uses layered namespaces that match
the implemented model.

Role-layer reads:

- `facade.roles.get(role)`
- `facade.roles.list()`

Instance-layer reads and control:

- `facade.instances.get(agent_id)`
- `facade.instances.list(...)`
- `facade.instances.active()`
- `facade.instances.wait(agent_id, ...)`
- `facade.instances.respond_to_request(agent_id, request_id, ...)`

Run-layer reads:

- `facade.runs.get(run_id)`
- `facade.runs.list(...)`
- `facade.runs.for_task(task_id, ...)`
- `facade.runs.for_instance(agent_id)`
- `facade.runs.latest_for_task(task_id, ...)`

Top-level snapshot defaults:

- `OrchestratorFacade.snapshot()` returns `OrchestratorSnapshot`
- `OrchestratorSnapshot.roles` is a tuple of role snapshots
- `OrchestratorSnapshot.instances` is a tuple of stable instance snapshots

## Public API Direction

The stable surface should continue moving toward explicit layer nouns instead of
mixed compatibility helpers.

### Keep these nouns first-class

- roles
- instances
- runs
- workflow
- tasks
- questions
- documents

### Prefer stable read models over raw persistence records

The canonical public shapes should be snapshots such as:

- `RoleSnapshot`
- `AgentInstanceSnapshot`
- `AgentRunSnapshot`
- `WorkflowSnapshot`
- `DocumentSnapshot`
- `QuestionRecord`

The public contract should not treat raw `AgentRunRecord` persistence models as
the preferred facade type.

### Keep instance identity and run detail separate

`AgentInstanceSnapshot` should own stable identity, scope, defaults, and the
current latest/active run linkage.

`AgentRunSnapshot` should own one execution's runtime envelope and role payload.

That keeps the API aligned with the durable model instead of collapsing back
into one overloaded "agent snapshot".

## Coordination Boundaries Today

### Durable ownership

- `basic/stores/agent_instances.py`
  - owns stable instance persistence
- `basic/stores/agent_runs.py`
  - owns run persistence

### Runtime and orchestration ownership

- `basic/runtime/service.py`
  - owns live runtime mechanics
- `policy/task_loop/execution.py`
  - owns worker launch policy around task attempts
- `policy/gatekeeper_loop/lifecycle.py`
  - owns Gatekeeper-specific session lifecycle policy
- `OrchestratorFacade`
  - exposes the stable read/control surface

This is why the model is already structurally correct even though some policy is
still bespoke: durable identity and execution history are now separate.

## Recommended Direction

The right next step is not another broad rename. The useful follow-up work is:

- keep tightening policy around the stable instance boundary
- make role metadata or payload extraction authoritative where that removes ad
  hoc branching
- keep public read models instance-aware by default
- remove redundant compatibility names only when the layered surface stays
  clearer afterward

## Short Checklist

### Implemented

- [x] separate stable instance records from run records
- [x] persist instances in `.vibrant/agent-instances`
- [x] persist runs in `.vibrant/agent-runs`
- [x] resolve task execution through stable task-scoped instances
- [x] resolve managed Gatekeeper execution through a stable project-scoped
  instance
- [x] expose role-, instance-, and run-oriented facade namespaces

### Follow-up direction

- [ ] keep runtime and conversation traces explicitly run-aware where that
  improves debugging
- [ ] finish removing remaining run-centric compatibility naming from public
  projections
- [ ] keep pushing role semantics into typed payloads instead of ad hoc
  orchestration branching

## Final Summary

Vibrant is now best described as:

- role defines policy and capabilities
- agent instance defines stable actor identity and scope
- run defines one execution of that actor

That architecture is already real in the durable model and in the main
orchestration paths. The remaining work is behavioral and API cleanup, not a
return to the old single-record model.
