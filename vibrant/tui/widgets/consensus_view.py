"""Consensus pool summary widget for the Phase 6 TUI."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from ...models.consensus import ConsensusDocument
from ...models.task import TaskInfo, TaskStatus


COMPLETED_TASK_STATUSES = {TaskStatus.COMPLETED, TaskStatus.ACCEPTED}
FALLBACK_CONSENSUS_MARKDOWN = "# Consensus Pool\n\n_No consensus markdown available._\n"


class ConsensusMarkdownScreen(ModalScreen[None]):
    """Modal overlay showing the full consensus markdown document."""

    CSS = """
    ConsensusMarkdownScreen {
        align: center middle;
    }

    #consensus-markdown-modal {
        width: 82%;
        height: 88%;
        border: heavy $accent;
        background: $surface;
    }

    #consensus-markdown-header {
        height: 3;
        padding: 1;
        background: $primary-background;
        color: $text;
        text-align: center;
    }

    #consensus-markdown-scroll {
        height: 1fr;
        padding: 1 2;
    }

    #consensus-markdown-content {
        width: 1fr;
        color: $text;
    }

    #consensus-markdown-footer {
        height: 2;
        padding: 0 2;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("escape", "close_overlay", "Close"),
        Binding("f3", "close_overlay", "Close", show=False),
    ]

    def __init__(self, markdown_text: str) -> None:
        super().__init__(id="consensus-markdown-screen")
        self._markdown_text = markdown_text or FALLBACK_CONSENSUS_MARKDOWN

    @property
    def markdown_text(self) -> str:
        """Return the raw markdown being displayed in the overlay."""

        return self._markdown_text

    def compose(self) -> ComposeResult:
        with Vertical(id="consensus-markdown-modal"):
            yield Static("[b]Consensus Markdown[/b]", id="consensus-markdown-header", markup=True)
            with VerticalScroll(id="consensus-markdown-scroll"):
                yield Static(self._markdown_text, id="consensus-markdown-content", markup=False)
            yield Static("Esc / F3 close", id="consensus-markdown-footer")

    def action_close_overlay(self) -> None:
        self.dismiss(None)


