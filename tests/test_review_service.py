from __future__ import annotations

from types import SimpleNamespace

from vibrant.agents.role_results import GatekeeperRoleResult
from vibrant.models.task import TaskInfo, TaskStatus
from vibrant.orchestrator.tasks.review import ReviewService


class _RoadmapServiceStub:
    def __init__(self, task: TaskInfo | None) -> None:
        self._task = task
        self.dispatcher = None

    def get_task(self, task_id: str) -> TaskInfo | None:
        if self._task is None or self._task.id != task_id:
            return None
        return self._task


def _build_review_service(task: TaskInfo | None) -> ReviewService:
    return ReviewService(
        gatekeeper=SimpleNamespace(),
        state_store=SimpleNamespace(state=SimpleNamespace(concurrency_limit=1)),
        roadmap_service=_RoadmapServiceStub(task),
        git_service=SimpleNamespace(),
        task_store=SimpleNamespace(),
    )


def test_resolve_decision_prefers_durable_task_state() -> None:
    service = _build_review_service(
        TaskInfo(
            id="task-001",
            title="Example",
            acceptance_criteria=[],
            status=TaskStatus.ACCEPTED,
        )
    )
    result = SimpleNamespace(
        awaiting_input=False,
        input_requests=[],
        error=None,
        role_result=GatekeeperRoleResult(suggested_decision="rejected"),
    )

    assert service.resolve_decision(result, task_id="task-001") == "accepted"


def test_resolve_decision_uses_suggested_decision_as_fallback() -> None:
    service = _build_review_service(
        TaskInfo(
            id="task-001",
            title="Example",
            acceptance_criteria=[],
            status=TaskStatus.COMPLETED,
        )
    )
    result = SimpleNamespace(
        awaiting_input=False,
        input_requests=[],
        error=None,
        role_result=GatekeeperRoleResult(suggested_decision="retry"),
    )

    assert service.resolve_decision(result, task_id="task-001") == "retry"
