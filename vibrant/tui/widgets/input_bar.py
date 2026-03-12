"""Input bar widget for sending messages to Codex."""

from __future__ import annotations

import os
import re
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.widgets import Input, OptionList, Static


@dataclass(slots=True)
class _CompletionSuggestion:
    """Single autocomplete candidate for the chat input."""

    display: str
    replacement: str
    start: int
    end: int


@dataclass(slots=True)
class _CompletionTarget:
    """Current token under completion."""

    kind: str
    token: str
    start: int
    end: int


class _ChatInput(Input):
    """Input that forwards autocomplete actions to the parent input bar."""

    _COMMAND_PATTERN = re.compile(r"^/\S+")
    _FILE_PATTERN = re.compile(r"(?<!\S)@\S+")

    BINDINGS = [
        Binding("ctrl+backspace", "delete_left_word", show=False),
        Binding("down", "suggestion_next", show=False),
        Binding("up", "suggestion_previous", show=False),
        Binding("tab", "suggestion_apply", show=False),
        Binding("escape", "suggestion_dismiss", show=False),
    ]

    class SuggestionNavigate(Message):
        """Move the highlighted autocomplete suggestion."""

        def __init__(self, delta: int) -> None:
            super().__init__()
            self.delta = delta

    class SuggestionApply(Message):
        """Apply the highlighted autocomplete suggestion."""

    class SuggestionDismiss(Message):
        """Hide the autocomplete dropdown."""

    def action_suggestion_next(self) -> None:
        self.post_message(self.SuggestionNavigate(1))

    def action_suggestion_previous(self) -> None:
        self.post_message(self.SuggestionNavigate(-1))

    def action_suggestion_apply(self) -> None:
        self.post_message(self.SuggestionApply())

    def action_suggestion_dismiss(self) -> None:
        self.post_message(self.SuggestionDismiss())

    @property
    def _value(self) -> Text:
        text = super()._value
        if self.password or not self.value:
            return text

        command_match = self._COMMAND_PATTERN.match(self.value)
        if command_match is not None:
            text.stylize(self._command_style, command_match.start(), command_match.end())

        for file_match in self._FILE_PATTERN.finditer(self.value):
            text.stylize(self._file_style, file_match.start(), file_match.end())

        return text

    @property
    def _command_style(self) -> Style:
        primary = self.app.theme_variables.get("primary", "#0178D4")
        return Style(color=primary, bold=True)

    @property
    def _file_style(self) -> Style:
        primary = self.app.theme_variables.get("primary", "#0178D4")
        primary_background = self.app.theme_variables.get("primary-background", "#33424E")
        return Style(color=primary, bgcolor=primary_background, underline=True)


