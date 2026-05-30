"""Curses-based terminal UI for the interactive agent shell."""

from __future__ import annotations

import curses
import textwrap
from collections.abc import Sequence
from dataclasses import dataclass, field

from hpc_agent.core.plan import Plan
from hpc_agent.core.shell import ShellSession

MIN_HEIGHT = 12
MIN_WIDTH = 60


@dataclass
class TuiState:
    transcript: list[str] = field(default_factory=list)
    input_text: str = ""
    status: str = "Type an intent, /run, /approve, /tools, /help, or /exit."
    transcript_scroll: int = 0
    plan_scroll: int = 0

    def write(self, text: str) -> None:
        self.transcript.extend(text.splitlines() or [""])
        self.transcript_scroll = 0


@dataclass(frozen=True)
class Layout:
    lines: list[str]
    transcript_height: int
    plan_height: int


def render_layout(
    *,
    width: int,
    height: int,
    transcript: Sequence[str],
    plan: Plan | None,
    input_text: str,
    status: str,
    transcript_scroll: int = 0,
    plan_scroll: int = 0,
) -> Layout:
    """Render the TUI to fixed-width text lines for tests and curses drawing."""
    if height < MIN_HEIGHT or width < MIN_WIDTH:
        message = f"Terminal too small. Need at least {MIN_WIDTH}x{MIN_HEIGHT}."
        return Layout(
            lines=_fit_lines([message], width, height),
            transcript_height=0,
            plan_height=0,
        )

    left_width = max(34, int(width * 0.58))
    right_width = width - left_width - 1
    body_height = height - 3

    transcript_lines = _wrap_lines(transcript, left_width - 4)
    plan_lines = _wrap_lines(_plan_lines(plan), right_width - 4)
    visible_transcript = _viewport(transcript_lines, body_height - 2, transcript_scroll)
    visible_plan = _viewport(plan_lines, body_height - 2, plan_scroll)

    lines = [_header(width)]
    lines.extend(
        _side_by_side(
            _box("Conversation", visible_transcript, left_width, body_height),
            _box("Plan", visible_plan, right_width, body_height),
        )
    )
    lines.append(_status(status, width))
    lines.append(_input(input_text, width))
    return Layout(
        lines=lines[:height],
        transcript_height=body_height - 2,
        plan_height=body_height - 2,
    )


class TuiApp:
    """Interactive curses frontend backed by ShellSession."""

    def __init__(self, session: ShellSession) -> None:
        self.session = session
        self.state = TuiState()
        self.session.write = self._write

    def run(self) -> None:
        curses.wrapper(self._main)

    def _main(self, stdscr: curses.window) -> None:
        curses.curs_set(1)
        curses.use_default_colors()
        self._init_colors()
        stdscr.keypad(True)
        self._write("hpc-agent TUI. Type /help for commands, /exit to quit.")

        while self.session.running:
            self._draw(stdscr)
            key = stdscr.get_wch()
            self._handle_key(key)

    def _handle_key(self, key: object) -> None:
        if key in ("\n", "\r", curses.KEY_ENTER):
            line = self.state.input_text.strip()
            self.state.input_text = ""
            if not line:
                return
            self._write(f"> {line}")
            self.session.handle_line(line)
            self._sync_status()
            return
        if key in (curses.KEY_BACKSPACE, "\b", "\x7f"):
            self.state.input_text = self.state.input_text[:-1]
            return
        if key == curses.KEY_UP:
            self.state.transcript_scroll += 1
            return
        if key == curses.KEY_DOWN:
            self.state.transcript_scroll = max(0, self.state.transcript_scroll - 1)
            return
        if key == curses.KEY_PPAGE:
            self.state.plan_scroll += 5
            return
        if key == curses.KEY_NPAGE:
            self.state.plan_scroll = max(0, self.state.plan_scroll - 5)
            return
        if isinstance(key, str) and key.isprintable():
            self.state.input_text += key

    def _draw(self, stdscr: curses.window) -> None:
        height, width = stdscr.getmaxyx()
        layout = render_layout(
            width=width,
            height=height,
            transcript=self.state.transcript,
            plan=self.session.current_plan,
            input_text=self.state.input_text,
            status=self.state.status,
            transcript_scroll=self.state.transcript_scroll,
            plan_scroll=self.state.plan_scroll,
        )
        stdscr.erase()
        for row, line in enumerate(layout.lines):
            attr = curses.color_pair(0)
            if row == 0:
                attr = curses.color_pair(1) | curses.A_BOLD
            elif row == len(layout.lines) - 2:
                attr = curses.color_pair(2)
            elif row == len(layout.lines) - 1:
                attr = curses.color_pair(3)
            stdscr.addnstr(row, 0, line, max(0, width - 1), attr)
        cursor_x = min(len("> " + self.state.input_text), max(0, width - 1))
        stdscr.move(max(0, height - 1), cursor_x)
        stdscr.refresh()

    def _write(self, text: str) -> None:
        self.state.write(text)

    def _sync_status(self) -> None:
        plan = self.session.current_plan
        if plan is None:
            self.state.status = "No active plan."
            return
        self.state.status = f"Plan {plan.id} is {plan.state.value}."

    @staticmethod
    def _init_colors() -> None:
        if not curses.has_colors():
            return
        curses.start_color()
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(3, curses.COLOR_GREEN, -1)


