"""Curses-based terminal UI for the interactive agent shell."""

from __future__ import annotations

import curses
import textwrap
from collections.abc import Sequence
from dataclasses import dataclass, field

from hpc_agent.core.plan import Plan
from hpc_agent.core.shell import ShellSession

MIN_HEIGHT = 16
MIN_WIDTH = 72
COMMAND_PALETTE = (
    "/run",
    "/approve",
    "/show",
    "/tools",
    "/help",
    "/exit",
)
SPINNER_FRAMES = ("[A--]", "[-A-]", "[--A]", "[-A-]")
LOGO_BEACON_FRAMES = (
    "<== AutoHPC ==>",
    "-<= AutoHPC =>-",
    "--< AutoHPC >--",
    "-=> AutoHPC <=-",
)
LOGO_CLUSTER_FRAMES = (
    "o---o---o     mgmt -> login -> compute",
    "o===o---o     mgmt => login -> compute",
    "o---o===o     mgmt -> login => compute",
    "o---o---O     mgmt -> login -> compute",
)
ASCII_LOGO = (
    "    ___         __        __  __ ____   ______",
    "   /   | __  __/ /_____  / / / // __ \\ / ____/",
    "  / /| |/ / / / __/ __ \\/ /_/ // /_/ // /     ",
    " / ___ / /_/ / /_/ /_/ / __  // ____// /___   ",
    "/_/  |_\\__,_/\\__/\\____/_/ /_//_/     \\____/   ",
)
COMPACT_LOGO = (
    "  ___        __        __  ______",
    " / _ |__ ___/ /____   / / / / __/",
    "/ __ / // / __/ _ \\ / /_/ / /_  ",
    "/_/ |_\\_,_/\\__/\\___/ \\____/___/  ",
)


