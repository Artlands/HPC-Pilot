"""Textual-based terminal UI — Claude Code / OpenCode inspired design."""
from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, RichLog, Static
from textual import on

from hpc_agent.core.shell import ShellSession

COMMAND_PALETTE = ("/run", "/approve", "/show", "/tools", "/help", "/exit")

_STEP_ICON: dict[str, tuple[str, str]] = {
    "pending":        ("○", "dim"),
    "running":        ("◐", "bold yellow"),
    "done":           ("●", "bold green"),
    "failed":         ("✕", "bold red"),
    "needs_approval": ("!", "bold yellow"),
    "skipped":        ("─", "dim"),
}

_STATE_COLOR: dict[str, str] = {
    "draft":     "yellow",
    "running":   "cyan",
    "paused":    "orange3",
    "done":      "green",
    "failed":    "red",
    "cancelled": "dim",
}

# ANSI Shadow block-character logo — each line is exactly 59 chars wide.
ASCII_LOGO = [
    " █████╗ ██╗   ██╗████████╗ ██████╗ ██╗  ██╗██████╗  ██████╗",
    "██╔══██╗██║   ██║╚══██╔══╝██╔═══██╗██║  ██║██╔══██╗██╔════╝",
    "███████║██║   ██║   ██║   ██║   ██║███████║██████╔╝██║     ",
    "██╔══██║██║   ██║   ██║   ██║   ██║██╔══██║██╔═══╝ ██║     ",
    "██║  ██║╚██████╔╝   ██║   ╚██████╔╝██║  ██║██║     ╚██████╗",
    "╚═╝  ╚═╝ ╚═════╝    ╚═╝    ╚═════╝ ╚═╝  ╚═╝╚═╝      ╚═════╝",
]

_BOX_WIDTH = 54

CSS = """
Screen {
    background: $background;
}

#layout {
    height: 100%;
    width: 100%;
}

#status-bar {
    height: 1;
    background: $primary-darken-3;
    color: $primary-lighten-1;
    padding: 0 1;
}

#chat-log {
    height: 1fr;
    padding: 0 2;
    scrollbar-gutter: stable;
    scrollbar-size: 1 1;
    scrollbar-color: $primary-darken-2;
}

#actions-bar {
    height: 1;
    background: $surface;
    padding: 0;
}

#action-cmds {
    width: auto;
    background: $surface;
    color: $text-muted;
    padding: 0 2;
}

#action-hints {
    width: 1fr;
    background: $surface;
    color: $text-muted;
    padding: 0 2;
    text-align: right;
}

#input-area {
    height: 3;
    border-top: solid $accent;
    padding: 0 1;
    align: left middle;
    background: $background;
}

#prompt-label {
    width: auto;
    color: $success;
    padding: 0 1 0 0;
}

#chat-input {
    width: 1fr;
    background: transparent;
    border: none;
    color: $text;
    padding: 0;
}

#chat-input:focus {
    border: none;
    background: transparent;
}
"""


