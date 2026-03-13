"""Read-only sub-agent orchestrator MCP tools."""

from __future__ import annotations

from typing import Any

from vibrant.orchestrator.facade import OrchestratorFacade

from .resources import ResourceHandlers, _serialize_value


class AgentToolHandlers:
    """Read-only tools exposed to execution agents."""

    def __init__(self, facade: OrchestratorFacade) -> None:
        self.facade = facade
        self.resources = ResourceHandlers(facade)

    def consensus_get(self) -> dict[str, Any] | None:
        return self.resources.consensus_current()

    def roadmap_get(self) -> dict[str, Any] | None:
        return self.resources.roadmap_current()

    def task_get(self, task_id: str) -> dict[str, Any]:
        return self.resources.task_by_id(task_id)

    def role_get(self, role: str) -> dict[str, Any]:
        snapshot = self.facade.roles.get(role)
        if snapshot is None:
            raise KeyError(f"Unknown role: {role}")
        return _serialize_value(snapshot)

    def role_list(self) -> list[dict[str, Any]]:
        return self.resources.role_list()

    def instance_get(self, agent_id: str) -> dict[str, Any]:
        return self.resources.instance_by_id(agent_id)

    def instance_list(
        self,
        *,
        task_id: str | None = None,
        role: str | None = None,
        include_completed: bool = True,
        active_only: bool = False,
    ) -> list[dict[str, Any]]:
        return [
            _serialize_value(snapshot)
            for snapshot in self.facade.instances.list(
                task_id=task_id,
                role=role,
                include_completed=include_completed,
                active_only=active_only,
            )
        ]

    def run_get(self, run_id: str) -> dict[str, Any]:
        record = self.facade.runs.get(run_id)
        if record is None:
            raise KeyError(f"Unknown run: {run_id}")
        return _serialize_value(record)

    def run_list(
        self,
        *,
        task_id: str | None = None,
        agent_id: str | None = None,
        role: str | None = None,
    ) -> list[dict[str, Any]]:
        return [
            _serialize_value(record)
            for record in self.facade.runs.list(
                task_id=task_id,
                agent_id=agent_id,
                role=role,
            )
        ]

    async def workflow_execute_next_task(self) -> dict[str, Any] | None:
        result = await self.facade.execute_next_task()
        return _serialize_value(result)

    async def instance_wait(self, agent_id: str, *, release_terminal: bool = True) -> dict[str, Any]:
        result = await self.facade.instances.wait(agent_id, release_terminal=release_terminal)
        return _serialize_value(result)

    async def instance_respond_to_request(
        self,
        agent_id: str,
        request_id: int | str,
        *,
        result: Any | None = None,
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        snapshot = await self.facade.instances.respond_to_request(
            agent_id,
            request_id,
            result=result,
            error=error,
        )
        return _serialize_value(snapshot)
