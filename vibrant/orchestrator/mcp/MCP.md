# Orchestrator MCP Surface

This document lists the orchestrator MCP surface currently implemented under `vibrant/orchestrator/mcp/`.

The implementation has three main layers:

- `server.py` defines the typed registry of MCP resources and tools.
- `resources.py` implements read-only resource handlers.
- `tools_agents.py` and `tools_gatekeeper.py` implement the callable tool handlers.
- `fastmcp.py` exposes the registry through FastMCP resource URIs and tool names.

## Summary

- Resources implemented: `8`
- Tools implemented: `24`
- FastMCP resource scheme: `vibrant://...`
- Access control model: every resource and tool requires `mcp:access` plus one or more task/orchestrator scopes.

## Default Scope Bundles

The authorization helpers in `vibrant/mcp/authz.py` define these default orchestrator bundles:

- `agent`: `mcp:access`, `tasks:read`, `orchestrator:consensus:read`
- `gatekeeper`: `mcp:access`, `tasks:read`, `tasks:write`, `orchestrator:consensus:read`, `orchestrator:consensus:write`, `orchestrator:questions:read`, `orchestrator:questions:write`, `orchestrator:workflow:read`, `orchestrator:workflow:write`

## Implemented Resources

### 1. `consensus.current`

- FastMCP URI: `vibrant://consensus/current`
- Handler: `ResourceHandlers.consensus_current`
- Required scopes: `mcp:access`, `orchestrator:consensus:read`
- Purpose: returns the current consensus document, or `null` if none exists.
- Parameters: none

### 2. `roadmap.current`

- FastMCP URI: `vibrant://roadmap/current`
- Handler: `ResourceHandlers.roadmap_current`
- Required scopes: `mcp:access`, `tasks:read`
- Purpose: returns the current roadmap document with `project` and serialized `tasks`, or `null` if none exists.
- Parameters: none

### 3. `workflow.status`

- FastMCP URI: `vibrant://workflow/status`
- Handler: `ResourceHandlers.workflow_status`
- Required scopes: `mcp:access`, `orchestrator:workflow:read`
- Purpose: returns the current orchestrator workflow status as `{"status": ...}`.
- Parameters: none

### 4. `questions.pending`

- FastMCP URI: `vibrant://questions/pending`
- Handler: `ResourceHandlers.questions_pending`
- Required scopes: `mcp:access`, `orchestrator:questions:read`
- Purpose: returns all unresolved user-facing question records.
- Parameters: none

### 5. `task.by_id`

- FastMCP URI: `vibrant://task/{task_id}`
- Handler: `ResourceHandlers.task_by_id`
- Required scopes: `mcp:access`, `tasks:read`
- Purpose: returns one roadmap task by id.
- Parameters:
  - `task_id: str`

### 6. `task.assigned`

- FastMCP URI: `vibrant://task/{task_id}/assigned`
- Handler: `ResourceHandlers.task_assigned`
- Required scopes: `mcp:access`, `tasks:read`
- Purpose: returns the task, all related agents, and the latest agent snapshot.
- Parameters:
  - Registry handler supports `task_id: str | None` or `agent_id: str | None`
  - FastMCP URI currently exposes the `task_id` form

### 7. `agent.status`

- FastMCP URI: `vibrant://agent/{agent_id}/status`
- Handler: `ResourceHandlers.agent_status`
- Required scopes: `mcp:access`, `tasks:read`
- Purpose: returns one agent snapshot, or a list of agent snapshots when used directly through the registry.
- Parameters:
  - Registry handler supports `agent_id: str | None`, `task_id: str | None`, `include_completed: bool = True`, `active_only: bool = False`
  - FastMCP URI currently exposes the `agent_id` form

### 8. `events.recent`

- FastMCP URI: `vibrant://events/recent/{task_id}`
- Handler: `ResourceHandlers.events_recent`
- Required scopes: `mcp:access`, `tasks:read`
- Purpose: returns recent canonical orchestrator events from the engine/state store.
- Parameters:
  - Registry handler supports `agent_id: str | None`, `task_id: str | None`, `limit: int = 20`
  - FastMCP URI currently exposes `task_id` and optional `limit`
- Notes:
  - `limit < 0` raises `ValueError`
  - non-dict events are ignored

## Implemented Tools

The tool surface is split between read-oriented agent tools and write/control-oriented Gatekeeper tools.

### Agent / execution tools

#### 1. `agent_get`

- Handler: `AgentToolHandlers.agent_get`
- Required scopes: `mcp:access`, `tasks:read`
- Purpose: reads a single agent snapshot.
- Parameters:
  - `agent_id: str`

