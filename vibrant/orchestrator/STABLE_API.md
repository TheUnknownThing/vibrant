# Orchestrator Facade Stable API

This document records the stable first-party Python API for the orchestrator.

As of **March 16, 2026**, the stable integration surface is
[`vibrant.orchestrator.OrchestratorFacade`](/home/rogerw/project/vibrant/vibrant/orchestrator/facade.py)
plus the documented companion read models and helper views it exposes.

This document is intentionally narrow. It does not define the internal control
plane, MCP implementation details, durable store layout, or other internal
orchestrator wiring.

## Scope

The following are promised as stable:

- the import path `vibrant.orchestrator.OrchestratorFacade`
- the import path `vibrant.orchestrator.OrchestratorSnapshot`
- the documented public methods on `OrchestratorFacade`
- the documented helper attributes `facade.roles`, `facade.instances`, and `facade.runs`
- the semantic meaning of the typed read models returned through the facade

The following are not part of the stable contract:

- `facade._orchestrator`
- `facade._control_plane`
- underscore-prefixed attributes anywhere in the orchestrator package
- undocumented internal packages such as `vibrant.orchestrator.interface.*`
- direct access to stores, policy loops, or provider internals

## Construction

```python
from vibrant.orchestrator import OrchestratorFacade, create_orchestrator

orchestrator = create_orchestrator(project_root)
facade = OrchestratorFacade(orchestrator)
```

`OrchestratorFacade` is the stable Python entry point once an orchestrator root
has been created.

## Companion Read Models

### `OrchestratorSnapshot`

`facade.snapshot()` returns `OrchestratorSnapshot`, a consumer-ready read model
with these fields:

- `status`
- `pending_questions`
- `question_records`
- `roadmap`
- `consensus`
- `consensus_path`
- `roles`
- `instances`
- `runs`
- `execution_mode`
- `user_input_banner`

### Read Helper Views

`OrchestratorFacade` exposes three stable helper attributes:

- `facade.roles`
- `facade.instances`
- `facade.runs`

They are convenience read adapters over the same stable data exposed by the
main facade methods.

## Stable Facade API

### Workflow And Snapshot Reads

| API | Notes |
| --- | --- |
| `snapshot()` | Returns `OrchestratorSnapshot`. |
| `get_workflow_status()` | Returns the current `OrchestratorStatus`. |
| `workflow_snapshot()` | Returns the current typed workflow snapshot. |
| `workflow_session()` | Returns the durable workflow-session read model. |
| `gatekeeper_state()` | Returns the current Gatekeeper loop state. |
| `gatekeeper_session()` | Returns the current Gatekeeper session snapshot. |
| `task_loop_state()` | Returns the task-loop read model. |
| `get_execution_mode()` | Returns the configured `RoadmapExecutionMode`. |
| `get_user_input_banner()` | Returns the current user-input banner string. |
| `gatekeeper_busy()` | Returns whether the Gatekeeper is currently busy. |

### Consensus, Roadmap, And Task Reads/Writes

| API | Notes |
| --- | --- |
| `get_consensus_document()` | Returns the current `ConsensusDocument`, if present. |
| `write_consensus_document(document)` | Replaces the consensus document with the provided typed document. |
| `update_consensus(status=None, context=None)` | Applies a semantic consensus update. |
| `get_consensus_source_path()` | Returns the consensus document path, if present. |
| `get_roadmap()` | Returns the current `RoadmapDocument`. |
| `replace_roadmap(tasks, project=None)` | Replaces the roadmap using typed task definitions. |
| `get_task(task_id)` | Returns a task definition by id. |
| `add_task(task, index=None)` | Adds a task definition. |
| `update_task_definition(task_id, ..., max_retries=None)` | Updates selected task-definition fields. |
| `reorder_tasks(ordered_task_ids)` | Reorders roadmap tasks. |
| `get_task_summaries()` | Returns latest task summaries keyed by `task_id`. |
| `get_run_task_ids()` | Returns the run-to-task mapping. |
| `task_id_for_run(run_id)` | Returns the task id for a run, if known. |

### Role, Instance, And Run Reads

| API | Notes |
| --- | --- |
| `list_roles()` | Lists `RoleSnapshot` values. |
| `get_role(role)` | Returns one `RoleSnapshot`, if present. |
| `list_instances(role=None, active_only=False)` | Lists `AgentInstanceSnapshot` values. |
| `get_instance(agent_id)` | Returns one `AgentInstanceSnapshot`, if present. |
| `list_runs(task_id=None, role=None, agent_id=None, include_completed=True, active_only=False)` | Lists `AgentRunSnapshot` values. |
| `list_active_runs()` | Lists currently active runs. |
| `get_run(run_id)` | Returns one `AgentRunSnapshot`, if present. |
| `get_attempt_execution(attempt_id)` | Returns one attempt-execution view, if present. |
| `list_active_attempts()` | Lists active attempt views. |
| `list_attempt_executions(task_id=None, status=None)` | Lists attempt-execution views. |

### Questions And User Decisions

