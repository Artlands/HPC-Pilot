"""Approval modal screen for HPC Pilot TUI v2."""
from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, RichLog, Static

from hpc_agent.core.plan import Step
from hpc_agent.safety.diff import Diff
from hpc_agent.safety.gate import Gate


class ApprovalResult(Message):
    """Posted when the user approves or denies."""

    def __init__(self, approved: bool, step_id: str) -> None:
        super().__init__()
        self.approved = approved
        self.step_id = step_id


class ApprovalModal(ModalScreen[None]):
    """Modal overlay showing diff preview with approve/deny buttons."""

    DEFAULT_CSS = """
    ApprovalModal {
        background: $background;
        align: center middle;
    }

    #approval-dialog {
        width: 80;
        max-width: 90%;
        height: auto;
        max-height: 80%;
        border: tall $accent;
        background: $surface;
        padding: 1 2;
    }

    #approval-title {
        text-style: bold;
        color: $warning;
        padding: 0 0 1 0;
    }

    #approval-diff {
        height: auto;
        max-height: 40;
        scrollbar-gutter: stable;
        overflow-y: auto;
        padding: 1 0;
    }

    #approval-meta {
        height: auto;
        padding: 1 0;
        color: $text-muted;
    }

    #approval-actions {
        height: 3;
        align: center middle;
    }

    .approve-btn {
        width: 16;
        margin: 0 2;
    }

    .deny-btn {
        width: 16;
        margin: 0 2;
    }
    """

    def __init__(
        self,
        step: Step,
        diff: Diff,
        gate: Gate,
    ) -> None:
        super().__init__()
        self.step = step
        self.diff = diff
        self.gate = gate

    def compose(self) -> ComposeResult:
        with Vertical(id="approval-dialog"):
            yield Static("⚠  APPROVAL REQUIRED", id="approval-title")
            yield Static(
                f"Step: [bold]{self.step.id}[/]  [cyan]{self.step.tool}[/]\n"
                f"Risk: {self.step.tool.split('.')[0]}  "
                f"Blast radius: {self.diff.blast_radius}  "
                f"Reversible: {'yes' if self.diff.reversible else 'no'}",
                id="approval-meta",
            )
            yield RichLog(id="approval-diff", highlight=True, markup=True, wrap=True)
            with Horizontal(id="approval-actions"):
                yield Button(
                    "[green]Approve[/]", id="approve-btn",
                    classes="approve-btn", variant="success",
                )
                yield Button(
                    "[red]Deny[/]", id="deny-btn",
                    classes="deny-btn", variant="error",
                )

    def on_mount(self) -> None:
        diff_log = self.query_one("#approval-diff", RichLog)
        rendered = self.diff.render()
        for line in rendered.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                diff_log.write(f"[green]{line}[/]")
            elif line.startswith("-") and not line.startswith("---"):
                diff_log.write(f"[red]{line}[/]")
            elif line.startswith("@@"):
                diff_log.write(f"[cyan]{line}[/]")
            else:
                diff_log.write(line)

        if self.diff.config_diff:
            diff_log.write("")
            diff_log.write("[dim]Config diff:[/]")
            for line in self.diff.config_diff.splitlines():
                diff_log.write(f"  [dim]{line}[/]")

        if self.diff.revert_hint:
            diff_log.write("")
            diff_log.write(f"[dim]Revert: {self.diff.revert_hint}[/]")

    @on(Button.Pressed, "#approve-btn")
    def handle_approve(self) -> None:
        self.post_message(ApprovalResult(approved=True, step_id=self.step.id))
        self.dismiss()

    @on(Button.Pressed, "#deny-btn")
    def handle_deny(self) -> None:
        self.post_message(ApprovalResult(approved=False, step_id=self.step.id))
        self.dismiss()
