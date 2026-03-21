"""Initialization flow screens for the Vibrant TUI."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Label, Static

from ...config import GatekeeperRole
from ...orchestrator.facade import OrchestratorFacade
from ..utility import initialize_git_repository, is_git_repository, is_under_git_repository
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

    def __init__(self, initial_path: Path) -> None:
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


class GitInitializationScreen(ModalScreen[bool]):
    """Prompt for Git repository initialization."""

    CSS = """
    GitInitializationScreen {
        align: center middle;
        background: $surface 92%;
    }

    #git-initialization-modal {
        width: 72;
        height: auto;
        border: heavy $accent;
        background: $surface;
        padding: 1 2;
    }

    #git-initialization-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }

    #git-initialization-body {
        color: $text-muted;
        margin-bottom: 1;
    }

    #git-initialization-path {
        text-style: bold;
        margin-bottom: 1;
    }

    #git-initialization-buttons {
        height: auto;
        margin-top: 1;
    }

    .git-initialization-button {
        width: 1fr;
        margin-right: 2;
    }

    #git-initialization-cancel {
        margin-left: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "confirm", "Initialize Git", show=False),
    ]

    def __init__(self, target_path: Path) -> None:
        super().__init__(id="git-initialization-screen")
        self._target_path = Path(target_path).expanduser().resolve()

    def compose(self) -> ComposeResult:
        with Vertical(id="git-initialization-modal"):
            yield Static("Initialize Git Repository", id="git-initialization-title")
            yield Static(
                "Vibrant uses Git worktrees for task execution and needs a valid HEAD for `git rev-parse`. "
                "If you continue, initialization will create the repository here and make the first commit from "
                "the current directory contents, falling back to an empty commit when nothing can be staged.",
                id="git-initialization-body",
            )
            yield Static(str(self._target_path), id="git-initialization-path")
            with Horizontal(id="git-initialization-buttons"):
                yield Button(
                    "Initialize Git",
                    variant="primary",
                    id="git-initialization-confirm",
                    classes="git-initialization-button",
                )
                yield Button("Cancel", id="git-initialization-cancel", classes="git-initialization-button")

    def on_mount(self) -> None:
        under_git = is_under_git_repository(self._target_path)
        if under_git:
            self.query_one("#git-initialization-body", Static).update(
                "This directory is under another Git repository. Proceeding will create a nested repository here, "
                "and initialization will still create an initial commit so later workspace setup can use "
                "`git rev-parse`. It is recommended to initialize the project in another directory. "
                "Do you want to proceed anyway?"
            )
        self.query_one("#git-initialization-confirm", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "git-initialization-confirm":
            self.action_confirm()
        elif event.button.id == "git-initialization-cancel":
            self.action_cancel()

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class GatekeeperRoleSelectionScreen(ModalScreen[GatekeeperRole | None]):
    """Prompt for selecting the Gatekeeper role during project initialization."""

    CSS = """
    GatekeeperRoleSelectionScreen {
        align: center middle;
        background: $surface 92%;
    }

    #gatekeeper-role-modal {
        width: 72;
        height: auto;
        border: heavy $accent;
        background: $surface;
        padding: 1 2;
    }

    #gatekeeper-role-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }

    #gatekeeper-role-body {
        color: $text-muted;
        margin-bottom: 1;
    }

    #gatekeeper-role-options {
        width: 1fr;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("up", "cursor_up", "Up", show=True),
        Binding("down", "cursor_down", "Down", show=True),
        Binding("enter", "confirm", "Confirm", show=True),
    ]

    def __init__(self, *, non_trivial_workspace: bool) -> None:
        super().__init__(id="gatekeeper-role-selection-screen")
        self._non_trivial_workspace = non_trivial_workspace

    def compose(self) -> ComposeResult:
        accent_color = self.app.theme_variables.get("accent", "yellow")
        with Vertical(id="gatekeeper-role-modal"):
            yield Static("Select Gatekeeper Role", id="gatekeeper-role-title")
            recommendation = (
                "Detected visible files or folders in this workspace. "
                "You may want `maintainer` to evolve an existing codebase."
                if self._non_trivial_workspace
                else "No visible files or folders detected yet. `builder` is often a good default for greenfield work."
            )
            yield Static(recommendation, id="gatekeeper-role-body")
            yield Multiselect(
                entries=[
                    "Maintainer",
                    "Builder",
                    # "Cancel",
                ],
                show_frame=True,
                active_style=f"bold {accent_color}",
                inactive_style="dim bold",
                active_prefix="> ",
                inactive_prefix="  ",
                id="gatekeeper-role-options",
                padding=1,
            )

    def on_mount(self) -> None:
        options = self.query_one("#gatekeeper-role-options", Multiselect)
        options.focus()
        if not self._non_trivial_workspace:
            options.action_move_cursor(1)

    async def on_multiselect_selected(self, event: Multiselect.Selected) -> None:
        if event.index == 0:
            self.dismiss(GatekeeperRole.MAINTAINER)
            return
        if event.index == 1:
            self.dismiss(GatekeeperRole.BUILDER)
            return
        self.action_cancel()

    def action_cursor_up(self) -> None:
        self.query_one("#gatekeeper-role-options", Multiselect).action_move_cursor(-1)

    def action_cursor_down(self) -> None:
        self.query_one("#gatekeeper-role-options", Multiselect).action_move_cursor(1)

    def action_confirm(self) -> None:
        self.query_one("#gatekeeper-role-options", Multiselect).action_select()

    def action_cancel(self) -> None:
        self.dismiss(None)


class InitializationScreen(ModalScreen[None]):
    """Full-screen entry flow for uninitialized workspaces."""

    class InitializeRequested(Message):
        """Request project initialization for a target directory."""

        def __init__(self, target_path: Path, gatekeeper_role: GatekeeperRole) -> None:
            super().__init__()
            self.target_path = target_path
            self.gatekeeper_role = gatekeeper_role

    class ExitRequested(Message):
        """Request the app to exit from the initialization screen."""

        pass

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
        Binding("ctrl+c", "exit_app", "Quit", show=True),
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

    def __init__(self, current_directory: Path) -> None:
        super().__init__(id="initialization-screen")
        self._current_directory = Path(current_directory).expanduser().resolve()
        self._pending_initialization_path: Path | None = None
        self._pending_non_trivial_workspace: bool = False

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
        self._request_initialization(self._current_directory)

    async def action_select_directory(self) -> None:
        self.app.push_screen(
            DirectorySelectionScreen(self._current_directory),
            callback=self._on_directory_selected,
        )

    def _on_directory_selected(self, selected_path: Path | None) -> None:
        if selected_path is None:
            return
        self._pending_non_trivial_workspace = OrchestratorFacade.is_non_trivial_workspace(selected_path)
        self._request_initialization(selected_path)

    def _request_initialization(self, target_path: Path) -> None:
        self._pending_non_trivial_workspace = OrchestratorFacade.is_non_trivial_workspace(target_path)
        self._pending_initialization_path = target_path
        if is_git_repository(target_path):
            self._prompt_for_gatekeeper_role()
            return

        self.app.push_screen(
            GitInitializationScreen(target_path),
            callback=self._on_git_initialization_selected,
        )

    def _on_git_initialization_selected(self, confirmed: bool) -> None:
        target_path = self._pending_initialization_path
        if not confirmed or target_path is None:
            self._pending_initialization_path = None
            return

        try:
            initialize_git_repository(target_path)
        except Exception as exc:
            self.notify(f"Failed to initialize Git repository: {exc}", severity="error")
            return

        self._prompt_for_gatekeeper_role()

    def _prompt_for_gatekeeper_role(self) -> None:
        self.app.push_screen(
            GatekeeperRoleSelectionScreen(non_trivial_workspace=self._pending_non_trivial_workspace),
            callback=self._on_gatekeeper_role_selected,
        )

    def _on_gatekeeper_role_selected(self, selected_role: GatekeeperRole | None) -> None:
        target_path = self._pending_initialization_path
        self._pending_initialization_path = None
        if target_path is None or selected_role is None:
            return
        self.post_message(self.InitializeRequested(target_path, selected_role))

    def action_exit_app(self) -> None:
        self.post_message(self.ExitRequested())
