"""Integration-style tests for the Phase 5.1 code-agent lifecycle."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest

from vibrant.agents import (
    Gatekeeper,
    GatekeeperRequest,
    GatekeeperRoleResult,
    GatekeeperRunResult,
    GatekeeperTrigger,
    serialize_role_result,
)
from vibrant.agents.runtime import InputRequest, RunState
from vibrant.consensus import ConsensusParser, ConsensusWriter, RoadmapParser
from vibrant.models.agent import AgentProviderMetadata, AgentRunRecord, AgentStatus
from vibrant.models.consensus import ConsensusDocument, ConsensusStatus
from vibrant.models.state import OrchestratorStatus
from vibrant.models.task import TaskStatus
from vibrant.orchestrator import OrchestratorFacade, OrchestratorStateBackend, create_orchestrator
from vibrant.orchestrator.tasks.models import TaskReviewDecision, TaskRunStatus
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


def _prepare_project(tmp_path: Path) -> tuple[Path, OrchestratorStateBackend]:
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
            context="## Objectives\nShip the lifecycle runner.\n\n## Getting Started\nReview the roadmap and implement one task at a time.",
        ),
    )
    RoadmapParser().write(repo / ".vibrant" / "roadmap.md", RoadmapParser().parse(ROADMAP_TEXT))

    engine = OrchestratorStateBackend.load(repo)
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
        self.provider_thread_id = f"thread-{self.agent_record.identity.task_id}-{len(self.start_thread_calls)}"
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
            _git(self.cwd, "commit", "-m", f"[vibrant:{self.agent_record.identity.task_id}] implement")
            await self._emit({"type": "content.delta", "delta": f"Implemented {self.agent_record.identity.task_id}."})
            await self._emit({"type": "turn.completed", "turn": {"id": f"turn-{self.agent_record.identity.task_id}"}})
            self.client._process.returncode = 0
        elif scenario == "failure":
            self.client._process.returncode = 1
            await self._emit({"type": "runtime.error", "error": {"message": "simulated failure"}})
        else:
            raise AssertionError(f"Unknown fake scenario: {scenario}")

        return {"turn": {"id": f"turn-{self.agent_record.identity.task_id}"}}

    async def interrupt_turn(self, **kwargs: Any) -> Any:
        return kwargs

    async def respond_to_request(self, request_id: int | str, *, result: Any | None = None, error=None) -> Any:
        return {"request_id": request_id, "result": result, "error": error}

    async def _emit(self, event: dict[str, Any]) -> None:
        if self.on_canonical_event is not None:
            payload = {
                "provider": "codex",
                "agent_id": self.agent_record.identity.agent_id,
                "task_id": self.agent_record.identity.task_id,
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
            elif verdict in {"retry", "retried"}:
                roadmap.tasks[0].status = TaskStatus.QUEUED
                roadmap.tasks[0].prompt = "Retry after gatekeeper review."
                plan_modified = True
            elif verdict in {"escalate", "escalated"}:
                roadmap.tasks[0].retry_count = roadmap.tasks[0].max_retries
                roadmap.tasks[0].status = TaskStatus.ESCALATED
                plan_modified = True
            else:
                roadmap.tasks[0].prompt = "Retry after gatekeeper review."
                plan_modified = True
        elif request.trigger is GatekeeperTrigger.TASK_FAILURE:
            verdict = "retry"
            roadmap.tasks[0].prompt = self.failure_prompts.pop(0) if self.failure_prompts else "Retry with adjustments."
            plan_modified = True
        elif request.trigger is GatekeeperTrigger.MAX_RETRIES_EXCEEDED:
            verdict = "escalate"
            questions = [self.escalation_question]
        elif request.trigger in {GatekeeperTrigger.PROJECT_START, GatekeeperTrigger.USER_CONVERSATION}:
            verdict = "planned"
            consensus.status = ConsensusStatus.PLANNING
            consensus.context = f"## Objectives\n{request.trigger_description}"
            roadmap = RoadmapParser().parse(ROADMAP_TEXT)
            plan_modified = True

        ConsensusWriter().write(consensus_path, consensus)
        RoadmapParser().write(roadmap_path, roadmap)

        transcript = f"Verdict: {verdict}"
        suggested_decision = "needs_input" if questions else verdict
        role_result = GatekeeperRoleResult(
            succeeded=not questions,
            awaiting_input=bool(questions),
            summary=transcript,
            pending_questions=list(questions),
            suggested_decision=suggested_decision,
        )
        gatekeeper_record = AgentRunRecord(
            identity={
                "agent_id": f"gatekeeper-{request.trigger.value}-test",
                "task_id": f"gatekeeper-{request.trigger.value}",
                "role": "gatekeeper",
            },
            lifecycle={
                "status": AgentStatus.AWAITING_INPUT if questions else AgentStatus.COMPLETED,
            },
            outcome={"summary": transcript, "role_result": serialize_role_result(role_result)},
            provider=AgentProviderMetadata(
                provider_thread_id="thread-gatekeeper-1",
                resume_cursor={"threadId": "thread-gatekeeper-1"},
            ),
        )
        return GatekeeperRunResult(
            agent_record=gatekeeper_record,
            state=RunState.AWAITING_INPUT if questions else RunState.COMPLETED,
            transcript=transcript,
            summary=transcript,
            error=None,
            input_requests=[
                InputRequest(
                    request_id="req-escalation",
                    request_kind="user-input",
                    message=question,
                )
                for question in questions
            ],
            turn_result={"turn": {"id": "gatekeeper-turn-1"}},
            role_result=role_result,
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
    role: str = "code",
    status: AgentStatus = AgentStatus.RUNNING,
) -> AgentRunRecord:
    return AgentRunRecord(
        identity={"agent_id": agent_id, "task_id": task_id, "role": role},
        lifecycle={"status": status},
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

    lifecycle = create_orchestrator(repo, state_backend=engine, gatekeeper=gatekeeper, adapter_factory=FakeCodeAgentAdapter)
    result = await lifecycle.run_next_task()

    assert result is not None
    assert result.outcome == "accepted"
    assert result.task_status is TaskStatus.ACCEPTED
    assert (repo / "app.txt").read_text(encoding="utf-8") == "feature\n"
    assert not lifecycle.git_manager.worktree_path("task-001").exists()
    assert gatekeeper.requests[0].trigger is GatekeeperTrigger.TASK_COMPLETION
    assert "Write focused tests before broader validation." in FakeCodeAgentAdapter.prompts[0]

    roadmap = RoadmapParser().parse_file(repo / ".vibrant" / "roadmap.md")
    assert roadmap.tasks[0].status is TaskStatus.ACCEPTED

    agent_files = sorted((repo / ".vibrant" / "agent-runs").glob("run-agent-task-001-*.json"))
    assert len(agent_files) == 1


@pytest.mark.asyncio
async def test_task_execution_uses_task_agent_role_for_run_creation(tmp_path):
    repo, engine = _prepare_project(tmp_path)
    roadmap_path = repo / ".vibrant" / "roadmap.md"
    roadmap = RoadmapParser().parse_file(roadmap_path)
    roadmap.tasks[0].agent_role = "test"
    RoadmapParser().write(roadmap_path, roadmap)

    FakeCodeAgentAdapter.instances.clear()
    FakeCodeAgentAdapter.prompts.clear()
    FakeCodeAgentAdapter.scenarios = ["success"]
    FakeCodeAgentAdapter.success_contents = ["feature\n"]

    lifecycle = create_orchestrator(
        repo,
        state_backend=engine,
        gatekeeper=FakeGatekeeper(repo),
        adapter_factory=FakeCodeAgentAdapter,
    )
    result = await lifecycle.run_next_task()

    assert result is not None
    assert result.agent_record is not None
    assert result.agent_record.identity.role == "test"
    assert result.agent_record.provider.runtime_mode == "read-only"
    assert result.role_result is not None
    assert result.role_result.role == "test"
    agent_files = sorted((repo / ".vibrant" / "agent-runs").glob("run-test-task-001-*.json"))
    assert len(agent_files) == 1
    record = AgentRunRecord.model_validate_json(agent_files[0].read_text(encoding="utf-8"))
    assert record.lifecycle.status is record.lifecycle.status.COMPLETED
    assert record.context.branch == "vibrant/task-001"
    assert record.outcome.summary == "Implemented task-001."
    assert record.context.prompt_used is not None and "Change `app.txt`" in record.context.prompt_used
    assert record.context.skills_loaded == ["testing-strategy"]
    assert record.lifecycle.pid is not None
    assert record.provider.provider_thread_id == "thread-task-001-1"
    assert record.provider.runtime_mode == "read-only"
    assert record.provider.native_event_log is not None
    assert record.provider.canonical_event_log is not None
    assert lifecycle.state_backend.state.total_agent_spawns == 2
    assert lifecycle.state_backend.state.active_agents == []
    assert lifecycle.state_backend.state.status is OrchestratorStatus.COMPLETED


@pytest.mark.asyncio
async def test_task_history_is_persisted_in_orchestrator_state(tmp_path):
    repo, engine = _prepare_project(tmp_path)
    FakeCodeAgentAdapter.instances.clear()
    FakeCodeAgentAdapter.prompts.clear()
    FakeCodeAgentAdapter.scenarios = ["success"]
    FakeCodeAgentAdapter.success_contents = ["feature\n"]
    gatekeeper = FakeGatekeeper(repo)

    lifecycle = create_orchestrator(repo, state_backend=engine, gatekeeper=gatekeeper, adapter_factory=FakeCodeAgentAdapter)

    result = await lifecycle.run_next_task()

    assert result is not None
    task_state = lifecycle.state_backend.state.tasks["task-001"]
    assert task_state.status is TaskStatus.ACCEPTED
    assert len(task_state.runs) == 1
    assert task_state.runs[0].status is TaskRunStatus.SUCCEEDED
    assert len(task_state.reviews) == 1
    assert task_state.reviews[0].decision is TaskReviewDecision.ACCEPTED

    reloaded = OrchestratorStateBackend.load(repo)
    reloaded_task_state = reloaded.state.tasks["task-001"]
    assert reloaded_task_state.status is TaskStatus.ACCEPTED
    assert reloaded_task_state.runs[0].status is TaskRunStatus.SUCCEEDED
    assert reloaded_task_state.reviews[0].decision is TaskReviewDecision.ACCEPTED


@pytest.mark.asyncio
async def test_submit_gatekeeper_message_routes_initial_prompt_to_project_start(tmp_path):
    repo = _init_repo(tmp_path)
    initialize_project(repo)
    gatekeeper = FakeGatekeeper(repo)

    lifecycle = create_orchestrator(repo, gatekeeper=gatekeeper, adapter_factory=FakeCodeAgentAdapter)
    result = await lifecycle.submit_gatekeeper_message("Build an auth MVP for the app.")

    assert result.state is RunState.COMPLETED
    assert gatekeeper.requests[0].trigger is GatekeeperTrigger.PROJECT_START
    assert lifecycle.state_backend.state.status is OrchestratorStatus.PLANNING

    roadmap = RoadmapParser().parse_file(repo / ".vibrant" / "roadmap.md")
    assert roadmap.tasks
    assert roadmap.tasks[0].title == "Update the tracked file"


@pytest.mark.asyncio
async def test_start_gatekeeper_message_returns_handle_before_result_is_applied(tmp_path):
    repo = _init_repo(tmp_path)
    initialize_project(repo)
    gatekeeper = AsyncFakeGatekeeper(repo)

    lifecycle = create_orchestrator(repo, gatekeeper=gatekeeper, adapter_factory=FakeCodeAgentAdapter)
    handle = await lifecycle.start_gatekeeper_message("Build an auth MVP for the app.")

    assert handle.done() is False
    assert gatekeeper.requests[0].trigger is GatekeeperTrigger.PROJECT_START

    result = await handle.wait()
    await asyncio.sleep(0)

    assert result.state is RunState.COMPLETED
    assert lifecycle.state_backend.state.status is OrchestratorStatus.PLANNING


@pytest.mark.asyncio
async def test_real_gatekeeper_runs_through_shared_agent_manager(tmp_path):
    repo = _init_repo(tmp_path)
    initialize_project(repo)
    gatekeeper = Gatekeeper(repo, adapter_factory=ManagedGatekeeperAdapter, timeout_seconds=1)

    lifecycle = create_orchestrator(repo, gatekeeper=gatekeeper, adapter_factory=FakeCodeAgentAdapter)
    handle = await lifecycle.start_gatekeeper_message("Build an auth MVP for the app.")

    for _ in range(50):
        active = lifecycle.agent_manager.list_instance_snapshots(role="gatekeeper", include_completed=False)
        if active and active[0].runtime.awaiting_input:
            break
        await asyncio.sleep(0)
    else:
        raise AssertionError("Managed Gatekeeper run never surfaced through agent_manager")

    snapshot = lifecycle.agent_manager.get_instance_snapshot(handle.agent_record.identity.agent_id)
    assert snapshot is not None
    assert snapshot.identity.role == "gatekeeper"
    assert snapshot.runtime.has_handle is True
    assert snapshot.runtime.awaiting_input is True
    assert snapshot.runtime.input_requests[0].request_id == "req-1"

    updated = await lifecycle.agent_manager.respond_to_instance_request(
        handle.agent_record.identity.agent_id,
        "req-1",
        result={"answer": "Use OAuth first."},
    )
    assert updated.identity.agent_id == handle.agent_record.identity.agent_id

    result = await handle.wait()
    await asyncio.sleep(0)

    assert result.state is RunState.COMPLETED
    assert lifecycle.agent_manager.get_handle(handle.agent_record.identity.agent_id) is None
    completed = lifecycle.agent_manager.get_instance_snapshot(handle.agent_record.identity.agent_id)
    assert completed is not None
    assert completed.runtime.has_handle is False
    assert completed.runtime.done is True
    assert completed.outcome.summary == "Recorded the user decision."


def test_agent_store_rebuilds_state_without_engine_agent_cache(tmp_path):
    repo, engine = _prepare_project(tmp_path)
    lifecycle = create_orchestrator(
        repo,
        state_backend=engine,
        gatekeeper=FakeGatekeeper(repo),
        adapter_factory=FakeCodeAgentAdapter,
    )

    record = _agent_record("agent-task-standalone", status=AgentStatus.RUNNING)
    lifecycle.agent_store.upsert(record)

    assert not hasattr(lifecycle.state_backend, "agents")
    assert [item.identity.agent_id for item in lifecycle.state_store.agent_records()] == ["agent-task-standalone"]
    assert lifecycle.state_store.state.active_agents == ["agent-task-standalone"]

    facade = OrchestratorFacade(lifecycle)
    assert [item.identity.agent_id for item in facade.runs.list()] == ["agent-task-standalone"]


def test_facade_transition_to_planning_tolerates_consensus_sync_promoting_state(tmp_path):
    repo = _init_repo(tmp_path)
    initialize_project(repo)

    lifecycle = create_orchestrator(repo, gatekeeper=FakeGatekeeper(repo), adapter_factory=FakeCodeAgentAdapter)
    facade = OrchestratorFacade(lifecycle)

    assert lifecycle.state_backend.state.status is OrchestratorStatus.INIT

    facade.transition_workflow_state(OrchestratorStatus.PLANNING)

    assert lifecycle.state_backend.state.status is OrchestratorStatus.PLANNING


def test_lifecycle_passes_canonical_callback_to_default_gatekeeper(tmp_path, monkeypatch):
    repo, engine = _prepare_project(tmp_path)
    captured: dict[str, object] = {}
    callback = object()

    class CapturingGatekeeper:
        def __init__(self, project_root: Path, *, on_canonical_event=None, **kwargs: Any) -> None:
            captured["project_root"] = project_root
            captured["callback"] = on_canonical_event
            captured["kwargs"] = kwargs

    monkeypatch.setattr("vibrant.orchestrator.bootstrap.Gatekeeper", CapturingGatekeeper)

    lifecycle = create_orchestrator(
        repo,
        state_backend=engine,
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

    lifecycle = create_orchestrator(repo, state_backend=engine, gatekeeper=gatekeeper, adapter_factory=FakeCodeAgentAdapter)

    first = await lifecycle.run_next_task()
    second = await lifecycle.run_next_task()

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

    lifecycle = create_orchestrator(repo, state_backend=engine, gatekeeper=gatekeeper, adapter_factory=FakeCodeAgentAdapter)
    assert lifecycle.dispatcher is not None
    lifecycle.dispatcher.get_task("task-001").max_retries = 0

    result = await lifecycle.run_next_task()

    assert result is not None
    assert result.outcome == "escalated"
    assert result.task_status is TaskStatus.ESCALATED
    assert gatekeeper.requests[0].trigger is GatekeeperTrigger.MAX_RETRIES_EXCEEDED
    assert (repo / "app.txt").read_text(encoding="utf-8") == "base\n"

    roadmap = RoadmapParser().parse_file(repo / ".vibrant" / "roadmap.md")
    assert roadmap.tasks[0].status is TaskStatus.ESCALATED
    assert lifecycle.state_backend.state.pending_questions == [gatekeeper.escalation_question]
    assert await lifecycle.run_next_task() is None


@pytest.mark.asyncio
async def test_code_agent_lifecycle_honors_gatekeeper_retry_review_transition(tmp_path):
    repo, engine = _prepare_project(tmp_path)
    FakeCodeAgentAdapter.instances.clear()
    FakeCodeAgentAdapter.prompts.clear()
    FakeCodeAgentAdapter.scenarios = ["success"]
    FakeCodeAgentAdapter.success_contents = ["feature needs follow-up\n"]

    class DurableRetryGatekeeper(FakeGatekeeper):
        async def run(self, request: GatekeeperRequest, *, resume_latest_thread: bool | None = None) -> GatekeeperRunResult:
            result = await super().run(request, resume_latest_thread=resume_latest_thread)
            task_state = engine.state.tasks.get("task-001")
            assert task_state is not None
            task_state.status = TaskStatus.QUEUED
            task_state.failure_reason = "Retry after gatekeeper review."
            engine.persist_state()
            return result

    gatekeeper = DurableRetryGatekeeper(repo)
    gatekeeper.completion_verdicts = ["retry"]

    lifecycle = create_orchestrator(repo, state_backend=engine, gatekeeper=gatekeeper, adapter_factory=FakeCodeAgentAdapter)

    result = await lifecycle.run_next_task()

    assert result is not None
    assert result.outcome == "retried"
    assert (repo / "app.txt").read_text(encoding="utf-8") == "base\n"
    assert not lifecycle.git_manager.worktree_path("task-001").exists()


@pytest.mark.asyncio
async def test_code_agent_lifecycle_honors_gatekeeper_escalated_review_transition(tmp_path):
    repo, engine = _prepare_project(tmp_path)
    FakeCodeAgentAdapter.instances.clear()
    FakeCodeAgentAdapter.prompts.clear()
    FakeCodeAgentAdapter.scenarios = ["success"]
    FakeCodeAgentAdapter.success_contents = ["feature needs escalation\n"]

    class DurableEscalationGatekeeper(FakeGatekeeper):
        async def run(self, request: GatekeeperRequest, *, resume_latest_thread: bool | None = None) -> GatekeeperRunResult:
            result = await super().run(request, resume_latest_thread=resume_latest_thread)
            task_state = engine.state.tasks.get("task-001")
            assert task_state is not None
            task_state.retry_count = task_state.max_retries
            task_state.status = TaskStatus.ESCALATED
            task_state.failure_reason = "Gatekeeper escalated the task"
            engine.persist_state()
            return result

    gatekeeper = DurableEscalationGatekeeper(repo)
    gatekeeper.completion_verdicts = ["escalated"]

    lifecycle = create_orchestrator(repo, state_backend=engine, gatekeeper=gatekeeper, adapter_factory=FakeCodeAgentAdapter)

    result = await lifecycle.run_next_task()

    assert result is not None
    assert result.outcome == "escalated"
    assert (repo / "app.txt").read_text(encoding="utf-8") == "base\n"
    assert not lifecycle.git_manager.worktree_path("task-001").exists()


@pytest.mark.asyncio
async def test_task_execution_service_starts_with_handle_snapshot(tmp_path):
    repo, engine = _prepare_project(tmp_path)
    FakeCodeAgentAdapter.instances.clear()
    FakeCodeAgentAdapter.prompts.clear()
    FakeCodeAgentAdapter.scenarios = ["success"]
    FakeCodeAgentAdapter.success_contents = ["feature via handle\n"]
    gatekeeper = FakeGatekeeper(repo)

    lifecycle = create_orchestrator(repo, state_backend=engine, gatekeeper=gatekeeper, adapter_factory=FakeCodeAgentAdapter)
    assert lifecycle.dispatcher is not None

    lifecycle.workflow_service.begin_execution_if_needed()
    task = lifecycle.dispatcher.dispatch_next_task()
    assert task is not None

    attempt = await lifecycle.agent_manager.start_task(task)
    snapshots = lifecycle.agent_manager.list_handle_snapshots()

    assert len(snapshots) == 1
    assert snapshots[0].identity.agent_id == attempt.agent_record.identity.agent_id
    assert snapshots[0].identity.task_id == task.id
    assert snapshots[0].identity.role == "code"

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

    lifecycle = create_orchestrator(repo, state_backend=engine, gatekeeper=gatekeeper, adapter_factory=FakeCodeAgentAdapter)
    assert lifecycle.dispatcher is not None

    lifecycle.workflow_service.begin_execution_if_needed()
    task = lifecycle.dispatcher.dispatch_next_task()
    assert task is not None

    attempt = await lifecycle.agent_manager.start_task(task)

    active = lifecycle.agent_manager.list_active_instance_snapshots()
    assert len(active) == 1
    assert active[0].identity.agent_id == attempt.agent_record.identity.agent_id
    assert active[0].identity.task_id == task.id
    assert active[0].identity.role == "code"
    assert active[0].runtime.has_handle is True
    assert active[0].runtime.active is True
    assert active[0].provider.thread_id == "thread-task-001-1"

    result = await lifecycle.agent_manager.wait_for_task(attempt)

    assert result.outcome == "accepted"

    snapshot = lifecycle.agent_manager.get_instance_snapshot(attempt.agent_record.identity.agent_id)
    assert snapshot is not None
    assert snapshot.identity.agent_id == attempt.agent_record.identity.agent_id
    assert snapshot.runtime.has_handle is False
    assert snapshot.runtime.active is False
    assert snapshot.runtime.done is True
    assert snapshot.runtime.status == "completed"
    assert snapshot.runtime.state == "completed"
    assert snapshot.outcome.summary == "Implemented task-001."
    assert snapshot.provider.thread_id == "thread-task-001-1"

    latest = lifecycle.agent_manager.latest_instance_snapshot_for_task(task.id)
    assert latest is not None
    assert latest.identity.agent_id == attempt.agent_record.identity.agent_id


def test_agent_management_service_keeps_persisted_awaiting_input_agents_active(tmp_path):
    repo, engine = _prepare_project(tmp_path)
    lifecycle = create_orchestrator(
        repo,
        state_backend=engine,
        gatekeeper=FakeGatekeeper(repo),
        adapter_factory=FakeCodeAgentAdapter,
    )

    record = _agent_record(
        "agent-task-001-awaiting",
        status=AgentStatus.AWAITING_INPUT,
    )
    record.provider.provider_thread_id = "thread-task-001-persisted"
    record.provider.resume_cursor = {"threadId": "thread-task-001-persisted"}
    lifecycle.agent_registry.upsert(record, increment_spawn=True)

    active = lifecycle.agent_manager.list_active_instance_snapshots()

    assert [snapshot.identity.agent_id for snapshot in active] == [record.identity.agent_id]
    assert active[0].runtime.has_handle is False
    assert active[0].runtime.active is True
    assert active[0].runtime.awaiting_input is True


@pytest.mark.asyncio
async def test_agent_management_service_exposes_managed_agent_instance_lifecycle(tmp_path):
    repo, engine = _prepare_project(tmp_path)
    FakeCodeAgentAdapter.instances.clear()
    FakeCodeAgentAdapter.prompts.clear()
    FakeCodeAgentAdapter.scenarios = ["success"]
    FakeCodeAgentAdapter.success_contents = ["instance-managed feature\n"]

    lifecycle = create_orchestrator(
        repo,
        state_backend=engine,
        gatekeeper=FakeGatekeeper(repo),
        adapter_factory=FakeCodeAgentAdapter,
    )
    assert lifecycle.dispatcher is not None

    lifecycle.workflow_service.begin_execution_if_needed()
    task = lifecycle.dispatcher.dispatch_next_task()
    assert task is not None

    attempt = await lifecycle.agent_manager.start_task(task)
    instance = lifecycle.agent_manager.get_instance(attempt.agent_record.identity.agent_id)

    assert attempt.agent.agent_id == attempt.agent_record.identity.agent_id
    assert instance is not None
    assert instance.agent_id == attempt.agent_record.identity.agent_id
    assert instance.role == "code"
    assert instance.scope_type == "task"
    assert instance.scope_id == task.id
    assert instance.record.latest_run_id == attempt.agent_record.identity.run_id
    assert instance.latest_run() is not None
    assert instance.latest_run().identity.run_id == attempt.agent_record.identity.run_id
    assert instance.snapshot().identity.run_id == attempt.agent_record.identity.run_id

    result = await lifecycle.agent_manager.wait_for_task(attempt)

    assert result.outcome == "accepted"
    assert instance.latest_run() is not None
    assert instance.latest_run().identity.run_id == attempt.agent_record.identity.run_id
    assert instance.active_run() is None
    assert instance.snapshot().runtime.done is True


def test_agent_management_service_tracks_multiple_runs_on_one_instance(tmp_path):
    repo, engine = _prepare_project(tmp_path)
    lifecycle = create_orchestrator(
        repo,
        state_backend=engine,
        gatekeeper=FakeGatekeeper(repo),
        adapter_factory=FakeCodeAgentAdapter,
    )

    instance = lifecycle.agent_manager.resolve_instance(
        role="code",
        scope_type="task",
        scope_id="task-001",
    )
    first_run = instance.create_run_record(
        task_id="task-001",
        branch="vibrant/task-001",
        worktree_path=str(repo),
        prompt="first run",
    )
    first_run.lifecycle.status = AgentStatus.FAILED
    lifecycle.agent_registry.upsert(first_run, increment_spawn=True)

    second_run = instance.create_run_record(
        task_id="task-001",
        branch="vibrant/task-001",
        worktree_path=str(repo),
        prompt="second run",
    )
    second_run.lifecycle.status = AgentStatus.COMPLETED
    lifecycle.agent_registry.upsert(second_run, increment_spawn=True)

    instances = lifecycle.agent_manager.list_instances(task_id="task-001")

    assert len(instances) == 1
    assert instances[0].agent_id == instance.agent_id
    assert instances[0].record.latest_run_id == second_run.identity.run_id
    assert instances[0].latest_run() is not None
    assert instances[0].latest_run().identity.run_id == second_run.identity.run_id
    assert instances[0].snapshot().identity.run_id == second_run.identity.run_id


def test_facade_exposes_explicit_instance_and_run_aliases(tmp_path):
    repo, engine = _prepare_project(tmp_path)
    lifecycle = create_orchestrator(
        repo,
        state_backend=engine,
        gatekeeper=FakeGatekeeper(repo),
        adapter_factory=FakeCodeAgentAdapter,
    )
    facade = OrchestratorFacade(lifecycle)

    instance = lifecycle.agent_manager.resolve_instance(
        role="code",
        scope_type="task",
        scope_id="task-001",
    )
    run = instance.create_run_record(
        task_id="task-001",
        branch="vibrant/task-001",
        worktree_path=str(repo),
        prompt="alias test",
    )
    run.lifecycle.status = AgentStatus.COMPLETED
    lifecycle.agent_registry.upsert(run, increment_spawn=True)

    agent_snapshot = facade.instances.get(instance.agent_id)
    instances = facade.instances.list(task_id="task-001")
    run_records = facade.runs.list()

    assert agent_snapshot is not None
    assert agent_snapshot.identity.agent_id == instance.agent_id
    assert agent_snapshot.identity.run_id == run.identity.run_id
    assert [snapshot.identity.agent_id for snapshot in instances] == [instance.agent_id]
    assert [record.identity.run_id for record in run_records] == [run.identity.run_id]


def test_agent_management_service_normalizes_string_agent_type_filters(tmp_path):
    repo, engine = _prepare_project(tmp_path)
    lifecycle = create_orchestrator(
        repo,
        state_backend=engine,
        gatekeeper=FakeGatekeeper(repo),
        adapter_factory=FakeCodeAgentAdapter,
    )

    code_record = _agent_record("agent-task-001-code", role="code")
    merge_record = _agent_record("merge-task-001-merge", role="merge")
    lifecycle.agent_registry.upsert(code_record, increment_spawn=True)
    lifecycle.agent_registry.upsert(merge_record, increment_spawn=True)

    snapshots = lifecycle.agent_manager.list_instance_snapshots(role=" CODE ")

    assert [snapshot.identity.agent_id for snapshot in snapshots] == [code_record.identity.agent_id]


def test_agent_management_service_rejects_invalid_string_agent_type_filters(tmp_path):
    repo, engine = _prepare_project(tmp_path)
    lifecycle = create_orchestrator(
        repo,
        state_backend=engine,
        gatekeeper=FakeGatekeeper(repo),
        adapter_factory=FakeCodeAgentAdapter,
    )

    with pytest.raises(ValueError, match="Unsupported agent role filter"):
        lifecycle.agent_manager.list_instance_snapshots(role="bogus")
