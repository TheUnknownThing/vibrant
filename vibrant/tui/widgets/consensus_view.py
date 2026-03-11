"""Consensus editor/viewer widget for the redesigned TUI."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Sequence

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Button, Markdown, Static, TextArea

from ...consensus import ConsensusWriter
from ...models.consensus import ConsensusDocument
from ...models.task import TaskInfo, TaskStatus


COMPLETED_TASK_STATUSES = {TaskStatus.COMPLETED, TaskStatus.ACCEPTED}
FALLBACK_CONSENSUS_MARKDOWN = "# Consensus Pool\n\n_No consensus markdown available._\n"
EMPTY_EDITABLE_MARKDOWN = "## Objectives\n\n## Design Choices\n\n## Getting Started\n"


EDITOR_OBJECTIVES_PATTERN = re.compile(r"^## Objectives\n(?P<body>.*?)(?=^## Design Choices\n|\Z)", re.DOTALL | re.MULTILINE)
EDITOR_DECISIONS_PATTERN = re.compile(r"^## Design Choices\n(?P<body>.*?)(?=^## Getting Started\n|\Z)", re.DOTALL | re.MULTILINE)
EDITOR_GETTING_STARTED_PATTERN = re.compile(r"^## Getting Started\n(?P<body>.*?)(?=^## Questions\n|\Z)", re.DOTALL | re.MULTILINE)
EDITOR_QUESTIONS_PATTERN = re.compile(r"^## Questions\n(?P<body>.*)\Z", re.DOTALL | re.MULTILINE)


class ConsensusView(Static):
    """Shared consensus metadata + markdown preview/editor component."""

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

    #consensus-metadata {
        height: auto;
        padding: 1 2;
        border-bottom: solid $primary-background;
        color: $text;
    }

    #consensus-actions {
        height: auto;
        padding: 1 2 0 2;
    }

    #consensus-save,
    #consensus-revert {
        margin-right: 1;
    }

    #consensus-sync-status {
        height: auto;
        padding: 1 2;
        color: $text-muted;
    }

    #consensus-preview-label,
    #consensus-editor-label {
        height: auto;
        padding: 0 2;
        color: $text-muted;
    }

    #consensus-preview,
    #consensus-editor {
        height: 1fr;
        min-height: 8;
        margin: 0 1 1 1;
        border: round $panel;
        background: $surface-lighten-1;
    }

    #consensus-preview {
        padding: 1 2;
        overflow-y: auto;
    }

    #consensus-footer {
        height: auto;
        padding: 0 2 1 2;
        color: $text-muted;
    }

    .consensus-dirty {
        color: $warning;
    }

    .consensus-external-update {
        color: $error;
    }
    """

    class SaveRequested(Message):
        """Posted when the user wants to persist consensus edits."""

        def __init__(self, document: ConsensusDocument, *, source_path: Path | None) -> None:
            super().__init__()
            self.document = document
            self.source_path = source_path

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._document: ConsensusDocument | None = None
        self._tasks: tuple[TaskInfo, ...] = ()
        self._consensus_path: Path | None = None
        self._raw_markdown = FALLBACK_CONSENSUS_MARKDOWN
        self._editable_markdown = EMPTY_EDITABLE_MARKDOWN
        self._last_synced_editable_markdown = EMPTY_EDITABLE_MARKDOWN
        self._latest_external_editable_markdown = EMPTY_EDITABLE_MARKDOWN
        self._empty_message = "No consensus available."
        self._metadata_text = "[dim]No consensus metadata available.[/dim]"
        self._sync_status_text = "Read-only metadata · editable markdown body"
        self._suspend_editor_events = False
        self._dirty = False
        self._has_external_update = False

    def compose(self) -> ComposeResult:
        yield Static("[b]Consensus[/b]", id="consensus-header", markup=True)
        yield Static(self._metadata_text, id="consensus-metadata", markup=True)
        with Horizontal(id="consensus-actions"):
            yield Button("Save edits", id="consensus-save")
            yield Button("Revert", id="consensus-revert")
        yield Static(self._sync_status_text, id="consensus-sync-status")
        yield Static("Preview", id="consensus-preview-label")
        yield Markdown(_preview_markdown(self._editable_markdown), id="consensus-preview")
        yield Static("Editor", id="consensus-editor-label")
        yield TextArea(
            self._editable_markdown,
            id="consensus-editor",
            language="markdown",
            soft_wrap=True,
            tab_behavior="indent",
            show_line_numbers=False,
        )
        yield Static(
            "Metadata updates are read-only here. Edit the markdown body and save to persist changes.",
            id="consensus-footer",
        )

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
        """Refresh the component from the latest consensus document and roadmap tasks."""

        self._document = document
        self._tasks = tuple(tasks)
        self._consensus_path = Path(source_path) if source_path is not None else None
        if raw_markdown is not None:
            self._raw_markdown = raw_markdown
        elif self._consensus_path is not None:
            self._raw_markdown = self._read_markdown_from_path(self._consensus_path)
        elif document is not None:
            self._raw_markdown = ConsensusWriter().render(document)
        else:
            self._raw_markdown = FALLBACK_CONSENSUS_MARKDOWN

        incoming_editable_markdown = _extract_editable_markdown(self._raw_markdown)
        if document is None:
            self._editable_markdown = EMPTY_EDITABLE_MARKDOWN
            self._last_synced_editable_markdown = EMPTY_EDITABLE_MARKDOWN
            self._latest_external_editable_markdown = EMPTY_EDITABLE_MARKDOWN
            self._dirty = False
            self._has_external_update = False
            self._empty_message = "No consensus available."
        else:
            self._latest_external_editable_markdown = incoming_editable_markdown
            if not self._dirty or incoming_editable_markdown == self._last_synced_editable_markdown:
                self._editable_markdown = incoming_editable_markdown
                self._last_synced_editable_markdown = incoming_editable_markdown
                self._dirty = False
                self._has_external_update = False
            elif incoming_editable_markdown != self._last_synced_editable_markdown:
                self._has_external_update = True

            self._empty_message = "No consensus available."
        self._refresh_content()

    def clear_summary(self, message: str = "No consensus available.") -> None:
        """Reset the component when there is no consensus file to show."""

        self._document = None
        self._tasks = ()
        self._consensus_path = None
        self._raw_markdown = FALLBACK_CONSENSUS_MARKDOWN
        self._editable_markdown = EMPTY_EDITABLE_MARKDOWN
        self._last_synced_editable_markdown = EMPTY_EDITABLE_MARKDOWN
        self._latest_external_editable_markdown = EMPTY_EDITABLE_MARKDOWN
        self._empty_message = message
        self._dirty = False
        self._has_external_update = False
        self._refresh_content()

    @property
    def current_editable_markdown(self) -> str:
        """Return the current editor content."""

        if self.is_mounted:
            return self.query_one("#consensus-editor", TextArea).text
        return self._editable_markdown

    @property
    def has_unsaved_changes(self) -> bool:
        """Return whether the editor has unsaved local edits."""

        return self._dirty

    @property
    def has_external_update(self) -> bool:
        """Return whether a fresher external consensus update exists."""

        return self._has_external_update

    @property
    def metadata_text(self) -> str:
        """Return the current rendered metadata string."""

        return self._metadata_text

    def get_full_markdown_text(self) -> str:
        """Return the full markdown represented by the current editor state."""

        if self._document is None:
            return self._raw_markdown or FALLBACK_CONSENSUS_MARKDOWN
        return _merge_document_with_editable_markdown(self._document, self.current_editable_markdown)

    def action_save_edits(self) -> None:
        """Post a save request for the currently edited consensus body."""

        if self._document is None:
            self.app.notify("No consensus document is available to save.", severity="warning")
            return

        try:
            updated_document = self._build_pending_document()
        except Exception as exc:
            self.app.notify(f"Consensus markdown is invalid: {exc}", severity="error")
            return

        self.post_message(self.SaveRequested(updated_document, source_path=self._consensus_path))

    def action_revert_edits(self) -> None:
        """Discard local edits and restore the latest synced markdown body."""

        self._editable_markdown = self._latest_external_editable_markdown
        self._last_synced_editable_markdown = self._latest_external_editable_markdown
        self._dirty = False
        self._has_external_update = False
        self._refresh_content()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "consensus-save":
            self.action_save_edits()
        elif button_id == "consensus-revert":
            self.action_revert_edits()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if self._suspend_editor_events or event.control.id != "consensus-editor":
            return
        self._editable_markdown = event.control.text
        self._dirty = self._editable_markdown != self._last_synced_editable_markdown
        self._refresh_preview()
        self._refresh_sync_status()

    def _build_pending_document(self) -> ConsensusDocument:
        if self._document is None:
            raise RuntimeError("No consensus document is loaded")
        merged_markdown = _merge_document_with_editable_markdown(self._document, self.current_editable_markdown)
        return ConsensusWriter().parser.parse(merged_markdown)

    def _refresh_content(self) -> None:
        self._metadata_text = _format_metadata(self._document, self._tasks, empty_message=self._empty_message)
        self._refresh_sync_status()
        if not self.is_mounted:
            return

        self.query_one("#consensus-metadata", Static).update(self._metadata_text)
        self._replace_editor_text(self._editable_markdown)
        self._refresh_preview()

    def _replace_editor_text(self, text: str) -> None:
        editor = self.query_one("#consensus-editor", TextArea)
        if editor.text == text:
            return
        self._suspend_editor_events = True
        try:
            editor.load_text(text)
        finally:
            self._suspend_editor_events = False

    def _refresh_preview(self) -> None:
        if not self.is_mounted:
            return
        self.query_one("#consensus-preview", Markdown).update(_preview_markdown(self.current_editable_markdown))

    def _refresh_sync_status(self) -> None:
        if self._document is None:
            self._sync_status_text = self._empty_message
            status_class = ""
        elif self._has_external_update:
            self._sync_status_text = "External consensus update available — revert or save to resync"
            status_class = "consensus-external-update"
        elif self._dirty:
            self._sync_status_text = "Unsaved local edits"
            status_class = "consensus-dirty"
        else:
            self._sync_status_text = "Synced with `.vibrant/consensus.md`"
            status_class = ""

        if not self.is_mounted:
            return

        status_widget = self.query_one("#consensus-sync-status", Static)
        status_widget.update(self._sync_status_text)
        status_widget.remove_class("consensus-dirty")
        status_widget.remove_class("consensus-external-update")
        if status_class:
            status_widget.add_class(status_class)

    @staticmethod
    def _read_markdown_from_path(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return FALLBACK_CONSENSUS_MARKDOWN



def _count_completed_tasks(tasks: Sequence[TaskInfo]) -> int:
    return sum(1 for task in tasks if task.status in COMPLETED_TASK_STATUSES)



def _preview_markdown(markdown_text: str) -> str:
    stripped = markdown_text.strip()
    if stripped:
        return stripped
    return "_No editable consensus content yet._"



def _extract_editable_markdown(markdown_text: str) -> str:
    text = markdown_text.strip()
    if not text:
        return EMPTY_EDITABLE_MARKDOWN

    meta_end_marker = "<!-- META:END -->"
    if meta_end_marker in text:
        text = text.split(meta_end_marker, maxsplit=1)[1].lstrip("\n")

    for marker in ("<!-- OBJECTIVES:START -->", "<!-- OBJECTIVES:END -->", "<!-- DECISIONS:START -->", "<!-- DECISIONS:END -->"):
        text = text.replace(marker + "\n", "")
        text = text.replace(marker, "")

    text = text.strip("\n")
    if not text:
        return EMPTY_EDITABLE_MARKDOWN
    return text + "\n"



def _merge_document_with_editable_markdown(document: ConsensusDocument, editable_markdown: str) -> str:
    rendered = ConsensusWriter().render(document)
    prefix = rendered.split("<!-- META:END -->", maxsplit=1)[0] + "<!-- META:END -->\n"
    editable = _extract_editable_markdown(editable_markdown)

    objectives = _editor_section_body(EDITOR_OBJECTIVES_PATTERN, editable)
    decisions = _editor_section_body(EDITOR_DECISIONS_PATTERN, editable)
    getting_started = _editor_section_body(EDITOR_GETTING_STARTED_PATTERN, editable)
    questions = _editor_section_body(EDITOR_QUESTIONS_PATTERN, editable)

    lines = [
        prefix.rstrip(),
        "",
        "## Objectives",
        "<!-- OBJECTIVES:START -->",
        objectives,
        "<!-- OBJECTIVES:END -->",
        "## Design Choices",
        "<!-- DECISIONS:START -->",
        decisions,
        "<!-- DECISIONS:END -->",
        "## Getting Started",
        getting_started,
        "",
    ]

    if questions.strip():
        lines.extend(["## Questions", questions, ""])

    return "\n".join(lines).rstrip() + "\n"



def _editor_section_body(pattern: re.Pattern[str], editable_markdown: str) -> str:
    match = pattern.search(editable_markdown)
    if match is None:
        return ""
    return match.group("body").strip("\n")



def _format_metadata(
    document: ConsensusDocument | None,
    tasks: Sequence[TaskInfo],
    *,
    empty_message: str,
) -> str:
    if document is None:
        return f"[dim]{empty_message}[/dim]"

    completed_count = _count_completed_tasks(tasks)
    total_count = len(tasks)
    updated_at = document.updated_at.isoformat() if document.updated_at is not None else "unknown"
    return "\n".join(
        [
            f"[b]Project:[/b] {document.project}",
            f"[b]Status:[/b] {document.status.value}",
            f"[b]Version:[/b] {document.version}",
            f"[b]Updated:[/b] {updated_at}",
            f"[b]Tasks:[/b] {completed_count}/{total_count}",
            f"[b]Pending Questions:[/b] {len(document.questions)}",
        ]
    )
