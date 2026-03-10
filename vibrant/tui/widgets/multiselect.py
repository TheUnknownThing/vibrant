"""General-purpose multiline selection widget for Textual"""

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Header, Footer, Static
from rich.text import Text

class Multiselect(Widget, can_focus=True):
    """A multi-line entry widget selectable via keyboard and mouse."""

    DEFAULT_CSS = """
    Multiselect {
        height: auto;
        padding: 0 2;
        background: $surface;
    }
    /* Applied conditionally via the watch_show_frame method */
    Multiselect.-framed {
        border: round $primary;
        padding: 1 2;
    }
    """

    # Bindings map key presses to action_* methods
    BINDINGS =[
        Binding("up", "move_cursor(-1)", "Up", show=True),
        Binding("down", "move_cursor(1)", "Down", show=True),
        Binding("tab", "move_cursor(1)", "Next", show=False),
        Binding("shift+tab", "move_cursor(-1)", "Prev", show=False),
        Binding("ctrl+tab", "move_cursor(1)", "Ctrl+Next", show=False),
        Binding("enter", "select", "Confirm", show=True),
    ]

    # Reactive attributes automatically re-render the widget when modified
    cursor_index = reactive(0)
    show_frame = reactive(False)
    active_style = reactive("bold reverse")

    class Selected(Message):
        """Custom message emitted when an entry is selected via Enter or Mouse click."""
        def __init__(self, index: int, value: str) -> None:
            self.index = index
            self.value = value
            super().__init__()

    def __init__(
        self,
        entries: list[str],
        show_frame: bool = False,
        active_style: str = "bold cyan",
        inactive_style: str = "dim",
        active_prefix: str = ">  ",
        inactive_prefix: str = "  ",
        padding: int = 0,
        **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self.entries = entries
        self.show_frame = show_frame
        self.active_style = active_style
        self.inactive_style = inactive_style
        self.active_prefix = active_prefix
        self.inactive_prefix = inactive_prefix
        self.padding = padding
    def on_mount(self) -> None:
        """Called when the widget is added to the app."""
        # Initialize the frame class based on the reactive state
        self.set_class(self.show_frame, "-framed")

    def watch_show_frame(self, show_frame: bool) -> None:
        """Reacts dynamically if `show_frame` is toggled during runtime."""
        self.set_class(show_frame, "-framed")

    def validate_cursor_index(self, value: int) -> int:
        """Ensures the cursor index stays within valid bounds."""
        if not self.entries:
            return 0
        return max(0, min(value, len(self.entries) - 1))

    def render(self) -> Text:
        """Renders the textual lines, applying user formatting to the active index."""
        text = Text()
        for i, entry in enumerate(self.entries):
            if i == self.cursor_index:
                # Active selection logic
                text.append(f"{self.active_prefix}{entry}", style=self.active_style)
            else:
                # Inactive logic
                text.append(f"{self.inactive_prefix}{entry}", style=self.inactive_style)
            
            # Add a newline except for the very last item
            if i < len(self.entries) - 1:
                text.append("\n" * (self.padding + 1))
                
        return text

    def action_move_cursor(self, offset: int) -> None:
        """Triggered by Arrow keys, Tab, or Ctrl+Tab."""
        self.cursor_index += offset

    def action_select(self) -> None:
        """Triggered by the Enter key."""
        if self.entries:
            self.post_message(
                self.Selected(self.cursor_index, self.entries[self.cursor_index])
            )

    def on_click(self, event: events.Click) -> None:
        """Handle mouse click events to select an entry directly."""
        # Subtract the start of the content region from the global click Y position
        # This accurately calculates the line clicked, entirely ignoring borders and padding.
        content_y = event.y - self.content_region.y
        
        if 0 <= content_y < len(self.entries):
            self.cursor_index = content_y
            self.action_select()
