"""Help overlay for the Vibrant TUI."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Markdown


class HelpScreen(ModalScreen[None]):
    """Modal help overlay for the current TUI workflow."""

    CSS = """
    HelpScreen {
        align: center middle;
    }

    #help-markdown {
        width: 72%;
        height: 78%;
        border: heavy $accent;
        background: $surface;
        padding: 1 2;
    }
    """

    BINDINGS = [
        Binding("escape", "close_help", "Close"),
        Binding("f1", "close_help", "Close", show=False),
    ]

    def compose(self) -> ComposeResult:
        yield Markdown(
            """# Vibrant Help

## Screens
- **Initialization**: create a `.vibrant` workspace in the current or selected directory
- **Planning**: talk with the Gatekeeper until you are ready to type `/vibe`
- **Vibing**: monitor tasks, review consensus, inspect agent logs, and keep chatting with the Gatekeeper

## Keys
- `F1` help
- `F2` pause / resume workflow
- `F3` show Task Status tab
- `F4` toggle Consensus panel / tab
- `F5` show Chat History tab
- `F10` quit

## Commands
- `/vibe` move from planning to vibing
- `/run` execute the next roadmap task
- `/refresh` reload project state
- `/settings` open settings
- `/history` open the Gatekeeper chat tab
- `/logs` open the Agent Logs tab

Press `Esc` or `F1` to close this help.
""",
            id="help-markdown",
        )

    def action_close_help(self) -> None:
        self.dismiss(None)
