"""Tests for HPC Pilot TUI v2."""
from __future__ import annotations

from rich.text import Text
from textual.widgets import Input, RichLog

from hpc_agent.core.plan import Plan, PlanState, Step, StepStatus
from hpc_agent.core.shell import ShellSession
from hpc_agent.core.tui.app import VIEW_IDS, HPCPilotApp
from hpc_agent.core.tui.approval import ApprovalModal, ApprovalResult
from hpc_agent.core.tui.chat import ASCII_LOGO, ChatView, InputSubmitted
from hpc_agent.core.tui.dashboard import DashboardView
from hpc_agent.core.tui.plan_view import PlanView
from hpc_agent.core.tui.sidebar import NAV_ITEMS, NavListItem, Sidebar, ViewSelected
from hpc_agent.core.tui.tool_explorer import ToolExplorer
from hpc_agent.core.tui.widgets import (
    NODE_STATE_COLOR,
    PLAN_STATE_COLOR,
    RISK_COLOR,
    STEP_ICON,
    StatusBar,
    fmt_plan_state,
    fmt_risk,
    fmt_step_icon,
)
from hpc_agent.exec.rbac import Role
from hpc_agent.safety.diff import Change, Diff
from hpc_agent.safety.gate import Gate

# ── helpers ──────────────────────────────────────────────────────


def _session() -> ShellSession:
    return ShellSession(
        actor="alice",
        actor_role=Role.OPERATOR,
        policy=None,
        write=lambda _: None,
    )


def _plan() -> Plan:
    return Plan(
        id="test-plan-uuid-1234",
        intent="give alice 48h on gpu qos",
        actor="alice",
        steps=[
            Step(
                id="s1",
                tool="slurm.manage_qos",
                input={"name": "gpu", "op": "modify", "max_wall_min": 2880},
                status=StepStatus.PENDING,
            ),
            Step(
                id="s2",
                tool="slurm.extend_account",
                input={"name": "proj", "op": "modify"},
                depends_on=["s1"],
                status=StepStatus.PENDING,
            ),
        ],
        state=PlanState.DRAFT,
    )


def _step_done() -> Step:
    return Step(
        id="s1",
        tool="slurm.manage_qos",
        input={"name": "gpu", "op": "modify", "max_wall_min": 2880},
        status=StepStatus.DONE,
    )


def _step_failed() -> Step:
    from hpc_agent.tools.errors import ErrorKind, ToolError
    from hpc_agent.tools.result import ToolResult

    return Step(
        id="s1",
        tool="slurm.manage_qos",
        input={"name": "gpu"},
        status=StepStatus.FAILED,
        result=ToolResult.failed(
            ToolError(kind=ErrorKind.COMMAND_FAILED, message="sacctmgr failed")
        ),
    )


def _diff() -> Diff:
    return Diff(
        changes=[
            Change(
                target="qos/gpu",
                field="max_wall_min",
                before="24:00:00",
                after="48:00:00",
                op="modify",
            )
        ],
        blast_radius=1,
        reversible=True,
    )


# ══════════════════════════════════════════════════════════════════
# 1. Widgets (widgets.py)
# ══════════════════════════════════════════════════════════════════


class TestStepIcon:
    def test_covers_all_statuses(self) -> None:
        for status in StepStatus:
            assert status.value in STEP_ICON
            icon, style = STEP_ICON[status.value]
            assert icon and style

    def test_fmt_step_icon_pending(self) -> None:
        result = fmt_step_icon("pending")
        assert isinstance(result, Text)
        assert "○" in result.plain

    def test_fmt_step_icon_done(self) -> None:
        result = fmt_step_icon("done")
        assert "●" in result.plain

    def test_fmt_step_icon_unknown(self) -> None:
        result = fmt_step_icon("unknown")
        assert "?" in result.plain


class TestPlanStateColor:
    def test_covers_common_states(self) -> None:
        for state in ("draft", "running", "paused", "done", "failed", "cancelled"):
            assert state in PLAN_STATE_COLOR

    def test_fmt_plan_state(self) -> None:
        result = fmt_plan_state("running")
        assert isinstance(result, Text)
        assert "running" in result.plain
        assert "●" in result.plain


class TestRiskColor:
    def test_covers_all_tiers(self) -> None:
        for tier in ("read", "low", "medium", "high"):
            assert tier in RISK_COLOR

    def test_fmt_risk(self) -> None:
        result = fmt_risk("high")
        assert isinstance(result, Text)
        assert "HIGH" in result.plain


