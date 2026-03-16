"""Planning workspace screen."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Static

from ..widgets.chat_panel import ChatPanel
from ..widgets.consensus_view import ConsensusView
from ..widgets.input_bar import InputBar


class PlanningScreen(Static):
    """Consensus-building screen shown during planning."""

    DEFAULT_CSS = """
    PlanningScreen {
        height: 1fr;
    }

    PlanningScreen #planning-layout {
        height: 1fr;
    }

    PlanningScreen #planning-consensus-panel {
        width: 1fr;
        min-width: 48;
        margin-right: 1;
        display: none;
    }

    PlanningScreen #planning-shell {
        width: 1fr;
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
        border-top: solid $primary-background;
        padding: 0 1;
        background: $surface;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._consensus_visible = False
        self._consensus_auto_revealed = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="planning-layout"):
            yield ConsensusView(id="planning-consensus-panel")
            with Vertical(id="planning-shell"):
                yield Static(
                    "[b]Consensus Building[/b]\n"
                    "Tell the Gatekeeper what you want to build. When planning is ready, the orchestrator will move into execution automatically. Type [u]F7[/u] to toggle the consensus panel.",
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
    def consensus_view(self) -> ConsensusView:
        """Return the planning consensus panel."""

        return self.query_one(ConsensusView)

    @property
    def consensus_visible(self) -> bool:
        """Return whether the docked consensus panel is visible."""

        return self._consensus_visible

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

    def set_consensus_visible(self, visible: bool) -> None:
        """Show or hide the docked consensus panel."""

        self._consensus_visible = visible
        consensus_view = self.query_one("#planning-consensus-panel", ConsensusView)
        if visible:
            if not consensus_view.facade_available:
                self.notify("Consensus panel is unavailable until project initialization completes.", severity="warning")
                self._consensus_visible = False
                consensus_view.display = False
                return
            consensus_view.load_document()
            consensus_view.assert_facade()
        consensus_view.display = visible

    def toggle_consensus_panel(self) -> None:
        """Toggle the docked consensus panel."""

        self.set_consensus_visible(not self._consensus_visible)

    def reveal_consensus_once(self) -> None:
        """Auto-open the consensus panel the first time meaningful content appears."""

        if self._consensus_auto_revealed:
            return
        self._consensus_auto_revealed = True
        self.set_consensus_visible(True)
