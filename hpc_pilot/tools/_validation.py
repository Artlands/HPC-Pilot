"""Input validation helpers shared across all HPC tool modules."""
from __future__ import annotations

import re
import shlex

_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\[\],.-]*$")
_USER_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")


def _validate(value: str, field: str, pattern: re.Pattern[str] = _NAME_RE) -> None:
    """Raise ValueError if *value* is non-empty and does not match *pattern*."""
    if value and not pattern.match(value):
        raise ValueError(f"Invalid {field}: {value!r}")


def _shquote(cmd: list[str]) -> str:
    return " ".join(shlex.quote(c) for c in cmd)