class ConsensusView(Static):
    """Summary view of consensus metadata with a full-markdown overlay."""

    DEFAULT_CSS = """
    ConsensusView {
        border: round $primary-background;
        background: $surface;
        padding: 0;
    }

    #consensus-header {
        height: 3;
        padding: 1;
        background: $primary-background;
        color: $text;
        text-align: center;
    }

    #consensus-status,
    #consensus-version,
    #consensus-progress,
    #consensus-pending-questions,
    #consensus-footer {
        height: auto;
        padding: 0 2;
    }

    #consensus-recent-decisions {
        height: 1fr;
        padding: 1 2 0 2;
        color: $text;
    }

    .has-pending-questions {
        color: $warning;
        background: $warning 12%;
    }

    #consensus-footer {
        color: $text-muted;
        padding-bottom: 1;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._document: ConsensusDocument | None = None
        self._tasks: tuple[TaskInfo, ...] = ()
        self._consensus_path: Path | None = None
        self._raw_markdown = FALLBACK_CONSENSUS_MARKDOWN
        self._empty_message = "No consensus available."
        self._status_text = "Status: unavailable"
        self._version_text = "Version: --"
        self._progress_text = "Tasks: 0/0"
        self._pending_questions_text = "Pending Questions: 0"
        self._recent_decisions_text = "Recent Decisions:\n• No decisions recorded yet"

    def compose(self) -> ComposeResult:
        yield Static("[b]Consensus[/b]", id="consensus-header", markup=True)
        yield Static(self._status_text, id="consensus-status")
        yield Static(self._version_text, id="consensus-version")
        yield Static(self._progress_text, id="consensus-progress")
        yield Static(self._pending_questions_text, id="consensus-pending-questions")
        yield Static(self._recent_decisions_text, id="consensus-recent-decisions")
        yield Static("Press F3 for full consensus markdown", id="consensus-footer")

    def on_mount(self) -> None:
        self._refresh_content()

    def update_consensus(
        self,
        document: ConsensusDocument | None,
        *,
        tasks: Sequence[TaskInfo] = (),
        source_path: str | Path | None = None,
        raw_markdown: str | None = None,
    ) -> None:
        """Refresh the panel from the latest consensus document and roadmap tasks."""

        self._document = document
        self._tasks = tuple(tasks)
        self._consensus_path = Path(source_path) if source_path is not None else None
        if raw_markdown is not None:
            self._raw_markdown = raw_markdown
        elif self._consensus_path is not None:
            self._raw_markdown = self._read_markdown_from_path(self._consensus_path)
        elif document is None:
            self._raw_markdown = FALLBACK_CONSENSUS_MARKDOWN
        self._empty_message = "No consensus available."
        self._refresh_content()

    def clear_summary(self, message: str = "No consensus available.") -> None:
        """Reset the panel when there is no consensus file to summarize."""

        self._document = None
        self._tasks = ()
        self._consensus_path = None
        self._raw_markdown = FALLBACK_CONSENSUS_MARKDOWN
        self._empty_message = message
        self._refresh_content()

    def action_open_full_consensus(self) -> None:
        """Open the full consensus markdown in a modal overlay."""

        self.app.push_screen(ConsensusMarkdownScreen(self.get_full_markdown_text()))

    def get_summary_text(self) -> str:
        """Return the current summary text for testing and diagnostics."""

        return "\n".join(
            [
                self._status_text,
                self._version_text,
                self._progress_text,
                self._pending_questions_text,
            ]
        )

    def get_recent_decisions_text(self) -> str:
        """Return the currently rendered recent-decision block."""

        return self._recent_decisions_text

    def get_full_markdown_text(self) -> str:
        """Return the latest full consensus markdown available to the widget."""

        if self._consensus_path is not None:
            return self._read_markdown_from_path(self._consensus_path)
        return self._raw_markdown or FALLBACK_CONSENSUS_MARKDOWN

    @property
    def pending_questions_highlighted(self) -> bool:
        """Return whether the pending-question summary is highlighted."""

        if not self.is_mounted:
            return bool(self._document and self._document.questions)
        return self.query_one("#consensus-pending-questions", Static).has_class("has-pending-questions")

    def _refresh_content(self) -> None:
        completed_count = _count_completed_tasks(self._tasks)
        total_count = len(self._tasks)

        if self._document is None:
            self._status_text = "Status: unavailable"
            self._version_text = "Version: --"
            self._progress_text = f"Tasks: {completed_count}/{total_count}"
            self._pending_questions_text = "Pending Questions: 0"
            self._recent_decisions_text = f"Recent Decisions:\n• {self._empty_message}"
        else:
            self._status_text = f"Status: {self._document.status.value}"
            self._version_text = f"Version: {self._document.version}"
            self._progress_text = f"Tasks: {completed_count}/{total_count}"
            question_count = len(self._document.questions)
            self._pending_questions_text = f"Pending Questions: {question_count}"
            self._recent_decisions_text = _format_recent_decisions(self._document)

        if not self.is_mounted:
            return

        self.query_one("#consensus-status", Static).update(self._status_text)
        self.query_one("#consensus-version", Static).update(self._version_text)
        self.query_one("#consensus-progress", Static).update(self._progress_text)
        questions_widget = self.query_one("#consensus-pending-questions", Static)
        questions_widget.update(self._pending_questions_text)
        questions_widget.set_class(
            self._document is not None and bool(self._document.questions),
            "has-pending-questions",
        )
        self.query_one("#consensus-recent-decisions", Static).update(self._recent_decisions_text)

    @staticmethod
    def _read_markdown_from_path(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return FALLBACK_CONSENSUS_MARKDOWN


def _count_completed_tasks(tasks: Sequence[TaskInfo]) -> int:
    return sum(1 for task in tasks if task.status in COMPLETED_TASK_STATUSES)


def _format_recent_decisions(document: ConsensusDocument) -> str:
    if not document.decisions:
        return "Recent Decisions:\n• No decisions recorded yet"

    recent = list(reversed(document.decisions[-3:]))
    lines = ["Recent Decisions:"]
    for decision in recent:
        lines.append(f"• {decision.title}")
    return "\n".join(lines)
