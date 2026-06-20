"""Prometheus metrics for HPC Pilot tool invocations and system health.

This module is import-safe even when ``prometheus_client`` is not installed.
All metric objects are wrapped in a custom ``MetricsRegistry`` that returns
no-op placeholders when the optional dependency is missing.
"""

from __future__ import annotations

from typing import Any

try:
    from prometheus_client import Counter, Gauge, Histogram, push_to_gateway, registry

    _REGISTRY = registry.CollectorRegistry()
    _HAS_PROMETHEUS = True
except ImportError:
    _HAS_PROMETHEUS = False

    class _Noop:
        """No-op metric that can be called/incremented/observed without error."""

        def labels(self, **labels: Any) -> _Noop:
            return self

        def inc(self, amount: float = 1) -> None:
            pass

        def observe(self, amount: float) -> None:
            pass

        def set(self, value: float) -> None:
            pass

        def set_function(self, func: Any) -> None:
            pass

    Counter = _Noop  # type: ignore[assignment]
    Histogram = _Noop  # type: ignore[assignment]
    Gauge = _Noop  # type: ignore[assignment]

    class _Registry:
        def collect(self) -> list[Any]:
            return []

    _REGISTRY = _Registry()  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Tool-call counters
# ---------------------------------------------------------------------------

tool_calls_total: Counter = (
    Counter("hpc_tool_calls_total", "Total tool invocations", ["tool", "status"], registry=_REGISTRY)
    if _HAS_PROMETHEUS
    else Counter()
)
"""Labels: tool=<name>, status=<ok|denied|error>"""

tool_call_duration_seconds: Histogram = (
    Histogram(
        "hpc_tool_call_duration_seconds",
        "Tool call duration in seconds",
        ["tool"],
        buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
        registry=_REGISTRY,
    )
    if _HAS_PROMETHEUS
    else Histogram()
)
"""Labels: tool=<name>"""

denials_total: Counter = (
    Counter("hpc_denials_total", "Total permission denials", ["tool"], registry=_REGISTRY)
    if _HAS_PROMETHEUS
    else Counter()
)
"""Labels: tool=<name>"""

sink_errors_total: Counter = (
    Counter("hpc_sink_errors_total", "Total audit sink write errors", ["sink_type"], registry=_REGISTRY)
    if _HAS_PROMETHEUS
    else Counter()
)
"""Labels: sink_type=<file|syslog|http>"""

clusters_total: Gauge = (
    Gauge("hpc_clusters_total", "Number of configured clusters", registry=_REGISTRY)
    if _HAS_PROMETHEUS
    else Gauge()
)

active_gateway_sessions: Gauge = (
    Gauge("hpc_active_gateway_sessions", "Number of active gateway sessions", ["platform"], registry=_REGISTRY)
    if _HAS_PROMETHEUS
    else Gauge()
)
"""Labels: platform=<telegram|discord>"""

REGISTRY = _REGISTRY
