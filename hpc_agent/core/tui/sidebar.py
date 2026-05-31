"""Sidebar navigation widget for HPC Pilot TUI v2."""
from __future__ import annotations

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import ListItem, ListView, Static


class ViewSelected(Message):
    """Posted when a sidebar nav item is selected."""

    control: Widget = None  # type: ignore[assignment]

    def __init__(self, view_id: str, control: Widget) -> None:
        super().__init__()
        self.view_id = view_id
        self.control = control


NavItem = tuple[str, str, str]  # (view_id, icon, label)

NAV_ITEMS: list[NavItem] = [
    ("chat",     "💬", "Chat"),
    ("plan",     "◆",  "Plan"),
    ("dashboard","▦",  "Dashboard"),
    ("tools",    "⚙",  "Tools"),
    ("audit",    "📋", "Audit"),
]


class NavListItem(ListItem):
    """A styled nav item with icon and label."""

    def __init__(self, view_id: str, icon: str, label: str) -> None:
        super().__init__()
        self.view_id = view_id
        self.icon = icon
        self.label_text = label

    def render(self) -> Text:
        return Text(f" {self.icon}  {self.label_text}")


class Sidebar(Widget):
    """Left sidebar with navigation list and footer."""

    DEFAULT_CSS = """
    Sidebar {
        width: 22;
        height: 1fr;
        background: $surface;
        border-right: solid $primary-darken-2;
    }

    #sidebar-title {
        height: 3;
        padding: 1 1 0 1;
        color: $primary-lighten-1;
        text-style: bold;
        content-align: left middle;
    }

    #nav-list {
        height: 1fr;
        padding: 0;
    }

    NavListItem {
        padding: 0 1;
        height: 3;
    }

    NavListItem.-active {
        background: $primary-darken-2;
        color: $primary-lighten-2;
        text-style: bold;
    }

    #sidebar-footer {
        height: auto;
        min-height: 1;
        padding: 1 1;
        color: $text-muted;
        text-style: dim;
        border-top: solid $primary-darken-1;
    }
    """

    active_view: reactive[str] = reactive("chat")

    def compose(self) -> ComposeResult:
        yield Static(" HPC Pilot", id="sidebar-title")
        with ListView(id="nav-list"):
            for view_id, icon, label in NAV_ITEMS:
                yield NavListItem(view_id, icon, label)
        yield Static("ctrl+tab switch\nctrl+d exit", id="sidebar-footer")

    @on(ListView.Selected, "#nav-list")
    def handle_nav_select(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, NavListItem):
            self.active_view = item.view_id
            self.post_message(ViewSelected(item.view_id, control=self))

    def watch_active_view(self, old: str, new: str) -> None:
        """Update active styling when view changes."""
        nav_list = self.query_one("#nav-list", ListView)
        for item in nav_list.query(NavListItem):
            item.set_class(item.view_id == new, "-active")
