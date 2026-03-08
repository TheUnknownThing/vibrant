"""Tests for the Phase 4 Gatekeeper prompt and runtime wrapper."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from vibrant.consensus import ConsensusParser, ConsensusWriter, RoadmapParser
from vibrant.gatekeeper import Gatekeeper, GatekeeperRequest, GatekeeperTrigger
from vibrant.models.consensus import ConsensusDecision, ConsensusDocument, ConsensusStatus, DecisionAuthor
from vibrant.models.task import TaskInfo, TaskStatus
from vibrant.project_init import initialize_project
from vibrant.providers.base import RuntimeMode


class FakeGatekeeperAdapter:
    instances: list["FakeGatekeeperAdapter"] = []
    scenario: str = "project_start"

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.cwd = Path(kwargs["cwd"])
        self.on_canonical_event = kwargs.get("on_canonical_event")
        self.agent_record = kwargs.get("agent_record")
        self.provider_thread_id: str | None = None
        self.start_session_calls: list[dict[str, Any]] = []
        self.start_thread_calls: list[dict[str, Any]] = []
        self.start_turn_calls: list[dict[str, Any]] = []
        self.stop_calls = 0
        self.client = type("DummyClient", (), {"is_running": True})()
        FakeGatekeeperAdapter.instances.append(self)

    async def start_session(self, *, cwd: str | None = None, **kwargs: Any) -> Any:
        self.start_session_calls.append({"cwd": cwd, **kwargs})
        return {"serverInfo": {"name": "codex"}}

    async def stop_session(self) -> None:
        self.stop_calls += 1

    async def start_thread(self, **kwargs: Any) -> Any:
        self.start_thread_calls.append(kwargs)
        self.provider_thread_id = "thread-gatekeeper-123"
        if self.agent_record is not None:
            self.agent_record.provider.provider_thread_id = self.provider_thread_id
            self.agent_record.provider.resume_cursor = {"threadId": self.provider_thread_id}
        return {"thread": {"id": self.provider_thread_id}}

    async def resume_thread(self, provider_thread_id: str, **kwargs: Any) -> Any:
        self.provider_thread_id = provider_thread_id
        return {"thread": {"id": provider_thread_id}}

    async def start_turn(self, *, input_items, runtime_mode: RuntimeMode, approval_policy: str, **kwargs: Any) -> Any:
        self.start_turn_calls.append(
            {
                "input_items": list(input_items),
                "runtime_mode": runtime_mode,
                "approval_policy": approval_policy,
                **kwargs,
            }
        )
        if FakeGatekeeperAdapter.scenario == "project_start":
            await self._simulate_project_start()
        elif FakeGatekeeperAdapter.scenario == "task_completion":
            await self._simulate_task_completion()
        return {"turn": {"id": "turn-gatekeeper-1"}}

    async def interrupt_turn(self, **kwargs: Any) -> Any:
        return kwargs

    async def respond_to_request(self, request_id: int | str, *, result: Any | None = None, error=None) -> Any:
        return {"request_id": request_id, "result": result, "error": error}

    async def _emit(self, event: dict[str, Any]) -> None:
        if self.on_canonical_event is not None:
            await self.on_canonical_event(event)

    async def _simulate_project_start(self) -> None:
        consensus_path = self.cwd / ".vibrant" / "consensus.md"
        roadmap_path = self.cwd / ".vibrant" / "roadmap.md"
        current = ConsensusParser().parse_file(consensus_path)
        document = ConsensusDocument(
            project=current.project,
            created_at=current.created_at,
            updated_at=current.updated_at,
            version=current.version,
            status=ConsensusStatus.PLANNING,
            objectives="Turn the proposal into a roadmap and execution plan.",
            decisions=[
                ConsensusDecision(
                    title="Start with orchestrator foundations",
                    date=datetime(2026, 3, 8, 12, 0, tzinfo=timezone.utc),
                    made_by=DecisionAuthor.GATEKEEPER,
                    context="The proposal emphasizes durability.",
                    resolution="Prioritize state, roadmap, and Gatekeeper flows.",
                    impact="Phase 1 and Phase 3 tasks stay first.",
                )
            ],
            getting_started="Review docs/spec.md, then follow the roadmap in order.",
        )
        ConsensusWriter().write(consensus_path, document)
        RoadmapParser().write(
            roadmap_path,
            RoadmapParser().parse(
                """# Roadmap — Project Vibrant

