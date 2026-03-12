# Orchestrator MCP Surface

This document describes the orchestrator MCP surface implemented under
`vibrant/orchestrator/mcp/`.

## Implementation layout

- `server.py` defines the typed in-process registry of resources and tools
- `resources.py` implements read-only resource handlers
- `tools_agents.py` implements read-oriented agent tools
- `tools_gatekeeper.py` implements write and workflow tools
- `fastmcp.py` exposes the registry through FastMCP and protects HTTP transport
  with a bearer token

## Auth model

The current transport model is intentionally simple:

- HTTP transport requires `Authorization: Bearer <token>`
- the expected token value is read from the environment variable named by
  `MCPServerSettings.bearer_token_env_var`
- once the request is authenticated, Vibrant exposes the full MCP surface
- Vibrant does not perform local scope-based filtering of tools or resources
- Codex is responsible for deciding which tools an agent may use

## Implemented resources

Current stable resource names are:

- `agent.status`
- `consensus.current`
- `events.recent`
- `questions.pending`
- `roadmap.current`
- `task.assigned`
- `task.by_id`
- `workflow.status`

Resource URI templates exposed through FastMCP are:

- `vibrant://agent/{agent_id}/status`
- `vibrant://consensus/current`
- `vibrant://events/recent/{task_id}{?limit}`
- `vibrant://questions/pending`
- `vibrant://roadmap/current`
- `vibrant://task/{task_id}`
- `vibrant://task/{task_id}/assigned`
- `vibrant://workflow/status`

## Implemented tools

Agent and execution tools:

- `agent_get`
- `agent_list`
- `agent_result_get`
- `agent_respond_to_request`
- `agent_wait`
- `consensus_get`
- `roadmap_get`
- `task_get`
- `workflow_execute_next_task`

Gatekeeper and control-plane tools:

- `consensus_update`
- `question_ask_user`
- `question_resolve`
- `roadmap_add_task`
- `roadmap_reorder_tasks`
- `roadmap_update_task`
- `workflow_pause`
- `workflow_resume`
- `vibrant.end_planning_phase`
- `vibrant.request_user_decision`
- `vibrant.set_pending_questions`
- `vibrant.review_task_outcome`
- `vibrant.mark_task_for_retry`
- `vibrant.update_consensus`
- `vibrant.update_roadmap`

## Notable tool signatures

The FastMCP adapter mirrors the current handler signatures directly.

Examples:

- `roadmap_update_task(task_id, *, title=None, acceptance_criteria=None, status=None, branch=None, retry_count=None, max_retries=None, prompt=None, skills=None, dependencies=None, priority=None, failure_reason=None)`
- `vibrant.update_roadmap(*, tasks, project=None)`
- `vibrant.mark_task_for_retry(task_id, failure_reason, *, prompt=None, acceptance_criteria=None)`

## FastMCP helpers

- `create_orchestrator_fastmcp(...)` registers all resources and tools with a
  FastMCP server instance
- `create_orchestrator_fastmcp_app(..., settings=...)` returns an HTTP ASGI app
  that wraps the FastMCP server with bearer-token validation

For stdio transport, use `create_orchestrator_fastmcp(...)` directly.

For HTTP transport, use `MCPServerSettings` from `vibrant.mcp` and start the
server with the token environment variable already set.
