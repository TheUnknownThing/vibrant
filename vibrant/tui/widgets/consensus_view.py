"""Consensus editor/viewer widget for the redesigned TUI."""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, Center
from textual.message import Message
from textual.widgets import Button, Markdown, Static, TextArea

from ...consensus import ConsensusWriter
from ...models.consensus import ConsensusDocument, DEFAULT_CONSENSUS_CONTEXT
from ...models.task import TaskInfo, TaskStatus

from ...consensus import ConsensusParser, ConsensusWriter
from ...models.consensus import ConsensusDocument, ConsensusStatus
from ...models.task import TaskInfo

COMPLETED_TASK_STATUSES = {TaskStatus.COMPLETED, TaskStatus.ACCEPTED}
FALLBACK_CONSENSUS_MARKDOWN = "# Consensus Pool\n\n_No consensus markdown available._\n"
EMPTY_EDITABLE_MARKDOWN = DEFAULT_CONSENSUS_CONTEXT + "\n"


class ConsensusView(Static):
    """Shared consensus metadata + markdown preview/editor component."""

    class SaveRequested(Message):
        """Raised when the user wants to persist consensus edits."""

        def __init__(self, document: ConsensusDocument) -> None:
            super().__init__()
            self.document = document

    DEFAULT_CSS = """
    ConsensusView {
        height: 1fr;
        border: round $primary-background;
        background: $surface;
        padding: 0;
        layout: vertical;
    }

    #consensus-empty-state {
        width: 1fr;
        height: 1fr;
        align: center middle;
    }

    #consensus-empty-copy {
        width: 1fr;
        height: auto;
        align: center middle;
    }

    #consensus-empty-message,
    #consensus-empty-action {
        text-style: bold;
        content-align: center middle;
    }

    #consensus-empty-action {
        color: $accent;
    }

    #consensus-empty-action:hover {
        color: $surface;
    }

    #consensus-content {
        width: 1fr;
        height: 1fr;
    }

    #consensus-header {
        height: 3;
        padding: 1;
        background: $primary-background;
    }

    #consensus-title {
        width: 1fr;
        content-align: left middle;
        text-style: bold;
    }

    #consensus-body {
        height: 1fr;
    }

    #consensus-preview,
    #consensus-editor {
        width: 1fr;
        height: 1fr;
        padding: 0 1;
    }

    #consensus-preview {
        overflow-y: auto;
    }

    #consensus-editor {
        margin-top: 1;
        border: none;
    }

    #consensus-meta,
    #consensus-actions {
        height: auto;
        padding: 1;
        border-top: solid $primary-background;
    }

    #consensus-meta {
        color: $text-muted;
    }

    #consensus-actions {
        align: right middle;
    }

    #consensus-actions Button {
        margin-left: 1;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._writer = ConsensusWriter()
        self._parser = ConsensusParser()
        self._document: ConsensusDocument | None = self._get_consensus()
        self._source_path: Path | None = None
        self._tasks: tuple[TaskInfo, ...] = ()
        self._summary_message = "Consensus.md does not exist."
        self._editor_text_cache = ""
        self._last_saved_editable_markdown = ""
        self._last_saved_raw_markdown: str | None = None
        self._has_external_update = False
        self._is_editing = False

    def compose(self) -> ComposeResult:
        with Vertical(id="consensus-empty-state"):
            with Vertical(id="consensus-empty-copy"):
                yield Static("", id="consensus-empty-message")
                with Center():
                    yield Button("➕ Create File", id="consensus-empty-action", compact=True, flat=True)

        with Vertical(id="consensus-content"):
            with Horizontal(id="consensus-header"):
                yield Static("consensus.md", id="consensus-title")
                yield Button("Edit", id="consensus-edit-toggle", compact=True)
            with Vertical(id="consensus-body"):
                yield Markdown("", id="consensus-preview")
                yield TextArea("", id="consensus-editor", language="markdown", soft_wrap=True, show_line_numbers=True)
            yield Static("", id="consensus-meta", markup=True)
            with Horizontal(id="consensus-actions"):
                yield Button("Cancel", id="consensus-cancel", compact=True)
                yield Button("Save", id="consensus-save", variant="primary", compact=True)

    def on_mount(self) -> None:
        self._refresh_view()

    @property
    def is_editing(self) -> bool:
        """Return whether edit mode is active."""

        return self._is_editing

    @property
    def has_unsaved_changes(self) -> bool:
        """Return whether the editor differs from the saved baseline."""

        return self.current_editable_markdown != self._last_saved_editable_markdown

    @property
    def has_external_update(self) -> bool:
        """Return whether a newer saved document arrived while editing."""

        return self._has_external_update

    @property
    def current_editable_markdown(self) -> str:
        """Return the current editor contents."""

        editor = self._editor_widget()
        if editor is not None:
            self._editor_text_cache = editor.text
        return self._editor_text_cache

    def clear_summary(self, message: str = "File does not exist.") -> None:
        """Show the missing-file state with a short summary."""

        self._document = None
        self._tasks = ()
        self._source_path = None
        self._summary_message = message or "File does not exist."
        self._editor_text_cache = ""
        self._last_saved_editable_markdown = ""
        self._last_saved_raw_markdown = None
        self._has_external_update = False
        self._is_editing = False
        self._refresh_view()

    def update_consensus(
        self,
        document: ConsensusDocument,
        *,
        tasks: Sequence[TaskInfo] = (),
        source_path: str | Path | None = None,
        raw_markdown: str | None = None,
    ) -> None:
        """Refresh the widget from the latest consensus document."""

        editable_markdown = _extract_editable_markdown(raw_markdown or self._writer.render(document))
        current_text = self.current_editable_markdown
        had_unsaved_changes = self.has_unsaved_changes

        self._document = document.model_copy(deep=True)
        self._tasks = tuple(tasks)
        self._source_path = Path(source_path) if source_path is not None else None
        self._summary_message = ""
        self._last_saved_raw_markdown = raw_markdown or self._writer.render(document)
        self._last_saved_editable_markdown = editable_markdown

        if had_unsaved_changes:
            self._has_external_update = current_text != editable_markdown
            self._refresh_preview(current_text)
        else:
            self._set_editor_text(editable_markdown)
            self._refresh_preview(editable_markdown)
            self._has_external_update = False

        self._refresh_view()

    def action_toggle_edit_mode(self) -> None:
        """Switch between preview and edit modes."""

        if self._document is None:
            return
        self._is_editing = not self._is_editing
        self._refresh_preview(self.current_editable_markdown)
        self._refresh_view()

    def action_revert_edits(self) -> None:
        """Discard local edits and return to the latest saved document."""

        self._set_editor_text(self._last_saved_editable_markdown)
        self._refresh_preview(self._last_saved_editable_markdown)
        self._has_external_update = False
        self._is_editing = False
        self._refresh_view()

    def action_save_edits(self) -> None:
        """Emit a save request for the current editor contents."""

        if self._document is None:
            return
        document = self._parse_editor_document(self.current_editable_markdown)
        self._refresh_preview(self.current_editable_markdown)
        self._is_editing = False
        self.post_message(self.SaveRequested(document))
        self._refresh_view()

    def action_create_file(self) -> None:
        """Create a new consensus document using the default scaffold."""

        document = self._build_default_document()
        rendered = self._writer.render(document)
        self.update_consensus(document, source_path=self._source_path, raw_markdown=rendered)
        self.post_message(self.SaveRequested(document))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "consensus-edit-toggle":
            self.action_toggle_edit_mode()
        elif button_id == "consensus-cancel":
            self.action_revert_edits()
        elif button_id == "consensus-save":
            self.action_save_edits()
        elif button_id == "consensus-empty-action":
            self.action_create_file()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id != "consensus-editor":
            return
        self._editor_text_cache = event.text_area.text
        if self._is_editing:
            self._refresh_preview(self._editor_text_cache)
        self._refresh_view()

    def _refresh_view(self) -> None:
        empty_state = self.query_one("#consensus-empty-state", Vertical)
        content = self.query_one("#consensus-content", Vertical)
        preview = self.query_one("#consensus-preview", Markdown)
        editor = self.query_one("#consensus-editor", TextArea)
        meta = self.query_one("#consensus-meta", Static)
        actions = self.query_one("#consensus-actions", Horizontal)
        title = self.query_one("#consensus-title", Static)
        toggle = self.query_one("#consensus-edit-toggle", Button)
        create_message = self.query_one("#consensus-empty-message", Static)
        save_button = self.query_one("#consensus-save", Button)

        has_document = self._document is not None
        empty_state.display = not has_document
        content.display = has_document

        create_message.update(self._summary_message or "File does not exist.")
        title.update(self._source_label())

        toggle.label = "Preview" if self._is_editing else "Edit"
        toggle.display = has_document
        preview.display = has_document and not self._is_editing
        editor.display = has_document and self._is_editing
        meta.display = has_document and not self._is_editing
        actions.display = has_document and self._is_editing
        save_button.disabled = not self.has_unsaved_changes

        if has_document:
            meta.update(self._metadata_markup())
            self._refresh_preview(self.current_editable_markdown if self._is_editing else self._last_saved_editable_markdown)

    def _metadata_markup(self) -> str:
        if self._document is None:
            return ""

        status_color = {
            ConsensusStatus.INIT: "white",
            ConsensusStatus.PLANNING: "cyan",
            ConsensusStatus.EXECUTING: "purple",
            ConsensusStatus.PAUSED: "yellow",
            ConsensusStatus.COMPLETED: "blue",
            ConsensusStatus.FAILED: "red",
        }.get(self._document.status, "white")
        metadata = f"[b]Project[/b]: {self._document.project} v{self._document.version} (Status [{status_color}]{self._document.status.value}[/])"

        if self._document.updated_at is not None:
            metadata += f"\n[b]Updated[/b]: {self._document.updated_at.astimezone(timezone.utc).isoformat()}"
        return metadata

    def _source_label(self) -> str:
        if self._source_path is not None:
            return self._source_path.name
        return "consensus.md"

    def _editor_widget(self) -> TextArea | None:
        with suppress(Exception):
            return self.query_one("#consensus-editor", TextArea)
        return None

    def _set_editor_text(self, text: str) -> None:
        normalized = _normalize_markdown(text)
        self._editor_text_cache = normalized
        editor = self._editor_widget()
        if editor is not None and editor.text != normalized:
            editor.load_text(normalized)

    def _refresh_preview(self, markdown_text: str) -> None:
        preview = self.query_one("#consensus-preview", Markdown)
        preview.update(markdown_text or "_Consensus is empty._")

    def _parse_editor_document(self, editable_markdown: str) -> ConsensusDocument:
        if self._document is None:
            raise ValueError("Consensus document is unavailable")
        full_markdown = _compose_consensus_markdown(self._writer, self._document, editable_markdown)
        return self._parser.parse(full_markdown)

    def _build_default_document(self) -> ConsensusDocument:
        timestamp = datetime.now(timezone.utc).replace(microsecond=0)
        return ConsensusDocument(
            project=_infer_project_name(self._source_path),
            created_at=timestamp,
            updated_at=timestamp,
            version=0,
            status=ConsensusStatus.INIT,
            objectives="",
            decisions=[],
            getting_started=_DEFAULT_GETTING_STARTED,
        )
    
    @property
    def _orchestrator_facade(self) -> OrchestratorFacade:
        """Access the app's orchestrator facade"""
        assert self.app is not None
        return self.app.orchestrator_facade
    
    def _get_consensus(self) -> ConsensusDocument | None:
        """Get the current consensus from the orchestrator."""
        return self._orchestrator_facade.get_consensus_document()


def _extract_editable_markdown(markdown_text: str) -> str:
    if not markdown_text.strip():
        return EMPTY_EDITABLE_MARKDOWN

    meta_end_marker = "<!-- META:END -->"
    text = markdown_text
    if meta_end_marker in text:
        text = text.split(meta_end_marker, maxsplit=1)[1].removeprefix("\n")

    text = text.rstrip("\n")
    if not text.strip():
        return EMPTY_EDITABLE_MARKDOWN
    return text + "\n"

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
        ]
    )