### Task task-001 — Build orchestrator engine
- **Status**: pending
- **Priority**: high
- **Dependencies**: none
- **Skills**: orchestration
- **Branch**: vibrant/task-001
- **Prompt**: Implement lifecycle persistence.

**Acceptance Criteria**:
- [ ] Persist state transitions
- [ ] Recover from restart
"""
            ),
        )
        await self._emit({"type": "content.delta", "delta": "Verdict: planned\n"})
        await self._emit({"type": "turn.completed", "turn": {"id": "turn-gatekeeper-1"}})

    async def _simulate_task_completion(self) -> None:
        consensus_path = self.cwd / ".vibrant" / "consensus.md"
        roadmap_path = self.cwd / ".vibrant" / "roadmap.md"
        current = ConsensusParser().parse_file(consensus_path)
        current.status = ConsensusStatus.EXECUTING
        current.decisions.append(
            ConsensusDecision(
                title="Accept task-001",
                date=datetime(2026, 3, 8, 12, 30, tzinfo=timezone.utc),
                made_by=DecisionAuthor.GATEKEEPER,
                context="The code agent satisfied the acceptance criteria.",
                resolution="Accept task-001 and continue execution.",
                impact="Roadmap advances to the next task.",
            )
        )
        current.getting_started += "\n\n## Questions\n- [blocking] Should the UI ship in v1?"
        ConsensusWriter().write(consensus_path, current)

        roadmap_document = RoadmapParser().parse_file(roadmap_path)
        roadmap_document.tasks[0].status = TaskStatus.ACCEPTED
        RoadmapParser().write(roadmap_path, roadmap_document)

        await self._emit({"type": "content.delta", "delta": "Verdict: accepted\n"})
        await self._emit({"type": "turn.completed", "turn": {"id": "turn-gatekeeper-2"}})


@pytest.mark.parametrize(
    ("trigger", "description", "summary"),
    [
        (GatekeeperTrigger.PROJECT_START, "Create the initial plan.", None),
        (GatekeeperTrigger.TASK_COMPLETION, "Evaluate task-001 completion.", "Agent summary text."),
        (GatekeeperTrigger.TASK_FAILURE, "Task-002 failed with timeout.", "Failure summary."),
        (GatekeeperTrigger.MAX_RETRIES_EXCEEDED, "Task-003 exhausted retries.", "Retry history."),
        (GatekeeperTrigger.USER_CONVERSATION, "User wants to pivot scope.", "Conversation context."),
    ],
)
def test_prompt_template_renders_for_each_trigger(tmp_path, trigger, description, summary):
    initialize_project(tmp_path)
    skills_dir = tmp_path / ".vibrant" / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / "testing-strategy.md").write_text(
        "# testing-strategy\nWrite focused tests before broader validation.\n",
        encoding="utf-8",
    )

    gatekeeper = Gatekeeper(tmp_path, adapter_factory=FakeGatekeeperAdapter)
    prompt = gatekeeper.render_prompt(
        GatekeeperRequest(trigger=trigger, trigger_description=description, agent_summary=summary)
    )

    assert f"{trigger.value}: {description}" in prompt
    assert "## Your Responsibilities" in prompt
    assert "## Current Consensus" in prompt
    assert "## Rules" in prompt
    assert "## Available Skills" in prompt
    assert "testing-strategy: Write focused tests before broader validation." in prompt
    if summary:
        assert summary in prompt
    else:
        assert "N/A" in prompt


def test_gatekeeper_response_parsing_extracts_verdict_questions_and_plan_updates(tmp_path):
    initialize_project(tmp_path)
    gatekeeper = Gatekeeper(tmp_path, adapter_factory=FakeGatekeeperAdapter)
    before_consensus = (tmp_path / ".vibrant" / "consensus.md").read_text(encoding="utf-8")
    before_roadmap = (tmp_path / ".vibrant" / "roadmap.md").read_text(encoding="utf-8")
    after_consensus = before_consensus + "\n## Questions\n- [blocking] Should we ship plugins in v1?\n"
    after_roadmap = """# Roadmap — Project Vibrant

### Task task-001 — First task
- **Status**: pending
- **Priority**: high
- **Dependencies**: none
- **Skills**: orchestration
- **Branch**: vibrant/task-001
- **Prompt**: Implement the task.

