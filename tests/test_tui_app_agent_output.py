from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from vibrant.config import VibrantConfig, VibrantConfigPatch
from vibrant.models.task import TaskInfo, TaskStatus
from vibrant.orchestrator.types import AttemptStatus
from vibrant.orchestrator.types import (
    AgentStreamEvent,
    ConversationSummary,
    QuestionPriority,
    QuestionStatus,
    QuestionView,
    WorkflowStatus,
)
from vibrant.tui.app import VibrantApp
from vibrant.tui.widgets.input_bar import InputBar
from vibrant.tui.widgets.settings_panel import SettingsUpdate


class _FakeSubscription:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeControlPlane:
    def __init__(
        self,
        *,
        summaries: list[ConversationSummary],
        frames_by_conversation_id: dict[str, list[AgentStreamEvent]],
    ) -> None:
        self._summaries = summaries
        self._frames_by_conversation_id = frames_by_conversation_id
        self.frame_calls: list[str] = []
        self.subscribe_calls: list[tuple[str, bool]] = []

    def list_conversation_summaries(self) -> list[ConversationSummary]:
        return list(self._summaries)

    def conversation_frames(self, conversation_id: str) -> list[AgentStreamEvent]:
        self.frame_calls.append(conversation_id)
        return list(self._frames_by_conversation_id.get(conversation_id, []))

    def subscribe_conversation(self, conversation_id: str, callback, *, replay: bool = False) -> _FakeSubscription:
        self.subscribe_calls.append((conversation_id, replay))
        if replay:
            for frame in self._frames_by_conversation_id.get(conversation_id, []):
                callback(frame)
        return _FakeSubscription()


class _FakeAgentOutput:
    def __init__(self) -> None:
        self.synced_calls: list[tuple[list[str], list[object]]] = []
        self.ingested_events: list[AgentStreamEvent] = []
        self.ingested_batches: list[list[AgentStreamEvent]] = []

    def sync_conversations(self, conversations, agents) -> None:
        self.synced_calls.append(([summary.conversation_id for summary in conversations], list(agents)))

    def ingest_stream_event(self, event: AgentStreamEvent) -> None:
        self.ingested_events.append(event)

    def ingest_stream_events(self, events) -> None:
        self.ingested_batches.append(list(events))


