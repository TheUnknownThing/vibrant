"""Initialization flow screens for the Vibrant TUI."""

from __future__ import annotations

import asyncio
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Label, Static

from ..widgets.multiselect import Multiselect
from ..widgets.path_autocomplete import PathAutocomplete


class DirectorySelectionScreen(ModalScreen[Path | None]):
    """Prompt for selecting a directory to initialize."""

    CSS = """
    DirectorySelectionScreen {
        align: center middle;
        background: $surface 92%;
    }

    #directory-selection-modal {
        width: 72;
        height: auto;
        border: heavy $accent;
        background: $surface;
        padding: 1 2;
    }

    #directory-selection-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }

    #directory-selection-path {
        color: $text-muted;
        margin-bottom: 1;
    }

    #directory-selection-buttons {
        height: auto;
        margin-top: 1;
    }

    .directory-selection-button {
        width: 1fr;
        margin-right: 2;
    }

    #directory-selection-cancel {
        margin-left: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "submit", "Initialize", show=False),
    ]

    def __init__(self, initial_path: str | Path) -> None:
        super().__init__(id="directory-selection-screen")
        self._initial_path = Path(initial_path).expanduser().resolve()

    def compose(self) -> ComposeResult:
        with Vertical(id="directory-selection-modal"):
            yield Static("Initialize Project", id="directory-selection-title")
            yield Label("Directory", classes="setting-label")
            yield Static(
                "Choose an existing directory to place the `.vibrant` workspace in.",
                id="directory-selection-path",
            )
            yield PathAutocomplete(
                value=str(self._initial_path),
                base_path=self._initial_path.parent,
                directories_only=True,
                id="directory-selection-input",
            )
            with Horizontal(id="directory-selection-buttons"):
                yield Button(
                    "Initialize",
                    variant="primary",
                    id="directory-selection-confirm",
                    classes="directory-selection-button",
                )
                yield Button("Cancel", id="directory-selection-cancel", classes="directory-selection-button")

    def on_mount(self) -> None:
        self.query_one("#directory-selection-input", PathAutocomplete).focus_input()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "directory-selection-confirm":
            self.action_submit()
        elif event.button.id == "directory-selection-cancel":
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_submit(self) -> None:
        raw_value = self.query_one("#directory-selection-input", PathAutocomplete).value.strip()
        if not raw_value:
            self.notify("Enter a directory path first.", severity="warning")
            return

        selected_path = Path(raw_value).expanduser()
        if not selected_path.exists():
            self.notify(f"Directory does not exist: {selected_path}", severity="error")
            return
        if not selected_path.is_dir():
            self.notify(f"Path is not a directory: {selected_path}", severity="error")
            return

        self.dismiss(selected_path.resolve())


class InitializationScreen(ModalScreen[None]):
    """Full-screen entry flow for uninitialized workspaces."""

    CSS = """
    InitializationScreen {
        align: center middle;
        background: $surface 100%;
    }

    #initialization-shell {
        width: 78;
        height: auto;
        padding: 2 3;
        border: heavy $primary;
        background: $surface;
    }

    #initialization-logo {
        text-align: center;
        color: $accent;
        margin-bottom: 1;
    }

    #initialization-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }

    #initialization-path {
        color: $text-muted;
        text-align: center;
        margin-bottom: 2;
    }

    #initialization-options {
        width: 1fr;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("f10", "exit_app", "Quit", show=True),
        Binding("ctrl+q", "exit_app", "Quit", show=True),
        Binding("up", "cursor_up", "Up", show=True),
        Binding("down", "cursor_down", "Down", show=True),
        Binding("enter", "confirm", "Confirm", show=True),
        Binding("ctrl+p", "command_palette", "Command Palette", show=False),
        Binding("1", "initialize_here", "Initialize Here", show=False),
        Binding("2", "select_directory", "Select Directory", show=False),
        Binding("3", "exit_app", "Exit", show=False),
    ]

    _LOGO = """
██╗   ██╗██╗██████╗ ██████╗  █████╗ ███╗   ██╗████████╗
██║   ██║██║██╔══██╗██╔══██╗██╔══██╗████╗  ██║╚══██╔══╝
██║   ██║██║██████╔╝██████╔╝███████║██╔██╗ ██║   ██║   
╚██╗ ██╔╝██║██╔══██╗██╔══██╗██╔══██║██║╚██╗██║   ██║   
 ╚████╔╝ ██║██████╔╝██║  ██║██║  ██║██║ ╚████║   ██║   
  ╚═══╝  ╚═╝╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝   ╚═╝   
"""

    def __init__(self, current_directory: str | Path) -> None:
        super().__init__(id="initialization-screen")
        self._current_directory = Path(current_directory).expanduser().resolve()

    def compose(self) -> ComposeResult:
        accent_color = self.app.theme_variables.get("accent", "yellow")
        with Vertical(id="initialization-shell"):
            yield Static(self._LOGO, id="initialization-logo")
            yield Static("This workspace is not initialized yet.", id="initialization-title")
            yield Static(
                f"Workspace: {self._current_directory}",
                id="initialization-path",
            )
            yield Multiselect(
                entries=[
                    "Initialize Project Here",
                    "Initialize Project (Select Directory)",
                    "Exit",
                ],
                show_frame=True,
                active_style=f"bold {accent_color}",
                inactive_style="dim bold",
                active_prefix="> ",
                inactive_prefix="  ",
                id="initialization-options",
                padding=1,
            )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#initialization-options", Multiselect).focus()

    async def on_multiselect_selected(self, event: Multiselect.Selected) -> None:
        if event.index == 0:
            await self.action_initialize_here()
        elif event.index == 1:
            await self.action_select_directory()
        else:
            self.action_exit_app()

    def action_cursor_up(self) -> None:
        self.query_one("#initialization-options", Multiselect).action_move_cursor(-1)

    def action_cursor_down(self) -> None:
        self.query_one("#initialization-options", Multiselect).action_move_cursor(1)

    def action_confirm(self) -> None:
        self.query_one("#initialization-options", Multiselect).action_select()

    async def action_initialize_here(self) -> None:
        await self._initialize_directory(self._current_directory)

    async def action_select_directory(self) -> None:
        self.app.push_screen(
            DirectorySelectionScreen(self._current_directory),
            callback=self._on_directory_selected,
        )

    def _on_directory_selected(self, selected_path: Path | None) -> None:
        if selected_path is None:
            return
        asyncio.create_task(self._initialize_directory(selected_path), name="vibrant-init-selected-directory")

    def action_exit_app(self) -> None:
        self.app.exit()

    async def _initialize_directory(self, target_path: Path) -> None:
        initialize_for_app = getattr(self.app, "initialize_project_at", None)
        if not callable(initialize_for_app):
            self.notify("Initialization is unavailable in this app instance.", severity="error")
            return

        initialized = await initialize_for_app(target_path)
        if initialized:
            self.dismiss(None)
