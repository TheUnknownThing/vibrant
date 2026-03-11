"""Integration-style tests for the Phase 5.1 code-agent lifecycle."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest

from vibrant.agents.runtime import RunState
from vibrant.consensus import ConsensusParser, ConsensusWriter, RoadmapParser
from vibrant.gatekeeper import Gatekeeper, GatekeeperRequest, GatekeeperRunResult, GatekeeperTrigger
from vibrant.models.agent import AgentProviderMetadata, AgentRecord, AgentStatus, AgentType
from vibrant.models.consensus import ConsensusDocument, ConsensusStatus
from vibrant.models.state import OrchestratorStatus
from vibrant.models.task import TaskStatus
from vibrant.orchestrator import CodeAgentLifecycle, OrchestratorEngine, OrchestratorFacade
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


class ManagedGatekeeperAdapter:
    instances: list["ManagedGatekeeperAdapter"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.cwd = Path(kwargs["cwd"])
        self.on_canonical_event = kwargs.get("on_canonical_event")
        self.agent_record = kwargs.get("agent_record")
        self.start_session_calls: list[dict[str, Any]] = []
        self.start_thread_calls: list[dict[str, Any]] = []
        self.start_turn_calls: list[dict[str, Any]] = []
        self.respond_calls: list[dict[str, Any]] = []
        self._request_resolved = asyncio.Event()
        process = type("DummyProcess", (), {"pid": 6601, "returncode": None})()
        self.client = type("DummyClient", (), {"is_running": True, "_process": process})()
        ManagedGatekeeperAdapter.instances.append(self)

    async def start_session(self, *, cwd: str | None = None, **kwargs: Any) -> Any:
        self.start_session_calls.append({"cwd": cwd, **kwargs})
        return {"serverInfo": {"name": "codex"}}

    async def stop_session(self) -> None:
        if self.client._process.returncode is None:
            self.client._process.returncode = 0
        self.client.is_running = False

    async def start_thread(self, **kwargs: Any) -> Any:
        self.start_thread_calls.append(dict(kwargs))
        thread_id = "thread-managed-gatekeeper"
        if self.agent_record is not None:
            self.agent_record.provider.provider_thread_id = thread_id
            self.agent_record.provider.resume_cursor = {"threadId": thread_id}
        return {"thread": {"id": thread_id}}

    async def resume_thread(self, provider_thread_id: str, **kwargs: Any) -> Any:
        self.start_thread_calls.append({"provider_thread_id": provider_thread_id, **kwargs})
        if self.agent_record is not None:
            self.agent_record.provider.provider_thread_id = provider_thread_id
            self.agent_record.provider.resume_cursor = {"threadId": provider_thread_id}
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
        asyncio.create_task(self._simulate_request_flow(), name="managed-gatekeeper-request")
        return {"turn": {"id": "turn-managed-gatekeeper-1"}}

    async def respond_to_request(
        self,
        request_id: int | str,
        *,
        result: Any | None = None,
        error: dict[str, Any] | None = None,
    ) -> Any:
        self.respond_calls.append({"request_id": request_id, "result": result, "error": error})
        self._request_resolved.set()
        return {"request_id": request_id, "result": result, "error": error}

    async def interrupt_turn(self, **kwargs: Any) -> Any:
        return kwargs

    async def _simulate_request_flow(self) -> None:
        if self.on_canonical_event is not None:
            await self.on_canonical_event(
                {
                    "type": "request.opened",
                    "request_id": "req-1",
                    "request_kind": "user-input",
                    "message": "Choose the API strategy.",
                }
            )
        await self._request_resolved.wait()
        if self.on_canonical_event is not None:
            await self.on_canonical_event(
                {
                    "type": "request.resolved",
                    "request_id": "req-1",
                    "request_kind": "user-input",
                }
            )
            await self.on_canonical_event({"type": "content.delta", "delta": "Recorded the user decision."})
            await self.on_canonical_event({"type": "turn.completed", "turn": {"id": "turn-managed-gatekeeper-1"}})
        self.client._process.returncode = 0


class FakeGatekeeper:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.requests: list[GatekeeperRequest] = []
        self.completion_verdicts: list[str] = ["accepted"]
        self.failure_prompts: list[str] = ["Retry with guard clause."]
        self.escalation_question = "Should the task be broken down further?"

    async def run(self, request: GatekeeperRequest, *, resume_latest_thread: bool | None = None) -> GatekeeperRunResult:
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
        elif request.trigger in {GatekeeperTrigger.PROJECT_START, GatekeeperTrigger.USER_CONVERSATION}:
            verdict = "planned"
            consensus.status = ConsensusStatus.PLANNING
            consensus.objectives = request.trigger_description
            roadmap = RoadmapParser().parse(ROADMAP_TEXT)
            plan_modified = True

        ConsensusWriter().write(consensus_path, consensus)
        RoadmapParser().write(roadmap_path, roadmap)

        transcript = f"Verdict: {verdict}"
        gatekeeper_record = AgentRecord(
            agent_id=f"gatekeeper-{request.trigger.value}-test",
            task_id=f"gatekeeper-{request.trigger.value}",
            type=AgentType.GATEKEEPER,
            status=AgentStatus.AWAITING_INPUT if (questions or consensus.questions) else AgentStatus.COMPLETED,
            summary=transcript,
            provider=AgentProviderMetadata(
                provider_thread_id="thread-gatekeeper-1",
                resume_cursor={"threadId": "thread-gatekeeper-1"},
            ),
        )
        return GatekeeperRunResult(
            agent_record=gatekeeper_record,
            state=RunState.AWAITING_INPUT if (questions or consensus.questions) else RunState.COMPLETED,
            transcript=transcript,
            summary=transcript,
            error=None,
            turn_result={"turn": {"id": "gatekeeper-turn-1"}},
        )


class AsyncHandle:
    def __init__(self, result_future: asyncio.Future[GatekeeperRunResult]) -> None:
        self.result_future = result_future

    def done(self) -> bool:
        return self.result_future.done()

    async def wait(self) -> GatekeeperRunResult:
        return await self.result_future


def _agent_record(
    agent_id: str,
    *,
    task_id: str = "task-001",
    agent_type: AgentType = AgentType.CODE,
    status: AgentStatus = AgentStatus.RUNNING,
) -> AgentRecord:
    return AgentRecord(
        agent_id=agent_id,
        task_id=task_id,
        type=agent_type,
        status=status,
    )


class AsyncFakeGatekeeper(FakeGatekeeper):
    async def start_run(self, request: GatekeeperRequest, *, resume_latest_thread: bool | None = None, on_result=None):
        self.requests.append(request)

        async def finish() -> GatekeeperRunResult:
            await asyncio.sleep(0)
            result = await FakeGatekeeper.run(self, request, resume_latest_thread=resume_latest_thread)
            if on_result is not None:
                maybe_result = on_result(result)
                if asyncio.iscoroutine(maybe_result):
                    await maybe_result
            return result

        result_future = asyncio.create_task(finish())
        return AsyncHandle(result_future)


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
    assert lifecycle.engine.state.total_agent_spawns == 2
    assert lifecycle.engine.state.active_agents == []
    assert lifecycle.engine.state.status is OrchestratorStatus.COMPLETED


@pytest.mark.asyncio
async def test_submit_gatekeeper_message_routes_initial_prompt_to_project_start(tmp_path):
    repo = _init_repo(tmp_path)
    initialize_project(repo)
    gatekeeper = FakeGatekeeper(repo)

    lifecycle = CodeAgentLifecycle(repo, gatekeeper=gatekeeper, adapter_factory=FakeCodeAgentAdapter)
    result = await lifecycle.submit_gatekeeper_message("Build an auth MVP for the app.")

    assert result.state is RunState.COMPLETED
    assert gatekeeper.requests[0].trigger is GatekeeperTrigger.PROJECT_START
    assert lifecycle.engine.state.status is OrchestratorStatus.PLANNING

    roadmap = RoadmapParser().parse_file(repo / ".vibrant" / "roadmap.md")
    assert roadmap.tasks
    assert roadmap.tasks[0].title == "Update the tracked file"


@pytest.mark.asyncio
async def test_start_gatekeeper_message_returns_handle_before_result_is_applied(tmp_path):
    repo = _init_repo(tmp_path)
    initialize_project(repo)
    gatekeeper = AsyncFakeGatekeeper(repo)

    lifecycle = CodeAgentLifecycle(repo, gatekeeper=gatekeeper, adapter_factory=FakeCodeAgentAdapter)
    handle = await lifecycle.start_gatekeeper_message("Build an auth MVP for the app.")

    assert handle.done() is False
    assert gatekeeper.requests[0].trigger is GatekeeperTrigger.PROJECT_START

    result = await handle.wait()
    await asyncio.sleep(0)

    assert result.state is RunState.COMPLETED
    assert lifecycle.engine.state.status is OrchestratorStatus.PLANNING


@pytest.mark.asyncio
async def test_real_gatekeeper_runs_through_shared_agent_manager(tmp_path):
    repo = _init_repo(tmp_path)
    initialize_project(repo)
    gatekeeper = Gatekeeper(repo, adapter_factory=ManagedGatekeeperAdapter, timeout_seconds=1)

    lifecycle = CodeAgentLifecycle(repo, gatekeeper=gatekeeper, adapter_factory=FakeCodeAgentAdapter)
    handle = await lifecycle.start_gatekeeper_message("Build an auth MVP for the app.")

    for _ in range(50):
        active = lifecycle.agent_manager.list_agents(agent_type=AgentType.GATEKEEPER, include_completed=False)
        if active and active[0].awaiting_input:
            break
        await asyncio.sleep(0)
    else:
        raise AssertionError("Managed Gatekeeper run never surfaced through agent_manager")

    snapshot = lifecycle.agent_manager.get_agent(handle.agent_record.agent_id)
    assert snapshot is not None
    assert snapshot.agent_type == AgentType.GATEKEEPER.value
    assert snapshot.has_handle is True
    assert snapshot.awaiting_input is True
    assert snapshot.input_requests[0].request_id == "req-1"

    updated = await lifecycle.agent_manager.respond_to_request(
        handle.agent_record.agent_id,
        "req-1",
        result={"answer": "Use OAuth first."},
    )
    assert updated.agent_id == handle.agent_record.agent_id

    result = await handle.wait()
    await asyncio.sleep(0)

    assert result.state is RunState.COMPLETED
    assert lifecycle.agent_manager.get_handle(handle.agent_record.agent_id) is None
    completed = lifecycle.agent_manager.get_agent(handle.agent_record.agent_id)
    assert completed is not None
    assert completed.has_handle is False
    assert completed.done is True
    assert completed.summary == "Recorded the user decision."


def test_agent_store_rebuilds_state_without_engine_agent_cache(tmp_path):
    repo, engine = _prepare_project(tmp_path)
    lifecycle = CodeAgentLifecycle(repo, engine=engine, gatekeeper=FakeGatekeeper(repo), adapter_factory=FakeCodeAgentAdapter)

    record = _agent_record("agent-task-standalone", status=AgentStatus.RUNNING)
    lifecycle.agent_store.upsert(record)

    assert not hasattr(lifecycle.engine, "agents")
    assert [item.agent_id for item in lifecycle.state_store.agent_records()] == ["agent-task-standalone"]
    assert lifecycle.state_store.state.active_agents == ["agent-task-standalone"]

    facade = OrchestratorFacade(lifecycle)
    assert [item.agent_id for item in facade.agent_records()] == ["agent-task-standalone"]


def test_facade_transition_to_planning_tolerates_consensus_sync_promoting_state(tmp_path):
    repo = _init_repo(tmp_path)
    initialize_project(repo)

    lifecycle = CodeAgentLifecycle(repo, gatekeeper=FakeGatekeeper(repo), adapter_factory=FakeCodeAgentAdapter)
    facade = OrchestratorFacade(lifecycle)

    assert lifecycle.engine.state.status is OrchestratorStatus.INIT

    facade.transition_workflow_state(OrchestratorStatus.PLANNING)

    assert lifecycle.engine.state.status is OrchestratorStatus.PLANNING


def test_lifecycle_passes_canonical_callback_to_default_gatekeeper(tmp_path, monkeypatch):
    repo, engine = _prepare_project(tmp_path)
    captured: dict[str, object] = {}
    callback = object()

    class CapturingGatekeeper:
        def __init__(self, project_root: Path, *, on_canonical_event=None, **kwargs: Any) -> None:
            captured["project_root"] = project_root
            captured["callback"] = on_canonical_event
            captured["kwargs"] = kwargs

    monkeypatch.setattr("vibrant.orchestrator.lifecycle.Gatekeeper", CapturingGatekeeper)

    lifecycle = CodeAgentLifecycle(
        repo,
        engine=engine,
        adapter_factory=FakeCodeAgentAdapter,
        on_canonical_event=callback,
    )

    assert lifecycle.gatekeeper is not None
    assert captured["project_root"] == repo
    assert captured["callback"] is callback


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


@pytest.mark.asyncio
async def test_task_execution_service_starts_with_handle_snapshot(tmp_path):
    repo, engine = _prepare_project(tmp_path)
    FakeCodeAgentAdapter.instances.clear()
    FakeCodeAgentAdapter.prompts.clear()
    FakeCodeAgentAdapter.scenarios = ["success"]
    FakeCodeAgentAdapter.success_contents = ["feature via handle\n"]
    gatekeeper = FakeGatekeeper(repo)

    lifecycle = CodeAgentLifecycle(repo, engine=engine, gatekeeper=gatekeeper, adapter_factory=FakeCodeAgentAdapter)
    assert lifecycle.dispatcher is not None

    lifecycle.workflow_service.begin_execution_if_needed()
    task = lifecycle.dispatcher.dispatch_next_task()
    assert task is not None

    attempt = await lifecycle.agent_manager.start_task(task)
    snapshots = lifecycle.agent_manager.list_handle_snapshots()

    assert len(snapshots) == 1
    assert snapshots[0].agent_id == attempt.agent_record.agent_id
    assert snapshots[0].task_id == task.id
    assert snapshots[0].agent_type == "code"

    result = await lifecycle.agent_manager.wait_for_task(attempt)

    assert result.outcome == "accepted"
    assert lifecycle.agent_manager.list_handle_snapshots() == []


@pytest.mark.asyncio
async def test_agent_management_service_unifies_live_and_persisted_agent_state(tmp_path):
    repo, engine = _prepare_project(tmp_path)
    FakeCodeAgentAdapter.instances.clear()
    FakeCodeAgentAdapter.prompts.clear()
    FakeCodeAgentAdapter.scenarios = ["success"]
    FakeCodeAgentAdapter.success_contents = ["managed feature\n"]
    gatekeeper = FakeGatekeeper(repo)

    lifecycle = CodeAgentLifecycle(repo, engine=engine, gatekeeper=gatekeeper, adapter_factory=FakeCodeAgentAdapter)
    assert lifecycle.dispatcher is not None

    lifecycle.workflow_service.begin_execution_if_needed()
    task = lifecycle.dispatcher.dispatch_next_task()
    assert task is not None

    attempt = await lifecycle.agent_manager.start_task(task)

    active = lifecycle.agent_manager.list_active_agents()
    assert len(active) == 1
    assert active[0].agent_id == attempt.agent_record.agent_id
    assert active[0].task_id == task.id
    assert active[0].agent_type == "code"
    assert active[0].has_handle is True
    assert active[0].active is True
    assert active[0].provider_thread_id == "thread-task-001-1"

    result = await lifecycle.agent_manager.wait_for_task(attempt)

    assert result.outcome == "accepted"

    snapshot = lifecycle.agent_manager.get_agent(attempt.agent_record.agent_id)
    assert snapshot is not None
    assert snapshot.agent_id == attempt.agent_record.agent_id
    assert snapshot.has_handle is False
    assert snapshot.active is False
    assert snapshot.done is True
    assert snapshot.status == "completed"
    assert snapshot.state == "completed"
    assert snapshot.summary == "Implemented task-001."
    assert snapshot.provider_thread_id == "thread-task-001-1"

    latest = lifecycle.agent_manager.latest_for_task(task.id)
    assert latest is not None
    assert latest.agent_id == attempt.agent_record.agent_id


def test_agent_management_service_keeps_persisted_awaiting_input_agents_active(tmp_path):
    repo, engine = _prepare_project(tmp_path)
    lifecycle = CodeAgentLifecycle(repo, engine=engine, gatekeeper=FakeGatekeeper(repo), adapter_factory=FakeCodeAgentAdapter)

    record = _agent_record(
        "agent-task-001-awaiting",
        status=AgentStatus.AWAITING_INPUT,
    )
    record.provider.provider_thread_id = "thread-task-001-persisted"
    record.provider.resume_cursor = {"threadId": "thread-task-001-persisted"}
    lifecycle.agent_registry.upsert(record, increment_spawn=True)

    active = lifecycle.agent_manager.list_active_agents()

    assert [snapshot.agent_id for snapshot in active] == [record.agent_id]
    assert active[0].has_handle is False
    assert active[0].active is True
    assert active[0].awaiting_input is True


def test_agent_management_service_normalizes_string_agent_type_filters(tmp_path):
    repo, engine = _prepare_project(tmp_path)
    lifecycle = CodeAgentLifecycle(repo, engine=engine, gatekeeper=FakeGatekeeper(repo), adapter_factory=FakeCodeAgentAdapter)

    code_record = _agent_record("agent-task-001-code", agent_type=AgentType.CODE)
    merge_record = _agent_record("merge-task-001-merge", agent_type=AgentType.MERGE)
    lifecycle.agent_registry.upsert(code_record, increment_spawn=True)
    lifecycle.agent_registry.upsert(merge_record, increment_spawn=True)

    snapshots = lifecycle.agent_manager.list_agents(agent_type=" CODE ")

    assert [snapshot.agent_id for snapshot in snapshots] == [code_record.agent_id]


def test_agent_management_service_rejects_invalid_string_agent_type_filters(tmp_path):
    repo, engine = _prepare_project(tmp_path)
    lifecycle = CodeAgentLifecycle(repo, engine=engine, gatekeeper=FakeGatekeeper(repo), adapter_factory=FakeCodeAgentAdapter)

    with pytest.raises(ValueError, match="Unsupported agent type filter"):
        lifecycle.agent_manager.list_agents(agent_type="bogus")
