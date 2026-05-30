"""Uniform tool return type. See spec 00 §3.2."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from hpc_agent.safety.diff import Diff
from hpc_agent.tools.errors import ErrorKind, ToolError


class ToolStatus(StrEnum):
    OK = "ok"
    DRY_RUN = "dry_run"
    NEEDS_APPROVAL = "needs_approval"
    DENIED = "denied"
    ERROR = "error"


class ToolResult(BaseModel):
    status: ToolStatus
    data: dict[str, Any] | None = None
    diff: Diff | None = None
    audit_id: str | None = None
    config_commit: str | None = None
    error: ToolError | None = None

    @property
    def ok(self) -> bool:
        return self.status == ToolStatus.OK

    # ---- constructors ----
    @classmethod
    def dry_run(cls, diff: Diff) -> ToolResult:
        return cls(status=ToolStatus.DRY_RUN, diff=diff)

    @classmethod
    def needs_approval(cls, diff: Diff) -> ToolResult:
        return cls(status=ToolStatus.NEEDS_APPROVAL, diff=diff)

    @classmethod
    def denied(cls, reason: str) -> ToolResult:
        return cls(
            status=ToolStatus.DENIED,
            error=ToolError(kind=ErrorKind.POLICY_DENIED, message=reason),
        )

    @classmethod
    def failed(cls, error: ToolError) -> ToolResult:
        return cls(status=ToolStatus.ERROR, error=error)

    @classmethod
    def success(
        cls,
        data: dict[str, Any] | None = None,
        *,
        diff: Diff | None = None,
        audit_id: str | None = None,
        config_commit: str | None = None,
    ) -> ToolResult:
        return cls(
            status=ToolStatus.OK,
            data=data,
            diff=diff,
            audit_id=audit_id,
            config_commit=config_commit,
        )
