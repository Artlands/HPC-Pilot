"""CSS styles for HPC Pilot TUI v2."""

CSS = """
Screen {
    background: $background;
}

/* ── Layout ─────────────────────────────────────────────── */

#app-layout {
    height: 100%;
    width: 100%;
}

#sidebar {
    width: 22;
    background: $surface;
    border-right: solid $primary-darken-2;
    padding: 1 0;
}

#sidebar-title {
    padding: 0 1 1 1;
    color: $primary-lighten-1;
    text-style: bold;
}

#nav-list {
    height: 1fr;
    padding: 0;
}

.nav-item {
    padding: 0 1;
    height: 3;
    content-align: left middle;
}

.nav-item:hover {
    background: $primary-darken-3;
}

.nav-item.-active {
    background: $primary-darken-2;
    color: $primary-lighten-2;
    text-style: bold;
}

#sidebar-footer {
    height: auto;
    padding: 1 1 0 1;
    color: $text-muted;
    text-style: dim;
}

/* ── Main content ───────────────────────────────────────── */

#main-content {
    width: 1fr;
    height: 1fr;
}

/* ── Status bar (top) ──────────────────────────────────── */

#status-bar {
    height: 1;
    background: $primary-darken-3;
    color: $primary-lighten-1;
    padding: 0 1;
    dock: top;
}

/* ── Actions bar (bottom) ──────────────────────────────── */

#actions-bar {
    height: 1;
    background: $surface;
    dock: bottom;
    padding: 0 1;
}

#action-cmds {
    width: auto;
    color: $text-muted;
}

#action-hints {
    width: 1fr;
    color: $text-muted;
    text-align: right;
}

/* ── Chat view ─────────────────────────────────────────── */

#chat-container {
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

/* ── Plan view ─────────────────────────────────────────── */

#plan-container {
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

/* ── Dashboard view ────────────────────────────────────── */

#dashboard-container {
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

/* ── Tool explorer view ────────────────────────────────── */

#tools-container {
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

/* ── Approval modal ────────────────────────────────────── */

#approval-overlay {
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

/* ── Audit view ────────────────────────────────────────── */

#audit-container {
    height: 1fr;
    width: 1fr;
    padding: 1;
}

#audit-filters {
    height: 3;
    margin-bottom: 1;
}

#audit-table {
    height: 1fr;
    scrollbar-gutter: stable;
}

/* ── Shared ────────────────────────────────────────────── */

.step-icon-pending {
    color: $text-muted;
}

.step-icon-running {
    color: $warning;
}

.step-icon-done {
    color: $success;
}

.step-icon-failed {
    color: $error;
}

.step-icon-needs_approval {
    color: $warning;
    text-style: bold;
}

.step-icon-skipped {
    color: $text-muted;
}

.risk-read { color: $success; }
.risk-low { color: $primary-lighten-1; }
.risk-medium { color: $warning; }
.risk-high { color: $error; text-style: bold; }
"""
