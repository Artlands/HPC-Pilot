from __future__ import annotations

from hpc_agent.core.plan import Step
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
    assert "hpc-agent TUI" in layout.lines[0]
    assert "Conversation" in "\n".join(layout.lines)
    assert "Plan" in "\n".join(layout.lines)
    assert "slurm.manage_qos" in "\n".join(layout.lines)
    assert layout.lines[-2].strip() == "ready"
    assert layout.lines[-1].startswith("> /run")


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
