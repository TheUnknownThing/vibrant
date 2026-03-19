from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from vibrant.config import VibrantConfigPatch, load_config
from vibrant.models.agent import AgentRecord, AgentStatus, AgentType
from vibrant.orchestrator import OrchestratorFacade as ExportedFacade, create_orchestrator
from vibrant.orchestrator.facade import OrchestratorFacade
from vibrant.project_init import initialize_project


def _prepare_orchestrator(tmp_path: Path):
    initialize_project(tmp_path)
    return create_orchestrator(tmp_path)


def test_facade_import_path_is_stable() -> None:
    assert ExportedFacade is OrchestratorFacade


@pytest.mark.asyncio
async def test_facade_submit_gatekeeper_input_routes_through_control_plane(tmp_path: Path, monkeypatch) -> None:
    orchestrator = _prepare_orchestrator(tmp_path)
    facade = OrchestratorFacade(orchestrator)
    calls: list[tuple[str, object]] = []

    async def fake_submit(text: str, question_id: str | None = None):
        calls.append(("submit", (text, question_id)))
        return SimpleNamespace(conversation_id="gatekeeper-1", agent_id="gatekeeper-agent")

    async def fake_wait(submission):
        calls.append(("wait", submission.agent_id))
        return SimpleNamespace(events=[], summary="done")

    facade._control_plane = SimpleNamespace(
        submit_user_input=fake_submit,
        wait_for_gatekeeper_submission=fake_wait,
    )

    submission, result = await facade.submit_gatekeeper_input("Plan the architecture rewrite.")

    assert submission.conversation_id == "gatekeeper-1"
    assert result.summary == "done"
    assert calls == [
        ("submit", ("Plan the architecture rewrite.", None)),
        ("wait", "gatekeeper-agent"),
    ]


def test_facade_run_projection_propagates_runtime_errors(tmp_path: Path, monkeypatch) -> None:
    orchestrator = _prepare_orchestrator(tmp_path)
    facade = OrchestratorFacade(orchestrator)
    orchestrator._agent_run_store.upsert(
        AgentRecord(
            identity={
                "run_id": "run-broken",
                "agent_id": "agent-broken",
                "role": AgentType.CODE.value,
                "type": AgentType.CODE,
            },
            lifecycle={"status": AgentStatus.RUNNING},
        )
    )

    def broken_snapshot_handle(run_id: str):
        raise RuntimeError(f"runtime snapshot failed for {run_id}")

    monkeypatch.setattr(orchestrator._runtime_service, "snapshot_handle", broken_snapshot_handle)

    with pytest.raises(RuntimeError, match="runtime snapshot failed for run-broken"):
        facade.get_run("run-broken")


def test_facade_restart_failed_task_routes_through_control_plane(tmp_path: Path) -> None:
    orchestrator = _prepare_orchestrator(tmp_path)
    facade = OrchestratorFacade(orchestrator)
    calls: list[str] = []

    def fake_restart(task_id: str):
        calls.append(task_id)
        return SimpleNamespace(id=task_id, status="queued")

    facade._control_plane = SimpleNamespace(restart_failed_task=fake_restart)

    restarted = facade.restart_failed_task("task-1")

    assert restarted.id == "task-1"
    assert calls == ["task-1"]


def test_facade_updates_orchestrator_owned_config(tmp_path: Path) -> None:
    orchestrator = _prepare_orchestrator(tmp_path)
    facade = OrchestratorFacade(orchestrator)

    updated = facade.update_config(
        VibrantConfigPatch(
            model="gpt-5.4-codex",
            approval_policy="on-request",
            reasoning_effort="high",
        )
    )
    reloaded = load_config(start_path=tmp_path)

    assert facade.get_config().model == "gpt-5.4-codex"
    assert updated.model == "gpt-5.4-codex"
    assert updated.approval_policy == "on-request"
    assert updated.reasoning_effort == "high"
    assert reloaded.model == "gpt-5.4-codex"
    assert reloaded.approval_policy == "on-request"
    assert reloaded.reasoning_effort == "high"