@dataclass
class TuiState:
    transcript: list[str] = field(default_factory=list)
    input_text: str = ""
    status: str = "Ready. Describe a change, or use /run, /approve, /tools, /help."
    transcript_scroll: int = 0
    plan_scroll: int = 0
    selected_action: int = 0
    symbol_frame: int = 0

    def write(self, text: str) -> None:
        self.transcript.extend(text.splitlines() or [""])
        self.transcript_scroll = 0

    @property
    def action(self) -> str:
        return COMMAND_PALETTE[self.selected_action % len(COMMAND_PALETTE)]


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
    selected_action: int = 0,
    symbol_frame: int = 0,
) -> Layout:
    """Render the TUI to fixed-width text lines for tests and curses drawing."""
    if height < MIN_HEIGHT or width < MIN_WIDTH:
        message = f"Terminal too small. Need at least {MIN_WIDTH}x{MIN_HEIGHT}."
        return Layout(
            lines=_fit_lines([message], width, height),
            transcript_height=0,
            plan_height=0,
        )

    side_width = min(38, max(26, width // 3))
    main_width = width - side_width - 1
    body_height = height - 5

    transcript_lines = _wrap_lines(
        _chat_lines(transcript, width=main_width - 4, symbol_frame=symbol_frame),
        main_width - 4,
    )
    plan_lines = _wrap_lines(_context_lines(plan, selected_action=selected_action), side_width - 4)
    visible_transcript = _viewport(transcript_lines, body_height - 2, transcript_scroll)
    visible_plan = _viewport_from_top(plan_lines, body_height - 2, plan_scroll)

    lines = [_header(width, plan=plan, symbol_frame=symbol_frame)]
    lines.extend(
        _side_by_side(
            _box("Chat", visible_transcript, main_width, body_height),
            _box("Plan & Actions", visible_plan, side_width, body_height),
        )
    )
    lines.append(_status(status, width))
    lines.extend(_composer(input_text, width))
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
        stdscr.timeout(120)
        self._write("AutoHPC TUI. Type /help for commands, /exit to quit.")

        while self.session.running:
            self._draw(stdscr)
            try:
                key = stdscr.get_wch()
            except curses.error:
                continue
            self._handle_key(key)

    def _handle_key(self, key: object) -> None:
        if key in ("\n", "\r", curses.KEY_ENTER):
            self._submit(self.state.input_text.strip())
            return
        if key == "\x04":
            self.session.running = False
            return
        if key == "\x0c":
            self.state.transcript.clear()
            self.state.status = "Transcript cleared."
            return
        if key == "\x12":
            intent = self.state.input_text.strip()
            self._submit(intent if intent else "/run")
            return
        if key == "\x01":
            self._submit("/approve")
            return
        if key == "\x15":
            self.state.input_text = ""
            return
        if key == "\t":
            self._complete_or_insert_action()
            return
        if key == "\x0e":
            self._cycle_action(1)
            return
        if key == "\x10":
            self._cycle_action(-1)
            return
        if key == "\x05":
            self._insert_action()
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
            self.state.plan_scroll = max(0, self.state.plan_scroll - 5)
            return
        if key == curses.KEY_NPAGE:
            self.state.plan_scroll += 5
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
            selected_action=self.state.selected_action,
            symbol_frame=self.state.symbol_frame,
        )
        self.state.symbol_frame += 1
        stdscr.erase()
        for row, line in enumerate(layout.lines):
            attr = curses.color_pair(0)
            if row == 0:
                attr = curses.color_pair(1) | curses.A_BOLD
            elif _is_logo_beacon_line(line):
                attr = curses.color_pair(5) | curses.A_BOLD
            elif _is_logo_render_line(line):
                attr = curses.color_pair(4) | curses.A_BOLD
            elif row == len(layout.lines) - 4:
                attr = curses.color_pair(2)
            elif row == len(layout.lines) - 2:
                attr = curses.color_pair(3)
            stdscr.addnstr(row, 0, line, max(0, width - 1), attr)
        cursor_x = min(len("  > " + self.state.input_text), max(0, width - 1))
        stdscr.move(max(0, height - 2), cursor_x)
        stdscr.refresh()

    def _write(self, text: str) -> None:
        self.state.write(text)

    def _sync_status(self) -> None:
        plan = self.session.current_plan
        if plan is None:
            self.state.status = "No active plan. Enter an intent to draft one."
            return
        waiting = sum(1 for step in plan.steps if step.status.value == "needs_approval")
        approval = f" · {waiting} approval pending" if waiting else ""
        self.state.status = f"Plan {plan.id} · {plan.state.value}{approval}"

    def _submit(self, line: str) -> None:
        self.state.input_text = ""
        if not line:
            return
        self._write(f"> {line}")
        self.session.handle_line(line)
        self._sync_status()

    def _cycle_action(self, delta: int) -> None:
        self.state.selected_action = (self.state.selected_action + delta) % len(COMMAND_PALETTE)
        self.state.status = f"Selected quick action: {self.state.action}"

    def _insert_action(self) -> None:
        self.state.input_text = self.state.action
        self.state.status = f"Inserted {self.state.action}. Press Enter to send."

    def _complete_or_insert_action(self) -> None:
        text = self.state.input_text.strip()
        if not text:
            self._insert_action()
            return
        if not text.startswith("/"):
            return
        matches = [command for command in COMMAND_PALETTE if command.startswith(text)]
        if not matches:
            self.state.status = f"No command matches {text}."
            return
        self.state.input_text = matches[0]
        self.state.status = f"Completed command: {matches[0]}"

    @staticmethod
    def _init_colors() -> None:
        if not curses.has_colors():
            return
        curses.start_color()
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(3, curses.COLOR_GREEN, -1)
        curses.init_pair(4, curses.COLOR_MAGENTA, -1)
        curses.init_pair(5, curses.COLOR_CYAN, -1)


def _header(width: int, *, plan: Plan | None, symbol_frame: int) -> str:
    title = f" {_dynamic_symbol(plan, symbol_frame)} AutoHPC "
    hint = " chat ops | Tab complete | Ctrl-N/P actions | Ctrl-E insert "
    fill = max(1, width - len(title) - len(hint))
    return (title + ("─" * fill) + hint)[:width].ljust(width)


def _status(status: str, width: int) -> str:
    text = f" {status} | Up/Down chat · PgUp/PgDn plan · Ctrl-D exit"
    return _truncate(text, width).ljust(width)


def _composer(input_text: str, width: int) -> list[str]:
    title = " Message "
    top = f"╭─{title}" + "─" * max(0, width - len(title) - 3) + "╮"
    body = "│ > " + _truncate(input_text, width - 6).ljust(width - 6) + " │"
    bottom = "╰" + "─" * max(0, width - 2) + "╯"
    return [_truncate(top, width), _truncate(body, width), _truncate(bottom, width)]


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
        wrapped.extend(
            textwrap.wrap(
                line,
                width=width,
                replace_whitespace=False,
                drop_whitespace=False,
            )
            or [""]
        )
    return wrapped


def _viewport(lines: Sequence[str], height: int, scroll_from_bottom: int) -> list[str]:
    if height <= 0:
        return []
    if len(lines) <= height:
        return list(lines)
    bottom = max(height, len(lines) - scroll_from_bottom)
    top = max(0, bottom - height)
    return list(lines[top:bottom])


def _viewport_from_top(lines: Sequence[str], height: int, scroll_from_top: int) -> list[str]:
    if height <= 0:
        return []
    top = min(max(0, scroll_from_top), max(0, len(lines) - height))
    bottom = top + height
    return list(lines[top:bottom])


def _chat_lines(transcript: Sequence[str], *, width: int, symbol_frame: int) -> list[str]:
    if not transcript:
        return [
            *_logo_lines(width, symbol_frame=symbol_frame),
            "",
            "Start with a plain-language request:",
            "  give alice 48 hours of wall time on the gpu qos",
            "",
            "The agent will draft a plan first. Review the right rail, then run or approve.",
        ]
    if _is_startup_transcript(transcript):
        return [
            *_logo_lines(width, symbol_frame=symbol_frame),
            "",
            "AutoHPC TUI is ready.",
            "Start with a plain-language request, or use /help for commands.",
            "",
            *[f"      {line}" for line in transcript],
        ]
    out: list[str] = []
    for line in transcript:
        if line.startswith("> "):
            out.append(f"You  {line[2:]}")
        elif line.startswith("Plan "):
            out.append(f"Agent  {line}")
        elif line.startswith("Use /run") or line.startswith("Plan paused"):
            out.append(f"Hint  {line}")
        else:
            out.append(f"      {line}" if out else line)
    return out


def _is_startup_transcript(transcript: Sequence[str]) -> bool:
    return len(transcript) == 1 and transcript[0].startswith("AutoHPC TUI.")


def _logo_lines(width: int, *, symbol_frame: int) -> list[str]:
    logo = ASCII_LOGO if width >= max(len(line) for line in ASCII_LOGO) else COMPACT_LOGO
    beacon = LOGO_BEACON_FRAMES[symbol_frame % len(LOGO_BEACON_FRAMES)]
    cluster = LOGO_CLUSTER_FRAMES[symbol_frame % len(LOGO_CLUSTER_FRAMES)]
    return [
        _center(cluster, width),
        _center(beacon, width),
        "",
        *[_center(line.rstrip(), width) for line in logo],
        "",
        _center("typed tools | audited plans | reversible operations", width),
    ]


def _center(text: str, width: int) -> str:
    if width <= len(text):
        return text
    return text.center(width)


def _is_logo_render_line(line: str) -> bool:
    return any(
        marker in line
        for marker in (
            "AutoHPC [",
            "AutoHPC ==",
            "AutoHPC =>",
            "AutoHPC >",
            "AutoHPC <",
            "mgmt ->",
            "mgmt =>",
            "/   |",
            "/ /| |",
            "/ ___",
            "_\\__,_",
            "/ _ |",
            "/ __ /",
            "\\____/",
        )
    )


def _is_logo_beacon_line(line: str) -> bool:
    return "AutoHPC" in line and any(marker in line for marker in ("<==", "-<=", "--<", "-=>"))


def _context_lines(plan: Plan | None, *, selected_action: int = 0) -> list[str]:
    lines = ["Quick Actions"]
    for idx, command in enumerate(COMMAND_PALETTE):
        marker = ">" if idx == selected_action % len(COMMAND_PALETTE) else " "
        lines.append(f" {marker} {command}")
    lines.append("")
    lines.extend(_plan_lines(plan))
    lines.extend(
        [
            "",
            "Keys",
            "  Tab complete",
            "  Ctrl-N/P select",
            "  Ctrl-E insert",
            "  Ctrl-R draft/run",
            "  Ctrl-A approve",
        ]
    )
    return lines


def _plan_lines(plan: Plan | None) -> list[str]:
    if plan is None:
        return [
            "Plan",
            "  no active plan",
            "",
            "Next",
            "  describe a change",
            "  review generated steps",
            "  run when ready",
        ]
    lines = [
        "Plan",
        f"  id: {plan.id}",
        f"  state: {plan.state.value}",
        "",
        "Steps",
    ]
    for idx, step in enumerate(plan.steps, start=1):
        marker = _status_marker(step.status.value)
        lines.append(f"  {marker} {idx}. {step.tool}")
        lines.append(f"     id: {step.id}")
        lines.append(f"     status: {step.status.value}")
        if step.result is not None:
            lines.append(f"     result: {step.result.status.value}")
            if step.result.error is not None:
                lines.append(f"     error: {step.result.error.message}")
            if step.result.diff is not None:
                lines.append("     diff:")
                lines.extend(f"       {line}" for line in step.result.diff.render().splitlines())
    lines.extend(["", "Intent", f"  {plan.intent}"])
    return lines


def _status_marker(status: str) -> str:
    return {
        "pending": "○",
        "running": "◐",
        "done": "●",
        "failed": "×",
        "needs_approval": "!",
        "skipped": "-",
    }.get(status, "•")


def _dynamic_symbol(plan: Plan | None, symbol_frame: int) -> str:
    if plan is None:
        return SPINNER_FRAMES[symbol_frame % len(SPINNER_FRAMES)]
    if plan.state.value == "paused":
        return "[!!]"
    if plan.state.value == "done":
        return "[OK]"
    if plan.state.value == "failed":
        return "[XX]"
    if plan.state.value == "running":
        return SPINNER_FRAMES[symbol_frame % len(SPINNER_FRAMES)]
    return "[>>]"


def _fit_lines(lines: Sequence[str], width: int, height: int) -> list[str]:
    return [_truncate(line, width).ljust(width) for line in lines[:height]]


def _truncate(text: str, width: int) -> str:
    if width <= 0:
        return ""
    return text if len(text) <= width else text[: max(0, width - 1)] + "…"
