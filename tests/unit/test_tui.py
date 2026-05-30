from __future__ import annotations

from hpc_agent.core.plan import PlanState, Step
from hpc_agent.core.planner import plan_from_steps
from hpc_agent.core.shell import ShellSession
from hpc_agent.core.tui import TuiApp, render_layout
from hpc_agent.exec.rbac import Role


def test_render_layout_has_header_panes_status_and_input() -> None:
    plan = plan_from_steps(
        "extend gpu",
        "alice",
        [Step(id="s1", tool="slurm.manage_qos", input={"name": "gpu"})],
    )

    layout = render_layout(
        width=100,
        height=24,
        transcript=["hello", "world"],
        plan=plan,
        input_text="/run",
        status="ready",
    )

    assert len(layout.lines) == 24
    rendered = "\n".join(layout.lines)
    assert "[>>]" in layout.lines[0]
    assert "AutoHPC" in layout.lines[0]
    assert "Chat" in rendered
    assert "Plan & Actions" in rendered
    assert "Quick Actions" in rendered
    assert "> /run" in rendered
    assert "slurm.manage_qos" in rendered
    assert "ready" in layout.lines[-4]
    assert "> /run" in layout.lines[-2]


def test_render_layout_handles_small_terminal() -> None:
    layout = render_layout(
        width=30,
        height=5,
        transcript=[],
        plan=None,
        input_text="",
        status="",
    )

    assert len(layout.lines) == 1
    assert "Terminal too small" in layout.lines[0]


def test_render_layout_shows_logo_on_empty_chat() -> None:
    layout = render_layout(
        width=100,
        height=24,
        transcript=[],
        plan=None,
        input_text="",
        status="ready",
    )

    rendered = "\n".join(layout.lines)
    beacon_line = next(line for line in layout.lines if "│" in line and "<== AutoHPC" in line)
    assert beacon_line.index("<==") > 4
    assert "mgmt -> login -> compute" in rendered
    assert "____" in rendered
    assert "Start with a plain-language request" in rendered


def test_render_layout_shows_logo_after_tui_startup_message() -> None:
    layout = render_layout(
        width=100,
        height=24,
        transcript=["AutoHPC TUI. Type /help for commands, /exit to quit."],
        plan=None,
        input_text="",
        status="ready",
    )

    rendered = "\n".join(layout.lines)
    assert "____" in rendered
    assert "AutoHPC TUI is ready." in rendered


def test_render_layout_animates_centered_logo() -> None:
    first = render_layout(
        width=100,
        height=24,
        transcript=["AutoHPC TUI. Type /help for commands, /exit to quit."],
        plan=None,
        input_text="",
        status="ready",
        symbol_frame=0,
    )
    second = render_layout(
        width=100,
        height=24,
        transcript=["AutoHPC TUI. Type /help for commands, /exit to quit."],
        plan=None,
        input_text="",
        status="ready",
        symbol_frame=1,
    )

    first_rendered = "\n".join(first.lines)
    second_rendered = "\n".join(second.lines)
    assert "<== AutoHPC ==>" in first_rendered
    assert "-<= AutoHPC =>-" in second_rendered
    assert "mgmt -> login -> compute" in first_rendered
    assert "mgmt => login -> compute" in second_rendered


def test_tui_enter_dispatches_to_shell_session() -> None:
    writes: list[str] = []
    session = ShellSession(
        actor="alice",
        actor_role=Role.OPERATOR,
        policy=None,
        write=writes.append,
    )
    app = TuiApp(session)
    app.state.input_text = "give alice 48 hours of wall time on the gpu qos"

    app._handle_key("\n")

    assert app.state.input_text == ""
    assert session.current_plan is not None
    assert any(line.startswith("> give alice") for line in app.state.transcript)


def test_tui_ctrl_shortcuts_run_and_clear() -> None:
    writes: list[str] = []
    session = ShellSession(
        actor="alice",
        actor_role=Role.OPERATOR,
        policy=None,
        write=writes.append,
    )
    app = TuiApp(session)
    app.state.input_text = "give alice 48 hours of wall time on the gpu qos"

    app._handle_key("\x12")

    assert app.state.input_text == ""
    assert session.current_plan is not None
    assert any(line.startswith("> give alice") for line in app.state.transcript)

    app._handle_key("\x0c")

    assert app.state.transcript == []
    assert app.state.status == "Transcript cleared."


def test_render_layout_animates_ascii_symbol() -> None:
    first = render_layout(
        width=100,
        height=24,
        transcript=[],
        plan=None,
        input_text="",
        status="ready",
        symbol_frame=0,
    )
    second = render_layout(
        width=100,
        height=24,
        transcript=[],
        plan=None,
        input_text="",
        status="ready",
        symbol_frame=1,
    )
    plan = plan_from_steps("extend gpu", "alice", [Step(id="s1", tool="x", input={})])
    plan.state = PlanState.PAUSED
    paused = render_layout(
        width=100,
        height=24,
        transcript=[],
        plan=plan,
        input_text="",
        status="ready",
        symbol_frame=2,
    )

    assert "[A--]" in first.lines[0]
    assert "[-A-]" in second.lines[0]
    assert "[!!]" in paused.lines[0]


def test_tui_palette_completion_and_insertion() -> None:
    session = ShellSession(
        actor="alice",
        actor_role=Role.OPERATOR,
        policy=None,
        write=lambda _: None,
    )
    app = TuiApp(session)

    app._handle_key("\x0e")
    assert app.state.action == "/approve"

    app._handle_key("\x05")
    assert app.state.input_text == "/approve"

    app.state.input_text = "/to"
    app._handle_key("\t")
    assert app.state.input_text == "/tools"