#### 2. `agent_list`

- Handler: `AgentToolHandlers.agent_list`
- Required scopes: `mcp:access`, `tasks:read`
- Purpose: lists agent snapshots, optionally filtered by task or activity state.
- Parameters:
  - `task_id: str | None = None`
  - `include_completed: bool = True`
  - `active_only: bool = False`

#### 3. `agent_result_get`

- Handler: `AgentToolHandlers.agent_result_get`
- Required scopes: `mcp:access`, `tasks:read`
- Purpose: returns the latest known result payload for one agent.
- Response fields include:
  - `agent_id`
  - `task_id`
  - `agent_type`
  - `status`
  - `done`
  - `awaiting_input`
  - `summary`
  - `error`
  - `output`
  - `provider`
- Parameters:
  - `agent_id: str`

#### 4. `agent_respond_to_request`

- Handler: `AgentToolHandlers.agent_respond_to_request`
- Required scopes: `mcp:access`, `tasks:run`
- Purpose: answers a pending provider request for an already running agent.
- Parameters:
  - `agent_id: str`
  - `request_id: int | str`
  - `result: Any | None = None`
  - `error: dict[str, Any] | None = None`
- Notes:
  - raises `AttributeError` if the orchestrator does not expose `respond_to_request`

#### 5. `agent_wait`

- Handler: `AgentToolHandlers.agent_wait`
- Required scopes: `mcp:access`, `tasks:read`
- Purpose: waits for an existing agent to reach a result state.
- Parameters:
  - `agent_id: str`
  - `release_terminal: bool = True`
- Notes:
  - raises `AttributeError` if the orchestrator does not expose `wait_for_agent`

#### 6. `task_get`

- Handler: `AgentToolHandlers.task_get`
- Required scopes: `mcp:access`, `tasks:read`
- Purpose: reads one roadmap task by id.
- Parameters:
  - `task_id: str`

#### 7. `workflow_execute_next_task`

- Handler: `AgentToolHandlers.workflow_execute_next_task`
- Required scopes: `mcp:access`, `tasks:run`
- Purpose: dispatches and executes the next roadmap task according to workflow rules.
- Parameters: none

#### 8. `consensus_get`

- Handler: available through both `AgentToolHandlers.consensus_get` and `GatekeeperToolHandlers.consensus_get`
- Registered handler: `GatekeeperToolHandlers.consensus_get`
- Required scopes: `mcp:access`, `orchestrator:consensus:read`
- Purpose: reads the current consensus document.
- Parameters: none

#### 9. `roadmap_get`

- Handler: available through both `AgentToolHandlers.roadmap_get` and `GatekeeperToolHandlers.roadmap_get`
- Registered handler: `GatekeeperToolHandlers.roadmap_get`
- Required scopes: `mcp:access`, `tasks:read`
- Purpose: reads the current roadmap document.
- Parameters: none

### Gatekeeper / control-plane tools

#### 10. `consensus_update`

- Handler: `GatekeeperToolHandlers.consensus_update`
- Required scopes: `mcp:access`, `orchestrator:consensus:write`
- Purpose: updates orchestrator-owned consensus fields.
- Parameters:
  - `status: str | None = None`
  - `objectives: str | None = None`
  - `getting_started: str | None = None`
  - `questions: Sequence[str] | None = None`

#### 11. `question_ask_user`

- Handler: `GatekeeperToolHandlers.question_ask_user`
- Required scopes: `mcp:access`, `orchestrator:questions:write`
- Purpose: creates a structured user-facing question record.
- Parameters:
  - `text: str`
  - `source_agent_id: str | None = None`
  - `priority: str = "blocking"`
- Notes:
  - `source_role` is fixed internally to `"gatekeeper"`

#### 12. `question_resolve`

- Handler: `GatekeeperToolHandlers.question_resolve`
- Required scopes: `mcp:access`, `orchestrator:questions:write`
- Purpose: resolves an existing question record.
- Parameters:
  - `question_id: str`
  - `answer: str | None = None`

#### 13. `roadmap_add_task`

- Handler: `GatekeeperToolHandlers.roadmap_add_task`
- Required scopes: `mcp:access`, `tasks:write`
- Purpose: validates and inserts a task into the roadmap.
- Parameters:
  - `task: dict[str, Any]`
  - `index: int | None = None`
- Notes:
  - task input is validated via `TaskInfo.model_validate(...)`

#### 14. `roadmap_reorder_tasks`

