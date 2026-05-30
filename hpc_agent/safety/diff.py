"""Structured, human-renderable preview of what a tool would change. See spec 01 §2."""

from __future__ import annotations

from pydantic import BaseModel


class Change(BaseModel):
    target: str  # "qos/normal", "node/gpu01", "image/gpu-rocky9"
    field: str | None = None  # "max_wall_min"
    before: str | None = None
    after: str | None = None
    op: str  # create | modify | delete | build | drain | resume


class Diff(BaseModel):
    changes: list[Change] = []
    config_diff: str | None = None  # unified git diff if config files change
    commands_preview: list[list[str]] = []  # argv that WOULD run (redacted)
    blast_radius: int = 1  # #entities affected
    reversible: bool = True
    revert_hint: str | None = None

    def is_noop(self) -> bool:
        return not self.changes and not self.config_diff

    def render(self) -> str:
        """Pretty text for CLI/chat approval prompt."""
        if self.is_noop():
            return "(no changes — already in desired state)"
        lines: list[str] = []
        for c in self.changes:
            field = f".{c.field}" if c.field else ""
            if c.op == "modify":
                lines.append(f"  ~ {c.target}{field}: {c.before} -> {c.after}")
            elif c.op in ("create", "build", "resume"):
                lines.append(f"  + {c.target}{field}: {c.after}")
            elif c.op in ("delete", "drain"):
                lines.append(f"  - {c.target}{field}: {c.before}")
            else:
                lines.append(f"  * {c.target}{field}: {c.before} -> {c.after}")
        if self.commands_preview:
            lines.append("  commands:")
            lines += [f"    $ {' '.join(argv)}" for argv in self.commands_preview]
        lines.append(f"  blast_radius={self.blast_radius} reversible={self.reversible}")
        return "\n".join(lines)
