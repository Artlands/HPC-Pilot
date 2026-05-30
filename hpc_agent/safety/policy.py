"""Declarative policy engine. See spec 01 §4.

Rules are YAML, evaluated top-down. Each rule may match on tool/domain/risk/op, assert
conditions on the tool input or diff, and produce an effect (auto / require_approval /
deny).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml


class Effect(StrEnum):
    AUTO = "auto"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass
class PolicyDecision:
    effect: Effect | None = None  # None => no opinion (fall through to risk tier)
    message: str | None = None
    rule_id: str | None = None


@dataclass
class PolicyRule:
    id: str
    match: dict[str, Any] = field(default_factory=dict)
    assert_: dict[str, Any] = field(default_factory=dict)
    effect: Effect | None = None
    on_violation: Effect | None = None
    message: str | None = None


_COMPARATORS = {
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b,
    "<": lambda a, b: a < b,
    ">": lambda a, b: a > b,
}


def _parse_tres(tres: str | None) -> dict[str, float]:
    """'cpu=128,gres/gpu=8' -> {'cpu':128,'gpu':8}."""
    out: dict[str, float] = {}
    if not tres:
        return out
    for part in tres.split(","):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        key = k.split("/")[-1].strip()  # gres/gpu -> gpu
        try:
            out[key] = float(v)
        except ValueError:
            continue
    return out


def _resolve_path(ctx: dict[str, Any], dotted: str) -> Any:
    """Resolve a dotted path against the input dict, with TRES-aware indexing.

    e.g. 'max_tres.gpu' parses the max_tres TRES string then indexes 'gpu'.
    """
    parts = dotted.split(".")
    cur: Any = ctx
    for i, p in enumerate(parts):
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        elif isinstance(cur, str) and i == len(parts) - 1:
            # treat the string as TRES and index the last component
            return _parse_tres(cur).get(p)
        else:
            return None
    return cur


def _check_assert(ctx: dict[str, Any], assertions: dict[str, Any]) -> bool:
    """Return True if all assertions hold."""
    for path, cond in assertions.items():
        value = _resolve_path(ctx, path)
        if value is None:
            continue  # field not present in this call -> assertion not applicable
        if not isinstance(cond, dict):
            cond = {"==": cond}
        for op, expected in cond.items():
            if op == "regex":
                if not re.search(str(expected), str(value)):
                    return False
            elif op == "in":
                if value not in expected:
                    return False
            elif op in _COMPARATORS and not _COMPARATORS[op](value, expected):
                return False
    return True


def _match_rule(rule: PolicyRule, *, tool: str, domain: str, risk: str, op: str | None) -> bool:
    m = rule.match
    if "tool" in m and m["tool"] != tool:
        return False
    if "domain" in m and m["domain"] != domain:
        return False
    if "risk" in m:
        allowed = m["risk"] if isinstance(m["risk"], list) else [m["risk"]]
        if risk not in allowed:
            return False
    return not ("op" in m and m["op"] != op)


class PolicyEngine:
    def __init__(self, rules: list[PolicyRule]) -> None:
        self.rules = rules

    @classmethod
    def from_dir(cls, policy_dir: str | Path) -> PolicyEngine:
        rules: list[PolicyRule] = []
        p = Path(policy_dir)
        for yml in sorted(p.glob("*.yaml")):
            data = yaml.safe_load(yml.read_text()) or []
            for raw in data:
                rules.append(
                    PolicyRule(
                        id=raw["id"],
                        match=raw.get("match", {}),
                        assert_=raw.get("assert", {}),
                        effect=Effect(raw["effect"]) if raw.get("effect") else None,
                        on_violation=(
                            Effect(raw["on_violation"]) if raw.get("on_violation") else None
                        ),
                        message=raw.get("message"),
                    )
                )
        return cls(rules)

    def evaluate(
        self,
        *,
        tool: str,
        domain: str,
        risk: str,
        op: str | None,
        input_ctx: dict[str, Any],
    ) -> PolicyDecision:
        """Return the first decisive PolicyDecision, or an empty one (fall through)."""
        for rule in self.rules:
            if not _match_rule(rule, tool=tool, domain=domain, risk=risk, op=op):
                continue
            holds = _check_assert(input_ctx, rule.assert_) if rule.assert_ else True
            if rule.assert_:
                if holds and rule.effect is not None:
                    return PolicyDecision(rule.effect, rule.message, rule.id)
                if not holds and rule.on_violation is not None:
                    return PolicyDecision(rule.on_violation, rule.message, rule.id)
            elif rule.effect is not None:
                return PolicyDecision(rule.effect, rule.message, rule.id)
        return PolicyDecision()