- Handler: `GatekeeperToolHandlers.roadmap_reorder_tasks`
- Required scopes: `mcp:access`, `tasks:write`
- Purpose: reorders roadmap tasks by id.
- Parameters:
  - `ordered_task_ids: list[str]`

#### 15. `roadmap_update_task`

- Handler: `GatekeeperToolHandlers.roadmap_update_task`
- Required scopes: `mcp:access`, `tasks:write`
- Purpose: updates a roadmap task definition.
- Parameters:
  - `task_id: str`
  - `updates: dict[str, Any]`

#### 16. `workflow_pause`

- Handler: `GatekeeperToolHandlers.workflow_pause`
- Required scopes: `mcp:access`, `orchestrator:workflow:write`
- Purpose: pauses the orchestrator workflow.
- Parameters: none

#### 17. `workflow_resume`

- Handler: `GatekeeperToolHandlers.workflow_resume`
- Required scopes: `mcp:access`, `orchestrator:workflow:write`
- Purpose: resumes the orchestrator workflow.
- Parameters: none

#### 18. `vibrant.end_planning_phase`

- Handler: `GatekeeperToolHandlers.end_planning_phase`
- Required scopes: `mcp:access`, `orchestrator:workflow:write`
- Purpose: transitions the orchestrator from planning into execution.
- Parameters: none

#### 19. `vibrant.request_user_decision`

- Handler: `GatekeeperToolHandlers.request_user_decision`
- Required scopes: `mcp:access`, `orchestrator:questions:write`
- Purpose: creates one user-facing decision request for the Gatekeeper.
- Parameters:
  - `question: str`
  - `source_agent_id: str | None = None`
  - `priority: str = "blocking"`

#### 20. `vibrant.set_pending_questions`

- Handler: `GatekeeperToolHandlers.set_pending_questions`
- Required scopes: `mcp:access`, `orchestrator:questions:write`
- Purpose: replaces the pending Gatekeeper question set.
- Parameters:
  - `questions: Sequence[str]`
  - `source_agent_id: str | None = None`
- Notes:
  - `source_role` is fixed internally to `"gatekeeper"`

#### 21. `vibrant.review_task_outcome`

- Handler: `GatekeeperToolHandlers.review_task_outcome`
- Required scopes: `mcp:access`, `tasks:write`
- Purpose: records the Gatekeeper verdict for a task outcome.
- Parameters:
  - `task_id: str`
  - `decision: str`
  - `failure_reason: str | None = None`

#### 22. `vibrant.mark_task_for_retry`

- Handler: `GatekeeperToolHandlers.mark_task_for_retry`
- Required scopes: `mcp:access`, `tasks:write`
- Purpose: updates a task for retry and requeues or escalates it.
- Parameters:
  - `task_id: str`
  - `failure_reason: str`
  - `prompt: str | None = None`
  - `acceptance_criteria: Sequence[str] | None = None`

#### 23. `vibrant.update_consensus`

- Handler: `GatekeeperToolHandlers.update_consensus`
- Required scopes: `mcp:access`, `orchestrator:consensus:write`
- Purpose: alias-style control-plane entry point for consensus updates.
- Parameters:
  - `status: str | None = None`
  - `objectives: str | None = None`
  - `getting_started: str | None = None`
  - `questions: Sequence[str] | None = None`
- Notes:
  - delegates to `consensus_update(...)`

#### 24. `vibrant.update_roadmap`

- Handler: `GatekeeperToolHandlers.update_roadmap`
- Required scopes: `mcp:access`, `tasks:write`
- Purpose: replaces the roadmap document with a validated task list.
- Parameters:
  - `tasks: Sequence[dict[str, Any]]`
  - `project: str | None = None`

## FastMCP Exposure Notes

- `create_orchestrator_fastmcp(...)` registers all resources and tools with FastMCP.
- `create_orchestrator_fastmcp_app(...)` exposes the server as an HTTP app with default MCP path `/mcp`.
- If auth is configured, FastMCP applies scope checks per resource/tool using the registry definitions.
- If auth is not configured, a trusted local principal is synthesized with all scopes required by every registered resource and tool.

## Embedded OAuth Endpoints

When using `EmbeddedOAuthProvider` in `fastmcp.py`, the MCP app also exposes embedded OAuth endpoints in addition to the MCP server itself:

- metadata endpoint: `service.settings.metadata_endpoint`
- JWKS endpoint: `service.settings.jwks_endpoint`
- authorization endpoint: `service.settings.authorization_endpoint`
- token endpoint: `service.settings.token_endpoint`

These endpoints are transport/auth plumbing for the MCP server, not orchestrator resources or tools themselves.
