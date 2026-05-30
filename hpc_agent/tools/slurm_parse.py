"""Parsers for Slurm's parseable output. See spec 05 (parsing rule).

We always query with `-P` (pipe-delimited, with header) and parse structurally — never
screen-scrape human tables.
"""

from __future__ import annotations


def parse_pipe_table(text: str) -> list[dict[str, str]]:
    """Parse `sacctmgr show ... -P` output (header line + '|'-delimited rows)."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    header = lines[0].split("|")
    rows: list[dict[str, str]] = []
    for line in lines[1:]:
        fields = line.split("|")
        if len(fields) != len(header):
            continue
        rows.append(dict(zip(header, fields, strict=True)))
    return rows


def minutes_to_slurm_time(minutes: int | None) -> str | None:
    """Convert minutes to Slurm D-HH:MM:SS (or HH:MM:SS when < 1 day)."""
    if minutes is None:
        return None
    days, rem = divmod(minutes, 1440)
    hours, mins = divmod(rem, 60)
    if days:
        return f"{days}-{hours:02d}:{mins:02d}:00"
    return f"{hours:02d}:{mins:02d}:00"


def slurm_time_to_minutes(value: str | None) -> int | None:
    """Inverse of minutes_to_slurm_time. Handles 'D-HH:MM:SS', 'HH:MM:SS', or plain mins."""
    if value is None or value in ("", "UNLIMITED", "NONE"):
        return None
    days = 0
    rest = value
    if "-" in value:
        d, rest = value.split("-", 1)
        days = int(d)
    parts = rest.split(":")
    if len(parts) == 3:
        h, m, _s = (int(p) for p in parts)
    elif len(parts) == 2:
        h, m = int(parts[0]), int(parts[1])
    else:
        try:
            return int(value)
        except ValueError:
            return None
    return days * 1440 + h * 60 + m