class TuiApp(App[None]):
    """Claude Code / OpenCode-inspired TUI for AutoHPC."""

    DARK = True
    CSS = CSS
    TITLE = "AutoHPC"

    BINDINGS = [
        Binding("ctrl+d", "quit", "Exit"),
        Binding("ctrl+c", "quit", "Exit", show=False),
        Binding("ctrl+l", "clear_chat", "Clear", show=False),
        Binding("ctrl+r", "quick_run", "/run", show=False),
        Binding("ctrl+a", "quick_approve", "/approve", show=False),
        Binding("tab", "tab_complete", "Complete", show=False, priority=True),
    ]

    def __init__(self, session: ShellSession) -> None:
        super().__init__()
        self.session = session
        self.session.write = self._write

    def compose(self) -> ComposeResult:
        with Vertical(id="layout"):
            yield Static(self._status_text(), id="status-bar")
            yield RichLog(id="chat-log", highlight=True, markup=True, wrap=True)
            with Horizontal(id="actions-bar"):
                yield Static(self._commands_text(), id="action-cmds")
                yield Static(self._hints_text(), id="action-hints")
            with Horizontal(id="input-area"):
                yield Static("[bold green]>[/] ", id="prompt-label")
                yield Input(
                    placeholder="Describe a change, or type /help…",
                    id="chat-input",
                )

    def on_mount(self) -> None:
        self._show_welcome()
        self.query_one("#chat-input", Input).focus()

    # ── input handling ───────────────────────────────────────────────

    @on(Input.Submitted, "#chat-input")
    def handle_input_submit(self, event: Input.Submitted) -> None:
        line = event.value.strip()
        self.query_one("#chat-input", Input).value = ""
        if not line:
            return
        self._submit(line)

    # ── actions ──────────────────────────────────────────────────────

    def action_quit(self) -> None:
        self.exit()

    def action_clear_chat(self) -> None:
        self.query_one("#chat-log", RichLog).clear()
        self._show_welcome()

    def action_quick_run(self) -> None:
        self._submit("/run")

    def action_quick_approve(self) -> None:
        self._submit("/approve")

    def action_tab_complete(self) -> None:
        inp = self.query_one("#chat-input", Input)
        text = inp.value.strip()
        if not text or not text.startswith("/"):
            return
        matches = [c for c in COMMAND_PALETTE if c.startswith(text)]
        if matches:
            inp.value = matches[0]
            inp.cursor_position = len(matches[0])

    # ── rendering helpers ────────────────────────────────────────────

    def _status_text(self) -> str:
        plan = self.session.current_plan
        if plan is None:
            plan_info = "[dim]no active plan[/]"
        else:
            short_id = plan.id[:8] if len(plan.id) >= 8 else plan.id
            color = _STATE_COLOR.get(plan.state.value, "dim")
            plan_info = f"[dim]#{short_id}[/]  [{color}]● {plan.state.value}[/]"
        return (
            f"[bold cyan] AutoHPC[/]"
            f"  [dim]{self.session.actor} · {self.session.actor_role.value}[/]"
            f"  [dim]│[/]  {plan_info}"
        )

    def _commands_text(self) -> str:
        return "  ".join(f"[cyan]{c}[/]" for c in COMMAND_PALETTE)

    def _hints_text(self) -> str:
        return "[dim]⇥ complete  ^r /run  ^a /approve  ^l clear  ^d exit[/]"

    def _show_welcome(self) -> None:
        log = self.query_one("#chat-log", RichLog)
        log.write("")
        for line in ASCII_LOGO:
            log.write(f"[green]{line}[/]")
        log.write("")
        log.write("[dim]typed tools · audited plans · reversible operations[/]")
        log.write("[dim]" + "─" * 59 + "[/]")
        log.write("")
        log.write(
            "[dim]Start with a plain-language request:[/]  "
            "[italic]give alice 48h of wall time on the gpu qos[/]"
        )
        log.write(
            "[dim]The agent drafts a plan first — review, then [/]"
            "[cyan]/run[/][dim] or [/][cyan]/approve[/][dim].[/]"
        )
        log.write("")

    def _submit(self, line: str) -> None:
        log = self.query_one("#chat-log", RichLog)
        log.write(f"\n[bold yellow]You[/]  {line}")
        self.session.handle_line(line)
        self.query_one("#status-bar", Static).update(self._status_text())
        if not self.session.running:
            self.exit()

    def _write(self, text: str) -> None:
        log = self.query_one("#chat-log", RichLog)
        lines = text.splitlines()
        if not lines:
            return

        first = lines[0]

        if first.startswith("Plan "):
            self._write_plan(log, lines)
        elif first.startswith("Use /run") or first.startswith("Plan paused"):
            log.write(f"  [dim italic]↳ {first}[/]")
        elif first == "bye":
            log.write("[dim]Session ended.[/]")
        else:
            # Agent label inline with the first content line.
            log.write(f"\n[bold cyan]Agent[/]  {first}")
            for ln in lines[1:]:
                log.write(f"  {ln}")

    def _write_plan(self, log: RichLog, lines: list[str]) -> None:
        log.write("\n[bold cyan]Agent[/]  [dim]Plan drafted[/]")
        log.write("[dim]┌" + "─" * _BOX_WIDTH + "[/]")
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("Plan "):
                uuid = stripped[5:]
                short = f"#{uuid[:8]}" if len(uuid) >= 8 else f"#{uuid}"
                log.write(f"[dim]│[/]  [bold]{short}[/]")
            elif stripped.startswith("Intent: "):
                log.write(f"[dim]│  {stripped[8:]}[/]")
            elif stripped.startswith("State: "):
                state = stripped[7:]
                color = _STATE_COLOR.get(state, "dim")
                log.write(f"[dim]│[/]  [{color}]● {state}[/]")
            elif stripped == "Steps:":
                log.write("[dim]│[/]")
            elif stripped.startswith("- ") and "status=" in stripped:
                log.write(f"[dim]│[/]    {self._fmt_step(stripped)}")
            elif stripped.startswith("input="):
                log.write(f"[dim]│      {stripped}[/]")
            elif stripped.startswith(("result=", "error=", "diff:")):
                log.write(f"[dim]│      {stripped}[/]")
            elif stripped:
                log.write(f"[dim]│[/]  {line.rstrip()}")
        log.write("[dim]└" + "─" * _BOX_WIDTH + "[/]")

    def _fmt_step(self, line: str) -> str:
        """Format a step line: strip raw prefix, remove noise, colour the status."""
        clean = line.removeprefix("- ").strip()
        clean = clean.replace(" depends_on=None", "").replace(" depends_on=[]", "")
        for status, (icon, style) in _STEP_ICON.items():
            if f"status={status}" in clean:
                rest = clean.replace(f"status={status}", f"[{style}]{status}[/]")
                return f"[{style}]{icon}[/]  {rest}"
        return clean
