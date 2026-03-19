"""Settings panel — modal overlay for app configuration."""

from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static

from ...config import VibrantConfig, VibrantConfigPatch
from ...providers.base import ProviderKind

APPROVAL_OPTIONS = [
    ("Never", "never"),
    ("On Request", "on-request"),
]

EFFORT_OPTIONS = [
    ("Low", "low"),
    ("Medium", "medium"),
    ("High", "high"),
]


@dataclass(slots=True)
class SettingsUpdate:
    """Result payload emitted when the settings panel is saved."""

    working_directory: str | None
    config_patch: VibrantConfigPatch


class SettingsPanel(ModalScreen[SettingsUpdate | None]):
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

    def __init__(
        self,
        config: VibrantConfig,
        *,
        working_directory: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._config = config
        self._working_directory = working_directory

    def _approval_options(self) -> list[tuple[str, str]]:
        if self._config.provider_kind is ProviderKind.CLAUDE:
            return [APPROVAL_OPTIONS[0]]
        return APPROVAL_OPTIONS

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-container"):
            yield Static("⚙ Settings", id="settings-title")

            yield Label("Model:", classes="setting-label")
            yield Input(
                value=self._config.model,
                placeholder="e.g. gpt-5.3-codex or claude-sonnet-4-5",
                id="model-input",
            )

            yield Label("Approval Policy:", classes="setting-label")
            yield Select(
                self._approval_options(),
                value=self._config.approval_policy,
                id="approval-select",
                allow_blank=False,
            )

            yield Label("Effort:", classes="setting-label")
            yield Select(
                EFFORT_OPTIONS,
                value=self._config.reasoning_effort,
                id="effort-select",
                allow_blank=False,
            )

            yield Label("Working Directory:", classes="setting-label")
            yield Input(
                value=self._working_directory or "",
                placeholder="Leave empty for current directory",
                id="cwd-input",
            )

            with Vertical(id="settings-buttons"):
                yield Button("Save", variant="primary", id="save-btn")
                yield Button("Cancel", variant="default", id="cancel-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            model = self.query_one("#model-input", Input).value.strip()
            approval = self.query_one("#approval-select", Select).value
            effort = self.query_one("#effort-select", Select).value
            cwd = self.query_one("#cwd-input", Input).value.strip()
            resolved_cwd = cwd or None

            patch = VibrantConfigPatch(
                model=model if model and model != self._config.model else None,
                approval_policy=(
                    str(approval)
                    if approval and str(approval) != self._config.approval_policy
                    else None
                ),
                reasoning_effort=(
                    str(effort)
                    if effort and str(effort) != self._config.reasoning_effort
                    else None
                ),
            )
            self.dismiss(
                SettingsUpdate(
                    working_directory=resolved_cwd,
                    config_patch=patch,
                )
            )
        elif event.button.id == "cancel-btn":
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