| API | Notes |
| --- | --- |
| `list_question_records()` | Lists all `QuestionView` records. |
| `get_question(question_id)` | Returns one `QuestionView`, if present. |
| `list_pending_question_records()` | Lists currently pending `QuestionView` records. |
| `request_user_decision(text, ..., source_turn_id=None)` | Creates a typed user-decision request. |
| `withdraw_question(question_id, reason=None)` | Withdraws a pending question. |
| `list_pending_questions()` | Returns pending question text strings. |
| `get_current_pending_question()` | Returns the current pending question text, if any. |

### Gatekeeper Submission And Conversation Flows

All user-message submission flows use the same stable pattern:

1. submit input through the facade
2. receive a typed submission receipt
3. optionally wait for completion with `wait_for_gatekeeper_submission(...)`

| API | Notes |
| --- | --- |
| `submit_user_message(text)` | Submits a free-form user message to the Gatekeeper. |
| `answer_user_decision(question_id, answer)` | Answers a specific pending question. |
| `wait_for_gatekeeper_submission(submission)` | Waits for a prior submission to complete. |
| `respond_to_gatekeeper_request(run_id, request_id, result=None, error=None)` | Responds to a typed interactive Gatekeeper request. |
| `submit_gatekeeper_input(text, question_id=None)` | Convenience helper returning `(submission, result)`. |
| `submit_gatekeeper_message(text)` | Convenience helper returning the completed result directly. |
| `interrupt_gatekeeper()` | Interrupts the Gatekeeper if it is busy. |
| `answer_pending_question(answer, question=None)` | Convenience helper for the current pending question. |
| `gatekeeper_conversation_id()` | Returns the Gatekeeper conversation id, if present. |
| `get_conversation(conversation_id)` | Returns the conversation session projection, if present. |
| `conversation(conversation_id)` | Returns the conversation view, if present. |
| `subscribe_conversation(conversation_id, callback, replay=False)` | Subscribes to a conversation stream. |
| `subscribe_runtime_events(callback, agent_id=None, run_id=None, task_id=None, event_types=None)` | Subscribes to runtime events. |
| `list_recent_events(limit=20)` | Returns recent runtime events. |

### Workflow Control

| API | Notes |
| --- | --- |
| `run_next_task()` | Runs one eligible task. |
| `run_until_blocked()` | Runs until workflow progress blocks. |
| `pause_workflow()` | Pauses the workflow. |
| `resume_workflow()` | Resumes the workflow. |
| `end_planning_phase()` | Transitions planning into execution. |
| `set_workflow_status(status)` | Sets the workflow status directly. |
| `can_transition_to(next_status)` | Returns whether a UI transition is currently allowed. |
| `transition_workflow_state(next_status)` | Applies the semantic UI transition plan. |
| `infer_resume_status()` | Infers the resume status from the current facade-visible state. |

### Review Flows

| API | Notes |
| --- | --- |
| `get_review_ticket(ticket_id)` | Returns one review ticket, if present. |
| `list_review_tickets(task_id=None, status=None)` | Lists review tickets. |
| `list_pending_review_tickets()` | Lists pending review tickets. |
| `accept_review_ticket(ticket_id)` | Accepts a review ticket. |
| `retry_review_ticket(ticket_id, failure_reason, prompt_patch=None, acceptance_patch=None)` | Retries a review ticket. |
| `escalate_review_ticket(ticket_id, reason)` | Escalates a review ticket. |

## Stable Read Helper Views

### `facade.roles`

| API | Notes |
| --- | --- |
| `roles.list()` | Equivalent to `facade.list_roles()`. |
| `roles.get(role)` | Equivalent to `facade.get_role(role)`. |

### `facade.instances`

| API | Notes |
| --- | --- |
| `instances.list(role=None, active_only=False)` | Equivalent to `facade.list_instances(...)`. |
| `instances.active(role=None)` | Equivalent to `facade.list_instances(active_only=True, ...)`. |
| `instances.get(agent_id)` | Equivalent to `facade.get_instance(agent_id)`. |

### `facade.runs`

| API | Notes |
| --- | --- |
| `runs.list(task_id=None, role=None, agent_id=None, include_completed=True, active_only=False)` | Equivalent to `facade.list_runs(...)`. |
| `runs.active(task_id=None, role=None, agent_id=None)` | Equivalent to `facade.list_runs(active_only=True, ...)`. |
| `runs.get(run_id)` | Equivalent to `facade.get_run(run_id)`. |
| `runs.for_task(task_id, role=None, include_completed=True)` | Returns runs for one task. |
| `runs.for_instance(agent_id, include_completed=True)` | Returns runs for one instance. |
| `runs.latest_for_task(task_id, role=None)` | Returns the latest run for one task, if present. |

## Stability Rules

The documented facade contract follows these rules:

1. Consumers should depend on the documented facade methods and helper views, not on internal orchestrator objects.
2. Returned read models are stable in meaning. Additive fields are allowed, but removing or changing the meaning of documented data is a breaking change.
3. Submission flows remain typed and explicit. Consumers should not need to infer control outcomes from free-form text.
4. Conversation access remains facade-owned through `conversation(...)` and `subscribe_conversation(...)`.
5. Breaking facade changes require updating this document and providing a migration path for first-party consumers.
