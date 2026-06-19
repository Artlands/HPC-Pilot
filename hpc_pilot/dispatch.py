"""Centralized tool invocation: RBAC check → audit → dispatch → result string."""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any, cast

from hpc_pilot.audit import AuditEvent, audit_tool, log_audit
from hpc_pilot.rbac import Role, check_permission


def invoke(
    name: str,
    args: dict[str, Any],
    *,
    role: Role,
    actor: str,
    dry_run: bool = False,
) -> str:
    """RBAC-check, audit-log, and execute one HPC tool call.

    Permission denials are audited with returncode=126 before re-raising.
    """
    try:
        check_permission(name, role)
    except PermissionError as exc:
        log_audit(AuditEvent(
            tool=name,
            actor=actor,
            role=role.value,
            args=args,
            dry_run=dry_run,
            ts=time.time(),
            returncode=126,
            error=f"permission_denied: {exc}",
        ))
        raise

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
# Dispatch registry — maps tool name → callable(args, tools) → str
# ---------------------------------------------------------------------------

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


_DISPATCH: dict[str, Callable[[dict[str, Any], Any], str]] = {
    # ---- Slurm query ----
    "hpc_slurm_node_status": lambda args, t: t.hpc_slurm_node_status(
        args.get("node", ""), cluster=_cl(args)
    ),
    "hpc_slurm_queue": lambda args, t: t.hpc_slurm_queue(
        {k: v for k, v in args.items() if k in ("user", "partition", "state") and v} or None,
        cluster=_cl(args),
    ),
    "hpc_slurm_job_status": lambda args, t: t.hpc_slurm_job_status(
        args["job_id"], cluster=_cl(args)
    ),
    "hpc_slurm_reservation_list": lambda args, t: t.hpc_slurm_reservation_list(
        cluster=_cl(args)
    ),
    "hpc_slurm_partition_list": lambda args, t: t.hpc_slurm_partition_list(
        cluster=_cl(args)
    ),
    "hpc_slurm_account_list": lambda args, t: t.hpc_slurm_account_list(
        cluster=_cl(args)
    ),
    "hpc_slurm_association_list": lambda args, t: t.hpc_slurm_association_list(
        args.get("account", ""),
        args.get("user", ""),
        cluster=_cl(args),
    ),
    "hpc_slurm_qos_list": lambda args, t: t.hpc_slurm_qos_list(cluster=_cl(args)),
    "hpc_slurm_fairshare": lambda args, t: t.hpc_slurm_fairshare(cluster=_cl(args)),
    "hpc_slurm_accounting": lambda args, t: t.hpc_slurm_accounting(
        args.get("user", ""),
        args.get("account", ""),
        args.get("start", ""),
        args.get("end", ""),
        args.get("state", ""),
        cluster=_cl(args),
    ),
    "hpc_slurm_usage_report": lambda args, t: t.hpc_slurm_usage_report(
        args.get("report_type", "cluster"),
        args.get("start", ""),
        args.get("end", ""),
        cluster=_cl(args),
    ),
    "hpc_slurm_sdiag": lambda args, t: json.dumps(
        t.hpc_slurm_sdiag(cluster=_cl(args)), indent=2, default=str
    ),
    "hpc_slurm_config_show": lambda args, t: t.hpc_slurm_config_show(cluster=_cl(args)),
    # ---- Slurm mutation (operator) ----
    "hpc_slurm_node_state": lambda args, t: t.hpc_slurm_node_state(
        args["node"],
        args["target"],
        args.get("reason") or None,
        _dr(args),
        cluster=_cl(args),
    ),
    "hpc_slurm_job_hold": lambda args, t: t.hpc_slurm_job_hold(
        args["job_id"], cluster=_cl(args), dry_run=_dr(args)
    ),
    "hpc_slurm_job_release": lambda args, t: t.hpc_slurm_job_release(
        args["job_id"], cluster=_cl(args), dry_run=_dr(args)
    ),
    "hpc_slurm_job_requeue": lambda args, t: t.hpc_slurm_job_requeue(
        args["job_id"], cluster=_cl(args), dry_run=_dr(args)
    ),
    # hpc_slurm_job_cancel is handled specially in invoke() via _dispatch_job_cancel
    # ---- Slurm mutation (admin) ----
    "hpc_slurm_qos_modify": lambda args, t: t.hpc_slurm_qos_modify(
        args["name"],
        max_wall_min=args.get("max_wall_min"),
        grp_tres=args.get("grp_tres"),
        max_tres_per_user=args.get("max_tres_per_user"),
        dry_run=_dr(args, default=True),
        cluster=_cl(args),
    ),
    "hpc_slurm_qos_create": lambda args, t: t.hpc_slurm_qos_create(
        args["name"],
        max_wall_min=args.get("max_wall_min"),
        grp_tres=args.get("grp_tres"),
        max_tres_per_user=args.get("max_tres_per_user"),
        cluster=_cl(args),
        dry_run=_dr(args),
    ),
    "hpc_slurm_reservation_create": lambda args, t: t.hpc_slurm_reservation_create(
        args["name"],
        args["nodes"],
        args["start"],
        args["duration"],
        args.get("users", ""),
        args.get("accounts", ""),
        args.get("flags", ""),
        cluster=_cl(args),
        dry_run=_dr(args),
    ),
    "hpc_slurm_reservation_update": lambda args, t: t.hpc_slurm_reservation_update(
        args["name"],
        args.get("nodes", ""),
        args.get("start", ""),
        args.get("duration", ""),
        args.get("users", ""),
        args.get("flags", ""),
        cluster=_cl(args),
        dry_run=_dr(args),
    ),
    "hpc_slurm_reservation_delete": lambda args, t: t.hpc_slurm_reservation_delete(
        args["name"], cluster=_cl(args), dry_run=_dr(args)
    ),
    "hpc_slurm_partition_update": lambda args, t: t.hpc_slurm_partition_update(
        args["name"],
        args.get("state", ""),
        args.get("max_time", ""),
        cluster=_cl(args),
        dry_run=_dr(args),
    ),
    # ---- Slurm mutation (superadmin) ----
    "hpc_slurm_account_create": lambda args, t: t.hpc_slurm_account_create(
        args["name"],
        args.get("description", ""),
        args.get("organization", ""),
        cluster=_cl(args),
        dry_run=_dr(args),
    ),
    "hpc_slurm_association_create": lambda args, t: t.hpc_slurm_association_create(
        args["user"],
        args["account"],
        cluster=_cl(args),
        dry_run=_dr(args),
    ),
    "hpc_slurm_reconfigure": lambda args, t: t.hpc_slurm_reconfigure(
        cluster=_cl(args), dry_run=_dr(args)
    ),
    # ---- Warewulf ----
    "hpc_warewulf_node_status": lambda args, t: t.hpc_warewulf_node_status(
        cluster=_cl(args)
    ),
    "hpc_warewulf_image_list": lambda args, t: t.hpc_warewulf_image_list(
        cluster=_cl(args)
    ),
    "hpc_warewulf_power_reset": lambda args, t: t.hpc_warewulf_power_reset(
        args["node"],
        bool(args.get("dry_run", True)),
        cluster=_cl(args),
    ),
    "hpc_warewulf_image_import": lambda args, t: t.hpc_warewulf_image_import(
        args["name"], args["source"], cluster=_cl(args), dry_run=_dr(args)
    ),
    "hpc_warewulf_image_build": lambda args, t: json.dumps(
        t.hpc_warewulf_image_build(
            args["name"], args.get("base", ""),
            exec_steps=args.get("exec_steps"),
            gpu=bool(args.get("gpu", False)),
            cluster=_cl(args), dry_run=_dr(args),
        ), indent=2, default=str,
    ),
    "hpc_warewulf_image_delete": lambda args, t: t.hpc_warewulf_image_delete(
        args["name"], cluster=_cl(args), dry_run=_dr(args)
    ),
    "hpc_warewulf_node_show": lambda args, t: t.hpc_warewulf_node_show(
        args["name"], cluster=_cl(args)
    ),
    "hpc_warewulf_node_add": lambda args, t: t.hpc_warewulf_node_add(
        args["name"], args["mac"], args["ipaddr"],
        profile=args.get("profile"), cluster=_cl(args), dry_run=_dr(args)
    ),
    "hpc_warewulf_node_add_bulk": lambda args, t: t.hpc_warewulf_node_add_bulk(
        args["nodes"], cluster=_cl(args), dry_run=_dr(args)
    ),
    "hpc_warewulf_node_set": lambda args, t: t.hpc_warewulf_node_set(
        args["name"],
        **{k: v for k, v in args.items() if k not in ("name", "cluster", "dry_run")},
        cluster=_cl(args), dry_run=_dr(args),
    ),
    "hpc_warewulf_node_delete": lambda args, t: t.hpc_warewulf_node_delete(
        args["name"], cluster=_cl(args), dry_run=_dr(args)
    ),
    "hpc_warewulf_profile_list": lambda args, t: t.hpc_warewulf_profile_list(
        cluster=_cl(args)
    ),
    "hpc_warewulf_profile_set": lambda args, t: t.hpc_warewulf_profile_set(
        args["name"],
        **{k: v for k, v in args.items() if k not in ("name", "cluster", "dry_run")},
        cluster=_cl(args), dry_run=_dr(args),
    ),
    "hpc_warewulf_overlay_list": lambda args, t: t.hpc_warewulf_overlay_list(
        cluster=_cl(args)
    ),
    "hpc_warewulf_overlay_edit": lambda args, t: json.dumps(
        t.hpc_warewulf_overlay_edit(
            args["overlay"], args["path"], args["content"],
            cluster=_cl(args), dry_run=_dr(args),
        ), indent=2, default=str,
    ),
    "hpc_warewulf_overlay_build": lambda args, t: t.hpc_warewulf_overlay_build(
        args["overlay"], cluster=_cl(args), dry_run=_dr(args)
    ),
    "hpc_warewulf_overlay_revert": lambda args, t: json.dumps(
        t.hpc_warewulf_overlay_revert(
            args["overlay"], commit=args.get("commit", "HEAD"),
            cluster=_cl(args), dry_run=_dr(args),
        ), indent=2, default=str,
    ),
    "hpc_warewulf_configure_dhcp": lambda args, t: json.dumps(
        t.hpc_warewulf_configure_dhcp(
            range_start=args.get("range_start"),
            range_end=args.get("range_end"),
            template=args.get("template"),
            cluster=_cl(args), dry_run=_dr(args),
        ), indent=2, default=str,
    ),
    "hpc_warewulf_configure_tftp": lambda args, t: json.dumps(
        t.hpc_warewulf_configure_tftp(cluster=_cl(args), dry_run=_dr(args)),
        indent=2, default=str,
    ),
    "hpc_warewulf_configure_nfs": lambda args, t: json.dumps(
        t.hpc_warewulf_configure_nfs(cluster=_cl(args), dry_run=_dr(args)),
        indent=2, default=str,
    ),
    "hpc_warewulf_server_status": lambda args, t: json.dumps(
        t.hpc_warewulf_server_status(cluster=_cl(args)),
        indent=2, default=str,
    ),
    "hpc_warewulf_power_status": lambda args, t: t.hpc_warewulf_power_status(
        args["node"], cluster=_cl(args)
    ),
    "hpc_warewulf_power_on": lambda args, t: t.hpc_warewulf_power_on(
        args["node"], cluster=_cl(args), dry_run=_dr(args)
    ),
    "hpc_warewulf_power_off": lambda args, t: t.hpc_warewulf_power_off(
        args["node"], cluster=_cl(args), dry_run=_dr(args)
    ),
    # ---- Spack ----
    "hpc_spack_env_list": lambda args, t: t.hpc_spack_env_list(cluster=_cl(args)),
    "hpc_spack_find": lambda args, t: t.hpc_spack_find(
        args["env"], cluster=_cl(args)
    ),
    "hpc_spack_compilers": lambda args, t: t.hpc_spack_compilers(cluster=_cl(args)),
    "hpc_spack_env_create": lambda args, t: t.hpc_spack_env_create(
        args["name"], manifest=args.get("manifest"), cluster=_cl(args), dry_run=_dr(args)
    ),
    "hpc_spack_env_delete": lambda args, t: t.hpc_spack_env_delete(
        args["name"], cluster=_cl(args), dry_run=_dr(args)
    ),
    "hpc_spack_env_concretize": lambda args, t: json.dumps(
        t.hpc_spack_env_concretize(args["env"], cluster=_cl(args), dry_run=_dr(args)),
        indent=2, default=str,
    ),
    "hpc_spack_env_install": lambda args, t: json.dumps(
        t.hpc_spack_env_install(args["env"], cluster=_cl(args), dry_run=_dr(args)),
        indent=2, default=str,
    ),
    "hpc_spack_env_status": lambda args, t: json.dumps(
        t.hpc_spack_env_status(args["env"], cluster=_cl(args)),
        indent=2, default=str,
    ),
    "hpc_spack_install_spec": lambda args, t: t.hpc_spack_install_spec(
        args["spec"], cluster=_cl(args), dry_run=_dr(args)
    ),
    "hpc_spack_uninstall": lambda args, t: t.hpc_spack_uninstall(
        args["spec"],
        dependents=bool(args.get("dependents", False)),
        cluster=_cl(args), dry_run=_dr(args, default=True),
    ),
    "hpc_spack_mirror_list": lambda args, t: t.hpc_spack_mirror_list(cluster=_cl(args)),
    "hpc_spack_mirror_add": lambda args, t: t.hpc_spack_mirror_add(
        args["name"], args["url"], cluster=_cl(args), dry_run=_dr(args)
    ),
    "hpc_spack_buildcache_push": lambda args, t: t.hpc_spack_buildcache_push(
        args["mirror_name"],
        spec=args.get("spec"),
        gpg_key=args.get("gpg_key"),
        cluster=_cl(args), dry_run=_dr(args),
    ),
    "hpc_spack_buildcache_update_index": lambda args, t: t.hpc_spack_buildcache_update_index(
        args["mirror_name"], cluster=_cl(args), dry_run=_dr(args)
    ),
    "hpc_spack_module_refresh": lambda args, t: t.hpc_spack_module_refresh(
        cluster=_cl(args), dry_run=_dr(args)
    ),
    "hpc_spack_compiler_find": lambda args, t: t.hpc_spack_compiler_find(
        cluster=_cl(args), dry_run=_dr(args)
    ),
    # Job management (Phase 3)
    "hpc_job_status": lambda args, t: json.dumps(
        t.hpc_job_status(args["run_id"]), indent=2, default=str
    ),
    "hpc_job_logs": lambda args, t: t.hpc_job_logs(
        args["run_id"], tail=int(args.get("tail", 200))
    ),
    # ---- Ansible ----
    "hpc_ansible_playbook_run": lambda args, t: json.dumps(
        t.hpc_ansible_playbook_run(
            args["playbook"],
            args.get("limit") or None,
            bool(args.get("check", False)),
            _dr(args, default=True),
            cluster=_cl(args),
        ),
        indent=2,
        default=str,
    ),
    "hpc_ansible_inventory_generate": lambda args, t: t.hpc_ansible_inventory_generate(
        cluster=_cl(args)
    ),
    "hpc_ansible_playbook_check": lambda args, t: json.dumps(
        t.hpc_ansible_playbook_check(
            args["playbook"],
            args.get("limit") or None,
            cluster=_cl(args),
            dry_run=_dr(args, default=True),
        ),
        indent=2,
        default=str,
    ),
    "hpc_ansible_playbook_list": lambda args, t: json.dumps(
        t.hpc_ansible_playbook_list(cluster=_cl(args)),
        indent=2,
        default=str,
    ),
    "hpc_ansible_role_list": lambda args, t: json.dumps(
        t.hpc_ansible_role_list(cluster=_cl(args)),
        indent=2,
        default=str,
    ),
    "hpc_ansible_inventory_from_truth": lambda args, t: json.dumps(
        t.hpc_ansible_inventory_from_truth(cluster=_cl(args)),
        indent=2,
        default=str,
    ),
    "hpc_ansible_drift_check": lambda args, t: json.dumps(
        t.hpc_ansible_drift_check(
            args.get("which", "all"),
            cluster=_cl(args),
        ),
        indent=2,
        default=str,
    ),
    "hpc_ansible_vault_decrypt": lambda args, t: t.hpc_ansible_vault_decrypt(
        args["path"],
        cluster=_cl(args),
        dry_run=_dr(args, default=True),
    ),
    "hpc_ansible_run_history": lambda args, t: json.dumps(
        t.hpc_ansible_run_history(cluster=_cl(args)),
        indent=2,
        default=str,
    ),
    # ---- Health ----
    "hpc_cluster_health_check": lambda args, t: json.dumps(
        t.hpc_cluster_health_check(cluster=_cl(args)),
        indent=2,
        default=str,
    ),
    # ---- Metrics / Observability ----
    "hpc_metrics_prometheus_query": lambda args, t: json.dumps(
        t.hpc_metrics_prometheus_query(
            args["query"],
            start=args.get("start"),
            end=args.get("end"),
            step=args.get("step"),
            cluster=_cl(args),
        ),
        indent=2, default=str,
    ),
    "hpc_metrics_prometheus_alerts": lambda args, t: json.dumps(
        t.hpc_metrics_prometheus_alerts(cluster=_cl(args)),
        indent=2, default=str,
    ),
    "hpc_metrics_node_summary": lambda args, t: json.dumps(
        t.hpc_metrics_node_summary(
            args["node"], cluster=_cl(args),
        ),
        indent=2, default=str,
    ),
    "hpc_gpu_nvidia_smi": lambda args, t: json.dumps(
        t.hpc_gpu_nvidia_smi(args["node"], cluster=_cl(args)),
        indent=2, default=str,
    ),
    "hpc_gpu_dcgm_diag": lambda args, t: t.hpc_gpu_dcgm_diag(
        args["node"], cluster=_cl(args), dry_run=_dr(args),
    ),
    "hpc_storage_lustre_status": lambda args, t: json.dumps(
        t.hpc_storage_lustre_status(cluster=_cl(args)),
        indent=2, default=str,
    ),
    "hpc_storage_mounts": lambda args, t: json.dumps(
        t.hpc_storage_mounts(cluster=_cl(args)),
        indent=2, default=str,
    ),
    "hpc_fabric_ib_link_status": lambda args, t: json.dumps(
        t.hpc_fabric_ib_link_status(args["node"], cluster=_cl(args)),
        indent=2, default=str,
    ),
    "hpc_logs_slurmctld_tail": lambda args, t: t.hpc_logs_slurmctld_tail(
        lines=int(args.get("lines", 50)),
        grep=args.get("grep"),
        cluster=_cl(args),
    ),
    "hpc_logs_slurmd_tail": lambda args, t: t.hpc_logs_slurmd_tail(
        args["node"],
        lines=int(args.get("lines", 50)),
        cluster=_cl(args),
    ),
    "hpc_logs_dmesg_xid": lambda args, t: json.dumps(
        t.hpc_logs_dmesg_xid(args["node"], cluster=_cl(args)),
        indent=2, default=str,
    ),
    "hpc_logs_search": lambda args, t: t.hpc_logs_search(
        args["pattern"],
        since=args.get("since", "24h ago"),
        cluster=_cl(args),
    ),
    # ---- Phase 7: Multi-cluster federation ----
    "hpc_multi_query": lambda args, t: json.dumps(
        t.hpc_multi_query(
            args["tool"],
            args.get("args", {}),
            args["clusters"],
            dry_run=bool(args.get("dry_run", False)),
        ),
        indent=2,
        default=str,
    ),
    # ---- System & admin tools ----
    "hpc_audit_query": lambda args, t: t.hpc_audit_query(
        tool=args.get("tool", ""),
        actor=args.get("actor", ""),
        role=args.get("role", ""),
        error_only=bool(args.get("error_only", False)),
        since_ts=args.get("since_ts"),
        limit=int(args.get("limit", 50)),
        cluster=_cl(args),
    ),
    "hpc_slurm_service": lambda args, t: t.hpc_slurm_service(
        args["service"],
        args["action"],
        cluster=_cl(args),
        dry_run=_dr(args),
    ),
    "hpc_system_user_add": lambda args, t: t.hpc_system_user_add(
        args["username"],
        uid=args.get("uid"),
        groups=args.get("groups", ""),
        shell=args.get("shell", "/bin/bash"),
        home=args.get("home", ""),
        cluster=_cl(args),
        dry_run=_dr(args),
    ),
    "hpc_system_user_delete": lambda args, t: t.hpc_system_user_delete(
        args["username"],
        remove_home=bool(args.get("remove_home", False)),
        cluster=_cl(args),
        dry_run=_dr(args),
    ),
    "hpc_system_user_group_add": lambda args, t: t.hpc_system_user_group_add(
        args["username"],
        args["group"],
        cluster=_cl(args),
        dry_run=_dr(args),
    ),
    "hpc_system_ssh_key_deploy": lambda args, t: t.hpc_system_ssh_key_deploy(
        args["username"],
        args["public_key"],
        cluster=_cl(args),
        dry_run=_dr(args),
    ),
    "hpc_login_node_processes": lambda args, t: t.hpc_login_node_processes(
        sort_by=args.get("sort_by", "cpu"),
        limit=int(args.get("limit", 20)),
        cluster=_cl(args),
    ),
    "hpc_storage_large_files": lambda args, t: t.hpc_storage_large_files(
        args["path"],
        min_size_mb=int(args.get("min_size_mb", 100)),
        limit=int(args.get("limit", 50)),
        cluster=_cl(args),
    ),
    "hpc_storage_quota_check": lambda args, t: t.hpc_storage_quota_check(
        cluster=_cl(args),
    ),
    "hpc_config_backup": lambda args, t: t.hpc_config_backup(
        cluster=_cl(args),
        dry_run=_dr(args),
    ),
    # Additional system tools (Phase 3.6)
    "hpc_usage_vs_budget": lambda args, t: t.hpc_usage_vs_budget(
        args["account"],
        args["qos_name"],
        start=args.get("start", ""),
        end=args.get("end", ""),
        cluster=_cl(args),
    ),
    "hpc_notify": lambda args, t: t.hpc_notify(
        args["message"],
        platform=args.get("platform", "telegram"),
        target=args.get("target", ""),
        cluster=_cl(args),
        dry_run=_dr(args),
    ),
    "hpc_warewulf_image_build_from_env": lambda args, t: t.hpc_warewulf_image_build_from_env(
        args["name"],
        base=args.get("base", "rockylinux:9"),
        spack_env=args.get("spack_env", ""),
        exec_steps=args.get("exec_steps"),
        gpu=bool(args.get("gpu", False)),
        cluster=_cl(args),
        dry_run=_dr(args),
    ),
    "hpc_job_submit_test": lambda args, t: t.hpc_job_submit_test(
        partition=args.get("partition", ""),
        num_nodes=int(args.get("num_nodes", 1)),
        ntasks=int(args.get("ntasks", 1)),
        cluster=_cl(args),
        dry_run=_dr(args),
    ),
    "hpc_storage_lustre_balance": lambda args, t: t.hpc_storage_lustre_balance(
        fs_name=args.get("fs_name", "/scratch"),
        min_migrate_size_mb=int(args.get("min_migrate_size_mb", 10240)),
        cluster=_cl(args),
        dry_run=_dr(args),
    ),
    "hpc_storage_scrub_orphans": lambda args, t: t.hpc_storage_scrub_orphans(
        work_dir=args.get("work_dir", "/scratch"),
        max_age_days=int(args.get("max_age_days", 30)),
        cluster=_cl(args),
        dry_run=_dr(args),
    ),
    "hpc_slurm_job_step_metrics": lambda args, t: t.hpc_slurm_job_step_metrics(
        args["job_id"],
        cluster=_cl(args),
    ),
    "hpc_multi_migration_plan": lambda args, t: t.hpc_multi_migration_plan(
        args["source_cluster"],
        args["target_cluster"],
        dry_run=_dr(args, default=True),
    ),
}


def _dispatch_job_cancel(
    args: dict[str, Any], tools: Any, role: Role, actor: str
) -> str:
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
    handler = _DISPATCH.get(name)
    if handler is None:
        return f"[unknown tool: {name}]"
    return handler(args, tools)
