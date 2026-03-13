from __future__ import annotations

from pathlib import Path

from vibrant.models import ThreadInfo
from vibrant.tui.app import VibrantApp
from vibrant.tui.widgets.chat_panel import ChatPanel, ChatPanelState
from vibrant.tui.widgets.conversation_renderer import MessageBlock, TextPart


def test_gatekeeper_history_match_requires_cwd_for_synthetic_thread(tmp_path: Path) -> None:
    app = VibrantApp()
    app._project_root = tmp_path.resolve()

    thread_without_cwd = ThreadInfo(id=ChatPanel.GATEKEEPER_THREAD_ID, model="gatekeeper")
    assert app._gatekeeper_history_matches_project(thread_without_cwd) is False

    thread_with_matching_cwd = ThreadInfo(
        id=ChatPanel.GATEKEEPER_THREAD_ID,
        model="gatekeeper",
        cwd=str(tmp_path.resolve()),
    )
    assert app._gatekeeper_history_matches_project(thread_with_matching_cwd) is True


def test_chat_panel_export_thread_uses_provider_thread_id_when_available() -> None:
    panel = ChatPanel()
    panel._state = ChatPanelState(
        provider_thread_id="provider-thread-123",
        messages=[MessageBlock(message_id="m1", role="user", parts=[TextPart("hello")])],
    )

    exported = panel.export_thread()

    assert exported is not None
    assert exported.id == "provider-thread-123"
    assert exported.codex_thread_id == "provider-thread-123"
