"""Unit tests for the Phase 1 task dispatch engine."""

from __future__ import annotations

from vibrant.consensus import RoadmapParser
from vibrant.models.task import TaskInfo, TaskStatus
from vibrant.orchestrator.artifacts.roadmap import RoadmapService
from vibrant.orchestrator.tasks.dispatcher import TaskDispatcher


class TestTaskDispatcher:
    def test_tasks_dispatched_with_priority_and_dependency_order(self):
        dispatcher = TaskDispatcher(concurrency_limit=1)
        task_a = TaskInfo(id="task-a", title="Foundation", priority=10)
        task_b = TaskInfo(id="task-b", title="Dependent", priority=0, dependencies=["task-a"])
        task_c = TaskInfo(id="task-c", title="Independent", priority=1)

        dispatcher.add_task(task_a)
        dispatcher.add_task(task_b)
        dispatcher.add_task(task_c)

        assert dispatcher.queued_task_ids == ["task-c", "task-a"]
        assert dispatcher.get_task("task-b").status is TaskStatus.PENDING

        first_batch = dispatcher.dispatch_ready_tasks()
        assert [task.id for task in first_batch] == ["task-c"]
        assert dispatcher.active_task_ids == ["task-c"]

        dispatcher.mark_completed("task-c")
        second_batch = dispatcher.dispatch_ready_tasks()
        assert [task.id for task in second_batch] == ["task-a"]

        dispatcher.mark_completed("task-a")
        assert dispatcher.queued_task_ids == ["task-b"]

        third_batch = dispatcher.dispatch_ready_tasks()
        assert [task.id for task in third_batch] == ["task-b"]

    def test_concurrency_limit_is_respected(self):
        dispatcher = TaskDispatcher(concurrency_limit=2)
        for index in range(4):
            dispatcher.add_task(TaskInfo(id=f"task-{index}", title=f"Task {index}"))

        first_batch = dispatcher.dispatch_ready_tasks()
        assert [task.id for task in first_batch] == ["task-0", "task-1"]
        assert dispatcher.active_count == 2

        second_batch = dispatcher.dispatch_ready_tasks()
        assert second_batch == []
        assert dispatcher.active_count == 2

        dispatcher.mark_completed("task-0")
        third_batch = dispatcher.dispatch_ready_tasks()
        assert [task.id for task in third_batch] == ["task-2"]
        assert dispatcher.active_count == 2

    def test_failed_task_retried_up_to_max_retries_then_escalated(self):
        dispatcher = TaskDispatcher(concurrency_limit=1)
        task = TaskInfo(id="task-retry", title="Retry me", max_retries=2)
        dispatcher.add_task(task)

        assert task.status is TaskStatus.QUEUED

        dispatcher.dispatch_ready_tasks()
        dispatcher.fail_task("task-retry", failure_reason="boom-1")
        assert task.status is TaskStatus.QUEUED
        assert task.retry_count == 1
        assert task.failure_reason is None
        assert dispatcher.queued_task_ids == ["task-retry"]

        dispatcher.dispatch_ready_tasks()
        dispatcher.fail_task("task-retry", failure_reason="boom-2")
        assert task.status is TaskStatus.QUEUED
        assert task.retry_count == 2
        assert dispatcher.queued_task_ids == ["task-retry"]

        dispatcher.dispatch_ready_tasks()
        dispatcher.fail_task("task-retry", failure_reason="boom-3")
        assert task.status is TaskStatus.ESCALATED
        assert task.retry_count == 2
        assert task.failure_reason == "boom-3"
        assert dispatcher.queued_task_ids == []

    def test_status_transitions_follow_dispatch_lifecycle(self):
        dispatcher = TaskDispatcher(concurrency_limit=1)
        task = TaskInfo(id="task-accept", title="Accept me")

        dispatcher.add_task(task)
        assert task.status is TaskStatus.QUEUED

        dispatched = dispatcher.dispatch_ready_tasks()
        assert [item.id for item in dispatched] == ["task-accept"]
        assert task.status is TaskStatus.IN_PROGRESS

        dispatcher.mark_completed("task-accept")
        assert task.status is TaskStatus.COMPLETED

        dispatcher.accept_task("task-accept")
        assert task.status is TaskStatus.ACCEPTED


class TestRoadmapDispatcherSync:
    def test_add_task_updates_live_dispatcher(self, tmp_path):
        service = RoadmapService(tmp_path / "roadmap.md", parser=RoadmapParser())

        task = TaskInfo(id="task-added", title="Added later")
        service.add_task(task)

        assert service.dispatcher is not None
        assert service.dispatcher.get_task("task-added") is task
        assert service.dispatcher.queued_task_ids == ["task-added"]

    def test_update_task_updates_live_dispatcher_metadata(self, tmp_path):
        roadmap_path = tmp_path / "roadmap.md"
        service = RoadmapService(roadmap_path, parser=RoadmapParser())
        service.add_task(TaskInfo(id="task-a", title="Task A", priority=10))
        service.add_task(TaskInfo(id="task-b", title="Task B", priority=20))

        updated = service.update_task("task-a", priority=-1, title="Task A updated")

        assert service.dispatcher is not None
        queued = service.dispatcher.queued_task_ids
        assert queued == ["task-a", "task-b"]
        dispatcher_task = service.dispatcher.get_task("task-a")
        assert dispatcher_task is updated
        assert dispatcher_task.title == "Task A updated"
        assert dispatcher_task.priority == -1

    def test_reorder_tasks_updates_live_dispatcher_order(self, tmp_path):
        service = RoadmapService(tmp_path / "roadmap.md", parser=RoadmapParser())
        service.add_task(TaskInfo(id="task-a", title="Task A"))
        service.add_task(TaskInfo(id="task-b", title="Task B"))
        service.add_task(TaskInfo(id="task-c", title="Task C"))

        service.reorder_tasks(["task-c", "task-a", "task-b"])

        assert service.dispatcher is not None
        assert service.dispatcher.queued_task_ids == ["task-c", "task-a", "task-b"]
