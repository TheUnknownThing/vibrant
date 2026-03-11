"""Input bar widget for sending messages to Codex."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.message import Message
from textual.widgets import Static, Input


class InputBar(Static):
    """Message input bar at the bottom of the conversation panel."""

    DEFAULT_PLACEHOLDER = "Type a message for the Gatekeeper..."

    DEFAULT_CSS = """
    InputBar #input-context {
        height: 1;
        padding: 0 1;
    }

    InputBar #message-input {
        margin: 0 0 1 0;
    }
    """

    class MessageSubmitted(Message):
        """Emitted when the user submits a message."""
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class SlashCommand(Message):
        """Emitted when the user types a /command."""
        def __init__(self, command: str, args: str) -> None:
            super().__init__()
            self.command = command
            self.args = args

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._input: Input | None = None
        self._context_label: Static | None = None
        self._enabled = True
        self._placeholder = self.DEFAULT_PLACEHOLDER

    def compose(self) -> ComposeResult:
        self._context_label = Static(
            "[dim]No active thread[/dim]",
            id="input-context",
            markup=True,
        )
        yield self._context_label
        self._input = Input(
            placeholder=self._placeholder,
            id="message-input",
        )
        yield self._input

    def set_context(self, model: str | None = None, status: str = "") -> None:
        """Update the context label above the input."""
        if self._context_label:
            parts = []
            if model:
                parts.append(f"model:{model}")
            if status:
                parts.append(status)
            if parts:
                self._context_label.update(f"[dim]{' · '.join(parts)}[/dim]")
            else:
                self._context_label.update("[dim]Ready[/dim]")

    def set_enabled(self, enabled: bool) -> None:
        """Enable/disable the input."""
        self._enabled = enabled
        if self._input:
            self._input.disabled = not enabled

    def set_placeholder(self, text: str) -> None:
        """Update the placeholder shown when the input is empty."""

        self._placeholder = text
        if self._input is not None:
            self._input.placeholder = text

    @property
    def placeholder(self) -> str:
        """Return the currently configured input placeholder."""

        return self._placeholder

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle Enter key in the input field."""
        text = event.value.strip()
        if not text:
            return
        if self._input:
            self._input.clear()

        # Check for slash commands
        if text.startswith("/"):
            parts = text[1:].split(None, 1)
            command = parts[0] if parts else ""
            args = parts[1] if len(parts) > 1 else ""
            self.post_message(self.SlashCommand(command, args))
        else:
            self.post_message(self.MessageSubmitted(text))

    def focus_input(self) -> None:
        """Focus the text input."""
        if self._input:
            self._input.focus()
