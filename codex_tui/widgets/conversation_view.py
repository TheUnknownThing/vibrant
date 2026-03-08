"""Conversation view widget — renders thread messages with Rich/Markdown formatting."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll, Vertical
from textual.widgets import Static, Markdown, Collapsible

from ..models import ItemInfo, ItemType, ThreadInfo, TurnInfo, TurnRole


class MessageBubble(Static):
    """A single message (user or assistant) in the conversation."""

    def __init__(self, turn: TurnInfo, **kwargs) -> None:
        super().__init__(**kwargs)
        self.turn = turn

    def compose(self) -> ComposeResult:
        role_label = "🧑 You" if self.turn.role == TurnRole.USER else "🤖 Codex"
        role_class = "user-msg" if self.turn.role == TurnRole.USER else "assistant-msg"
        self.add_class(role_class)

        yield Static(f"[b]{role_label}[/b]", classes="msg-role", markup=True)

        for item in self.turn.items:
            yield from self._render_item(item)

    def _render_item(self, item: ItemInfo) -> list:
        """Yield one or more widgets for a single item."""
        widgets = []

        if item.type == ItemType.TEXT:
            is_reasoning = item.metadata.get("is_reasoning", False)
            if is_reasoning:
                widgets.append(Collapsible(
                    Static(item.content, markup=False),
                    title="💭 Reasoning",
                    collapsed=True,
                    classes="reasoning-collapsible",
                ))
            else:
                if self.turn.role == TurnRole.ASSISTANT:
                    widgets.append(Markdown(item.content, classes="msg-content"))
                else:
                    widgets.append(Static(item.content, classes="msg-content", markup=False))

        elif item.type == ItemType.COMMAND:
            cmd = item.metadata.get("command", item.content)
            output = item.metadata.get("output", "")
            exit_code = item.metadata.get("exit_code")
            duration = item.metadata.get("duration_ms")

            status_icon = "✅" if exit_code == 0 else "❌" if exit_code is not None else "⏳"
            duration_str = f" ({duration}ms)" if duration is not None else ""
            title = f"{status_icon} $ {cmd}{duration_str}"

            # Command output inside a Collapsible — collapsed by default
            if output:
                display_output = output if len(output) <= 2000 else output[:2000] + "\n… (truncated)"
                widgets.append(Collapsible(
                    Static(display_output, markup=False, classes="msg-command-output"),
                    title=title,
                    collapsed=True,
                    classes="command-collapsible",
                ))
            else:
                widgets.append(Static(title, classes="msg-command-header", markup=False))

        elif item.type == ItemType.FILE_CHANGE:
            widgets.append(Static(
                f"✏ Modified: {item.content}",
                classes="msg-file",
                markup=False,
            ))

        elif item.type == ItemType.FILE_READ:
            widgets.append(Static(
                f"📖 Read: {item.content}",
                classes="msg-file",
                markup=False,
            ))

        elif item.type == ItemType.APPROVAL_REQUEST:
            widgets.append(Static(
                f"⚠ Approval needed: {item.content}",
                classes="msg-approval",
                markup=False,
            ))

        else:
            if item.content:
                widgets.append(Static(
                    item.content[:300],
                    classes="msg-unknown",
                    markup=False,
                ))

        return widgets


class StreamingBubble(Vertical):
    """A live-updating container for streaming assistant responses."""

    DEFAULT_CSS = """
    StreamingBubble {
        margin: 1 0;
        padding: 0 1;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._md: Markdown | None = None

    def compose(self) -> ComposeResult:
        yield Static("[b]🤖 Codex[/b]", classes="msg-role", markup=True)
        self._md = Markdown("", classes="msg-content")
        yield self._md

    def update_text(self, text: str) -> None:
        if self._md is not None:
            self._md.update(text)


class ConversationView(Static):
    """Scrollable view of the conversation for the active thread."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._scroll: VerticalScroll | None = None
        self._current_thread_id: str | None = None
        self._streaming_bubble: StreamingBubble | None = None

    def compose(self) -> ComposeResult:
        yield Static(
            "[dim]No thread selected. Press [b]Ctrl+N[/b] to create one.[/dim]",
            id="empty-state",
            markup=True,
        )
        self._scroll = VerticalScroll(id="conversation-scroll")
        self._scroll.display = False
        yield self._scroll

    def show_thread(self, thread: ThreadInfo) -> None:
        """Display the conversation for a thread."""
        self._current_thread_id = thread.id
        self._streaming_bubble = None

        empty_state = self.query_one("#empty-state", Static)
        empty_state.display = False

        if self._scroll:
            self._scroll.display = True
            self._scroll.remove_children()
            for turn in thread.turns:
                if turn.items:
                    self._scroll.mount(MessageBubble(turn))
            self._scroll.scroll_end(animate=False)

    def update_streaming_text(self, text: str) -> None:
        """Update streaming text in real-time."""
        if not self._scroll:
            return

        try:
            empty_state = self.query_one("#empty-state", Static)
            empty_state.display = False
            self._scroll.display = True
        except Exception:
            pass

        if self._streaming_bubble is None:
            self._streaming_bubble = StreamingBubble(classes="assistant-msg")
            self._scroll.mount(self._streaming_bubble)

        self._streaming_bubble.update_text(text)
        self._scroll.scroll_end(animate=False)

    def clear(self) -> None:
        """Clear the conversation view."""
        self._streaming_bubble = None
        if self._scroll:
            self._scroll.remove_children()
        empty_state = self.query_one("#empty-state", Static)
        empty_state.display = True
        if self._scroll:
            self._scroll.display = False
        self._current_thread_id = None
