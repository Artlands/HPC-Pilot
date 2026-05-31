"""Cluster dashboard view for HPC Pilot TUI v2."""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import DataTable, Static

from hpc_agent.core.tui.widgets import NODE_STATE_COLOR


class DashboardView(Widget):
    """Cluster node status dashboard with summary and table."""

    DEFAULT_CSS = """
    DashboardView {
        height: 1fr;
        width: 1fr;
        padding: 1;
    }

    #dashboard-summary {
        height: auto;
        padding: 0 0 1 0;
    }

    #node-table {
        height: 1fr;
        scrollbar-gutter: stable;
    }
    """

    nodes: reactive[list[dict]] = reactive(list)

    def compose(self) -> ComposeResult:
        yield Static(self._summary_text(), id="dashboard-summary")
        table = DataTable(id="node-table")
        yield table

    def on_mount(self) -> None:
        table = self.query_one("#node-table", DataTable)
        table.add_columns("Node", "State", "Partition", "GPUs", "Reason")
        table.cursor_type = "row"

    def watch_nodes(self) -> None:
        if not self.is_mounted:
            return
        self._refresh_table()

    def _summary_text(self) -> Text:
        if not self.nodes:
            return Text("No node data. Press ctrl+r to refresh.", style="dim")

        total = len(self.nodes)
        up = sum(1 for n in self.nodes if n.get("state") in ("UP", "IDLE", "ALLOCATED", "MIXED"))
        drained = sum(1 for n in self.nodes if n.get("state") in ("DRAINED", "DRAINING"))
        down = sum(1 for n in self.nodes if n.get("state") == "DOWN")

        parts = Text()
        parts.append(f"  {total} node(s)", style="bold")
        parts.append("  │  ")
        parts.append(f"{up} up", style="green")
        parts.append("  ")
        if drained:
            parts.append(f"{drained} drained", style="yellow")
            parts.append("  ")
        if down:
            parts.append(f"{down} down", style="red")
            parts.append("  ")
        return parts

    def _refresh_table(self) -> None:
        table = self.query_one("#node-table", DataTable)
        table.clear()
        for node in self.nodes:
            name = node.get("node") or node.get("name") or "?"
            state = node.get("state") or "UNKNOWN"
            partition = node.get("partition") or "-"
            gpus = node.get("gres") or "-"
            reason = node.get("reason") or ""

            state_color = NODE_STATE_COLOR.get(state, "dim")
            table.add_row(
                name,
                Text(state, style=state_color),
                partition,
                gpus,
                reason[:40] if reason else "",
            )

    def set_nodes(self, nodes: list[dict]) -> None:
        """Update the node data and refresh the table."""
        self.nodes = nodes
