"""Error taxonomy. See spec 00 §3.3.

Tools never raise to the agent loop; they catch, classify, and return a structured error
inside a ToolResult.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class ErrorKind(StrEnum):
    PRECONDITION = "precondition"  # state not as required (e.g. node not drained)
    COMMAND_FAILED = "command_failed"  # underlying CLI returned nonzero
    POLICY_DENIED = "policy_denied"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"  # idempotency / concurrent change
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


class ToolError(BaseModel):
    kind: ErrorKind
    message: str  # human-readable, safe to show
    detail: str | None = None  # stderr / traceback, for logs only
    remediation: str | None = None


class ToolFailure(Exception):
    """Raised internally by tool helpers; caught at the tool boundary and converted to a
    ToolError-bearing ToolResult."""

    def __init__(self, error: ToolError) -> None:
        super().__init__(error.message)
        self.error = error
