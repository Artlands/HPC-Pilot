"""Centralized tool invocation: RBAC check → approval → audit → dispatch → result string."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from typing import Any, cast

from hpc_pilot.audit import AuditEvent, audit_tool, log_audit
from hpc_pilot.rbac import Role, check_permission

# Tools that require out-of-band approval before execution.
_APPROVAL_REQUIRED_TOOLS: frozenset[str] = frozenset(
    {
        "hpc_slurm_reconfigure",
        "hpc_warewulf_configure_dhcp",
        "hpc_warewulf_configure_tftp",
        "hpc_warewulf_configure_nfs",
        "hpc_self_evolve",
        "hpc_self_evolve_create_pr",
    }
)


# ---------------------------------------------------------------------------
# Per-actor token-bucket rate limiter
# ---------------------------------------------------------------------------


class _TokenBucket:
    """Simple in-memory token bucket per actor."""

    def __init__(self, calls_per_minute: int) -> None:
        self._calls_per_minute = calls_per_minute
        self._tokens: dict[str, float] = {}
        self._last_refill: dict[str, float] = {}

    def _refill(self, actor: str, now: float) -> None:
        elapsed = now - self._last_refill.get(actor, now)
        self._tokens[actor] = min(
            float(self._calls_per_minute),
            self._tokens.get(actor, float(self._calls_per_minute))
            + elapsed * (self._calls_per_minute / 60.0),
        )
        self._last_refill[actor] = now

    def consume(self, actor: str) -> bool:
        """Return True if *actor* may proceed, False if rate limited."""
        if self._calls_per_minute <= 0:
            return True  # disabled
        now = time.monotonic()
        self._refill(actor, now)
        if self._tokens[actor] >= 1.0:
            self._tokens[actor] -= 1.0
            return True
        return False


# Read rate limit config once at module load
_RATE_LIMITER: _TokenBucket | None = None


def _get_rate_limiter() -> _TokenBucket:
    global _RATE_LIMITER  # noqa: PLW0603
    if _RATE_LIMITER is None:
        _RATE_LIMITER = _TokenBucket(calls_per_minute=60)
        try:
            import yaml

            from hpc_pilot.paths import config_path

            path = config_path()
            if os.path.exists(path):
                with open(path) as f:
                    data = yaml.safe_load(f) or {}
                cpm = int(data.get("rate_limit", {}).get("calls_per_minute", 60))
                _RATE_LIMITER = _TokenBucket(calls_per_minute=cpm)
        except Exception:
            pass
    return _RATE_LIMITER


def invoke(
    name: str,
    args: dict[str, Any],
    *,
    role: Role,
    actor: str,
    dry_run: bool = False,
) -> str:
    """RBAC-check, approval-check, audit-log, and execute one HPC tool call.

    Permission denials are audited with returncode=126 before re-raising.
    High-risk tools create an approval request and raise with its ID.
    """
    try:
        check_permission(name, role)
    except PermissionError as exc:
        log_audit(
            AuditEvent(
                tool=name,
                actor=actor,
                role=role.value,
                args=args,
                dry_run=dry_run,
                ts=time.time(),
                returncode=126,
                error=f"permission_denied: {exc}",
            )
        )
        raise

    # Per-actor rate limiting
    limiter = _get_rate_limiter()
    if not limiter.consume(actor):
        log_audit(
            AuditEvent(
                tool=name,
                actor=actor,
                role=role.value,
                args=args,
                dry_run=dry_run,
                ts=time.time(),
                returncode=429,
                error="rate_limited",
            )
        )
        raise PermissionError(
            f"Rate limit exceeded for actor '{actor}'. "
            f"Configure 'rate_limit.calls_per_minute' in config.yaml."
        )

    # Out-of-band approval for high-risk tools
    if name in _APPROVAL_REQUIRED_TOOLS:
        from hpc_pilot.approvals import create_approval

        cluster = args.get("cluster", "default")
        req = create_approval(
            tool=name,
            args=args,
            actor=actor,
            role=role.value,
            cluster=cluster,
            risk_summary=f"Tool '{name}' requires out-of-band approval. Args: {json.dumps(args, default=str)}",
        )
        raise PermissionError(
            f"Tool '{name}' requires out-of-band approval. "
            f"Use: hpc-pilot approvals approve {req.id} "
            f"or visit the Web UI approvals page. "
            f"Approval ID: {req.id}"
        )

    from hpc_pilot import tools

    with audit_tool(name, actor, role.value, args, dry_run=dry_run):
        if name in ("hpc_skill_describe", "hpc_skill_run"):
            result = _dispatch_skill(name, args, role, actor)
        elif name == "hpc_slurm_job_cancel":
            result = _dispatch_job_cancel(args, tools, role, actor)
        else:
            result = _dispatch(name, args, tools)
    return result or "(no output)"


# ---------------------------------------------------------------------------
# Dispatch registry — derived from the canonical @hpc_tool registry.
# ---------------------------------------------------------------------------

_DISPATCH: dict[str, Callable[[dict[str, Any], Any], str]] = {}
_DISPATCH_LOADED: bool = False


def _ensure_dispatch() -> None:
    """Lazily populate _DISPATCH from the canonical tool registry on first use."""
    global _DISPATCH_LOADED  # noqa: PLW0603
    if _DISPATCH_LOADED:
        return
    from hpc_pilot.tools._registry import get_dispatch

    _DISPATCH.clear()
    _DISPATCH.update(get_dispatch())
    _DISPATCH_LOADED = True


def _mk(
    fn_name: str, *positional_keys: str, **kwarg_keys: str
) -> Callable[[dict[str, Any], Any], str]:
    """Build a dispatch handler for tools with simple positional + keyword args."""

    def _handler(args: dict[str, Any], tools: Any) -> str:
        pos = [args[k] for k in positional_keys]
        kw = {dest: args[src] for dest, src in kwarg_keys.items() if src in args}
        cluster = args.get("cluster", "default")
        result = getattr(tools, fn_name)(*pos, cluster=cluster, **kw)
        if isinstance(result, dict):
            return json.dumps(result, indent=2, default=str)
        return str(result)

    return _handler


def _cl(args: dict[str, Any]) -> str:
    val: str | None = args.get("cluster", "default")
    return val or "default"


def _dr(args: dict[str, Any], default: bool = False) -> bool:
    return bool(args.get("dry_run", default))


# OLD _DISPATCH literal deleted — now derived from @hpc_tool registry.
# See _rebuild_dispatch() above and hpc_pilot.tools._registry.get_dispatch().


def _dispatch_job_cancel(args: dict[str, Any], tools: Any, role: Role, actor: str) -> str:
    """Special dispatch for hpc_slurm_job_cancel — passes role and actor for ownership check."""
    result = tools.hpc_slurm_job_cancel(
        args["job_id"],
        actor=actor,
        role=role,
        cluster=args.get("cluster", "default"),
        dry_run=bool(args.get("dry_run", False)),
    )
    return cast(str, result)


def _dispatch_skill(name: str, args: dict[str, Any], role: Role, actor: str) -> str:
    from hpc_pilot.skills.runner import hpc_skill_describe, hpc_skill_run

    if name == "hpc_skill_describe":
        return hpc_skill_describe(args["name"])

    if name == "hpc_skill_run":
        result = hpc_skill_run(
            args["name"],
            args.get("inputs"),
            role=role,
            actor=actor,
            cluster=args.get("cluster", "default"),
            resume_run_id=args.get("resume_run_id"),
        )
        return json.dumps(result, indent=2, default=str)

    return f"[unknown tool: {name}]"


def _dispatch(name: str, args: dict[str, Any], tools: Any) -> str:
    _ensure_dispatch()
    handler = _DISPATCH.get(name)
    if handler is None:
        return f"[unknown tool: {name}]"
    return handler(args, tools)
