from __future__ import annotations

from pathlib import Path

from vibrant.models.task import TaskInfo
from vibrant.orchestrator import create_orchestrator
from vibrant.orchestrator.types import QuestionPriority, WorkflowStatus
from vibrant.project_init import initialize_project


def test_workflow_policy_selects_ready_tasks_and_blocks_on_questions(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    orchestrator = create_orchestrator(tmp_path)

    orchestrator.roadmap_store.add_task(TaskInfo(id="task-1", title="First"), index=0)
    orchestrator.roadmap_store.add_task(TaskInfo(id="task-2", title="Second", dependencies=["task-1"]), index=1)
    orchestrator.workflow_state_store.update_workflow_status(WorkflowStatus.EXECUTING)

    leases = orchestrator.task_loop.select_next(limit=5)

    assert [lease.task_id for lease in leases] == ["task-1"]

    orchestrator.question_store.create(
        text="Need input",
        priority=QuestionPriority.BLOCKING,
        source_role="gatekeeper",
        source_agent_id=None,
        source_conversation_id=None,
        source_turn_id=None,
        blocking_scope="planning",
        task_id=None,
    )

    assert orchestrator.task_loop.select_next(limit=5) == []


def test_workflow_policy_ignores_normal_questions_for_task_dispatch(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    orchestrator = create_orchestrator(tmp_path)

    orchestrator.roadmap_store.add_task(TaskInfo(id="task-1", title="First"), index=0)
    orchestrator.workflow_state_store.update_workflow_status(WorkflowStatus.EXECUTING)

    orchestrator.question_store.create(
        text="Advisory question",
        priority=QuestionPriority.NORMAL,
        source_role="gatekeeper",
        source_agent_id=None,
        source_conversation_id=None,
        source_turn_id=None,
        blocking_scope="workflow",
        task_id=None,
    )

    leases = orchestrator.task_loop.select_next(limit=5)

    assert [lease.task_id for lease in leases] == ["task-1"]


def test_workflow_policy_does_not_globally_block_on_task_scoped_questions(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    orchestrator = create_orchestrator(tmp_path)

    orchestrator.roadmap_store.add_task(TaskInfo(id="task-1", title="First"), index=0)
    orchestrator.workflow_state_store.update_workflow_status(WorkflowStatus.EXECUTING)

    orchestrator.question_store.create(
        text="Question about a specific task",
        priority=QuestionPriority.BLOCKING,
        source_role="gatekeeper",
        source_agent_id=None,
        source_conversation_id=None,
        source_turn_id=None,
        blocking_scope="task",
        task_id="task-1",
    )

    leases = orchestrator.task_loop.select_next(limit=5)

    assert [lease.task_id for lease in leases] == ["task-1"]
