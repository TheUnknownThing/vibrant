"""Execution workspace screen."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Static

from ..widgets.agent_output import AgentOutput
from ..widgets.chat_panel import ChatPanel
from ..widgets.consensus_view import ConsensusView
from ..widgets.input_bar import InputBar
from ..widgets.plan_tree import PlanTree
from ..widgets.task_status import TaskStatusView


class VibingScreen(Static):
    """Task execution screen shown during vibing."""

    DEFAULT_CSS = """
    VibingScreen {
        height: 1fr;
    }

    VibingScreen #vibing-shell {
        height: 1fr;
    }

    VibingScreen #plan-panel {
        width: 34;
        min-width: 24;
        margin-right: 1;
    }

    VibingScreen #vibing-main {
        height: 1fr;
    }

    VibingScreen #workspace-tabs {
        height: auto;
        margin-bottom: 1;
    }

    VibingScreen .workspace-tab {
        width: 1fr;
        margin-right: 1;
    }

    VibingScreen .workspace-tab.-active {
        text-style: bold;
    }

    VibingScreen #workspace-content {
        height: 1fr;
    }

    VibingScreen #task-status-panel,
    VibingScreen #chat-history-panel,
    VibingScreen #consensus-panel,
    VibingScreen #agent-output-panel {
        height: 1fr;
    }

    VibingScreen #chat-roadmap-status {
        height: auto;
        padding: 1 2;
        border: round $primary-background;
        background: $surface;
        margin-bottom: 1;
    }

    VibingScreen #conversation-panel {
        height: 1fr;
    }

    VibingScreen #input-panel {
        height: auto;
        max-height: 8;
        border-top: solid $primary-background;
        padding: 0 1;
        background: $surface;
        margin-top: 1;
    }
    """

    _BUTTON_TO_TAB = {
        "workspace-tab-task-status": "task-status",
        "workspace-tab-chat-history": "chat-history",
        "workspace-tab-consensus": "consensus",
        "workspace-tab-agent-logs": "agent-logs",
    }

    _TAB_TO_PANEL = {
        "task-status": "#task-status-panel",
        "chat-history": "#chat-history-panel",
        "consensus": "#consensus-panel",
        "agent-logs": "#agent-output-panel",
    }

    def __init__(self, *, initial_tab: str = "task-status") -> None:
        super().__init__()
        self._initial_tab = initial_tab
        self._active_tab = initial_tab

    def compose(self) -> ComposeResult:
        with Horizontal(id="vibing-shell"):
            yield PlanTree(id="plan-panel")
            with Vertical(id="vibing-main"):
                with Horizontal(id="workspace-tabs"):
                    yield Button("Task Status", id="workspace-tab-task-status", classes="workspace-tab")
                    yield Button("Chat History", id="workspace-tab-chat-history", classes="workspace-tab")
                    yield Button("Consensus File", id="workspace-tab-consensus", classes="workspace-tab")
                    yield Button("Agent Logs", id="workspace-tab-agent-logs", classes="workspace-tab")
                with Vertical(id="workspace-content"):
                    yield TaskStatusView(id="task-status-panel")
                    with Vertical(id="chat-history-panel"):
                        yield Static("[b]Generating Roadmap[/b]\n\nChat history will appear here once roadmap generation finishes.", id="chat-roadmap-status", markup=True)
                        yield ChatPanel(id="conversation-panel")
                    yield ConsensusView(id="consensus-panel")
                    yield AgentOutput(id="agent-output-panel")
                yield InputBar(id="input-panel")

    def on_mount(self) -> None:
        self.set_active_tab(self._initial_tab)
        self.set_roadmap_loading(True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        tab_id = self._BUTTON_TO_TAB.get(event.button.id or "")
        if tab_id is not None:
            self.set_active_tab(tab_id)

    def set_active_tab(self, tab_id: str) -> None:
        if tab_id not in self._TAB_TO_PANEL:
            return

        self._active_tab = tab_id
        for button_id, candidate_tab in self._BUTTON_TO_TAB.items():
            button = self.query_one(f"#{button_id}", Button)
            button.set_class(candidate_tab == tab_id, "-active")

        for candidate_tab, selector in self._TAB_TO_PANEL.items():
            panel = self.query_one(selector, Static)
            panel.display = candidate_tab == tab_id

    def set_roadmap_loading(self, is_loading: bool) -> None:
        self.query_one(TaskStatusView).set_generating_roadmap(is_loading)
        chat_notice = self.query_one("#chat-roadmap-status", Static)
        chat_panel = self.query_one(ChatPanel)
        chat_notice.display = is_loading
        chat_panel.display = not is_loading
