"""Tool explorer view for HPC Pilot TUI v2."""
from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import DataTable, Input, RichLog

from hpc_agent.core.tui.widgets import RISK_COLOR, fmt_risk
from hpc_agent.tools.base import ToolMeta, all_tools


class ToolExplorer(Widget):
    """Browse registered tools with search, table, and detail pane."""

    DEFAULT_CSS = """
    ToolExplorer {
        height: 1fr;
        width: 1fr;
        padding: 1;
    }

    #tool-search {
        height: 3;
        margin-bottom: 1;
    }

    #tool-table {
        height: 1fr;
        scrollbar-gutter: stable;
    }

    #tool-detail {
        height: auto;
        max-height: 15;
        border-top: solid $primary-darken-1;
        padding: 1 0;
        scrollbar-gutter: stable;
        overflow-y: auto;
    }
    """

    all_tools: reactive[list[ToolMeta]] = reactive(list)
    filter_text: reactive[str] = reactive("")

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Search tools…", id="tool-search")
        yield DataTable(id="tool-table")
        yield RichLog(id="tool-detail", highlight=True, markup=True, wrap=True)

    def on_mount(self) -> None:
        self.all_tools = sorted(all_tools(), key=lambda t: t.name)
        table = self.query_one("#tool-table", DataTable)
        table.add_columns("Tool", "Domain", "Risk", "Description")
        table.cursor_type = "row"
        self._refresh_table()

    @on(Input.Changed, "#tool-search")
    def handle_search(self, event: Input.Changed) -> None:
        self.filter_text = event.value.lower()
        self._refresh_table()

    @on(DataTable.RowSelected, "#tool-table")
    def handle_row_selected(self, event: DataTable.RowSelected) -> None:
        table = self.query_one("#tool-table", DataTable)
        if event.row_index is None:
            return
        row_data = table.get_row_at(event.row_index)
        if row_data:
            tool_name = str(row_data[0])
            self._show_detail(tool_name)

    def _refresh_table(self) -> None:
        table = self.query_one("#tool-table", DataTable)
        table.clear()
        for tool_meta in self.all_tools:
            if self.filter_text and self.filter_text not in tool_meta.name.lower():
                continue
            if self.filter_text and self.filter_text not in tool_meta.domain.lower():
                continue
            risk_text = fmt_risk(tool_meta.risk.value)
            table.add_row(
                tool_meta.name,
                tool_meta.domain,
                risk_text,
                tool_meta.description[:60] if tool_meta.description else "",
            )

    def _show_detail(self, tool_name: str) -> None:
        detail = self.query_one("#tool-detail", RichLog)
        detail.clear()
        for meta in self.all_tools:
            if meta.name == tool_name:
                risk_color = RISK_COLOR.get(meta.risk.value, "dim")
                detail.write(f"[bold]{meta.name}[/]  [{risk_color}]{meta.risk.value.upper()}[/]")
                detail.write(f"Domain: {meta.domain}")
                if meta.description:
                    detail.write(f"Description: {meta.description}")
                detail.write("")
                detail.write("[dim]Input Schema:[/]")
                schema = meta.input_model.model_json_schema()
                props = schema.get("properties", {})
                required = set(schema.get("required", []))
                for prop_name, prop_def in props.items():
                    prop_type = prop_def.get("type", "?")
                    desc = prop_def.get("description", "")
                    req = " [red]*[/]" if prop_name in required else ""
                    detail.write(f"  {prop_name}: {prop_type}{req}  [dim]{desc}[/]")
                break