class _FakeChatPanel:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def set_gatekeeper_state(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


class _FakeInputPanel:
    def __init__(self) -> None:
        self.enabled: bool | None = None
        self.context: tuple[str | None, str] | None = None
        self.placeholder: str | None = None

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled

    def set_context(self, model: str | None = None, status: str = "") -> None:
        self.context = (model, status)

    def set_placeholder(self, text: str) -> None:
        self.placeholder = text


def _summary(conversation_id: str) -> ConversationSummary:
    return ConversationSummary(
        conversation_id=conversation_id,
        agent_ids=["agent-1"],
        task_ids=["task-1"],
        latest_run_id="run-1",
        updated_at="2026-03-16T00:00:00Z",
    )


def _event(conversation_id: str, sequence: int) -> AgentStreamEvent:
    return AgentStreamEvent(
        conversation_id=conversation_id,
        entry_id=f"evt-{sequence}",
        source_event_id=None,
        sequence=sequence,
        agent_id="agent-1",
        run_id="run-1",
        task_id="task-1",
        turn_id="turn-1",
        item_id=None,
        type="conversation.assistant.message.completed",
        text=f"message {sequence}",
        payload=None,
        created_at=f"2026-03-16T00:00:0{sequence}Z",
    )


def test_refresh_agent_output_registry_keeps_startup_summary_only() -> None:
    summary = _summary("conv-1")
    control_plane = _FakeControlPlane(
        summaries=[summary],
        frames_by_conversation_id={"conv-1": [_event("conv-1", 1)]},
    )
    agent_output = _FakeAgentOutput()
    app = VibrantApp()
    app.orchestrator = SimpleNamespace(control_plane=control_plane)
    app.vibing_screen = lambda: SimpleNamespace(active_tab="task-status", agent_output=agent_output)

    app._refresh_agent_output_registry(SimpleNamespace(agent_records=[]))

    assert agent_output.synced_calls == [(["conv-1"], [])]
    assert control_plane.frame_calls == []
    assert control_plane.subscribe_calls == []
    assert agent_output.ingested_events == []


def test_refresh_agent_output_registry_hydrates_and_subscribes_when_logs_are_visible() -> None:
    summary = _summary("conv-1")
    frame = _event("conv-1", 1)
    control_plane = _FakeControlPlane(
        summaries=[summary],
        frames_by_conversation_id={"conv-1": [frame]},
    )
    agent_output = _FakeAgentOutput()
    app = VibrantApp()
    app.orchestrator = SimpleNamespace(control_plane=control_plane)
    app.vibing_screen = lambda: SimpleNamespace(active_tab="agent-logs", agent_output=agent_output)

    app._refresh_agent_output_registry(SimpleNamespace(agent_records=[]))
    app._refresh_agent_output_registry(SimpleNamespace(agent_records=[]))

    assert agent_output.synced_calls == [(["conv-1"], []), (["conv-1"], [])]
    assert control_plane.frame_calls == []
    assert control_plane.subscribe_calls == [("conv-1", True)]
    assert agent_output.ingested_batches == [[frame]]
    assert agent_output.ingested_events == []


def test_app_bar_uses_explicit_active_directory_as_subtitle() -> None:
    app = VibrantApp(cwd="/tmp/vibrant-active-dir")

    assert app.sub_title == "/tmp/vibrant-active-dir"


def test_app_bar_falls_back_to_current_directory_as_subtitle(monkeypatch) -> None:
    monkeypatch.setattr("vibrant.tui.app.os.getcwd", lambda: "/tmp/vibrant-cwd")
    monkeypatch.setattr("vibrant.tui.app.Path.home", lambda: Path("/home/tester"))

    app = VibrantApp()

    assert app.sub_title == "/tmp/vibrant-cwd"


def test_agent_logs_visibility_defaults_to_dev_mode() -> None:
    assert VibrantApp(dev_mode=False)._agent_logs_tab_available() is False
    assert VibrantApp(dev_mode=True)._agent_logs_tab_available() is True


def test_agent_logs_visibility_respects_project_override() -> None:
    app = VibrantApp(dev_mode=True)
    app._project_config = VibrantConfig(show_agent_logs=False)

    assert app._agent_logs_tab_available() is False

    app._project_config = VibrantConfig(show_agent_logs=True)

    assert app._agent_logs_tab_available() is True


def test_refresh_gatekeeper_state_uses_app_bar_and_chat_highlight_for_pending_questions(monkeypatch) -> None:
    app = VibrantApp(cwd="/tmp/vibrant-active-dir")
    chat_panel = _FakeChatPanel()
    input_panel = _FakeInputPanel()
    statuses: list[str] = []
    notifications: list[tuple[str, str]] = []
    banners: list[str | None] = []
    question = QuestionView(
        question_id="question-1",
        text="Should we keep the existing layout?",
        priority=QuestionPriority.BLOCKING,
        blocking_scope="workflow",
        status=QuestionStatus.PENDING,
    )

    app.orchestrator_facade = SimpleNamespace(
        get_workflow_status=lambda: WorkflowStatus.EXECUTING,
        gatekeeper_busy=lambda: False,
        get_config=lambda: SimpleNamespace(model="gatekeeper"),
    )
    monkeypatch.setattr(app, "_chat_panel", lambda: chat_panel)
    monkeypatch.setattr(app, "_input_bar", lambda: input_panel)
    monkeypatch.setattr(app, "_list_question_records", lambda: [question])
    monkeypatch.setattr(app, "_set_status", statuses.append)
    monkeypatch.setattr(app, "_set_banner", banners.append)
    monkeypatch.setattr(app, "_notification_bell_enabled", lambda: False)
    monkeypatch.setattr(app, "notify", lambda message, *, severity="information", **kwargs: notifications.append((message, severity)))

    app._refresh_gatekeeper_state(force_flash=True)

    assert app.sub_title == "awaiting user input"
    assert chat_panel.calls == [
        {
            "status": WorkflowStatus.EXECUTING,
            "question_records": [question],
            "flash": True,
        }
    ]
    assert input_panel.enabled is True
    assert input_panel.context == ("gatekeeper", "awaiting user input")
    assert input_panel.placeholder == InputBar.DEFAULT_PLACEHOLDER
    assert banners == [None]
    assert statuses == ["awaiting user input"]
    assert notifications == []


def test_refresh_gatekeeper_state_does_not_flash_existing_questions_on_first_sync(monkeypatch) -> None:
    app = VibrantApp(cwd="/tmp/vibrant-active-dir")
    chat_panel = _FakeChatPanel()
    input_panel = _FakeInputPanel()
    question = QuestionView(
        question_id="question-1",
        text="Should we keep the existing layout?",
        priority=QuestionPriority.BLOCKING,
        blocking_scope="workflow",
        status=QuestionStatus.PENDING,
    )

    app.orchestrator_facade = SimpleNamespace(
        get_workflow_status=lambda: WorkflowStatus.EXECUTING,
        gatekeeper_busy=lambda: False,
        get_config=lambda: SimpleNamespace(model="gatekeeper"),
    )
    monkeypatch.setattr(app, "_chat_panel", lambda: chat_panel)
    monkeypatch.setattr(app, "_input_bar", lambda: input_panel)
    monkeypatch.setattr(app, "_list_question_records", lambda: [question])
    monkeypatch.setattr(app, "_set_status", lambda _: None)
    monkeypatch.setattr(app, "_set_banner", lambda _: None)
    monkeypatch.setattr(app, "_notification_bell_enabled", lambda: False)

    app._refresh_gatekeeper_state()

    assert chat_panel.calls == [
        {
            "status": WorkflowStatus.EXECUTING,
            "question_records": [question],
            "flash": False,
        }
    ]


def test_handle_task_result_awaiting_user_updates_status_without_popup(monkeypatch) -> None:
    app = VibrantApp()
    statuses: list[str] = []
    notifications: list[tuple[str, str]] = []

    monkeypatch.setattr(app, "_set_status", statuses.append)
    monkeypatch.setattr(app, "notify", lambda message, *, severity="information", **kwargs: notifications.append((message, severity)))

    app._handle_task_result(SimpleNamespace(task_id="task-1", outcome="awaiting_user", error=None, worktree_path=None))

    assert statuses == ["awaiting user input"]
    assert notifications == []


def test_handle_runtime_event_skips_project_snapshot_for_task_progress(monkeypatch) -> None:
    app = VibrantApp()
    refreshed_task_ids: list[str | None] = []

    monkeypatch.setattr(
        app,
        "_refresh_selected_task_status_execution",
        lambda *, task_id=None: refreshed_task_ids.append(task_id),
    )
    monkeypatch.setattr(
        app,
        "_project_snapshot",
        lambda: pytest.fail("task.progress should not rebuild the full project snapshot"),
    )

    app._handle_runtime_event({"type": "task.progress", "task_id": "task-7", "agent_id": "agent-7"})

    assert refreshed_task_ids == ["task-7"]


def test_refresh_selected_task_status_execution_ignores_unselected_task() -> None:
    app = VibrantApp()
    refresh_calls: list[str] = []
    task_status = SimpleNamespace(
        selected_task_id="task-1",
        refresh_selected_task_execution=lambda: refresh_calls.append("refresh"),
    )
    app.vibing_screen = lambda: SimpleNamespace(task_status=task_status)

    app._refresh_selected_task_status_execution(task_id="task-2")

    assert refresh_calls == []


@pytest.mark.asyncio
async def test_restart_slash_command_restarts_selected_failed_task(monkeypatch) -> None:
    app = VibrantApp()
    calls: list[str] = []
    statuses: list[str] = []
    notifications: list[tuple[str, str]] = []

    def fake_restart(task_id: str):
        calls.append(task_id)
        return SimpleNamespace(id=task_id)

    app.orchestrator_facade = SimpleNamespace(restart_failed_task=fake_restart)
    app.vibing_screen = lambda: SimpleNamespace(task_status=SimpleNamespace(selected_task_id="task-1"))
    monkeypatch.setattr(app, "_refresh_project_views", lambda: None)
    monkeypatch.setattr(app, "_start_automatic_workflow_if_needed", lambda: None)
    monkeypatch.setattr(app, "_set_status", statuses.append)
    monkeypatch.setattr(app, "notify", lambda message, *, severity="information", **kwargs: notifications.append((message, severity)))

    await app.on_input_bar_slash_command(InputBar.SlashCommand("restart", "", "/restart"))

    assert calls == ["task-1"]
    assert notifications == [("Task task-1 queued for retry.", "information")]
    assert statuses == ["Task task-1 queued for retry"]


@pytest.mark.asyncio
async def test_restart_slash_command_prefers_failed_task_over_selected_pending_task(monkeypatch) -> None:
    app = VibrantApp()
    calls: list[str] = []
    statuses: list[str] = []
    notifications: list[tuple[str, str]] = []
    roadmap = SimpleNamespace(
        tasks=[
            TaskInfo(id="task-1", title="Failed", status=TaskStatus.FAILED, failure_reason="boom"),
            TaskInfo(id="task-2", title="Pending", status=TaskStatus.PENDING),
        ]
    )

    def fake_restart(task_id: str):
        calls.append(task_id)
        return SimpleNamespace(id=task_id)

    app.orchestrator_facade = SimpleNamespace(
        restart_failed_task=fake_restart,
        get_task=lambda task_id: next((task for task in roadmap.tasks if task.id == task_id), None),
        get_roadmap=lambda: roadmap,
        list_attempt_executions=lambda: [
            SimpleNamespace(
                task_id="task-1",
                status=AttemptStatus.FAILED,
                updated_at="2026-03-18T00:00:00Z",
            )
        ],
    )
    app.vibing_screen = lambda: SimpleNamespace(task_status=SimpleNamespace(selected_task_id="task-2"))
    monkeypatch.setattr(app, "_refresh_project_views", lambda: None)
    monkeypatch.setattr(app, "_start_automatic_workflow_if_needed", lambda: None)
    monkeypatch.setattr(app, "_set_status", statuses.append)
    monkeypatch.setattr(app, "notify", lambda message, *, severity="information", **kwargs: notifications.append((message, severity)))

    await app.on_input_bar_slash_command(InputBar.SlashCommand("restart", "", "/restart"))

    assert calls == ["task-1"]
    assert notifications == [("Task task-1 queued for retry.", "information")]
    assert statuses == ["Task task-1 queued for retry"]


@pytest.mark.asyncio
async def test_toggle_pause_pauses_and_resumes_live_policies(monkeypatch) -> None:
    app = VibrantApp()
    statuses: list[str] = []
    notifications: list[tuple[str, str]] = []
    facade_calls: list[tuple[str, str | None]] = []
    workflow_status = WorkflowStatus.EXECUTING

    async def fake_pause_policies(reason: str | None = None):
        nonlocal workflow_status
        facade_calls.append(("pause", reason))
        workflow_status = WorkflowStatus.PAUSED
        return {"gatekeeper": None, "attempts": []}

    async def fake_resume_policies():
        nonlocal workflow_status
        facade_calls.append(("resume", None))
        workflow_status = WorkflowStatus.EXECUTING
        return {"workflow": workflow_status, "gatekeeper": None, "attempt": None}

    app.orchestrator_facade = SimpleNamespace(
        get_workflow_status=lambda: workflow_status,
        pause_policies=fake_pause_policies,
        resume_policies=fake_resume_policies,
    )
    monkeypatch.setattr(app, "_refresh_project_views", lambda: None)
    monkeypatch.setattr(app, "_start_automatic_workflow_if_needed", lambda: facade_calls.append(("auto", None)))
    monkeypatch.setattr(app, "_set_status", statuses.append)
    monkeypatch.setattr(app, "notify", lambda message, *, severity="information", **kwargs: notifications.append((message, severity)))

    await app.action_toggle_pause()
    await app.action_toggle_pause()

    assert facade_calls == [("pause", "user_paused"), ("resume", None), ("auto", None)]
    assert statuses == ["Workflow paused", "Workflow resumed (executing)"]
    assert notifications == [
        ("Workflow paused.", "information"),
        ("Workflow resumed (executing).", "information"),
    ]


@pytest.mark.asyncio
async def test_toggle_pause_restarts_automatic_workflow_after_resuming_attempt(monkeypatch) -> None:
    app = VibrantApp()
    statuses: list[str] = []
    notifications: list[tuple[str, str]] = []
    facade_calls: list[tuple[str, str | None]] = []
    workflow_status = WorkflowStatus.PAUSED
    app._paused_return_status = WorkflowStatus.EXECUTING

    async def fake_resume_policies():
        nonlocal workflow_status
        facade_calls.append(("resume", None))
        workflow_status = WorkflowStatus.EXECUTING
        return {"workflow": workflow_status, "gatekeeper": None, "attempt": SimpleNamespace(attempt_id="attempt-1")}

    app.orchestrator_facade = SimpleNamespace(
        get_workflow_status=lambda: workflow_status,
        resume_policies=fake_resume_policies,
    )
    monkeypatch.setattr(app, "_refresh_project_views", lambda: None)
    monkeypatch.setattr(app, "_start_automatic_workflow_if_needed", lambda: facade_calls.append(("auto", None)))
    monkeypatch.setattr(app, "_set_status", statuses.append)
    monkeypatch.setattr(app, "notify", lambda message, *, severity="information", **kwargs: notifications.append((message, severity)))

    await app.action_toggle_pause()

    assert facade_calls == [("resume", None), ("auto", None)]
    assert statuses == ["Workflow resumed (executing)"]
    assert notifications == [("Workflow resumed (executing).", "information")]


@pytest.mark.asyncio
async def test_model_slash_command_updates_orchestrator_config(monkeypatch) -> None:
    app = VibrantApp()
    patches: list[VibrantConfigPatch] = []
    statuses: list[str] = []
    reloads: list[bool] = []

    app.orchestrator_facade = SimpleNamespace(
        update_config=lambda patch: patches.append(patch) or SimpleNamespace(model=patch.model),
        get_config=lambda: SimpleNamespace(model="gpt-5.3-codex"),
    )
    monkeypatch.setattr(app, "_reload_active_project", lambda: reloads.append(True) or True)
    monkeypatch.setattr(app, "_set_status", statuses.append)

    await app.on_input_bar_slash_command(InputBar.SlashCommand("model", "gpt-5.4-codex", "/model gpt-5.4-codex"))

    assert [patch.model for patch in patches] == ["gpt-5.4-codex"]
    assert reloads == [True]
    assert statuses == ["Model set to gpt-5.4-codex"]


def test_settings_dismissed_updates_config_via_orchestrator_facade(monkeypatch) -> None:
    app = VibrantApp()
    patches: list[VibrantConfigPatch] = []
    statuses: list[str] = []
    reloads: list[bool] = []

    app.orchestrator_facade = SimpleNamespace(update_config=lambda patch: patches.append(patch))
    monkeypatch.setattr(app, "_reload_active_project", lambda: reloads.append(True) or True)
    monkeypatch.setattr(app, "_set_status", statuses.append)

    app._handle_settings_dismissed(
        SettingsUpdate(
            working_directory=None,
            config_patch=VibrantConfigPatch(
                model="gpt-5.4-codex",
                approval_policy="on-request",
                reasoning_effort="high",
            ),
        )
    )

    assert len(patches) == 1
    assert patches[0].model == "gpt-5.4-codex"
    assert patches[0].approval_policy == "on-request"
    assert patches[0].reasoning_effort == "high"
    assert reloads == [True]
    assert statuses == ["Settings updated"]
