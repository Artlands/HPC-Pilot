#!/usr/bin/env python3
"""Check that hpc_pilot/tools/ modules don't call subprocess.run directly.

Exceptions:
  - _run.py — the single canonical subprocess runner
  - evolve.py — self-evolve codegen (isolated, tested separately)
  - __init__.py — re-exports for test patching only

Usage:
    python scripts/check_no_direct_subprocess.py
    exit 0 if clean, 1 with filename + line info on violations.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent.parent / "hpc_pilot" / "tools"

ALLOWED = {"_run.py", "evolve.py", "__init__.py"}


def _check_file(path: Path) -> list[str]:
    """Return list of violation messages for *path*, or empty list."""
    if path.name in ALLOWED:
        return []

    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:
        return [f"{path}: (syntax error — cannot parse)"]

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                if func.value.id == "subprocess" and func.attr in {
                    "run",
                    "Popen",
                    "call",
                    "check_call",
                    "check_output",
                }:
                    violations.append(
                        f"{path}:{node.lineno}: "
                        f"direct subprocess.{func.attr}() call — "
                        f"use _run() from hpc_pilot/tools/_run.py instead"
                    )
    return violations


def main() -> int:
    all_violations: list[str] = []

    for py_file in sorted(TOOLS_DIR.rglob("*.py")):
        all_violations.extend(_check_file(py_file))

    if all_violations:
        print(f"ERROR: {len(all_violations)} direct subprocess call(s) found:\n")
        for v in all_violations:
            print(f"  {v}")
        return 1

    print("OK: No direct subprocess calls in hpc_pilot/tools/.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
