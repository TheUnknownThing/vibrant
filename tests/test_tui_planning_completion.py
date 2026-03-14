from types import SimpleNamespace

from vibrant.models import OrchestratorStatus
from vibrant.tui.app import VibrantApp
from vibrant.tui.screens import PlanningScreen, VibingScreen


class _ScreenlessApp(VibrantApp):
    def _mount_workspace(self, workspace) -> None:
        self._workspace_screen = workspace

    def set_class(self, *args, **kwargs) -> None:
        return None

    def refresh_bindings(self) -> None:
        return None

    def call_after_refresh(self, *args, **kwargs) -> None:
        return None


def test_runtime_tool_events_do_not_drive_planning_completion(monkeypatch) -> None:
    app = VibrantApp()
    refreshes: list[str] = []

    monkeypatch.setattr(app, "_refresh_project_views", lambda: refreshes.append("refresh"))

    app._handle_runtime_event(
        {
            "type": "tool.call.completed",
            "tool_name": "vibrant.end_planning_phase",
        }
    )

    assert refreshes == ["refresh"]


def test_workspace_mode_tracks_workflow_status() -> None:
    app = _ScreenlessApp()
    app._orchestrator = object()
    app._orchestrator_facade = SimpleNamespace(get_workflow_status=lambda: OrchestratorStatus.PLANNING)

    app._sync_workspace_screen()
    assert isinstance(app._workspace_screen, PlanningScreen)

    app._orchestrator_facade = SimpleNamespace(get_workflow_status=lambda: OrchestratorStatus.EXECUTING)
    app._sync_workspace_screen()
    assert isinstance(app._workspace_screen, VibingScreen)
