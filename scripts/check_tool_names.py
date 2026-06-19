#!/usr/bin/env python3
"""Lint HPC Pilot tool names against the hpc_<subsystem>_<noun>_<verb?> convention.

Usage:
    python scripts/check_tool_names.py          # check hpc_pilot/tools/
    python scripts/check_tool_names.py --fix    # print violations only (no auto-fix)

Exit code 0 = clean, 1 = violations found.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

VALID_SUBSYSTEMS = {
    "slurm",
    "warewulf",
    "spack",
    "ansible",
    "cluster",
    "skill",
    "multi",
    "gpu",
    "storage",
    "fabric",
    "logs",
    "metrics",
    "job",
}

_NAME_PATTERN_MIN_PARTS = 3  # hpc + subsystem + noun = 3 parts minimum


def check_file(path: Path) -> list[str]:
    violations: list[str] = []
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError as exc:
        violations.append(f"{path}: SyntaxError: {exc}")
        return violations

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        name: str = node.name
        if not name.startswith("hpc_"):
            continue
        parts = name.split("_")
        # Must be at least: hpc + subsystem + noun
        if len(parts) < _NAME_PATTERN_MIN_PARTS:
            violations.append(
                f"{path}:{node.lineno}: '{name}' too short"
                " (expected hpc_<subsystem>_<noun>[_<verb>])"
            )
            continue
        subsystem = parts[1]
        if subsystem not in VALID_SUBSYSTEMS:
            violations.append(
                f"{path}:{node.lineno}: '{name}' has unknown subsystem '{subsystem}' "
                f"(valid: {sorted(VALID_SUBSYSTEMS)})"
            )
    return violations


def main() -> int:
    root = Path(__file__).parent.parent / "hpc_pilot"
    all_violations: list[str] = []

    for py_file in sorted(root.rglob("*.py")):
        if py_file.name.startswith("_") and py_file.name != "__init__.py":
            continue  # skip private helpers like _run.py, _validation.py
        all_violations.extend(check_file(py_file))

    if all_violations:
        print("Tool name violations found:", file=sys.stderr)
        for v in all_violations:
            print(f"  {v}", file=sys.stderr)
        return 1

    print(f"Tool name check passed ({sum(1 for _ in root.rglob('*.py'))} files scanned).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
