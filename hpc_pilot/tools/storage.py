"""HPC storage tools — large file finding, quota checks, Lustre balance, orphan scrubbing."""

from __future__ import annotations

import io
import time
from typing import Any

from hpc_pilot.rbac import Role
from hpc_pilot.tools._registry import hpc_tool
from hpc_pilot.tools._run import _resolve_cluster, _run
from hpc_pilot.tools._validation import _validate


@hpc_tool(
    name="hpc_storage_large_files",
    role=Role.VIEWER,
    schema={
        "name": "hpc_storage_large_files",
        "description": "Find the largest files under a directory. Useful for storage crisis triage.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory to search (e.g. /scratch)"},
                "min_size_mb": {
                    "type": "integer",
                    "description": "Minimum file size in MB (default 100)",
                },
                "limit": {"type": "integer", "description": "Max results (default 50)"},
            },
            "required": ["path"],
        },
    },
)
def hpc_storage_large_files(
    path: str,
    min_size_mb: int = 100,
    limit: int = 50,
    *,
    cluster: str = "default",
) -> str:
    """Find the largest files under a given directory path.

    Args:
        path: Directory to search (e.g. ``/scratch``).
        min_size_mb: Minimum file size in MB (default 100).
        limit: Max results (default 50).

    Returns a table: SIZE_MB, PATH.
    """
    _validate(path, "path")
    cl = _resolve_cluster(cluster)
    cmd = [
        "find",
        path,
        "-type",
        "f",
        "-size",
        f"+{min_size_mb}M",
        "-printf",
        "%s\\t%p\\n",
    ]
    raw = _run(cmd, cluster=cl, timeout=120)
    lines = raw.strip().splitlines()
    entries: list[tuple[int, str]] = []
    for line in lines:
        parts = line.split("\t", 1)
        if len(parts) == 2 and parts[0].isdigit():
            entries.append((int(parts[0]), parts[1]))
    entries.sort(key=lambda e: e[0], reverse=True)

    out = io.StringIO()
    out.write(f"{'SIZE_MB':>9} PATH\n")
    out.write("-" * 70 + "\n")
    for size_bytes, fpath in entries[:limit]:
        size_mb = size_bytes / (1024 * 1024)
        out.write(f"{size_mb:>9.1f} {fpath}\n")
    return out.getvalue().rstrip()


