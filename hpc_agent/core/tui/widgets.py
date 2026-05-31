"""Shared widgets for HPC Pilot TUI v2."""
from __future__ import annotations

from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import RichLog, Static

from hpc_agent.core.plan import Plan, Step, StepStatus

# ── Icons and colors ────────────────────────────────────────────

STEP_ICON: dict[str, tuple[str, str]] = {
    StepStatus.PENDING.value:        ("○", "dim"),
    StepStatus.RUNNING.value:        ("◐", "bold yellow"),
    StepStatus.DONE.value:           ("●", "bold green"),
    StepStatus.FAILED.value:         ("✕", "bold red"),
    StepStatus.NEEDS_APPROVAL.value: ("!", "bold yellow"),
    StepStatus.SKIPPED.value:        ("─", "dim"),
}

PLAN_STATE_COLOR: dict[str, str] = {
    "draft":     "yellow",
    "running":   "cyan",
    "paused":    "orange3",
    "done":      "green",
    "failed":    "red",
    "cancelled": "dim",
}

RISK_COLOR: dict[str, str] = {
    "read":   "green",
    "low":    "blue",
    "medium": "yellow",
    "high":   "red",
}

NODE_STATE_COLOR: dict[str, str] = {
    "UP":          "green",
    "IDLE":        "green",
    "ALLOCATED":   "green",
    "DRAINED":     "yellow",
    "DRAINING":    "yellow",
    "DOWN":        "red",
    "MAINT":       "yellow",
    "PROVISIONING":"cyan",
    "UNKNOWN":     "dim",
}


def fmt_step_icon(status: str) -> Text:
    """Return a Rich Text with the step status icon."""
    icon, style = STEP_ICON.get(status, ("?", "dim"))
    return Text(icon, style=style)


def fmt_plan_state(state: str) -> Text:
    """Return a Rich Text with the plan state."""
    color = PLAN_STATE_COLOR.get(state, "dim")
    return Text(f"● {state}", style=color)


def fmt_risk(risk: str) -> Text:
    """Return a Rich Text with the risk tier."""
    color = RISK_COLOR.get(risk, "dim")
    return Text(risk.upper(), style=color)


# ── StatusBar ───────────────────────────────────────────────────

class StatusBar(Static):
    """Top status bar showing session info and plan state."""

    plan_state: reactive[str] = reactive("none")
    plan_id: reactive[str] = reactive("")
    actor: reactive[str] = reactive("")
    actor_role: reactive[str] = reactive("")

    def render(self) -> Text:
        parts = [Text(" HPC Pilot", style="bold cyan")]
        if self.actor:
            parts.append(Text(f"  {self.actor} · {self.actor_role}", style="dim"))
        parts.append(Text("  │  ", style="dim"))
        if self.plan_id:
            short = self.plan_id[:8] if len(self.plan_id) >= 8 else self.plan_id
            color = PLAN_STATE_COLOR.get(self.plan_state, "dim")
            parts.append(Text(f"#{short}", style="dim"))
            parts.append(Text("  ", style="dim"))
            parts.append(Text(f"● {self.plan_state}", style=color))
        else:
            parts.append(Text("no active plan", style="dim"))
        result = Text()
        for p in parts:
            result.append_text(p)
        return result


# ── StepTree ────────────────────────────────────────────────────

class StepTree(Widget):
    """A tree-like view of plan steps with live status."""

    DEFAULT_CSS = """
    StepTree {
        height: 1fr;
        scrollbar-gutter: stable;
        overflow-y: auto;
    }
    """

    plan: reactive[Plan | None] = reactive(None)
    show_results: reactive[bool] = reactive(False)

    def render(self) -> RichLog:
        log = RichLog(highlight=True, markup=True, wrap=True)
        if self.plan is None:
            log.write("[dim]No plan loaded.[/]")
            return log

        short_id = self.plan.id[:8] if len(self.plan.id) >= 8 else self.plan.id
        color = PLAN_STATE_COLOR.get(self.plan.state.value, "dim")
        log.write(f"[bold]Plan #{short_id}[/]  [{color}]● {self.plan.state.value}[/]")
        log.write(f"[dim]{self.plan.intent}[/]")
        log.write("")

        for step in self.plan.steps:
            self._render_step(log, step)

        return log

    def _render_step(self, log: RichLog, step: Step) -> None:
        icon_text = fmt_step_icon(step.status.value)
        status_str = step.status.value
        status_style = STEP_ICON.get(status_str, ("?", "dim"))[1]

        deps = ""
        if step.depends_on:
            deps = f" [dim]← {', '.join(step.depends_on)}[/]"

        header = Text()
        header.append_text(icon_text)
        header.append("  ")
        header.append(step.id, style="bold")
        header.append(f"  {step.tool}", style="cyan")
        header.append(f"  [{status_style}]{status_str}[/]", style=status_style)
        header.append(deps)
        log.write(header)

        if step.input:
            input_str = str(step.input)
            if len(input_str) > 120:
                input_str = input_str[:117] + "..."
            log.write(f"    [dim]input=[/]{input_str}")

        if self.show_results and step.result is not None:
            result_status = step.result.status.value
            log.write(f"    [dim]result=[/]{result_status}")
            if step.result.diff is not None:
                rendered = step.result.diff.render()
                for line in rendered.splitlines():
                    log.write(f"    [dim]{line}[/]")
            if step.result.error is not None:
                err = step.result.error
                log.write(f"    [red]error={err.kind.value}: {err.message}[/]")


# ── DiffViewer ──────────────────────────────────────────────────

class DiffViewer(Widget):
    """Renders a Diff with color-coded changes."""

    DEFAULT_CSS = """
    DiffViewer {
        height: auto;
        max-height: 40;
        scrollbar-gutter: stable;
        overflow-y: auto;
    }
    """

    diff_text: reactive[str] = reactive("")

    def render(self) -> RichLog:
        log = RichLog(highlight=True, markup=True, wrap=True)
        if not self.diff_text:
            log.write("[dim]No diff to display.[/]")
            return log
        for line in self.diff_text.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                log.write(f"[green]{line}[/]")
            elif line.startswith("-") and not line.startswith("---"):
                log.write(f"[red]{line}[/]")
            elif line.startswith("@@"):
                log.write(f"[cyan]{line}[/]")
            elif line.startswith("  blast_radius") or line.startswith("  reversible"):
                log.write(f"[dim]{line}[/]")
            elif line.startswith("  commands:"):
                log.write(f"[yellow]{line}[/]")
            elif line.startswith("    $"):
                log.write(f"    [dim]{line[4:]}[/]")
            else:
                log.write(line)
        return log
