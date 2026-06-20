"""Output parsers for Slurm commands."""

from __future__ import annotations

import re
from typing import Any


def parse_squeue_long(output: str) -> list[dict[str, str]]:
    """Parse ``squeue -l`` output into a list of job dicts."""
    rows: list[dict[str, str]] = []
    header: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("---"):
            continue
        if not header and re.match(r"^JOBID\s", stripped, re.IGNORECASE):
            header = stripped.split()
            continue
        if header:
            parts = stripped.split(None, len(header) - 1)
            if parts:
                rows.append(dict(zip(header, parts, strict=False)))
    return rows


def parse_reservations(output: str) -> list[dict[str, str]]:
    """Parse ``scontrol show reservation`` output into a list of reservation dicts."""
    reservations: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in output.splitlines():
        for key, value in re.findall(r"(\w+)=(\S+)", line):
            if key == "ReservationName":
                if current:
                    reservations.append(current)
                current = {"ReservationName": value}
            elif current:
                current[key] = value
    if current:
        reservations.append(current)
    return reservations


def parse_sacct(output: str) -> list[dict[str, str]]:
    """Parse ``sacct -P`` pipe-delimited output into a list of job accounting dicts."""
    rows: list[dict[str, str]] = []
    header: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split("|")
        if not header:
            header = parts
            continue
        rows.append(dict(zip(header, parts, strict=False)))
    return rows


def parse_sshare(output: str) -> list[dict[str, str]]:
    """Parse ``sshare -Pl`` output into a list of fairshare dicts."""
    rows: list[dict[str, str]] = []
    header: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("---"):
            continue
        if not header:
            header = stripped.split()
            continue
        parts = stripped.split(None, len(header) - 1)
        if parts:
            rows.append(dict(zip(header, parts, strict=False)))
    return rows


def parse_sdiag(output: str) -> dict[str, Any]:
    """Parse ``sdiag`` output into a structured dict with scheduler statistics."""
    result: dict[str, Any] = {}
    section = "general"
    result[section] = {}

    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Section headers end with ':' and contain no '='
        if stripped.endswith(":") and "=" not in stripped:
            section = (
                stripped[:-1].strip().lower().replace(" ", "_").replace("(", "").replace(")", "")
            )
            result.setdefault(section, {})
            continue
        # Key: value pairs
        if ":" in stripped:
            raw_key, _, val = stripped.partition(":")
            key = (
                raw_key.strip()
                .lower()
                .replace(" ", "_")
                .replace("(", "")
                .replace(")", "")
                .replace("/", "_")
            )
            val = val.strip()
            target = result.get(section)
            if isinstance(target, dict):
                target[key] = val
            else:
                result.setdefault("general", {})[key] = val

    return result