class TestNodeStateColor:
    def test_covers_common_states(self) -> None:
        for state in ("UP", "IDLE", "DRAINED", "DOWN", "UNKNOWN"):
            assert state in NODE_STATE_COLOR


class TestStatusBar:
    def test_render_no_plan(self) -> None:
        bar = StatusBar()
        bar.actor = "alice"
        bar.actor_role = "operator"
        bar.plan_state = "none"
        bar.plan_id = ""
        result = bar.render()
        assert "alice" in result.plain
        assert "operator" in result.plain
        assert "no active plan" in result.plain

    def test_render_with_plan(self) -> None:
        bar = StatusBar()
        bar.actor = "alice"
        bar.actor_role = "admin"
        bar.plan_id = "abc12345-xxxx"
        bar.plan_state = "running"
        result = bar.render()
        assert "abc12345" in result.plain
        assert "running" in result.plain


# ══════════════════════════════════════════════════════════════════
# 2. Sidebar (sidebar.py)
# ══════════════════════════════════════════════════════════════════


class TestNavItems:
    def test_has_five_views(self) -> None:
        assert len(NAV_ITEMS) == 5

    def test_first_is_chat(self) -> None:
        assert NAV_ITEMS[0][0] == "chat"

    def test_all_have_ids(self) -> None:
        for view_id, icon, label in NAV_ITEMS:
            assert view_id
            assert icon
            assert label


class TestNavListItem:
    def test_render(self) -> None:
        item = NavListItem("chat", "💬", "Chat")
        result = item.render()
        assert "Chat" in result.plain
        assert "💬" in result.plain

    def test_stores_view_id(self) -> None:
        item = NavListItem("plan", "◆", "Plan")
        assert item.view_id == "plan"


class TestViewSelected:
    def test_message_attributes(self) -> None:
        from textual.widget import Widget

        control = Widget()
        msg = ViewSelected("chat", control=control)
        assert msg.view_id == "chat"
        assert msg.control is control


# ══════════════════════════════════════════════════════════════════
# 3. Chat (chat.py)
# ══════════════════════════════════════════════════════════════════


class TestChatASCII:
    def test_logo_has_six_lines(self) -> None:
        assert len(ASCII_LOGO) == 6

    def test_logo_lines_are_equal_width(self) -> None:
        widths = [len(line) for line in ASCII_LOGO]
        assert len(set(widths)) == 1


class TestInputSubmitted:
    def test_message(self) -> None:
        msg = InputSubmitted("hello")
        assert msg.text == "hello"


class TestChatViewFmtStep:
    def test_fmt_step_done(self) -> None:
        session = _session()
        cv = ChatView(session)
        result = cv._fmt_step("- s1: slurm.manage_qos status=done")
        assert "●" in result
        assert "done" in result

    def test_fmt_step_needs_approval(self) -> None:
        session = _session()
        cv = ChatView(session)
        result = cv._fmt_step("- s1: tool status=needs_approval")
        assert "!" in result

    def test_fmt_step_failed(self) -> None:
        session = _session()
        cv = ChatView(session)
        result = cv._fmt_step("- s1: tool status=failed")
        assert "✕" in result

    def test_fmt_step_strips_depends_on_none(self) -> None:
        session = _session()
        cv = ChatView(session)
        result = cv._fmt_step("- s1: tool status=done depends_on=None")
        assert "depends_on" not in result

    def test_fmt_step_strips_depends_on_empty(self) -> None:
        session = _session()
        cv = ChatView(session)
        result = cv._fmt_step("- s1: tool status=done depends_on=[]")
        assert "depends_on" not in result

    def test_fmt_step_unknown_status(self) -> None:
        session = _session()
        cv = ChatView(session)
        result = cv._fmt_step("- s1: tool status=unknown_xyz")
        assert result == "s1: tool status=unknown_xyz"


# ══════════════════════════════════════════════════════════════════
# 4. Plan View (plan_view.py)
# ══════════════════════════════════════════════════════════════════


class TestPlanView:
    def test_header_text_no_plan(self) -> None:
        pv = PlanView()
        result = pv._header_text()
        assert "No plan loaded" in result.plain

    def test_header_text_with_plan(self) -> None:
        pv = PlanView()
        pv.plan = _plan()
        result = pv._header_text()
        assert "test-pla" in result.plain
        assert "draft" in result.plain
        assert "2 step(s)" in result.plain

    def test_compact_input_short(self) -> None:
        pv = PlanView()
        result = pv._compact_input({"name": "gpu", "op": "modify"})
        assert "name=gpu" in result
        assert "op=modify" in result

    def test_compact_input_skips_dry_run(self) -> None:
        pv = PlanView()
        result = pv._compact_input({"name": "gpu", "dry_run": True})
        assert "dry_run" not in result

    def test_compact_input_truncates_long_value(self) -> None:
        pv = PlanView()
        long_val = "x" * 50
        result = pv._compact_input({"key": long_val})
        assert "..." in result

    def test_compact_input_truncates_long_result(self) -> None:
        pv = PlanView()
        inp = {f"key{i}": f"val{i}" for i in range(20)}
        result = pv._compact_input(inp)
        assert len(result) <= 100