class InputBar(Static):
    """Message input bar at the bottom of the conversation panel."""

    DEFAULT_PLACEHOLDER = "Type a message for the Gatekeeper..."
    COMMAND_SUGGESTIONS = (
        "/help",
        "/history",
        "/logs",
        "/model",
        "/next",
        "/refresh",
        "/run",
        "/settings",
        "/task",
        "/vibe",
    )

    DEFAULT_CSS = """
    InputBar {
        height: auto;
    }

    InputBar #input-context {
        height: 1;
        padding: 0 1;
    }

    InputBar #message-input {
        margin: 0 0 1 0;
    }

    InputBar #input-suggestions {
        width: 1fr;
        max-height: 8;
        margin: 0 0 1 0;
        border: round $primary-background;
        background: $surface;
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

    def __init__(
        self,
        *,
        base_path: str | Path | None = None,
        max_suggestions: int = 8,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._input: _ChatInput | None = None
        self._context_label: Static | None = None
        self._options: OptionList | None = None
        self._enabled = True
        self._placeholder = self.DEFAULT_PLACEHOLDER
        self._completion_base_path = Path(base_path or Path.cwd()).expanduser().resolve()
        self._max_suggestions = max_suggestions
        self._suggestions: list[_CompletionSuggestion] = []
        self._applying_suggestion = False

    def compose(self) -> ComposeResult:
        self._context_label = Static(
            "[dim]No active thread[/dim]",
            id="input-context",
            markup=True,
        )
        yield self._context_label
        self._input = _ChatInput(
            placeholder=self._placeholder,
            id="message-input",
        )
        yield self._input
        self._options = OptionList(id="input-suggestions", compact=True)
        self._options.styles.max_height = self._max_suggestions
        self._options.display = False
        yield self._options

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
        if not enabled:
            self._hide_suggestions()

    def set_placeholder(self, text: str) -> None:
        """Update the placeholder shown when the input is empty."""

        self._placeholder = text
        if self._input is not None:
            self._input.placeholder = text

    def set_completion_base_path(self, base_path: str | Path) -> None:
        """Update the base path used for `@path` autocompletion."""

        self._completion_base_path = Path(base_path).expanduser().resolve()
        if self._input is not None and self._input.has_focus:
            self._refresh_suggestions(show=True)

    @property
    def placeholder(self) -> str:
        """Return the currently configured input placeholder."""

        return self._placeholder

    def on_input_changed(self, event: Input.Changed) -> None:
        """Refresh suggestions whenever the user edits the input."""

        if event.input is not self._input or self._applying_suggestion:
            return
        self._refresh_suggestions(show=True)

    def on_input_blurred(self, event: Input.Blurred) -> None:
        """Hide suggestions when the chat input loses focus."""

        if event.input is self._input:
            self._hide_suggestions()

    def on__chat_input_suggestion_navigate(self, event: _ChatInput.SuggestionNavigate) -> None:
        """Move the highlighted suggestion up or down."""

        if self._options is None:
            return

        if not self._options.display:
            self._refresh_suggestions(show=True)
            return

        option_count = self._options.option_count
        if option_count == 0:
            return

        highlighted = self._options.highlighted
        if highlighted is None:
            highlighted = 0 if event.delta > 0 else option_count - 1
        else:
            highlighted = max(0, min(highlighted + event.delta, option_count - 1))
        self._options.highlighted = highlighted

    def on__chat_input_suggestion_apply(self, _: _ChatInput.SuggestionApply) -> None:
        """Apply the highlighted suggestion to the chat input."""

        self._apply_highlighted_suggestion()

    def on__chat_input_suggestion_dismiss(self, _: _ChatInput.SuggestionDismiss) -> None:
        """Dismiss the autocomplete dropdown."""

        self._hide_suggestions()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Allow mouse selection from the autocomplete dropdown."""

        if event.option_list is not self._options:
            return
        self._apply_suggestion(event.index)
        event.stop()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle Enter key in the input field."""

        if event.input is not self._input:
            return

        if self._options is not None and self._options.display and self._options.option_count > 0:
            self._apply_highlighted_suggestion()
            event.stop()
            return

        self._hide_suggestions()
        text = event.value.strip()
        if not text:
            return
        if self._input:
            self._input.clear()

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

    def _hide_suggestions(self) -> None:
        if self._options is not None:
            self._options.display = False

    def _refresh_suggestions(self, *, show: bool) -> None:
        if self._options is None or self._input is None or not self._enabled:
            return

        suggestions = self._collect_suggestions(self._input.value, self._input.cursor_position)
        self._suggestions = suggestions
        self._options.clear_options()

        if not suggestions:
            self._hide_suggestions()
            return

        self._options.add_options([suggestion.display for suggestion in suggestions])
        self._options.highlighted = 0
        self._options.display = show

    def _apply_highlighted_suggestion(self) -> None:
        if self._options is None or self._options.option_count == 0:
            self._refresh_suggestions(show=True)
            return

        highlighted = self._options.highlighted
        if highlighted is None:
            highlighted = 0
        self._apply_suggestion(highlighted)

    def _apply_suggestion(self, index: int) -> None:
        if self._input is None:
            return
        if index < 0 or index >= len(self._suggestions):
            return

        suggestion = self._suggestions[index]
        updated_value = (
            f"{self._input.value[:suggestion.start]}"
            f"{suggestion.replacement}"
            f"{self._input.value[suggestion.end:]}"
        )
        cursor_position = suggestion.start + len(suggestion.replacement)

        self._applying_suggestion = True
        try:
            self._input.value = updated_value
            self._input.cursor_position = cursor_position
        finally:
            self._applying_suggestion = False

        self._input.focus()
        if suggestion.replacement.endswith(os.sep):
            self._refresh_suggestions(show=True)
        else:
            self._hide_suggestions()

    def _collect_suggestions(self, value: str, cursor_position: int) -> list[_CompletionSuggestion]:
        target = self._completion_target(value, cursor_position)
        if target is None:
            return []
        if target.kind == "command":
            return self._collect_command_suggestions(value, target)
        if target.kind == "path":
            return self._collect_path_suggestions(target)
        return []

    @staticmethod
    def _completion_target(value: str, cursor_position: int) -> _CompletionTarget | None:
        if not value:
            return None

        cursor = max(0, min(cursor_position, len(value)))
        if cursor > 0 and value[cursor - 1].isspace():
            return None

        start = cursor
        while start > 0 and not value[start - 1].isspace():
            start -= 1

        end = cursor
        while end < len(value) and not value[end].isspace():
            end += 1

        token = value[start:end]
        if not token:
            return None
        if start == 0 and token.startswith("/"):
            return _CompletionTarget("command", token, start, end)
        if token.startswith("@"):
            return _CompletionTarget("path", token, start, end)
        return None

    def _collect_command_suggestions(
        self,
        value: str,
        target: _CompletionTarget,
    ) -> list[_CompletionSuggestion]:
        fragment = target.token[1:].lower()
        suggestions: list[_CompletionSuggestion] = []
        for command in self.COMMAND_SUGGESTIONS:
            if fragment and not command[1:].startswith(fragment):
                continue
            replacement = command if target.end < len(value) else f"{command} "
            suggestions.append(
                _CompletionSuggestion(
                    display=command,
                    replacement=replacement,
                    start=target.start,
                    end=target.end,
                )
            )
        return suggestions

    def _collect_path_suggestions(self, target: _CompletionTarget) -> list[_CompletionSuggestion]:
        raw_path = target.token[1:]
        directory, fragment = self._path_completion_root(raw_path)
        if not directory.exists() or not directory.is_dir():
            return []

        fragment_lower = fragment.lower()
        candidates: list[Path] = []
        with suppress(OSError):
            for child in directory.iterdir():
                if fragment and not child.name.lower().startswith(fragment_lower):
                    continue
                candidates.append(child)

        candidates.sort(key=lambda path: (not path.is_dir(), path.name.lower(), str(path)))

        suggestions: list[_CompletionSuggestion] = []
        for path in candidates:
            display = f"@{self._format_path_suggestion(path, raw_path)}"
            suggestions.append(
                _CompletionSuggestion(
                    display=display,
                    replacement=display,
                    start=target.start,
                    end=target.end,
                )
            )
        return suggestions

    def _path_completion_root(self, raw_path: str) -> tuple[Path, str]:
        if not raw_path:
            return self._completion_base_path, ""

        expanded = os.path.expanduser(raw_path)
        has_trailing_sep = expanded.endswith(os.sep) or bool(os.altsep and expanded.endswith(os.altsep))
        path = Path(expanded)
        if not path.is_absolute():
            path = self._completion_base_path / path
        normalized = Path(os.path.abspath(str(path)))

        if has_trailing_sep:
            return normalized, ""
        return normalized.parent, normalized.name

    def _format_path_suggestion(self, path: Path, raw_path: str) -> str:
        resolved = path.resolve()
        if raw_path.startswith("~"):
            home = Path.home()
            with suppress(ValueError):
                relative_home = resolved.relative_to(home)
                suggestion = str(Path("~") / relative_home)
                return f"{suggestion}{os.sep}" if path.is_dir() else suggestion

        if Path(raw_path).is_absolute():
            suggestion = str(resolved)
        else:
            try:
                suggestion = str(resolved.relative_to(self._completion_base_path))
            except ValueError:
                suggestion = str(resolved)

        if path.is_dir():
            return f"{suggestion}{os.sep}"
        return suggestion
