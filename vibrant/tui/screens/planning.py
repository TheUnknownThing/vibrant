"""Planning workspace screen."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from ..widgets.chat_panel import ChatPanel
from ..widgets.input_bar import InputBar


class PlanningScreen(Static):
    """Consensus-building screen shown during planning."""

    DEFAULT_CSS = """
    PlanningScreen {
        height: 1fr;
    }

    PlanningScreen #planning-shell {
        height: 1fr;
        border: round $primary-background;
        background: $surface;
    }

    PlanningScreen #planning-hero {
        height: auto;
        padding: 1 2;
        border-bottom: solid $primary-background;
        background: $surface;
    }

    PlanningScreen #conversation-panel {
        height: 1fr;
    }

    PlanningScreen #input-panel {
        height: auto;
        max-height: 8;
        border-top: solid $primary-background;
        padding: 0 1;
        background: $surface;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="planning-shell"):
            yield Static(
                "[b]Consensus Building[/b]\n"
                "Tell the Gatekeeper what you want to build. Type `/vibe` when you are ready to move into execution.",
                id="planning-hero",
                markup=True,
            )
            yield ChatPanel(id="conversation-panel")
            yield InputBar(id="input-panel")

    @property
    def chat_panel(self) -> ChatPanel:
        """Return the planning chat panel."""

        return self.query_one(ChatPanel)

    @property
    def input_bar(self) -> InputBar:
        """Return the planning input bar."""

        return self.query_one(InputBar)

    def focus_primary_input(self) -> None:
        """Focus the planning input."""

        self.input_bar.focus_input()

    def set_input_placeholder(self, text: str) -> None:
        """Update the planning input placeholder."""

        self.input_bar.set_placeholder(text)
