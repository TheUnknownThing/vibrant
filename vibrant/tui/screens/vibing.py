"""Execution workspace screen."""

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


class VibingScreen(Static):
    """Task execution screen shown during vibing."""

    DEFAULT_CSS = """
    VibingScreen {
        height: 1fr;
    }

    VibingScreen #workspace-grid {
        layout: grid;
        grid-size: 2 2;
        grid-columns: 34 1fr;
        grid-rows: 1fr 1fr;
        height: 1fr;
    }

    VibingScreen #planning-hero {
        display: none;
    }
    """

    def compose(self) -> ComposeResult:
        with Grid(id="workspace-grid"):
            yield PlanTree(id="plan-panel")
            yield AgentOutput(id="agent-output-panel")
            yield ConsensusView(id="consensus-panel")
            with Vertical(id="chat-panel-container"):
                yield ThreadList(id="thread-panel")
                yield ChatPanel(id="conversation-panel")
                yield InputBar(id="input-panel")
