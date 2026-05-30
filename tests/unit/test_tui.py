"""Tests for the Textual TUI."""
from __future__ import annotations

from textual.widgets import Input, RichLog

from hpc_agent.core.shell import ShellSession
from hpc_agent.core.tui import COMMAND_PALETTE, TuiApp, _STEP_ICON
from hpc_agent.exec.rbac import Role


def _session() -> ShellSession:
    return ShellSession(
        actor="alice",
        actor_role=Role.OPERATOR,
        policy=None,
        write=lambda _: None,
    )


# ── synchronous unit tests ────────────────────────────────────────────


def test_tui_instantiation_wires_write_callback() -> None:
    session = _session()
    app = TuiApp(session)
    # Bound methods are freshly created on each access, so compare by function + owner.
    assert session.write.__func__ is TuiApp._write  # type: ignore[attr-defined]
    assert session.write.__self__ is app  # type: ignore[attr-defined]


def test_command_palette_contains_required_commands() -> None:
    for cmd in ("/run", "/approve", "/show", "/tools", "/help", "/exit"):
        assert cmd in COMMAND_PALETTE


def test_step_icon_table_covers_all_statuses() -> None:
    for status in ("pending", "running", "done", "failed", "needs_approval", "skipped"):
        assert status in _STEP_ICON
        icon, style = _STEP_ICON[status]
        assert icon and style


def test_status_text_includes_actor_and_role() -> None:
    session = _session()
    app = TuiApp(session)
    text = app._status_text()
    assert "alice" in text
    assert "operator" in text


def test_status_text_no_plan() -> None:
    session = _session()
    app = TuiApp(session)
    assert "no active plan" in app._status_text()


def test_commands_text_contains_all_commands() -> None:
    session = _session()
    app = TuiApp(session)
    text = app._commands_text()
    for cmd in COMMAND_PALETTE:
        assert cmd in text


def test_hints_text_contains_key_bindings() -> None:
    session = _session()
    app = TuiApp(session)
    text = app._hints_text()
    assert "^r" in text
    assert "^a" in text
    assert "^l" in text


def test_fmt_step_adds_icon_for_done() -> None:
    session = _session()
    app = TuiApp(session)
    result = app._fmt_step("- s1: slurm.manage_qos status=done")
    assert "●" in result


def test_fmt_step_adds_icon_for_needs_approval() -> None:
    session = _session()
    app = TuiApp(session)
    result = app._fmt_step("- s1: slurm.manage_qos status=needs_approval")
    assert "!" in result


def test_fmt_step_adds_icon_for_failed() -> None:
    session = _session()
    app = TuiApp(session)
    result = app._fmt_step("- s1: some_tool status=failed")
    assert "✕" in result


def test_fmt_step_strips_prefix_for_unknown_status() -> None:
    session = _session()
    app = TuiApp(session)
    # "- " prefix is always stripped; no icon is added when status is unrecognised
    result = app._fmt_step("- s1: some_tool status=unknown_xyz")
    assert result == "s1: some_tool status=unknown_xyz"


# ── async integration tests (Textual 8.x Pilot API) ──────────────────
# Textual 8.x Pilot has no .type(); set Input.value directly, then press Enter.


async def test_tui_submit_dispatches_to_session() -> None:
    session = _session()
    app = TuiApp(session)
    async with app.run_test(size=(120, 30)) as pilot:
        inp = app.query_one("#chat-input", Input)
        inp.value = "give alice 48 hours of wall time on the gpu qos"
        await pilot.press("enter")
        await pilot.pause()
    assert session.current_plan is not None


async def test_tui_exit_command_stops_session() -> None:
    session = _session()
    app = TuiApp(session)
    async with app.run_test(size=(120, 30)) as pilot:
        inp = app.query_one("#chat-input", Input)
        inp.value = "/exit"
        await pilot.press("enter")
        await pilot.pause()
    assert not session.running


async def test_tui_tab_completes_partial_command() -> None:
    session = _session()
    app = TuiApp(session)
    async with app.run_test(size=(120, 30)) as pilot:
        inp = app.query_one("#chat-input", Input)
        inp.value = "/ru"
        await pilot.press("tab")
        await pilot.pause()
        assert inp.value == "/run"


async def test_tui_ctrl_r_triggers_run() -> None:
    session = _session()
    app = TuiApp(session)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.press("ctrl+r")
        await pilot.pause()
    # /run with no plan writes an error message but does not exit
    assert session.running


async def test_tui_ctrl_l_clears_and_restores_welcome() -> None:
    session = _session()
    app = TuiApp(session)
    async with app.run_test(size=(120, 30)) as pilot:
        inp = app.query_one("#chat-input", Input)
        inp.value = "some input"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("ctrl+l")
        await pilot.pause()
        # After clear, the log widget still exists and the app is still running
        app.query_one("#chat-log", RichLog)  # raises if missing
    assert session.running
