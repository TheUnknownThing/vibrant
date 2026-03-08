"""Consensus panel placeholder for the 4-panel Vibrant layout."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Static


class ConsensusView(Static):
    """Placeholder for Panel C until the consensus summary lands."""

    DEFAULT_CSS = """
    ConsensusView {
        border: round $primary-background;
        background: $surface;
        padding: 0;
    }

    #consensus-header {
        height: 3;
        padding: 1;
        background: $primary-background;
        color: $text;
        text-align: center;
    }

    #consensus-body {
        height: 1fr;
        padding: 1 2;
        color: $text-muted;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("[b]Consensus[/b]", id="consensus-header", markup=True)
        yield Static("Phase 6.3 will summarize consensus and pending questions here.", id="consensus-body")