# ══════════════════════════════════════════════════════════════════
# 5. Dashboard (dashboard.py)
# ══════════════════════════════════════════════════════════════════


class TestDashboardView:
    def test_summary_text_no_nodes(self) -> None:
        dv = DashboardView()
        result = dv._summary_text()
        assert "No node data" in result.plain

    def test_summary_text_with_nodes(self) -> None:
        dv = DashboardView()
        dv.nodes = [
            {"node": "cpu01", "state": "UP"},
            {"node": "cpu02", "state": "DRAINED"},
            {"node": "gpu01", "state": "DOWN"},
        ]
        result = dv._summary_text()
        assert "3 node(s)" in result.plain
        assert "1 up" in result.plain
        assert "1 drained" in result.plain
        assert "1 down" in result.plain

    def test_summary_text_all_up(self) -> None:
        dv = DashboardView()
        dv.nodes = [
            {"node": "n1", "state": "UP"},
            {"node": "n2", "state": "IDLE"},
            {"node": "n3", "state": "ALLOCATED"},
        ]
        result = dv._summary_text()
        assert "3 up" in result.plain
        assert "drained" not in result.plain
        assert "down" not in result.plain


# ══════════════════════════════════════════════════════════════════
# 6. Approval Modal (approval.py)
# ══════════════════════════════════════════════════════════════════


class TestApprovalResult:
    def test_approve(self) -> None:
        msg = ApprovalResult(approved=True, step_id="s1")
        assert msg.approved is True
        assert msg.step_id == "s1"

    def test_deny(self) -> None:
        msg = ApprovalResult(approved=False, step_id="s2")
        assert msg.approved is False
        assert msg.step_id == "s2"


class TestApprovalModalConstruction:
    def test_init(self) -> None:
        step = _step_done()
        diff = _diff()
        gate = Gate(requires_approval=True)
        modal = ApprovalModal(step, diff, gate)
        assert modal.step is step
        assert modal.diff is diff
        assert modal.gate is gate


# ══════════════════════════════════════════════════════════════════
# 7. App Constants (app.py)
# ══════════════════════════════════════════════════════════════════


class TestAppConstants:
    def test_view_ids(self) -> None:
        assert VIEW_IDS == ["chat", "plan", "dashboard", "tools", "audit"]
        assert len(VIEW_IDS) == 5

    def test_app_title(self) -> None:
        assert HPCPilotApp.TITLE == "HPC Pilot"

    def test_app_is_dark(self) -> None:
        assert HPCPilotApp.DARK is True


# ══════════════════════════════════════════════════════════════════
# 8. Async integration tests
# ══════════════════════════════════════════════════════════════════


async def test_app_composes_successfully() -> None:
    session = _session()
    app = HPCPilotApp(session)
    async with app.run_test(size=(120, 30)):
        # Verify all expected widgets exist
        assert app.query_one("#status-bar", StatusBar)
        assert app.query_one("#sidebar", Sidebar)
        assert app.query_one("#main-content")
        assert app.query_one("#chat", ChatView)
        assert app.query_one("#plan", PlanView)
        assert app.query_one("#dashboard", DashboardView)
        assert app.query_one("#tools", ToolExplorer)
        assert app.query_one("#chat-input", Input)


async def test_status_bar_shows_actor() -> None:
    session = _session()
    app = HPCPilotApp(session)
    async with app.run_test(size=(120, 30)):
        bar = app.query_one("#status-bar", StatusBar)
        assert bar.actor == "alice"
        assert bar.actor_role == "operator"


async def test_sidebar_has_nav_items() -> None:
    session = _session()
    app = HPCPilotApp(session)
    async with app.run_test(size=(120, 30)):
        sidebar = app.query_one("#sidebar", Sidebar)
        items = sidebar.query(NavListItem)
        assert len(items) == 5


