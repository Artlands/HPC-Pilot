"""Plan DAG tree visualizer for HPC Pilot TUI v2."""
from __future__ import annotations

from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import RichLog, Static

from hpc_agent.core.plan import Plan
from hpc_agent.core.tui.widgets import (
    STEP_ICON,
    fmt_plan_state,
)


class PlanView(Widget):
    """Interactive plan viewer with step tree and detail pane."""

    DEFAULT_CSS = """
    PlanView {
        height: 1fr;
        width: 1fr;
    }

    #plan-header {
        height: auto;
        padding: 1 2 0 2;
    }

    #plan-tree {
        height: 1fr;
        padding: 0 1;
        scrollbar-gutter: stable;
    }

    #plan-detail {
        height: auto;
        max-height: 12;
        border-top: solid $primary-darken-1;
        padding: 1 2;
        scrollbar-gutter: stable;
        overflow-y: auto;
    }
    """

    plan: reactive[Plan | None] = reactive(None)
    selected_step: reactive[str] = reactive("")

    def compose(self):
        yield Static(self._header_text(), id="plan-header")
        yield RichLog(id="plan-tree", highlight=True, markup=True, wrap=True)
        yield RichLog(id="plan-detail", highlight=True, markup=True, wrap=True)

    def watch_plan(self) -> None:
        if not self.is_mounted:
            return
        self._refresh_tree()
        self.query_one("#plan-header", Static).update(self._header_text())

    def watch_selected_step(self) -> None:
        if not self.is_mounted:
            return
        self._refresh_detail()

    def _header_text(self) -> Text:
        if self.plan is None:
            return Text("No plan loaded", style="dim")
        short = self.plan.id[:8] if len(self.plan.id) >= 8 else self.plan.id
        parts = Text()
        parts.append(f"Plan #{short}  ", style="bold")
        parts.append_text(fmt_plan_state(self.plan.state.value))
        parts.append(f"  {len(self.plan.steps)} step(s)", style="dim")
        return parts

    def _refresh_tree(self) -> None:
        log = self.query_one("#plan-tree", RichLog)
        log.clear()
        if self.plan is None:
            log.write("[dim]No plan loaded. Enter an intent in the Chat view.[/]")
            return

        log.write(f"[dim]{self.plan.intent}[/]")
        log.write("")

        # Build dependency info
        dep_map = {s.id: s.depends_on for s in self.plan.steps}

        for step in self.plan.steps:
            self._render_step_node(log, step, dep_map)

    def _render_step_node(self, log: RichLog, step, dep_map: dict) -> None:
        status = step.status.value
        icon, style = STEP_ICON.get(status, ("?", "dim"))

        # Build tree connector
        header = Text()
        header.append("  ")
        header.append(icon, style=style)
        header.append("  ")
        header.append(step.id, style="bold")
        header.append(f"  {step.tool}", style="cyan")

        # Status badge
        header.append(f"  [{style}]{status}[/]", style=style)

        # Dependency indicator
        if step.depends_on:
            header.append(f"  [dim]← {', '.join(step.depends_on)}[/]")

        # Critical indicator
        if not step.critical:
            header.append("  [dim](non-critical)[/]")

        log.write(header)

        # Show compact input
        if step.input:
            input_str = self._compact_input(step.input)
            log.write(f"       [dim]$input[/] {input_str}")

        # Show result summary if available
        if step.result is not None:
            if step.result.error:
                log.write(f"       [red]$error[/] {step.result.error.message}")
            elif step.result.diff and not step.result.diff.is_noop():
                changes = len(step.result.diff.changes)
                log.write(f"       [green]$diff[/] {changes} change(s)")

    def _compact_input(self, inp: dict) -> str:
        """Create a compact one-line representation of step input."""
        parts = []
        for k, v in inp.items():
            if k == "dry_run":
                continue
            val = str(v)
            if len(val) > 40:
                val = val[:37] + "..."
            parts.append(f"{k}={val}")
        result = ", ".join(parts)
        if len(result) > 100:
            result = result[:97] + "..."
        return result

    def _refresh_detail(self) -> None:
        detail = self.query_one("#plan-detail", RichLog)
        detail.clear()
        if not self.selected_step or self.plan is None:
            detail.write("[dim]Select a step to view details.[/]")
            return

        step = None
        for s in self.plan.steps:
            if s.id == self.selected_step:
                step = s
                break

        if step is None:
            detail.write(f"[dim]Step '{self.selected_step}' not found.[/]")
            return

        detail.write(f"[bold]{step.id}[/]  [cyan]{step.tool}[/]")
        detail.write(f"Status: {step.status.value}")
        detail.write(f"Input: {step.input}")

        if step.depends_on:
            detail.write(f"Depends on: {', '.join(step.depends_on)}")

        if step.result is not None:
            detail.write(f"Result: {step.result.status.value}")
            if step.result.error:
                kind = step.result.error.kind.value
                msg = step.result.error.message
                detail.write(f"Error: [red]{kind}: {msg}[/]")
            if step.result.diff:
                detail.write("[dim]Diff:[/]")
                for line in step.result.diff.render().splitlines():
                    detail.write(f"  {line}")
            if step.result.data:
                detail.write(f"Data: {step.result.data}")
