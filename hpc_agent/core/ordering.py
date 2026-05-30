"""Dependency ordering for plan steps. See spec 02 §4."""

from __future__ import annotations

from hpc_agent.core.plan import Step


class CycleError(Exception):
    pass


def topological_order(steps: list[Step]) -> list[Step]:
    """Return steps in dependency order (Kahn's algorithm). Raises on cycles or unknown
    dependency ids."""
    by_id = {s.id: s for s in steps}
    for s in steps:
        for dep in s.depends_on:
            if dep not in by_id:
                raise KeyError(f"step {s.id} depends on unknown step {dep}")

    indegree = {s.id: len(s.depends_on) for s in steps}
    # children[dep] = steps that depend on dep
    children: dict[str, list[str]] = {s.id: [] for s in steps}
    for s in steps:
        for dep in s.depends_on:
            children[dep].append(s.id)

    # stable: preserve input order among ready nodes
    ready = [s.id for s in steps if indegree[s.id] == 0]
    ordered: list[Step] = []
    while ready:
        node = ready.pop(0)
        ordered.append(by_id[node])
        for child in children[node]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)

    if len(ordered) != len(steps):
        raise CycleError("dependency cycle detected in plan")
    return ordered