async def test_chat_submit_dispatches_to_session() -> None:
    session = _session()
    app = HPCPilotApp(session)
    async with app.run_test(size=(120, 30)) as pilot:
        inp = app.query_one("#chat-input", Input)
        inp.value = "give alice 48 hours of wall time on the gpu qos"
        await pilot.press("enter")
        await pilot.pause()
    assert session.current_plan is not None


async def test_chat_exit_stops_session() -> None:
    session = _session()
    app = HPCPilotApp(session)
    async with app.run_test(size=(120, 30)) as pilot:
        inp = app.query_one("#chat-input", Input)
        inp.value = "/exit"
        await pilot.press("enter")
        await pilot.pause()
    assert not session.running


async def test_view_switching_ctrl1() -> None:
    session = _session()
    app = HPCPilotApp(session)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.press("ctrl+1")
        await pilot.pause()
        assert app.current_view == "chat"

        await pilot.press("ctrl+2")
        await pilot.pause()
        assert app.current_view == "plan"

        await pilot.press("ctrl+3")
        await pilot.pause()
        assert app.current_view == "dashboard"

        await pilot.press("ctrl+4")
        await pilot.pause()
        assert app.current_view == "tools"

        await pilot.press("ctrl+5")
        await pilot.pause()
        assert app.current_view == "audit"


async def test_view_switching_ctrl_tab() -> None:
    session = _session()
    app = HPCPilotApp(session)
    async with app.run_test(size=(120, 30)) as pilot:
        assert app.current_view == "chat"
        await pilot.press("ctrl+tab")
        await pilot.pause()
        assert app.current_view == "plan"


async def test_clear_chat() -> None:
    session = _session()
    app = HPCPilotApp(session)
    async with app.run_test(size=(120, 30)) as pilot:
        inp = app.query_one("#chat-input", Input)
        inp.value = "some input"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("ctrl+l")
        await pilot.pause()
        # After clear, chat log still exists
        app.query_one("#chat", ChatView).query_one("#chat-log", RichLog)
    assert session.running


async def test_quick_run_no_plan() -> None:
    session = _session()
    app = HPCPilotApp(session)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.press("ctrl+r")
        await pilot.pause()
    # /run with no plan doesn't crash
    assert session.running


async def test_plan_updates_on_submit() -> None:
    session = _session()
    app = HPCPilotApp(session)
    async with app.run_test(size=(120, 30)) as pilot:
        inp = app.query_one("#chat-input", Input)
        inp.value = "give alice 48 hours of wall time on the gpu qos"
        await pilot.press("enter")
        await pilot.pause()

        # Status bar should show plan
        bar = app.query_one("#status-bar", StatusBar)
        assert bar.plan_id != ""
        assert bar.plan_state == "draft"

        # Plan view should have the plan
        pv = app.query_one("#plan", PlanView)
        assert pv.plan is not None
        assert "gpu qos" in pv.plan.intent


async def test_sidebar_view_selection() -> None:
    session = _session()
    app = HPCPilotApp(session)
    async with app.run_test(size=(120, 30)) as pilot:
        sidebar = app.query_one("#sidebar", Sidebar)
        items = sidebar.query(NavListItem)
        # Click on "Dashboard" (index 2)
        items[2].cursor_index = 0
        # Simulate selection by posting message directly
        sidebar.post_message(ViewSelected("dashboard", control=sidebar))
        await pilot.pause()
        assert app.current_view == "dashboard"


# ══════════════════════════════════════════════════════════════════
# 9. Edge cases
# ══════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_step_icon_all_styles_are_strings(self) -> None:
        for _status, (icon, style) in STEP_ICON.items():
            assert isinstance(icon, str)
            assert isinstance(style, str)

    def test_plan_state_colors_all_strings(self) -> None:
        for state, color in PLAN_STATE_COLOR.items():
            assert isinstance(state, str)
            assert isinstance(color, str)

    def test_risk_colors_all_strings(self) -> None:
        for risk, color in RISK_COLOR.items():
            assert isinstance(risk, str)
            assert isinstance(color, str)

    def test_fmt_step_icon_returns_text(self) -> None:
        for status in STEP_ICON:
            result = fmt_step_icon(status)
            assert isinstance(result, Text)
            assert len(result.plain) > 0

    def test_fmt_plan_state_returns_text(self) -> None:
        for state in PLAN_STATE_COLOR:
            result = fmt_plan_state(state)
            assert isinstance(result, Text)
            assert "●" in result.plain

    def test_fmt_risk_returns_text(self) -> None:
        for risk in RISK_COLOR:
            result = fmt_risk(risk)
            assert isinstance(result, Text)
            assert len(result.plain) > 0