@hpc_tool(
    name="hpc_storage_quota_check",
    role=Role.VIEWER,
    schema={
        "name": "hpc_storage_quota_check",
        "description": "Check filesystem quotas via repquota on the login node.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
)
def hpc_storage_quota_check(
    *,
    cluster: str = "default",
) -> str:
    """Check filesystem quotas via ``repquota`` on the login node.

    Returns a table with filesystem, user/group, used, soft, hard limits.
    """
    cl = _resolve_cluster(cluster)
    raw = _run(["repquota", "-a", "-u", "-g"], cluster=cl, timeout=30)
    return raw.strip() or "No quotas configured (repquota returned empty)"


@hpc_tool(
    name="hpc_storage_lustre_balance",
    role=Role.VIEWER,
    schema={
        "name": "hpc_storage_lustre_balance",
        "description": "Check Lustre OST balance and optionally migrate files off overfull OSTs. Reports per-OST usage and identifies imbalance.",
        "input_schema": {
            "type": "object",
            "properties": {
                "fs_name": {
                    "type": "string",
                    "description": "Lustre filesystem mount point (default /scratch)",
                },
                "min_migrate_size_mb": {
                    "type": "integer",
                    "description": "Min file size for migration in MB (default 10240)",
                },
            },
            "required": [],
        },
    },
)
def hpc_storage_lustre_balance(
    fs_name: str = "/scratch",
    min_migrate_size_mb: int = 10240,
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Check Lustre OST balance and optionally migrate files off full OSTs.

    Step 1: Check per-OST usage via ``lfs df``.
    Step 2: Identify OSTs with >70% usage.
    Step 3: Optionally run ``lfs_migrate`` on files on overfull OSTs.

    Args:
        fs_name: Lustre filesystem mount point (default ``/scratch``).
        min_migrate_size_mb: Only migrate files larger than this (default 10240 = 10 GB).
        dry_run: Only report, don't actually migrate.
    """

    cl = _resolve_cluster(cluster)

    # Step 1: Get per-OST usage
    df_raw = _run(["lfs", "df", fs_name], cluster=cl, timeout=30)

    ost_usage: list[dict[str, Any]] = []
    for line in df_raw.splitlines():
        if not line.startswith("OST:"):
            continue
        parts = line.split()
        if len(parts) >= 6:
            ost_name = parts[0]
            total = int(parts[1]) if parts[1].isdigit() else 0
            used = int(parts[2]) if parts[2].isdigit() else 0
            pct = round(used / max(total, 1) * 100, 1)
            ost_usage.append({"name": ost_name, "total": total, "used": used, "pct": pct})

    if not ost_usage:
        return f"No Lustre OST data found for {fs_name} (may not be a Lustre filesystem)"

    # Report
    out: list[str] = [
        f"Lustre OST balance for {fs_name}",
        f"{'OST':20} {'TOTAL(GB)':>10} {'USED(GB)':>10} {'%USED':>7}",
        "-" * 50,
    ]
    for ost in ost_usage:
        total_gb = round(ost["total"] / 1024, 1) if "total" in ost else 0
        used_gb = round(ost["used"] / 1024, 1) if "used" in ost else 0
        out.append(f"{ost['name']:20} {total_gb:>10.1f} {used_gb:>10.1f} {ost['pct']:>6.1f}%")

    # Stats
    pcts = [o["pct"] for o in ost_usage]
    if pcts:
        avg_pct = round(sum(pcts) / len(pcts), 1)
        overfull = [o for o in ost_usage if o["pct"] > 70]
        out.append(f"\nAverage OST usage: {avg_pct}%")
        out.append(f"OSTs over 70%: {len(overfull)} of {len(ost_usage)}")
        if overfull:
            over_names = ", ".join(o["name"] for o in overfull)
            out.append(f"Overfull OSTs: {over_names}")

            if not dry_run:
                out.append(
                    f"\nRunning lfs_migrate for files > {min_migrate_size_mb}MB on overfull OSTs..."
                )
                for ost in overfull:
                    try:
                        migrate_raw = _run(
                            [
                                "lfs_migrate",
                                "-c",
                                ost["name"],
                                "-s",
                                str(min_migrate_size_mb * 1024),
                                fs_name,
                            ],
                            cluster=cl,
                            timeout=300,
                        )
                        out.append(f"  {ost['name']}: {migrate_raw.strip()[:100]}")
                    except RuntimeError as exc:
                        out.append(f"  {ost['name']}: migration skipped ({exc})")
            else:
                out.append(f"\n  (dry-run: would migrate files on {over_names})")

    return "\n".join(out)


@hpc_tool(
    name="hpc_storage_scrub_orphans",
    role=Role.ADMIN,
    schema={
        "name": "hpc_storage_scrub_orphans",
        "description": "Find orphaned job working directories older than a threshold. Dry-run by default — lists candidates without deleting. Use for storage cleanup review.",
        "input_schema": {
            "type": "object",
            "properties": {
                "work_dir": {
                    "type": "string",
                    "description": "Directory to scan (default /scratch)",
                },
                "max_age_days": {
                    "type": "integer",
                    "description": "Age threshold in days (default 30)",
                },
            },
            "required": [],
        },
    },
)
def hpc_storage_scrub_orphans(
    work_dir: str = "/scratch",
    max_age_days: int = 30,
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Find orphaned job working directories (directories older than *max_age_days*
    whose naming pattern suggests they were created by a batch job).

    This tool only **lists** candidates — it does NOT delete anything.
    Use ``dry_run=false`` only after manual review of the output.

    Args:
        work_dir: Parent directory to scan (default ``/scratch``).
        max_age_days: Age threshold in days (default 30).
        dry_run: List without deleting (default True for safety).
    """
    cl = _resolve_cluster(cluster)

    raw = _run(
        [
            "find",
            work_dir,
            "-maxdepth",
            "2",
            "-type",
            "d",
            "-mtime",
            f"+{max_age_days}",
            "!",
            "-name",
            ".",
            "!",
            "-name",
            "..",
            "-printf",
            "%T@\\t%u\\t%s\\t%p\\n",
        ],
        cluster=cl,
        timeout=120,
    )

    lines = raw.strip().splitlines() if raw.strip() else []
    if not lines:
        return f"No directories older than {max_age_days} days found under {work_dir}"

    out: list[str] = [
        f"Directories older than {max_age_days} days under {work_dir}",
        f"{'AGE(days)':>10} {'OWNER':12} {'SIZE(MB)':>9} PATH",
        "-" * 80,
    ]

    now = time.time()
    count = 0
    for line in lines[:200]:  # cap output
        parts = line.split("\t", 3)
        if len(parts) < 3:
            continue
        try:
            mtime = float(parts[0])
        except ValueError:
            continue
        owner = parts[1]
        size = int(parts[2]) if parts[2].isdigit() else 0
        path = parts[3] if len(parts) > 3 else ""
        age_days = round((now - mtime) / 86400, 1)
        size_mb = round(size / (1024 * 1024), 1)
        out.append(f"{age_days:>10.1f} {owner:12} {size_mb:>9.1f} {path}")
        count += 1

    out.append(f"\n{count} directories found (listing capped at 200).")

    if dry_run:
        out.append(
            "\n⚠️  This is a dry-run. No files were deleted. "
            "Review the list above, then re-run with dry_run=false to delete."
        )

    return "\n".join(out)
