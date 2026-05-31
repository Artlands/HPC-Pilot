"""HPC Pilot TUI v2 — modern split-pane interface.

See spec 02 §7 for the interaction layer contract.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import ContentSwitcher, Static

from hpc_agent.core.tui.approval import ApprovalModal, ApprovalResult
from hpc_agent.core.tui.audit_view import AuditView
from hpc_agent.core.tui.chat import ChatView, InputSubmitted
from hpc_agent.core.tui.css import CSS
from hpc_agent.core.tui.dashboard import DashboardView
from hpc_agent.core.tui.plan_view import PlanView
from hpc_agent.core.tui.sidebar import Sidebar, ViewSelected
from hpc_agent.core.tui.tool_explorer import ToolExplorer
from hpc_agent.core.tui.widgets import StatusBar

if TYPE_CHECKING:
    from hpc_agent.core.plan import Plan, Step
    from hpc_agent.core.shell import ShellSession
    from hpc_agent.safety.diff import Diff
    from hpc_agent.safety.gate import Gate

# View index mapping for ctrl+number shortcuts
VIEW_IDS = ["chat", "plan", "dashboard", "tools", "audit"]


class HPCPilotApp(App[None]):
    """Modern split-pane TUI for HPC Pilot."""

    DARK = True
    CSS = CSS
    TITLE = "HPC Pilot"

    BINDINGS = [
        Binding("ctrl+d", "quit", "Exit", show=True),
        Binding("ctrl+c", "quit", "", show=False),
        Binding("ctrl+l", "clear_chat", "Clear", show=False),
        Binding("ctrl+r", "quick_run", "/run", show=False),
        Binding("ctrl+a", "quick_approve", "/approve", show=False),
        Binding("ctrl+tab", "next_view", "Next", show=False, priority=True),
        Binding("ctrl+shift+tab", "prev_view", "Prev", show=False, priority=True),
        Binding("ctrl+1", "switch_view_0", "", show=False, priority=True),
        Binding("ctrl+2", "switch_view_1", "", show=False, priority=True),
        Binding("ctrl+3", "switch_view_2", "", show=False, priority=True),
        Binding("ctrl+4", "switch_view_3", "", show=False, priority=True),
        Binding("ctrl+5", "switch_view_4", "", show=False, priority=True),
    ]

    current_view: reactive[str] = reactive("chat")
    current_plan: reactive[Plan | None] = reactive(None)

    def __init__(self, session: ShellSession) -> None:
        super().__init__()
        self.session = session

    def compose(self) -> ComposeResult:
        with Vertical(id="app-layout"):
            yield StatusBar(id="status-bar")
            with Horizontal(id="main-body"):
                yield Sidebar(id="sidebar")
                with ContentSwitcher(id="main-content", initial="chat"):
                    yield ChatView(self.session, id="chat")
                    yield PlanView(id="plan")
                    yield DashboardView(id="dashboard")
                    yield ToolExplorer(id="tools")
                    yield AuditView(id="audit")
            with Horizontal(id="actions-bar"):
                yield Static(
                    "[cyan]/run[/]  [cyan]/approve[/]  [cyan]/show[/]",
                    id="action-cmds",
                )
                yield Static(
                    "[dim]^tab switch  ^1-5 jump  ^d exit[/]",
                    id="action-hints",
                )

    def on_mount(self) -> None:
        # Wire up status bar
        status = self.query_one("#status-bar", StatusBar)
        status.actor = self.session.actor
        status.actor_role = self.session.actor_role.value

        # Wire session write to chat
        self.session.write = self._write_to_chat

    # ── View switching ─────────────────────────────────────────

    @on(ViewSelected, "#sidebar")
    def handle_view_selected(self, event: ViewSelected) -> None:
        self._switch_view(event.view_id)

    def action_next_view(self) -> None:
        idx = VIEW_IDS.index(self.current_view) if self.current_view in VIEW_IDS else 0
        next_idx = (idx + 1) % len(VIEW_IDS)
        self._switch_view(VIEW_IDS[next_idx])

    def action_prev_view(self) -> None:
        idx = VIEW_IDS.index(self.current_view) if self.current_view in VIEW_IDS else 0
        prev_idx = (idx - 1) % len(VIEW_IDS)
        self._switch_view(VIEW_IDS[prev_idx])

    def action_switch_view_0(self) -> None:
        self._switch_view("chat")

    def action_switch_view_1(self) -> None:
        self._switch_view("plan")

    def action_switch_view_2(self) -> None:
        self._switch_view("dashboard")

    def action_switch_view_3(self) -> None:
        self._switch_view("tools")

    def action_switch_view_4(self) -> None:
        self._switch_view("audit")

    def _switch_view(self, view_id: str) -> None:
        self.current_view = view_id
        content = self.query_one("#main-content", ContentSwitcher)
        content.current = view_id
        sidebar = self.query_one("#sidebar", Sidebar)
        sidebar.active_view = view_id

        # Focus the input if switching to chat
        if view_id == "chat":
            chat = self.query_one("#chat", ChatView)
            chat.query_one("#chat-input").focus()

    # ── Actions ────────────────────────────────────────────────

    def action_quit(self) -> None:
        self.exit()

    def action_clear_chat(self) -> None:
        chat = self.query_one("#chat", ChatView)
        chat.clear()

    def action_quick_run(self) -> None:
        self._submit("/run")

    def action_quick_approve(self) -> None:
        self._submit("/approve")

    # ── Chat input handling ────────────────────────────────────

    @on(InputSubmitted)
    def handle_input(self, event: InputSubmitted) -> None:
        self._submit(event.text)

    def _submit(self, line: str) -> None:
        chat = self.query_one("#chat", ChatView)
        chat.write_user(line)
        self.session.handle_line(line)
        self._update_status()

        if not self.session.running:
            self.exit()

    def _write_to_chat(self, text: str) -> None:
        """Callback for ShellSession.write — routes output to chat view."""
        chat = self.query_one("#chat", ChatView)
        chat.write_agent(text)

    # ── Plan updates ───────────────────────────────────────────

    def _update_status(self) -> None:
        status = self.query_one("#status-bar", StatusBar)
        plan = self.session.current_plan
        if plan is not None:
            status.plan_id = plan.id
            status.plan_state = plan.state.value
        else:
            status.plan_id = ""
            status.plan_state = "none"

        # Update plan view
        plan_view = self.query_one("#plan", PlanView)
        plan_view.plan = self.session.current_plan

    # ── Approval flow ──────────────────────────────────────────

    def request_approval(
        self, step: Step, diff: Diff, gate: Gate
    ) -> None:
        """Show the approval modal for a step."""
        self.push_screen(ApprovalModal(step, diff, gate))

    @on(ApprovalResult)
    def handle_approval_result(self, event: ApprovalResult) -> None:
        """Handle approve/deny from the modal."""
        if event.approved:
            self._submit(f"/approve {event.step_id}")
        else:
            chat = self.query_one("#chat", ChatView)
            chat.write_agent(f"Approval denied for step {event.step_id}.")
