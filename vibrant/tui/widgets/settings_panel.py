"""Settings panel — modal overlay for app configuration."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Static, Select, Input, Button, Label

from ...models import ApprovalMode, AppSettings


MODEL_OPTIONS = [
    ("gpt-5.3-codex", "gpt-5.3-codex"),
    ("gpt-5.3-codex-spark", "gpt-5.3-codex-spark"),
    ("o3", "o3"),
    ("o4-mini", "o4-mini"),
]

APPROVAL_OPTIONS = [
    ("Suggest (ask for everything)", ApprovalMode.SUGGEST.value),
    ("Auto Edit (auto-approve edits)", ApprovalMode.AUTO_EDIT.value),
    ("Full Auto (approve all)", ApprovalMode.FULL_AUTO.value),
]

EFFORT_OPTIONS = [
    ("Low", "low"),
    ("Medium", "medium"),
    ("High", "high"),
]


class SettingsPanel(ModalScreen[AppSettings | None]):
    """Modal settings screen."""

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    CSS = """
    SettingsPanel {
        align: center middle;
    }
    #settings-container {
        width: 60;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: tall $primary;
        padding: 1 2;
    }
    #settings-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }
    .setting-label {
        margin-top: 1;
        color: $text-muted;
    }
    .setting-row {
        height: auto;
        margin-bottom: 1;
    }
    #settings-buttons {
        margin-top: 1;
        height: 3;
        align: center middle;
    }
    """

    def __init__(self, settings: AppSettings, **kwargs) -> None:
        super().__init__(**kwargs)
        self._settings = settings

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-container"):
            yield Static("⚙ Settings", id="settings-title")

            yield Label("Model:", classes="setting-label")
            yield Select(
                MODEL_OPTIONS,
                value=self._settings.default_model,
                id="model-select",
                allow_blank=False,
            )

            yield Label("Approval Mode:", classes="setting-label")
            yield Select(
                APPROVAL_OPTIONS,
                value=self._settings.default_approval_mode.value,
                id="approval-select",
                allow_blank=False,
            )

            yield Label("Effort:", classes="setting-label")
            yield Select(
                EFFORT_OPTIONS,
                value=self._settings.default_effort,
                id="effort-select",
                allow_blank=False,
            )

            yield Label("Working Directory:", classes="setting-label")
            yield Input(
                value=self._settings.default_cwd or "",
                placeholder="Leave empty for current directory",
                id="cwd-input",
            )

            with Vertical(id="settings-buttons"):
                yield Button("Save", variant="primary", id="save-btn")
                yield Button("Cancel", variant="default", id="cancel-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            model = self.query_one("#model-select", Select).value
            approval = self.query_one("#approval-select", Select).value
            effort = self.query_one("#effort-select", Select).value
            cwd = self.query_one("#cwd-input", Input).value.strip()

            new_settings = AppSettings(
                default_model=str(model) if model else self._settings.default_model,
                default_approval_mode=ApprovalMode(approval) if approval else self._settings.default_approval_mode,
                default_effort=str(effort) if effort else self._settings.default_effort,
                default_cwd=cwd or None,
                codex_binary=self._settings.codex_binary,
            )
            self.dismiss(new_settings)
        elif event.button.id == "cancel-btn":
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
