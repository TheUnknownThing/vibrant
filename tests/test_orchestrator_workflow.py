from __future__ import annotations

from pathlib import Path

from vibrant.models.task import TaskInfo, TaskStatus
from vibrant.orchestrator import create_orchestrator
from vibrant.orchestrator.policy.task_loop.models import TaskLoopStage
from vibrant.orchestrator.types import AttemptStatus
from vibrant.orchestrator.types import QuestionPriority, WorkflowStatus
from vibrant.project_init import initialize_project


def test_workflow_policy_selects_ready_tasks_and_blocks_on_questions(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    orchestrator = create_orchestrator(tmp_path)

    orchestrator._roadmap_store.add_task(TaskInfo(id="task-1", title="First"), index=0)
    orchestrator._roadmap_store.add_task(TaskInfo(id="task-2", title="Second", dependencies=["task-1"]), index=1)
    orchestrator._workflow_state_store.update_workflow_status(WorkflowStatus.EXECUTING)

    leases = orchestrator._task_loop.select_next(limit=5)

    assert [lease.task_id for lease in leases] == ["task-1"]

    orchestrator._question_store.create(
        text="Need input",
        priority=QuestionPriority.BLOCKING,
        source_role="gatekeeper",
        source_agent_id=None,
        source_conversation_id=None,
        source_turn_id=None,
        blocking_scope="planning",
        task_id=None,
    )

    assert orchestrator._task_loop.select_next(limit=5) == []


def test_workflow_policy_ignores_normal_questions_for_task_dispatch(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    orchestrator = create_orchestrator(tmp_path)

    orchestrator._roadmap_store.add_task(TaskInfo(id="task-1", title="First"), index=0)
    orchestrator._workflow_state_store.update_workflow_status(WorkflowStatus.EXECUTING)

    orchestrator._question_store.create(
        text="Advisory question",
        priority=QuestionPriority.NORMAL,
        source_role="gatekeeper",
        source_agent_id=None,
        source_conversation_id=None,
        source_turn_id=None,
        blocking_scope="workflow",
        task_id=None,
    )

    leases = orchestrator._task_loop.select_next(limit=5)

    assert [lease.task_id for lease in leases] == ["task-1"]


def test_workflow_policy_does_not_globally_block_on_task_scoped_questions(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    orchestrator = create_orchestrator(tmp_path)

    orchestrator._roadmap_store.add_task(TaskInfo(id="task-1", title="First"), index=0)
    orchestrator._workflow_state_store.update_workflow_status(WorkflowStatus.EXECUTING)

    orchestrator._question_store.create(
        text="Question about a specific task",
        priority=QuestionPriority.BLOCKING,
        source_role="gatekeeper",
        source_agent_id=None,
        source_conversation_id=None,
        source_turn_id=None,
        blocking_scope="task",
        task_id="task-1",
    )

    leases = orchestrator._task_loop.select_next(limit=5)

    assert [lease.task_id for lease in leases] == ["task-1"]


def test_restart_failed_task_requeues_it_for_dispatch(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    orchestrator = create_orchestrator(tmp_path)

    orchestrator._roadmap_store.add_task(
        TaskInfo(
            id="task-1",
            title="First",
            status=TaskStatus.FAILED,
            retry_count=0,
            max_retries=3,
            failure_reason="boom",
        ),
        index=0,
    )
    attempt = orchestrator._attempt_store.create(
        task_id="task-1",
        task_definition_version=1,
        workspace_id="workspace-1",
        status=AttemptStatus.FAILED,
    )
    orchestrator._roadmap_store.replace_task(
        TaskInfo(
            id="task-1",
            title="First",
            status=TaskStatus.FAILED,
            retry_count=0,
            max_retries=3,
            failure_reason="boom",
        ),
        active_attempt_id=attempt.attempt_id,
    )
    orchestrator._workflow_state_store.update_workflow_status(WorkflowStatus.EXECUTING)
    orchestrator._task_loop._set_snapshot(
        stage=TaskLoopStage.BLOCKED,
        active_lease=None,
        active_attempt_id=attempt.attempt_id,
        blocking_reason="boom",
    )

    restarted = orchestrator._task_loop.restart_failed_task("task-1")
    leases = orchestrator._task_loop.select_next(limit=1)

    assert restarted.status is TaskStatus.QUEUED
    assert restarted.retry_count == 1
    assert restarted.failure_reason is None
    assert orchestrator._roadmap_store.active_attempt_id("task-1") is None
    assert orchestrator._task_loop.snapshot().stage is TaskLoopStage.IDLE
    assert orchestrator._task_loop.snapshot().blocking_reason is None
    assert [lease.task_id for lease in leases] == ["task-1"]
