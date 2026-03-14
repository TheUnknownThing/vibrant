from __future__ import annotations

from pathlib import Path

from vibrant.models.task import TaskInfo
from vibrant.orchestrator import OrchestratorFacade, create_orchestrator
from vibrant.project_init import initialize_project


def _prepare_project(tmp_path: Path):
    initialize_project(tmp_path)
    return create_orchestrator(tmp_path)


def test_create_orchestrator_bootstraps_redesigned_services(tmp_path: Path) -> None:
    orchestrator = _prepare_project(tmp_path)

    assert orchestrator.workflow_state_store.load().workflow_status.value == "init"
    assert orchestrator.mcp_server is not None
    assert orchestrator.mcp_host is not None
    assert orchestrator.binding_service is not None
    assert orchestrator.conversation_store.base_dir.exists()
    assert orchestrator.attempt_store.list_active() == []


def test_facade_manages_tasks_questions_and_status_projection(tmp_path: Path) -> None:
    orchestrator = _prepare_project(tmp_path)
    facade = OrchestratorFacade(orchestrator)

    facade.add_task(TaskInfo(id="task-1", title="Add redesign shell"), index=0)
    facade.request_user_decision("Pick the first subsystem to implement")
    status = facade.end_planning_phase()

    snapshot = facade.snapshot()

    assert status.value == "executing"
    assert snapshot.status.value == "executing"
    assert snapshot.pending_questions == ("Pick the first subsystem to implement",)
    assert facade.get_task("task-1") is not None
    assert facade.get_consensus_document() is not None


def test_ui_surface_excludes_mcp_only_compatibility_aliases(tmp_path: Path) -> None:
    orchestrator = _prepare_project(tmp_path)
    facade = OrchestratorFacade(orchestrator)

    assert not hasattr(orchestrator, "set_pending_questions")
    assert not hasattr(orchestrator, "review_task_outcome")
    assert not hasattr(orchestrator, "mark_task_for_retry")
    assert not hasattr(orchestrator.control_plane, "set_pending_questions")
    assert not hasattr(orchestrator.control_plane, "review_task_outcome")
    assert not hasattr(orchestrator.control_plane, "mark_task_for_retry")
    assert not hasattr(facade, "set_pending_questions")
    assert not hasattr(facade, "resolve_question")
    assert not hasattr(facade, "update_task")
    assert not hasattr(facade, "review_task_outcome")
    assert not hasattr(facade, "mark_task_for_retry")


def test_workflow_state_commands_are_sync(tmp_path: Path) -> None:
    orchestrator = _prepare_project(tmp_path)

    started = orchestrator.start_execution()
    paused = orchestrator.pause_workflow()
    resumed = orchestrator.resume_workflow()

    assert started.status.value == "executing"
    assert paused.status.value == "paused"
    assert resumed.status.value == "executing"
