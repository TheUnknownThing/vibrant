"""Stub task-status widget for the vibing screen."""

from __future__ import annotations

from textual.widgets import Static


class TaskStatusView(Static):
    """Display the current task state in the vibing workflow."""

    DEFAULT_CSS = """
    TaskStatusView {
        height: 1fr;
        border: round $primary-background;
        background: $surface;
        padding: 1 2;
    }
    """

    def on_mount(self) -> None:
        self.set_generating_roadmap(True)

    def set_generating_roadmap(self, is_loading: bool) -> None:
        if is_loading:
            self.update("[b]Generating Roadmap[/b]\n\nTask status will appear here once the roadmap is ready.")
            return

        self.update(
            "[b]Task Status[/b]\n\n"
            "Detailed task execution progress is not wired yet. "
            "This panel is intentionally left as a focused stub."
        )
