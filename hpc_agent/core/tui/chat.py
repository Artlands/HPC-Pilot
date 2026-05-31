"""Chat REPL view for HPC Pilot TUI v2."""
from __future__ import annotations

from typing import TYPE_CHECKING

from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, RichLog, Static

from hpc_agent.core.plan import Plan
from hpc_agent.core.tui.widgets import (
    PLAN_STATE_COLOR,
    STEP_ICON,
)

if TYPE_CHECKING:
    from hpc_agent.core.shell import ShellSession


class PlanReady(Message):
    """Posted when a plan has been built."""

    def __init__(self, plan: Plan) -> None:
        super().__init__()
        self.plan = plan


class InputSubmitted(Message):
    """Posted when the user submits input."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


ASCII_LOGO = [
    "██╗  ██╗██████╗  ██████╗    ██████╗ ██╗██╗      ██████╗ ████████╗",
    "██║  ██║██╔══██╗██╔════╝    ██╔══██╗██║██║     ██╔═══██╗╚══██╔══╝",
    "███████║██████╔╝██║         ██████╔╝██║██║     ██║   ██║   ██║   ",
    "██╔══██║██╔═══╝ ██║         ██╔═══╝ ██║██║     ██║   ██║   ██║   ",
    "██║  ██║██║     ╚██████╗    ██║     ██║███████╗╚██████╔╝   ██║   ",
    "╚═╝  ╚═╝╚═╝      ╚═════╝    ╚═╝     ╚═╝╚══════╝ ╚═════╝    ╚═╝   ",
]


class ChatView(Widget):
    """Main chat REPL view with log and input."""

    DEFAULT_CSS = """
    ChatView {
        height: 1fr;
        width: 1fr;
    }

    #chat-log {
        height: 1fr;
        padding: 0 1;
        scrollbar-gutter: stable;
        scrollbar-size: 1 1;
        scrollbar-color: $primary-darken-2;
    }

    #chat-input-area {
        height: 3;
        border-top: solid $accent;
        padding: 0 1;
        background: $background;
    }

    #chat-prompt {
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

    def __init__(self, session: ShellSession, **kwargs) -> None:
        super().__init__(**kwargs)
        self.session = session

    def compose(self) -> ComposeResult:
        yield RichLog(id="chat-log", highlight=True, markup=True, wrap=True)
        with Vertical(id="chat-input-area"), Vertical(id="chat-input-row"):
            yield Static("[bold green]>[/]", id="chat-prompt")
            yield Input(
                placeholder="Describe a change, or type /help…",
                id="chat-input",
            )

    def on_mount(self) -> None:
        self._show_welcome()
        self.query_one("#chat-input", Input).focus()

    @on(Input.Submitted, "#chat-input")
    def handle_input_submit(self, event: Input.Submitted) -> None:
        line = event.value.strip()
        self.query_one("#chat-input", Input).value = ""
        if not line:
            return
        self.post_message(InputSubmitted(line))

    def show_welcome(self) -> None:
        self._show_welcome()

    def write_user(self, text: str) -> None:
        log = self.query_one("#chat-log", RichLog)
        log.write(f"\n[bold yellow]You[/]  {text}")

    def write_agent(self, text: str) -> None:
        """Write agent output, streaming-capable."""
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
            log.write(f"\n[bold cyan]Agent[/]  {first}")
            for ln in lines[1:]:
                log.write(f"  {ln}")

    def write_streaming_chunk(self, text: str) -> None:
        """Append streaming text to the log (for real-time agent output)."""
        log = self.query_one("#chat-log", RichLog)
        log.write(text, end="")

    def clear(self) -> None:
        log = self.query_one("#chat-log", RichLog)
        log.clear()
        self._show_welcome()

    # ── private helpers ────────────────────────────────────────

    def _show_welcome(self) -> None:
        log = self.query_one("#chat-log", RichLog)
        log.write("")
        for line in ASCII_LOGO:
            log.write(f"[green]{line}[/]")
        log.write("")
        log.write("[dim]typed tools · audited plans · reversible operations[/]")
        log.write("[dim]" + "─" * 65 + "[/]")
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

    def _write_plan(self, log: RichLog, lines: list[str]) -> None:
        BOX_WIDTH = 54
        log.write("\n[bold cyan]Agent[/]  [dim]Plan drafted[/]")
        log.write("[dim]┌" + "─" * BOX_WIDTH + "[/]")
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
                color = PLAN_STATE_COLOR.get(state, "dim")
                log.write(f"[dim]│[/]  [{color}]● {state}[/]")
            elif stripped == "Steps:":
                log.write("[dim]│[/]")
            elif stripped.startswith("- ") and "status=" in stripped:
                log.write(f"[dim]│[/]    {self._fmt_step(stripped)}")
            elif stripped.startswith(
                ("input=", "result=", "error=", "diff:")
            ):
                log.write(f"[dim]│      {stripped}[/]")
            elif stripped:
                log.write(f"[dim]│[/]  {line.rstrip()}")
        log.write("[dim]└" + "─" * BOX_WIDTH + "[/]")

    def _fmt_step(self, line: str) -> str:
        clean = line.removeprefix("- ").strip()
        clean = clean.replace(" depends_on=None", "").replace(" depends_on=[]", "")
        for status, (icon, style) in STEP_ICON.items():
            if f"status={status}" in clean:
                rest = clean.replace(f"status={status}", f"[{style}]{status}[/]")
                return f"[{style}]{icon}[/]  {rest}"
        return clean
