"""Agent output panel placeholder for the 4-panel Vibrant layout."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Static


class AgentOutput(Static):
    """Placeholder for Panel B until the streaming view lands."""

    DEFAULT_CSS = """
    AgentOutput {
        border: round $primary-background;
        background: $surface;
        padding: 0;
    }

    #agent-output-header {
        height: 3;
        padding: 1;
        background: $primary-background;
        color: $text;
        text-align: center;
    }

    #agent-output-body {
        height: 1fr;
        padding: 1 2;
        color: $text-muted;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("[b]Agent Output[/b]", id="agent-output-header", markup=True)
        yield Static("Phase 6.2 will stream canonical agent events here.", id="agent-output-body")
