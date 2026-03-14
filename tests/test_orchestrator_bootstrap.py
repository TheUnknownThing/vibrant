from __future__ import annotations

from pathlib import Path

from vibrant.models.agent import AgentRecord, AgentStatus, AgentType
from vibrant.models.task import TaskInfo
from vibrant.orchestrator import OrchestratorFacade, create_orchestrator
from vibrant.orchestrator.types import WorkflowStatus
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

    assert not hasattr(orchestrator, "workflow_policy")
    assert not hasattr(orchestrator, "review_control")
    assert not hasattr(orchestrator, "set_pending_questions")
    assert not hasattr(orchestrator, "review_task_outcome")
    assert not hasattr(orchestrator, "mark_task_for_retry")
    assert not hasattr(orchestrator.control_plane, "set_pending_questions")
    assert not hasattr(orchestrator.control_plane, "review_task_outcome")
    assert not hasattr(orchestrator.control_plane, "mark_task_for_retry")
    assert not hasattr(orchestrator.control_plane, "list_agent_records")
    assert not hasattr(orchestrator.control_plane, "list_active_agents")
    assert not hasattr(orchestrator.control_plane, "get_agent_record")
    assert not hasattr(facade, "set_pending_questions")
    assert not hasattr(facade, "resolve_question")
    assert not hasattr(facade, "update_task")
    assert not hasattr(facade, "review_task_outcome")
    assert not hasattr(facade, "mark_task_for_retry")
    assert not hasattr(facade, "list_agent_records")
    assert not hasattr(facade, "list_active_agents")
    assert not hasattr(facade, "get_agent")


def test_facade_snapshot_exposes_role_instance_and_run_surfaces(tmp_path: Path) -> None:
    orchestrator = _prepare_project(tmp_path)
    facade = OrchestratorFacade(orchestrator)

    snapshot = facade.snapshot()

    assert {role.role for role in snapshot.roles} >= {"gatekeeper", "code"}
    assert snapshot.instances == tuple(facade.list_instances())
    assert snapshot.runs == tuple(facade.list_runs())
    assert hasattr(facade, "roles")
    assert hasattr(facade, "instances")
    assert hasattr(facade, "runs")


def test_facade_derives_task_run_queries_from_attempts(tmp_path: Path) -> None:
    orchestrator = _prepare_project(tmp_path)
    facade = OrchestratorFacade(orchestrator)

    orchestrator.agent_run_store.upsert(
        AgentRecord(
            identity={
                "run_id": "run-task-1",
                "agent_id": "agent-task-1",
                "role": AgentType.CODE.value,
                "type": AgentType.CODE,
            },
            lifecycle={"status": AgentStatus.COMPLETED},
            outcome={"summary": "Finished task 1"},
        )
    )
    orchestrator.agent_run_store.upsert(
        AgentRecord(
            identity={
                "run_id": "run-task-2",
                "agent_id": "agent-task-2",
                "role": AgentType.CODE.value,
                "type": AgentType.CODE,
            },
            lifecycle={"status": AgentStatus.COMPLETED},
            outcome={"summary": "Finished task 2"},
        )
    )
    orchestrator.attempt_store.create(
        task_id="task-1",
        workspace_id="workspace-1",
        task_definition_version=1,
        code_run_id="run-task-1",
    )

    runs = facade.list_runs(task_id="task-1")

    assert [run.identity.run_id for run in runs] == ["run-task-1"]
    assert facade.get_run_task_ids() == {"run-task-1": "task-1"}
    assert facade.task_id_for_run("run-task-1") == "task-1"
    assert facade.get_task_summaries() == {"task-1": "Finished task 1"}


def test_workflow_state_commands_are_sync(tmp_path: Path) -> None:
    orchestrator = _prepare_project(tmp_path)

    started = orchestrator.start_execution()
    paused = orchestrator.pause_workflow()
    resumed = orchestrator.resume_workflow()

    assert started.status.value == "executing"
    assert paused.status.value == "paused"
    assert resumed.status.value == "executing"


def test_resume_workflow_restores_planning_phase(tmp_path: Path) -> None:
    orchestrator = _prepare_project(tmp_path)

    orchestrator.control_plane.set_workflow_status(WorkflowStatus.PLANNING)
    paused = orchestrator.pause_workflow()
    resumed = orchestrator.resume_workflow()

    assert paused.status.value == "paused"
    assert resumed.status.value == "planning"


def test_facade_surfaces_failed_workflow_without_paused_fallback(tmp_path: Path) -> None:
    orchestrator = _prepare_project(tmp_path)
    facade = OrchestratorFacade(orchestrator)

    orchestrator.control_plane.set_workflow_status(WorkflowStatus.FAILED)

    assert facade.get_workflow_status().value == "failed"
