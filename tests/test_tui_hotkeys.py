from __future__ import annotations

from vibrant.tui.app import VibrantApp
from vibrant.tui.widgets.agent_output import AgentOutput


def test_vibrant_app_hotkeys_match_tui_guide() -> None:
    bindings = {binding.key: binding for binding in VibrantApp.BINDINGS}

    assert bindings["f1"].action == "open_help"
    assert bindings["f2"].action == "toggle_pause"
    assert bindings["f5"].action == "show_task_status"
    assert bindings["f6"].action == "show_chat_history"
    assert bindings["f7"].action == "toggle_consensus"
    assert bindings["f8"].action == "show_agent_logs"
    assert bindings["f10"].action == "quit_app"
    assert "f3" not in bindings
    assert "f4" not in bindings


def test_agent_output_no_longer_overrides_app_f5_binding() -> None:
    bindings = {binding.key: binding for binding in AgentOutput.BINDINGS}

    assert "f5" not in bindings
    assert bindings["tab"].action == "cycle_agent"
