"""Focused tests for the backend E2E fixture provider."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from tests.e2e.fixture_provider import FixtureProviderAdapter
from vibrant.models.agent import AgentRecord, AgentType
from vibrant.project_init import initialize_project
from vibrant.providers.base import RuntimeMode


def _make_agent_record(project_root: Path, *, run_id: str) -> AgentRecord:
    vibrant_dir = project_root / ".vibrant"
    return AgentRecord(
        identity={
            "run_id": run_id,
            "agent_id": f"agent-{run_id}",
            "role": AgentType.CODE.value,
            "type": AgentType.CODE,
        },
        context={"worktree_path": str(project_root)},
        provider={
            "kind": "fixture",
            "transport": "fixture-ndjson",
            "native_event_log": str(vibrant_dir / "logs" / "providers" / "native" / f"{run_id}.ndjson"),
            "canonical_event_log": str(vibrant_dir / "logs" / "providers" / "canonical" / f"{run_id}.ndjson"),
        },
    )


def _read_ndjson(path: str | Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


async def _run_turn(
    adapter: FixtureProviderAdapter,
    prompt: str,
    *,
    runtime_mode: RuntimeMode = RuntimeMode.WORKSPACE_WRITE,
) -> None:
    await adapter.start_turn(
        input_items=[{"type": "text", "text": prompt}],
        runtime_mode=runtime_mode,
        approval_policy="never",
    )
    assert adapter._turn_task is not None
    await adapter._turn_task


@pytest.mark.asyncio
async def test_fixture_provider_logs_and_writes_workspace_files(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    agent_record = _make_agent_record(tmp_path, run_id="run-write")
    adapter = FixtureProviderAdapter(cwd=str(tmp_path), agent_record=agent_record)

    await adapter.start_session(cwd=str(tmp_path))
    await adapter.start_thread(cwd=str(tmp_path))
    await _run_turn(
        adapter,
        "Update demo.txt with a deterministic change.\n"
        "[mock:write demo.txt]\n"
        "[mock:content workspace-change]\n"
        "[mock:tool]",
    )
    await adapter.stop_session()

    assert (tmp_path / "demo.txt").read_text(encoding="utf-8") == "workspace-change\n"
    assert agent_record.provider.provider_thread_id is not None
    assert agent_record.provider.thread_path is not None
    assert agent_record.provider.resume_cursor == {
        "threadId": agent_record.provider.provider_thread_id,
        "turnCount": 1,
    }

    native_lines = _read_ndjson(agent_record.provider.native_event_log or "")
    canonical_lines = _read_ndjson(agent_record.provider.canonical_event_log or "")

    assert any(line["event"] == "fixture.file.write" for line in native_lines)
    assert any(line["event"] == "fixture.turn.completed" for line in native_lines)
    assert any(line["event"] == "tool.call.started" for line in canonical_lines)
    assert any(line["event"] == "assistant.message.completed" for line in canonical_lines)
    assert canonical_lines[0]["data"]["run_id"] == "run-write"
    assert canonical_lines[0]["data"]["agent_id"] == "agent-run-write"
    assert "type" not in canonical_lines[0]["data"]
    assert "timestamp" not in canonical_lines[0]["data"]
    assert canonical_lines[-1]["event"] == "session.state.changed"


@pytest.mark.asyncio
async def test_fixture_provider_supports_question_resolution_and_resume(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    initial_record = _make_agent_record(tmp_path, run_id="run-question")
    opened_events: list[dict[str, Any]] = []
    adapter = FixtureProviderAdapter(
        cwd=str(tmp_path),
        agent_record=initial_record,
        on_canonical_event=opened_events.append,
    )

    await adapter.start_session(cwd=str(tmp_path))
    await adapter.start_thread(cwd=str(tmp_path))
    await adapter.start_turn(
        input_items=[{"type": "text", "text": "Need a follow-up answer.\n[mock:question]"}],
        runtime_mode=RuntimeMode.WORKSPACE_WRITE,
        approval_policy="never",
    )

    for _ in range(100):
        if any(event.get("type") == "request.opened" for event in opened_events):
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("fixture provider never emitted request.opened")

    await adapter.respond_to_request("fixture-request-1", result={"answer": "Use OAuth first."})
    assert adapter._turn_task is not None
    await adapter._turn_task
    await adapter.stop_session()

    thread_id = initial_record.provider.provider_thread_id
    assert thread_id is not None
    thread_state_path = Path(initial_record.provider.thread_path or "")
    assert thread_state_path.exists()

    resumed_record = _make_agent_record(tmp_path, run_id="run-resume")
    resumed = FixtureProviderAdapter(
        cwd=str(tmp_path),
        agent_record=resumed_record,
        resume_thread_id=thread_id,
    )
    await resumed.start_session(cwd=str(tmp_path))
    await resumed.resume_thread(thread_id, cwd=str(tmp_path))
    await _run_turn(resumed, "Continue the same thread with more output.\n[mock:long]")
    await resumed.stop_session()

    resumed_lines = _read_ndjson(resumed_record.provider.canonical_event_log or "")
    request_events = [line for line in _read_ndjson(initial_record.provider.canonical_event_log or "") if "request" in line["event"]]
    thread_state = json.loads(thread_state_path.read_text(encoding="utf-8"))

    assert any(event["event"] == "request.opened" for event in request_events)
    assert any(event["event"] == "request.resolved" for event in request_events)
    assert thread_state["turn_count"] == 2
    assert resumed_record.provider.resume_cursor == {"threadId": thread_id, "turnCount": 2}
    assert sum(1 for line in resumed_lines if line["event"] == "content.delta") > 3
    assert any(line["event"] == "thread.started" and line["data"]["resumed"] is True for line in resumed_lines)


@pytest.mark.asyncio
async def test_fixture_provider_stops_after_rejected_question_request(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    agent_record = _make_agent_record(tmp_path, run_id="run-question-rejected")
    captured_events: list[dict[str, Any]] = []
    adapter = FixtureProviderAdapter(
        cwd=str(tmp_path),
        agent_record=agent_record,
        on_canonical_event=captured_events.append,
    )

    await adapter.start_session(cwd=str(tmp_path))
    await adapter.start_thread(cwd=str(tmp_path))
    await adapter.start_turn(
        input_items=[{"type": "text", "text": "Need a follow-up answer.\n[mock:question]"}],
        runtime_mode=RuntimeMode.WORKSPACE_WRITE,
        approval_policy="never",
    )

    for _ in range(100):
        if any(event.get("type") == "request.opened" for event in captured_events):
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("fixture provider never emitted request.opened")

    await adapter.respond_to_request(
        "fixture-request-1",
        error={"code": -32000, "message": "Interactive input is disabled."},
    )
    assert adapter._turn_task is not None
    await adapter._turn_task
    assert adapter.client._process.returncode == 1
    await adapter.stop_session()

    canonical_lines = _read_ndjson(agent_record.provider.canonical_event_log or "")
    request_resolved = [line for line in canonical_lines if line["event"] == "request.resolved"]

    assert len(request_resolved) == 1
    assert request_resolved[0]["data"]["error"] == {
        "code": -32000,
        "message": "Interactive input is disabled.",
    }
    assert request_resolved[0]["data"]["error_message"] == "Interactive input is disabled."
    assert not any(line["event"] == "assistant.message.completed" for line in canonical_lines)
    assert not any(line["event"] == "turn.completed" for line in canonical_lines if line["data"].get("turn_id"))
    assert any(line["event"] == "runtime.error" for line in canonical_lines)


@pytest.mark.asyncio
async def test_fixture_provider_error_marker_emits_runtime_error(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    agent_record = _make_agent_record(tmp_path, run_id="run-error")
    adapter = FixtureProviderAdapter(cwd=str(tmp_path), agent_record=agent_record)

    await adapter.start_session(cwd=str(tmp_path))
    await adapter.start_thread(cwd=str(tmp_path))
    await _run_turn(adapter, "Reproduce a deterministic provider failure.\n[mock:error]")
    await adapter.stop_session()

    canonical_lines = _read_ndjson(agent_record.provider.canonical_event_log or "")

    assert adapter.client._process.returncode == 1
    assert any(line["event"] == "runtime.error" for line in canonical_lines)
    assert not any(line["event"] == "turn.completed" for line in canonical_lines if line["data"].get("turn_id"))


@pytest.mark.asyncio
async def test_fixture_provider_rejects_file_mutations_outside_workspace(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    agent_record = _make_agent_record(tmp_path, run_id="run-escape")
    adapter = FixtureProviderAdapter(cwd=str(tmp_path), agent_record=agent_record)
    escaped_path = tmp_path.parent / "escaped.txt"

    if escaped_path.exists():
        escaped_path.unlink()

    await adapter.start_session(cwd=str(tmp_path))
    await adapter.start_thread(cwd=str(tmp_path))
    await _run_turn(adapter, "Attempt an unsafe write.\n[mock:write ../escaped.txt]")
    await adapter.stop_session()

    canonical_lines = _read_ndjson(agent_record.provider.canonical_event_log or "")

    assert adapter.client._process.returncode == 1
    assert not escaped_path.exists()
    assert any(
        line["event"] == "runtime.error"
        and "escapes workspace" in str(line["data"].get("error_message") or "")
        for line in canonical_lines
    )