def _header(width: int) -> str:
    title = " hpc-agent TUI "
    hint = " intent | /run | /approve | /tools | /help | /exit "
    fill = max(1, width - len(title) - len(hint))
    return (title + ("─" * fill) + hint)[:width].ljust(width)


def _status(status: str, width: int) -> str:
    return _truncate(f" {status}", width).ljust(width)


def _input(input_text: str, width: int) -> str:
    return _truncate(f"> {input_text}", width).ljust(width)


def _box(title: str, lines: Sequence[str], width: int, height: int) -> list[str]:
    top = f"╭─ {title} " + "─" * max(0, width - len(title) - 5) + "╮"
    bottom = "╰" + "─" * max(0, width - 2) + "╯"
    inner_height = max(0, height - 2)
    out = [_truncate(top, width)]
    for idx in range(inner_height):
        content = lines[idx] if idx < len(lines) else ""
        out.append("│ " + _truncate(content, width - 4).ljust(width - 4) + " │")
    out.append(_truncate(bottom, width))
    return out


def _side_by_side(left: Sequence[str], right: Sequence[str]) -> list[str]:
    rows = max(len(left), len(right))
    out: list[str] = []
    left_width = len(left[0]) if left else 0
    right_width = len(right[0]) if right else 0
    for idx in range(rows):
        left_line = left[idx] if idx < len(left) else " " * left_width
        right_line = right[idx] if idx < len(right) else " " * right_width
        out.append(left_line + " " + right_line)
    return out


def _wrap_lines(lines: Sequence[str], width: int) -> list[str]:
    if width <= 0:
        return ["" for _ in lines]
    wrapped: list[str] = []
    for line in lines:
        if not line:
            wrapped.append("")
            continue
        wrapped.extend(textwrap.wrap(line, width=width, replace_whitespace=False) or [""])
    return wrapped


def _viewport(lines: Sequence[str], height: int, scroll_from_bottom: int) -> list[str]:
    if height <= 0:
        return []
    if len(lines) <= height:
        return list(lines)
    bottom = max(height, len(lines) - scroll_from_bottom)
    top = max(0, bottom - height)
    return list(lines[top:bottom])


def _plan_lines(plan: Plan | None) -> list[str]:
    if plan is None:
        return [
            "No active plan yet.",
            "",
            "Enter an intent such as:",
            "give alice 48 hours of wall time on the gpu qos",
        ]
    lines = [
        f"id: {plan.id}",
        f"state: {plan.state.value}",
        f"intent: {plan.intent}",
        "",
        "steps:",
    ]
    for step in plan.steps:
        lines.append(f"- {step.id}: {step.tool}")
        lines.append(f"  status: {step.status.value}")
        lines.append(f"  input: {step.input}")
        if step.result is not None:
            lines.append(f"  result: {step.result.status.value}")
            if step.result.error is not None:
                lines.append(f"  error: {step.result.error.message}")
            if step.result.diff is not None:
                lines.append("  diff:")
                lines.extend(f"    {line}" for line in step.result.diff.render().splitlines())
    return lines


def _fit_lines(lines: Sequence[str], width: int, height: int) -> list[str]:
    return [_truncate(line, width).ljust(width) for line in lines[:height]]


def _truncate(text: str, width: int) -> str:
    if width <= 0:
        return ""
    return text if len(text) <= width else text[: max(0, width - 1)] + "…"
