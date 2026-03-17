"""Execution workspace screen."""

from __future__ import annotations

from collections.abc import Sequence

from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Static, TabbedContent, TabPane

from ...models.task import TaskInfo
from ...orchestrator.facade import OrchestratorFacade
from ..widgets.agent_log import AgentOutput
from ..widgets.chat_panel import ChatPanel
from ..widgets.consensus_view import ConsensusView
from ..widgets.input_bar import InputBar
from ..widgets.plan_tree import PlanTree
from ..widgets.task_status import TaskStatusView

_MOBILE_BREAKPOINT = 110


class VibingScreen(Static):
    """Task execution screen shown during vibing."""

    DEFAULT_CSS = """
    VibingScreen {
        height: 1fr;
    }

    VibingScreen #vibing-shell {
        height: 1fr;
    }

    VibingScreen #plan-panel {
        width: 34;
        min-width: 24;
        margin-right: 1;
    }

    VibingScreen.-mobile #vibing-shell {
        layout: vertical;
    }

    VibingScreen.-mobile #plan-panel {
        width: 1fr;
        min-width: 0;
        height: 10;
        margin-right: 0;
        margin-bottom: 1;
    }

    VibingScreen.-mobile #workspace-tabs {
        margin-top: 0;
    }

    VibingScreen.-mobile #workspace-tabs > ContentTabs,
    VibingScreen.-mobile #workspace-tabs Tabs {
        height: auto;
    }

    VibingScreen #vibing-main {
        height: 1fr;
    }

    VibingScreen #workspace-tabs {
        margin-top: 1;
        height: 1fr;
    }

    VibingScreen #workspace-tabs > ContentSwitcher,
    VibingScreen #workspace-tabs > ContentSwitcher > TabPane {
        height: 1fr;
    }

    VibingScreen #workspace-tabs > ContentTabs,
    VibingScreen #workspace-tabs Tabs {
        height: 2;
        margin-bottom: 1;
    }

    VibingScreen #task-status-panel,
    VibingScreen #chat-history-panel,
    VibingScreen #consensus-panel,
    VibingScreen #agent-output-panel {
        height: 1fr;
    }

    VibingScreen #chat-roadmap-status {
        height: auto;
        padding: 1 2;
        border: round $primary-background;
        background: $surface;
        margin-bottom: 1;
    }

    VibingScreen #conversation-panel {
        height: 1fr;
    }

    VibingScreen #input-panel {
        height: auto;
        border-top: solid $primary-background;
        padding: 0 1;
        background: $surface;
        margin-top: 1;
    }
    """

    _VALID_TABS = {"task-status", "chat-history", "consensus", "agent-logs"}

    def __init__(self, *, initial_tab: str = "task-status") -> None:
        super().__init__()
        self._initial_tab = initial_tab if initial_tab in self._VALID_TABS else "task-status"
        self._active_tab = self._initial_tab
        self._selected_task_id: str | None = None
        self._is_mobile_layout: bool | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="vibing-shell"):
            yield PlanTree(id="plan-panel")
            with Vertical(id="vibing-main"):
                with TabbedContent(initial=self._initial_tab, id="workspace-tabs"):
                    with TabPane("Task Status", id="task-status"):
                        yield TaskStatusView(id="task-status-panel")
                    with TabPane("Chat History", id="chat-history"):
                        with Vertical(id="chat-history-panel"):
                            yield Static(
                                "[b]Generating Roadmap[/b]\n\nChat history will appear here once roadmap generation finishes.",
                                id="chat-roadmap-status",
                                markup=True,
                            )
                            yield ChatPanel(id="conversation-panel")
                    with TabPane("Consensus", id="consensus"):
                        yield ConsensusView(id="consensus-panel")
                    with TabPane("Agent Logs", id="agent-logs"):
                        yield AgentOutput(id="agent-output-panel")
                yield InputBar(id="input-panel")

    def on_mount(self) -> None:
        self.set_active_tab(self._initial_tab)
        self._apply_responsive_layout()

    def on_resize(self, event: events.Resize) -> None:
        del event
        self._apply_responsive_layout()

    def _apply_responsive_layout(self) -> None:
        is_mobile = self.size.width < _MOBILE_BREAKPOINT
        if self._is_mobile_layout == is_mobile:
            return
        self._is_mobile_layout = is_mobile
        self.set_class(is_mobile, "-mobile")

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        if event.control.id != "workspace-tabs":
            return
        tab_id = event.pane.id or ""
        if tab_id in self._VALID_TABS:
            self._active_tab = tab_id
            if tab_id == "agent-logs":
                self._request_agent_output_history()

    @property
    def active_tab(self) -> str:
        """Return the currently active main-screen tab."""

        return self._active_tab

    def set_active_tab(self, tab_id: str) -> None:
        if tab_id not in self._VALID_TABS:
            return

        if tab_id == "consensus":
            self.consensus_view.load_document()
            self.consensus_view.assert_facade()

        self._active_tab = tab_id
        self.query_one("#workspace-tabs", TabbedContent).active = tab_id
        if tab_id == "agent-logs":
            self._request_agent_output_history()

    def _request_agent_output_history(self) -> None:
        ensure_agent_output_loaded = getattr(self.app, "ensure_agent_output_loaded", None)
        if callable(ensure_agent_output_loaded):
            self.call_after_refresh(ensure_agent_output_loaded)

    def sync_task_views(
        self,
        tasks: Sequence[TaskInfo],
        *,
        facade: OrchestratorFacade | None,
        agent_summaries: dict[str, str] | None = None,
    ) -> None:
        """Refresh the task tree and task-status panel from the latest roadmap state."""

        task_list = list(tasks)
        self.task_status.bind(facade)
        if not task_list:
            self._selected_task_id = None
            self.plan_tree.clear_tasks("No roadmap tasks found.")
            self.task_status.clear_tasks("No roadmap tasks found.")
            return

        selected_task_id = self.task_status.sync(task_list, selected_task_id=self._selected_task_id)
        self._selected_task_id = selected_task_id
        self.plan_tree.update_tasks(
            task_list,
            agent_summaries=agent_summaries,
            selected_task_id=selected_task_id,
        )
        if selected_task_id is not None:
            self.call_after_refresh(self._restore_selected_task, selected_task_id)

    def on_plan_tree_task_highlighted(self, event: PlanTree.TaskHighlighted) -> None:
        self._selected_task_id = event.task.id
        self.task_status.select_task(event.task.id)

    def on_plan_tree_task_selected(self, event: PlanTree.TaskSelected) -> None:
        self._selected_task_id = event.task.id
        self.task_status.select_task(event.task.id)
        self.show_task_status()

    def _restore_selected_task(self, task_id: str) -> None:
        if self._selected_task_id != task_id:
            return
        self.task_status.select_task(task_id)
        self.plan_tree.select_task(task_id)

    @property
    def agent_output(self) -> AgentOutput:
        """Return the agent output widget."""

        return self.query_one(AgentOutput)

    @property
    def chat_panel(self) -> ChatPanel:
        """Return the Gatekeeper chat panel."""

        return self.query_one(ChatPanel)

    @property
    def consensus_view(self) -> ConsensusView:
        """Return the consensus view widget."""

        return self.query_one(ConsensusView)

    @property
    def input_bar(self) -> InputBar:
        """Return the vibing input bar."""

        return self.query_one(InputBar)

    @property
    def plan_tree(self) -> PlanTree:
        """Return the roadmap tree widget."""

        return self.query_one(PlanTree)

    @property
    def task_status(self) -> TaskStatusView:
        """Return the task status widget."""

        return self.query_one(TaskStatusView)

    def focus_primary_input(self) -> None:
        """Focus the vibing input."""

        self.input_bar.focus_input()

    def set_input_placeholder(self, text: str) -> None:
        """Update the vibing input placeholder."""

        self.input_bar.set_placeholder(text)

    def show_agent_logs(self) -> None:
        """Switch to the agent logs tab."""

        self.set_active_tab("agent-logs")

    def show_chat_history(self) -> None:
        """Switch to the chat history tab."""

        self.set_active_tab("chat-history")

    def show_consensus(self) -> None:
        """Switch to the consensus tab."""

        self.set_active_tab("consensus")

    def show_task_status(self) -> None:
        """Switch to the task status tab."""

        self.set_active_tab("task-status")