**Acceptance Criteria**:
- [ ] Finish the work
"""

    result = gatekeeper.parse_run_artifacts(
        request=GatekeeperRequest(
            trigger=GatekeeperTrigger.TASK_FAILURE,
            trigger_description="Task failed.",
            agent_summary="Summary",
        ),
        prompt="prompt",
        transcript="Verdict: escalate\nNeed a product decision.",
        before_consensus_text=before_consensus,
        after_consensus_text=after_consensus,
        before_roadmap_text=before_roadmap,
        after_roadmap_text=after_roadmap,
        events=[],
        agent_record=gatekeeper._build_agent_record(
            GatekeeperRequest(trigger=GatekeeperTrigger.TASK_FAILURE, trigger_description="Task failed.")
        ),
        error=None,
        turn_result={"turn": {"id": "turn-1"}},
    )

    assert result.verdict == "escalate"
    assert result.questions == ["Should we ship plugins in v1?"]
    assert result.consensus_updated is True
    assert result.roadmap_updated is True
    assert result.plan_modified is True
    assert result.roadmap_document is not None


@pytest.mark.asyncio
async def test_gatekeeper_project_start_run_updates_consensus_and_roadmap(tmp_path):
    FakeGatekeeperAdapter.instances.clear()
    FakeGatekeeperAdapter.scenario = "project_start"
    initialize_project(tmp_path)

    gatekeeper = Gatekeeper(tmp_path, adapter_factory=FakeGatekeeperAdapter, timeout_seconds=1)
    result = await gatekeeper.run(
        GatekeeperRequest(
            trigger=GatekeeperTrigger.PROJECT_START,
            trigger_description="Build a resilient multi-agent orchestrator.",
        )
    )

    adapter = FakeGatekeeperAdapter.instances[0]
    assert adapter.start_session_calls[0]["cwd"] == str(tmp_path)
    assert adapter.start_thread_calls[0]["runtime_mode"] is RuntimeMode.FULL_ACCESS
    assert adapter.start_turn_calls[0]["runtime_mode"] is RuntimeMode.FULL_ACCESS
    assert result.verdict == "planned"
    assert result.consensus_updated is True
    assert result.roadmap_updated is True
    assert result.consensus_document is not None
    assert result.consensus_document.status is ConsensusStatus.PLANNING
    assert result.roadmap_document is not None
    assert len(result.roadmap_document.tasks) == 1
    assert result.agent_record is not None
    assert result.agent_record.status is result.agent_record.status.COMPLETED


@pytest.mark.asyncio
async def test_gatekeeper_task_completion_run_records_verdict_and_questions(tmp_path):
    FakeGatekeeperAdapter.instances.clear()
    FakeGatekeeperAdapter.scenario = "task_completion"
    initialize_project(tmp_path)

    consensus_path = tmp_path / ".vibrant" / "consensus.md"
    roadmap_path = tmp_path / ".vibrant" / "roadmap.md"
    current = ConsensusParser().parse_file(consensus_path)
    current.status = ConsensusStatus.EXECUTING
    ConsensusWriter().write(consensus_path, current)
    RoadmapParser().write(
        roadmap_path,
        RoadmapParser().parse(
            """# Roadmap — Project Vibrant

### Task task-001 — Finish task one
- **Status**: completed
- **Priority**: high
- **Dependencies**: none
- **Skills**: none
- **Branch**: vibrant/task-001
- **Prompt**: Finalize and validate the task.

**Acceptance Criteria**:
- [ ] Task is done
"""
        ),
    )

    gatekeeper = Gatekeeper(tmp_path, adapter_factory=FakeGatekeeperAdapter, timeout_seconds=1)
    result = await gatekeeper.run(
        GatekeeperRequest(
            trigger=GatekeeperTrigger.TASK_COMPLETION,
            trigger_description="Review task-001 output.",
            agent_summary="The agent implemented the feature and ran tests.",
        )
    )

    assert result.verdict == "accepted"
    assert result.consensus_updated is True
    assert result.roadmap_updated is True
    assert result.questions == ["Should the UI ship in v1?"]
    assert result.roadmap_document is not None
    assert result.roadmap_document.tasks[0].status is TaskStatus.ACCEPTED
