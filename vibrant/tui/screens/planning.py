"""Planning workspace screen."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Grid, Vertical
from textual.widgets import Static

from ..widgets.agent_output import AgentOutput
from ..widgets.chat_panel import ChatPanel
from ..widgets.consensus_view import ConsensusView
from ..widgets.input_bar import InputBar
from ..widgets.plan_tree import PlanTree
from ..widgets.thread_list import ThreadList


class PlanningScreen(Static):
    """Consensus-building screen shown during planning."""

    DEFAULT_CSS = """
    PlanningScreen {
        height: 1fr;
    }

    PlanningScreen #workspace-grid {
        layout: grid;
        grid-size: 1 1;
        grid-columns: 1fr;
        grid-rows: 1fr;
        height: 1fr;
    }

    PlanningScreen #plan-panel,
    PlanningScreen #agent-output-panel,
    PlanningScreen #consensus-panel,
    PlanningScreen #thread-panel {
        display: none;
    }

    PlanningScreen #planning-hero {
        display: block;
    }
    """

    def compose(self) -> ComposeResult:
        with Grid(id="workspace-grid"):
            yield PlanTree(id="plan-panel")
            yield AgentOutput(id="agent-output-panel")
            yield ConsensusView(id="consensus-panel")
            with Vertical(id="chat-panel-container"):
                yield Static(
                    "[b]Consensus Building[/b]\n"
                    "Tell the Gatekeeper what you want to build. Planning stays open until the Gatekeeper ends it.",
                    id="planning-hero",
                    markup=True,
                )
                yield ThreadList(id="thread-panel")
                yield ChatPanel(id="conversation-panel")
                yield InputBar(id="input-panel")
