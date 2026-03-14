from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

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

    facade.control_plane = SimpleNamespace(
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
