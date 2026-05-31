"""Audit log viewer for HPC Pilot TUI v2."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import DataTable, Input

from hpc_agent.exec import audit
from hpc_agent.exec.audit import AuditEvent


class AuditView(Widget):
    """Searchable/filterable audit trail viewer."""

    DEFAULT_CSS = """
    AuditView {
        height: 1fr;
        width: 1fr;
        padding: 1;
    }

    #audit-filters {
        height: 3;
        margin-bottom: 1;
    }

    #audit-search {
        width: 1fr;
    }

    #audit-table {
        height: 1fr;
        scrollbar-gutter: stable;
    }
    """

    events: reactive[list[AuditEvent]] = reactive(list)

    def compose(self) -> ComposeResult:
        with Horizontal(id="audit-filters"):
            yield Input(placeholder="Filter by tool, actor, or status…", id="audit-search")
        yield DataTable(id="audit-table")

    def on_mount(self) -> None:
        table = self.query_one("#audit-table", DataTable)
        table.add_columns("Time", "Actor", "Tool", "Decision", "Status", "Commands")
        table.cursor_type = "row"
        self._load_events()

    def _load_events(self) -> None:
        try:
            self.events = audit.list_events(limit=50)
        except Exception:
            self.events = []
        self._refresh_table()

    def _refresh_table(self) -> None:
        table = self.query_one("#audit-table", DataTable)
        table.clear()
        for event in self.events:
            ts = event.ts.strftime("%H:%M:%S") if event.ts else "?"
            cmds = str(len(event.commands))
            table.add_row(
                ts,
                event.actor,
                event.tool,
                event.decision,
                event.result_status or "-",
                cmds,
            )
