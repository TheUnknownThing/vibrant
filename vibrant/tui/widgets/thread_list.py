"""Thread list sidebar widget."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Static, ListView, ListItem, Label

from ...models import ThreadInfo, ThreadStatus


STATUS_ICONS = {
    ThreadStatus.ACTIVE: "🟢",
    ThreadStatus.RUNNING: "⚡",
    ThreadStatus.IDLE: "🔵",
    ThreadStatus.ERROR: "🔴",
    ThreadStatus.STOPPED: "⚪",
}


class ThreadListItem(ListItem):
    """A single thread entry in the sidebar."""

    def __init__(self, thread: ThreadInfo, **kwargs) -> None:
        super().__init__(**kwargs)
        self.thread_info = thread

    def compose(self) -> ComposeResult:
        t = self.thread_info
        icon = STATUS_ICONS.get(t.status, "⚪")
        title = t.display_title
        model = t.model or "default"
        yield Static(
            f"{icon} {title}\n"
            f"  [dim]{model} · {t.message_count} msgs[/dim]",
            markup=True,
        )


class ThreadList(Static):
    """Sidebar listing all threads with status indicators."""

    BINDINGS = [
        Binding("n", "new_thread", "New Thread"),
        Binding("d", "delete_thread", "Delete"),
    ]

    selected_thread_id: reactive[str | None] = reactive(None)

    class ThreadSelected(Message):
        """Emitted when a thread is clicked/selected."""
        def __init__(self, thread_id: str) -> None:
            super().__init__()
            self.thread_id = thread_id

    class NewThreadRequested(Message):
        """Emitted when user wants a new thread."""

    class DeleteThreadRequested(Message):
        """Emitted when user wants to delete the selected thread."""
        def __init__(self, thread_id: str) -> None:
            super().__init__()
            self.thread_id = thread_id

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._list_view: ListView | None = None
        self._threads: list[ThreadInfo] = []
        self._items_map: dict[str, int] = {}  # thread_id → index

    def compose(self) -> ComposeResult:
        yield Static("[b]Threads[/b]", id="thread-list-header", markup=True)
        self._list_view = ListView(id="thread-listview")
        yield self._list_view

    def update_threads(self, threads: list[ThreadInfo]) -> None:
        """Refresh the list with the given threads."""
        self._threads = threads
        if self._list_view is None:
            return
        self._list_view.clear()
        self._items_map.clear()
        for idx, thread in enumerate(threads):
            self._list_view.append(ThreadListItem(thread))
            self._items_map[thread.id] = idx
        # Re-select if we had a selection
        if self.selected_thread_id and self.selected_thread_id in self._items_map:
            self._list_view.index = self._items_map[self.selected_thread_id]

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle list item selection."""
        item = event.item
        if isinstance(item, ThreadListItem):
            self.selected_thread_id = item.thread_info.id
            self.post_message(self.ThreadSelected(item.thread_info.id))

    def action_new_thread(self) -> None:
        self.post_message(self.NewThreadRequested())

    def action_delete_thread(self) -> None:
        if self.selected_thread_id:
            self.post_message(self.DeleteThreadRequested(self.selected_thread_id))
