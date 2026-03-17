"""Filesystem path input with dropdown autocomplete suggestions."""

from __future__ import annotations

import os
from contextlib import suppress
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.widgets import Input, OptionList, Static


class _AutocompleteInput(Input):
    """Input that forwards autocomplete navigation intents to its parent widget."""

    BINDINGS = [
        Binding("down", "suggestion_next", show=False),
        Binding("up", "suggestion_previous", show=False),
        Binding("tab", "suggestion_apply", show=False),
    ]

    class SuggestionNavigate(Message):
        """Move the highlighted suggestion in the parent widget."""

        def __init__(self, delta: int) -> None:
            super().__init__()
            self.delta = delta

    class SuggestionApply(Message):
        """Apply the currently highlighted suggestion in the parent widget."""

    def action_suggestion_next(self) -> None:
        self.post_message(self.SuggestionNavigate(1))

    def action_suggestion_previous(self) -> None:
        self.post_message(self.SuggestionNavigate(-1))

    def action_suggestion_apply(self) -> None:
        self.post_message(self.SuggestionApply())


class PathAutocomplete(Static):
    """Reusable input widget that suggests filesystem paths in a dropdown."""

    DEFAULT_CSS = """
    PathAutocomplete {
        height: auto;
    }

    PathAutocomplete > Input {
        width: 1fr;
    }

    PathAutocomplete > OptionList {
        width: 1fr;
        max-height: 8;
        margin-top: 1;
        border: round $primary-background;
        background: $surface;
    }
    """

    def __init__(
        self,
        value: str = "",
        *,
        placeholder: str = "",
        base_path: Path | None = None,
        directories_only: bool = False,
        max_suggestions: int = 8,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._initial_value = value
        self._placeholder = placeholder
        self._base_path = Path(base_path or Path.cwd()).expanduser().resolve()
        self._directories_only = directories_only
        self._max_suggestions = max_suggestions
        self._input: _AutocompleteInput | None = None
        self._options: OptionList | None = None
        self._suggestions: list[str] = []
        self._applying_suggestion = False

    def compose(self) -> ComposeResult:
        self._input = _AutocompleteInput(value=self._initial_value, placeholder=self._placeholder, select_on_focus=False)
        self._options = OptionList(compact=True)
        self._options.display = False
        yield self._input
        yield self._options

    @property
    def value(self) -> str:
        """Current input value."""
        if self._input is None:
            return self._initial_value
        return self._input.value

    def focus_input(self) -> None:
        """Move focus to the path input field."""
        if self._input is not None:
            self._input.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Refresh suggestions whenever the input text changes."""
        if event.input is not self._input or self._applying_suggestion:
            return
        self._refresh_suggestions(show=True)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Hide the dropdown after the user submits the current value."""
        if event.input is self._input:
            self._hide_suggestions()

    def on__autocomplete_input_suggestion_navigate(self, event: _AutocompleteInput.SuggestionNavigate) -> None:
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
            highlighted = (highlighted + event.delta) % option_count
        self._options.highlighted = highlighted

    def on__autocomplete_input_suggestion_apply(self, _: _AutocompleteInput.SuggestionApply) -> None:
        """Apply the highlighted suggestion to the input field."""
        self._apply_highlighted_suggestion()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Allow mouse selection from the dropdown."""
        if event.option_list is not self._options:
            return
        self._apply_suggestion(event.index)
        event.stop()

    def _hide_suggestions(self) -> None:
        if self._options is not None:
            self._options.display = False

    def _refresh_suggestions(self, *, show: bool) -> None:
        if self._options is None:
            return

        suggestions = self._collect_suggestions(self.value)
        self._suggestions = suggestions
        self._options.clear_options()

        if not suggestions:
            self._hide_suggestions()
            return

        self._options.add_options(suggestions)
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

        self._applying_suggestion = True
        try:
            self._input.value = self._suggestions[index]
        finally:
            self._applying_suggestion = False

        self._input.action_end()
        self._input.focus()
        self._hide_suggestions()

    def _collect_suggestions(self, raw_value: str) -> list[str]:
        directory, fragment = self._completion_root(raw_value)
        if not directory.exists() or not directory.is_dir():
            return []

        fragment_lower = fragment.lower()
        candidates: list[Path] = []
        with suppress(OSError):
            for child in directory.iterdir():
                if self._directories_only and not child.is_dir():
                    continue
                if fragment and not child.name.lower().startswith(fragment_lower):
                    continue
                candidates.append(child)

        candidates.sort(key=lambda path: (path.name.lower(), str(path)))
        return [self._format_suggestion(path) for path in candidates[: self._max_suggestions]]

    def _completion_root(self, raw_value: str) -> tuple[Path, str]:
        value = raw_value.strip()
        if not value:
            return self._base_path, ""

        expanded = os.path.expanduser(value)
        has_trailing_sep = expanded.endswith(os.sep) or bool(os.altsep and expanded.endswith(os.altsep))
        path = Path(expanded)
        if not path.is_absolute():
            path = self._base_path / path
        normalized = Path(os.path.abspath(str(path)))

        if has_trailing_sep:
            return normalized, ""
        return normalized.parent, normalized.name

    @staticmethod
    def _format_suggestion(path: Path) -> str:
        suggestion = str(path.resolve())
        if path.is_dir():
            return f"{suggestion}{os.sep}"
        return suggestion
