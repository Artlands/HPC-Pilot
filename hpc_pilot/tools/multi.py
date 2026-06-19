"""Cross-cluster query tool — run a tool against multiple clusters in parallel.

Usage:
    result = hpc_multi_query("hpc_slurm_queue", {"user": "alice"}, ["staging", "prod"])
    for cluster_name, output in result.items():
        print(f"{cluster_name}: {output}")
"""
from __future__ import annotations

from typing import Any


def _check_rbac(tool: str, cluster: str) -> None:
    """Validate the current user's RBAC permission for *tool*.

    Uses synchronous dispatch.invoke (without executing the tool) by
    calling check_permission directly.  Raises PermissionError when the
    caller lacks the required role.
    """
    from hpc_pilot.rbac import check_permission, get_role

    check_permission(tool, get_role())


def _query_single(tool: str, args: dict[str, Any], cluster: str) -> str:
    """Execute *tool* with *args* on *cluster* and return the result string.

    Each cluster call gets its own RBAC check so that partial-failure
    semantics are preserved: one cluster's permission error does not
    block another.
    """
    _check_rbac(tool, cluster)

    from hpc_pilot import tools
    from hpc_pilot.dispatch import _dispatch

    effective_args = dict(args)
    effective_args["cluster"] = cluster
    return _dispatch(tool, effective_args, tools)


def hpc_multi_query(
    tool: str,
    args: dict[str, Any],
    clusters: list[str],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run *tool* with *args* against each cluster in *clusters* in parallel.

    Parameters
    ----------
    tool:
        The hpc_* tool name to invoke (e.g. ``"hpc_slurm_queue"``).
    args:
        Keyword arguments for the tool (without the ``cluster`` key).
    clusters:
        One or more cluster names to target.
    dry_run:
        When True, preview the call without executing.

    Returns
    -------
    dict[str, Any]
        Mapping of ``{cluster_name: result_string_or_error_string}``.
        Partial success: one failing cluster does not affect the others.
    """
    if not clusters:
        return {}

    if dry_run:
        return {
            cluster: (
                f"DRY-RUN: hpc_multi_query(tool={tool!r}, "
                f"clusters={clusters!r}, args={args})"
            )
            for cluster in clusters
        }

    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: dict[str, Any] = {}

    with ThreadPoolExecutor(max_workers=len(clusters)) as pool:
        future_map = {
            pool.submit(_query_single, tool, args, cluster): cluster
            for cluster in clusters
        }
        for future in as_completed(future_map):
            cluster = future_map[future]
            try:
                result = future.result()
                results[cluster] = result
            except PermissionError as exc:
                results[cluster] = f"[Permission denied] {exc}"
            except Exception as exc:
                results[cluster] = f"[Error] {exc}"

    return results
