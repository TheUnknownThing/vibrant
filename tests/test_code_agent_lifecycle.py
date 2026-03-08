"""Integration-style tests for the Phase 5.1 code-agent lifecycle."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from vibrant.consensus import ConsensusParser, ConsensusWriter, RoadmapParser
from vibrant.gatekeeper import GatekeeperRequest, GatekeeperRunResult, GatekeeperTrigger
from vibrant.models.agent import AgentRecord
from vibrant.models.consensus import ConsensusDocument, ConsensusStatus
from vibrant.models.state import OrchestratorStatus
from vibrant.models.task import TaskStatus
from vibrant.orchestrator import CodeAgentLifecycle, OrchestratorEngine
from vibrant.project_init import initialize_project
from vibrant.providers.base import RuntimeMode


ROADMAP_TEXT = """# Roadmap — Project Vibrant

### Task task-001 — Update the tracked file
- **Status**: pending
- **Priority**: high
- **Dependencies**: none
- **Skills**: testing-strategy
- **Branch**: vibrant/task-001
- **Prompt**: Change `app.txt` so the feature is implemented.

**Acceptance Criteria**:
- [ ] Update app.txt in the task branch
- [ ] Commit the change
"""


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        text=True,
        capture_output=True,
        check=False,
    )
    if check and completed.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {(completed.stderr or completed.stdout).strip()}")
    return completed


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Vibrant Tests")
    _git(repo, "config", "user.email", "vibrant@example.com")
    _write(repo / "app.txt", "base\n")
    _git(repo, "add", "app.txt")
    _git(repo, "commit", "-m", "initial")
    return repo


def _prepare_project(tmp_path: Path) -> tuple[Path, OrchestratorEngine]:
    repo = _init_repo(tmp_path)
    initialize_project(repo)

    skills_dir = repo / ".vibrant" / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    _write(
        skills_dir / "testing-strategy.md",
        "# testing-strategy\nWrite focused tests before broader validation.\n",
    )

    ConsensusWriter().write(
        repo / ".vibrant" / "consensus.md",
        ConsensusDocument(
            project="Vibrant",
            version=1,
            status=ConsensusStatus.EXECUTING,
            objectives="Ship the lifecycle runner.",
            getting_started="Review the roadmap and implement one task at a time.",
        ),
    )
    RoadmapParser().write(repo / ".vibrant" / "roadmap.md", RoadmapParser().parse(ROADMAP_TEXT))

    engine = OrchestratorEngine.load(repo)
    engine.transition_to(OrchestratorStatus.PLANNING)
    return repo, engine


class FakeCodeAgentAdapter:
    instances: list["FakeCodeAgentAdapter"] = []
    scenarios: list[str] = []
    prompts: list[str] = []
    success_contents: list[str] = ["feature\n"]

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
        process = type("DummyProcess", (), {"pid": 4100 + len(self.instances), "returncode": None})()
        self.client = type("DummyClient", (), {"is_running": True, "_process": process})()
        FakeCodeAgentAdapter.instances.append(self)

    async def start_session(self, *, cwd: str | None = None, **kwargs: Any) -> Any:
        self.start_session_calls.append({"cwd": cwd, **kwargs})
        return {"serverInfo": {"name": "codex"}}

    async def stop_session(self) -> None:
        self.stop_calls += 1
        if self.client._process.returncode is None:
            self.client._process.returncode = 0
        self.client.is_running = False

    async def start_thread(self, **kwargs: Any) -> Any:
        self.start_thread_calls.append(kwargs)
        self.provider_thread_id = f"thread-{self.agent_record.task_id}-{len(self.start_thread_calls)}"
        if self.agent_record is not None:
            self.agent_record.provider.provider_thread_id = self.provider_thread_id
            self.agent_record.provider.resume_cursor = {"threadId": self.provider_thread_id}
            runtime_mode = kwargs.get("runtime_mode")
            if isinstance(runtime_mode, RuntimeMode):
                self.agent_record.provider.runtime_mode = runtime_mode.codex_thread_sandbox
        return {"thread": {"id": self.provider_thread_id, "path": str(self.cwd / ".codex" / self.provider_thread_id)}}

    async def resume_thread(self, provider_thread_id: str, **kwargs: Any) -> Any:
        self.provider_thread_id = provider_thread_id
        return {"thread": {"id": provider_thread_id}}

    async def start_turn(self, *, input_items, runtime_mode: RuntimeMode, approval_policy: str, **kwargs: Any) -> Any:
        prompt = input_items[0]["text"]
        FakeCodeAgentAdapter.prompts.append(prompt)
        self.start_turn_calls.append(
            {
                "input_items": list(input_items),
                "runtime_mode": runtime_mode,
                "approval_policy": approval_policy,
                **kwargs,
            }
        )

        scenario = FakeCodeAgentAdapter.scenarios.pop(0)
        if scenario == "success":
            content = FakeCodeAgentAdapter.success_contents.pop(0) if FakeCodeAgentAdapter.success_contents else "feature\n"
            _write(self.cwd / "app.txt", content)
            _git(self.cwd, "add", "app.txt")
            _git(self.cwd, "commit", "-m", f"[vibrant:{self.agent_record.task_id}] implement")
            await self._emit({"type": "content.delta", "delta": f"Implemented {self.agent_record.task_id}."})
            await self._emit({"type": "turn.completed", "turn": {"id": f"turn-{self.agent_record.task_id}"}})
            self.client._process.returncode = 0
        elif scenario == "failure":
            self.client._process.returncode = 1
            await self._emit({"type": "runtime.error", "error": {"message": "simulated failure"}})
        else:
            raise AssertionError(f"Unknown fake scenario: {scenario}")

        return {"turn": {"id": f"turn-{self.agent_record.task_id}"}}

    async def interrupt_turn(self, **kwargs: Any) -> Any:
        return kwargs

    async def respond_to_request(self, request_id: int | str, *, result: Any | None = None, error=None) -> Any:
        return {"request_id": request_id, "result": result, "error": error}

    async def _emit(self, event: dict[str, Any]) -> None:
        if self.on_canonical_event is not None:
            payload = {
                "provider": "codex",
                "agent_id": self.agent_record.agent_id,
                "task_id": self.agent_record.task_id,
                **event,
            }
            await self.on_canonical_event(payload)


class FakeGatekeeper:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.requests: list[GatekeeperRequest] = []
        self.completion_verdicts: list[str] = ["accepted"]
        self.failure_prompts: list[str] = ["Retry with guard clause."]
        self.escalation_question = "Should the task be broken down further?"

    async def run(self, request: GatekeeperRequest) -> GatekeeperRunResult:
        self.requests.append(request)
        consensus_path = self.project_root / ".vibrant" / "consensus.md"
        roadmap_path = self.project_root / ".vibrant" / "roadmap.md"

        consensus = ConsensusParser().parse_file(consensus_path)
        roadmap = RoadmapParser().parse_file(roadmap_path)
        verdict = "accepted"
        questions: list[str] = []
        plan_modified = False

        if request.trigger is GatekeeperTrigger.TASK_COMPLETION:
            verdict = self.completion_verdicts.pop(0) if self.completion_verdicts else "accepted"
            if verdict == "accepted":
                roadmap.tasks[0].status = TaskStatus.ACCEPTED
            else:
                roadmap.tasks[0].prompt = "Retry after gatekeeper review."
                plan_modified = True
        elif request.trigger is GatekeeperTrigger.TASK_FAILURE:
            verdict = "retry"
            roadmap.tasks[0].prompt = self.failure_prompts.pop(0) if self.failure_prompts else "Retry with adjustments."
            plan_modified = True
        elif request.trigger is GatekeeperTrigger.MAX_RETRIES_EXCEEDED:
            verdict = "escalate"
            consensus.questions = [self.escalation_question]
            questions = list(consensus.questions)

        ConsensusWriter().write(consensus_path, consensus)
        RoadmapParser().write(roadmap_path, roadmap)

        consensus_document = ConsensusParser().parse_file(consensus_path)
        roadmap_document = RoadmapParser().parse_file(roadmap_path)
        transcript = f"Verdict: {verdict}"
        return GatekeeperRunResult(
            request=request,
            prompt="gatekeeper prompt",
            transcript=transcript,
            verdict=verdict,
            questions=questions or list(consensus_document.questions),
            consensus_updated=True,
            roadmap_updated=True,
            plan_modified=plan_modified,
            consensus_document=consensus_document,
            roadmap_document=roadmap_document,
            error=None,
            turn_result={"turn": {"id": "gatekeeper-turn-1"}},
        )


@pytest.mark.asyncio
async def test_code_agent_lifecycle_executes_merges_and_persists_agent_record(tmp_path):
    repo, engine = _prepare_project(tmp_path)
    FakeCodeAgentAdapter.instances.clear()
    FakeCodeAgentAdapter.prompts.clear()
    FakeCodeAgentAdapter.scenarios = ["success"]
    FakeCodeAgentAdapter.success_contents = ["feature\n"]
    gatekeeper = FakeGatekeeper(repo)

    lifecycle = CodeAgentLifecycle(repo, engine=engine, gatekeeper=gatekeeper, adapter_factory=FakeCodeAgentAdapter)
    result = await lifecycle.execute_next_task()

    assert result is not None
    assert result.outcome == "accepted"
    assert result.task_status is TaskStatus.ACCEPTED
    assert (repo / "app.txt").read_text(encoding="utf-8") == "feature\n"
    assert not lifecycle.git_manager.worktree_path("task-001").exists()
    assert gatekeeper.requests[0].trigger is GatekeeperTrigger.TASK_COMPLETION
    assert "Write focused tests before broader validation." in FakeCodeAgentAdapter.prompts[0]

    roadmap = RoadmapParser().parse_file(repo / ".vibrant" / "roadmap.md")
    assert roadmap.tasks[0].status is TaskStatus.ACCEPTED

    agent_files = sorted((repo / ".vibrant" / "agents").glob("agent-task-001-*.json"))
    assert len(agent_files) == 1
    record = AgentRecord.model_validate_json(agent_files[0].read_text(encoding="utf-8"))
    assert record.status is record.status.COMPLETED
    assert record.branch == "vibrant/task-001"
    assert record.summary == "Implemented task-001."
    assert record.prompt_used is not None and "Change `app.txt`" in record.prompt_used
    assert record.skills_loaded == ["testing-strategy"]
    assert record.pid is not None
    assert record.provider.provider_thread_id == "thread-task-001-1"
    assert record.provider.runtime_mode == "workspace-write"
    assert record.provider.native_event_log is not None
    assert record.provider.canonical_event_log is not None
    assert lifecycle.engine.state.total_agent_spawns == 1
    assert lifecycle.engine.state.active_agents == []
    assert lifecycle.engine.state.status is OrchestratorStatus.EXECUTING


@pytest.mark.asyncio
async def test_code_agent_lifecycle_retries_after_gatekeeper_reprompt(tmp_path):
    repo, engine = _prepare_project(tmp_path)
    FakeCodeAgentAdapter.instances.clear()
    FakeCodeAgentAdapter.prompts.clear()
    FakeCodeAgentAdapter.scenarios = ["failure", "success"]
    FakeCodeAgentAdapter.success_contents = ["retried feature\n"]
    gatekeeper = FakeGatekeeper(repo)

    lifecycle = CodeAgentLifecycle(repo, engine=engine, gatekeeper=gatekeeper, adapter_factory=FakeCodeAgentAdapter)

    first = await lifecycle.execute_next_task()
    second = await lifecycle.execute_next_task()

    assert first is not None and first.outcome == "retried"
    assert first.task_status is TaskStatus.QUEUED
    assert second is not None and second.outcome == "accepted"
    assert second.task_status is TaskStatus.ACCEPTED
    assert [request.trigger for request in gatekeeper.requests] == [
        GatekeeperTrigger.TASK_FAILURE,
        GatekeeperTrigger.TASK_COMPLETION,
    ]
    assert "Retry with guard clause." in FakeCodeAgentAdapter.prompts[1]
    assert (repo / "app.txt").read_text(encoding="utf-8") == "retried feature\n"


@pytest.mark.asyncio
async def test_code_agent_lifecycle_escalates_after_max_retries(tmp_path):
    repo, engine = _prepare_project(tmp_path)
    FakeCodeAgentAdapter.instances.clear()
    FakeCodeAgentAdapter.prompts.clear()
    FakeCodeAgentAdapter.scenarios = ["failure"]
    gatekeeper = FakeGatekeeper(repo)

    lifecycle = CodeAgentLifecycle(repo, engine=engine, gatekeeper=gatekeeper, adapter_factory=FakeCodeAgentAdapter)
    assert lifecycle.dispatcher is not None
    lifecycle.dispatcher.get_task("task-001").max_retries = 0

    result = await lifecycle.execute_next_task()

    assert result is not None
    assert result.outcome == "escalated"
    assert result.task_status is TaskStatus.ESCALATED
    assert gatekeeper.requests[0].trigger is GatekeeperTrigger.MAX_RETRIES_EXCEEDED
    assert (repo / "app.txt").read_text(encoding="utf-8") == "base\n"

    roadmap = RoadmapParser().parse_file(repo / ".vibrant" / "roadmap.md")
    assert roadmap.tasks[0].status is TaskStatus.ESCALATED
    assert lifecycle.engine.state.pending_questions == [gatekeeper.escalation_question]
    assert await lifecycle.execute_next_task() is None
