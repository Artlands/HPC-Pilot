# Real-World HPC Operation & Management Scenarios

This document defines a series of real-world scenarios that an HPC operations team
encounters day-to-day. Each scenario is described in terms of:

- **Trigger**: What happened or what was requested.
- **Desired outcome**: The correct end state.
- **HPC-Pilot evaluation**: Whether the current tooling handles it, partially handles it,
  or cannot handle it, and what gaps exist.

---

## Scenario 1 — Onboard a New Research Group (CPU + GPU Allocation)

**Trigger**: A new research group ("astro-lab") has been approved for HPC access.
The PI (Principal Investigator) needs an allocation of 500,000 CPU-hours and
100,000 GPU-hours per quarter. Three group members need accounts.

**Desired outcome**:
1. Slurm accounting account `astro-lab` created with description and org.
2. A QOS entry `astro-lab-qos` with `GrpTRES=cpu=500000,gres/gpu=100000` limits.
3. Three user-account associations created (`alice`, `bob`, `carol` → `astro-lab`).
4. Each user gets a fairshare parent (so the PI can redistribute within group).
5. A notification to the PI with the allocation details and usage tracking info.

**HPC-Pilot evaluation**: MOSTLY COVERED (updated 2026-06-19)
- ✅ `hpc_slurm_account_create` — can create the account
- ✅ `hpc_slurm_association_create` — can add users
- ✅ `hpc_slurm_qos_create` — now accepts `GrpTRES` (e.g., ``"cpu=500000,gres/gpu=100000"``)
  and `MaxTRESPU` for per-user limits
- ✅ `hpc_slurm_qos_modify` — also accepts `GrpTRES` and `MaxTRESPU`
- ✅ `onboard-group` runbook — unified workflow that creates account + QOS with
  TRES limits + adds all users in one call (replaces 5+ manual tool calls)
- ✅ `hpc_usage_vs_budget` — compute used CPU/GPU hours vs QOS budget for any
  account, with percentage and warning at 90%
- ✅ `hpc_notify` — send notifications via Telegram or Discord gateway

---

## Scenario 2 — Investigate a DOWN Node and Restore It

**Trigger**: Node `gpu04` shows as `DOWN*` in `squeue`. Jobs are stuck in
`PD` (pending) waiting for that node. Researcher calls the help desk.

**Desired outcome**:
1. Check node state details (scontrol show node).
2. Check dmesg for hardware errors (XID, memory errors).
3. Check slurmctld log for the drain reason.
4. Decide: hardware issue → ticket to facilities; soft error → resume node.
5. Either resume the node or drain it permanently for maintenance.

**HPC-Pilot evaluation**: FULLY COVERED
- ✅ `hpc_slurm_node_status` — show node details
- ✅ `hpc_logs_dmesg_xid` — check for GPU XID errors
- ✅ `hpc_logs_slurmctld_tail` — check drain reason
- ✅ `hpc_slurm_node_state` — drain/resume
- ✅ `triage-node-down` runbook — orchestrates the whole flow

---

## Scenario 3 — Cluster-Wide Software Stack Update

**Trigger**: Users need a new software stack: Python 3.12 with PyTorch 2.5,
CUDA 12.4, and MPI support. Also upgrade the OS kernel across all compute nodes.

**Desired outcome**:
1. Create a new Spack environment `env-python312`.
2. Install Python 3.12, PyTorch 2.5, CUDA 12.4, OpenMPI in the environment.
3. Rebuild the Warewulf compute image with the new software.
4. Provision the new image to all compute nodes via rolling reboot.
5. Validate that the new stack works (run a test job).

**HPC-Pilot evaluation**: FULLY COVERED (updated 2026-06-19)
- ✅ `hpc_spack_env_create` — create environment
- ✅ `hpc_spack_env_concretize` / `hpc_spack_env_install` — install packages
- ✅ `hpc_warewulf_image_build_from_env` — build a Warewulf image with the
  Spack environment baked in (imports base, runs Spack setup, builds image)
- ✅ `rolling-reboot-partition` runbook — provision the new image via rolling reboot
- ✅ `hpc_job_submit_test` — submit a short validation test job to verify
  the new stack works on compute nodes

---

## Scenario 4 — Fairshare Imbalance Investigation

**Trigger**: A PI complains their group isn't getting fair queue priority.
They say another group is "using all the resources."

**Desired outcome**:
1. Check current fairshare values (`sshare -Pl`) across groups.
2. Check current queue to see who has running/pending jobs.
3. Check usage report (`sreport`) for the past month to compare allocations.
4. If imbalance is real, adjust QOS limits or fairshare factors accordingly.

**HPC-Pilot evaluation**: FULLY COVERED
- ✅ `hpc_slurm_fairshare` — `sshare` data
- ✅ `hpc_slurm_queue` — current queue
- ✅ `hpc_slurm_usage_report` — historical report
- ✅ `hpc_slurm_qos_modify` — adjust QOS if needed
- ✅ `parse_sshare` — structured fairshare data

---

## Scenario 5 — GPU XID Error Triage and Node Remediation

**Trigger**: `gpu07` has repeated GPU XID errors. Jobs on that node fail
with "GPU assertion failure."

**Desired outcome**:
1. Run `nvidia-smi` on the node to check GPU state.
2. Search `dmesg` for XID error codes.
3. If the XID is fatal (e.g., XID 48, 79, 109), drain the node and ticket
   the vendor. If transient (e.g., XID 13, 63), reset the GPU and resume.
4. Optionally run DCGM diagnostics for detailed health.

**HPC-Pilot evaluation**: FULLY COVERED
- ✅ `hpc_gpu_nvidia_smi` — remote GPU status
- ✅ `hpc_logs_dmesg_xid` — XID error search
- ✅ `hpc_slurm_node_state` — drain/resume
- ✅ `hpc_gpu_dcgm_diag` — deep GPU diagnostics
- ✅ `triage-gpu-xid` runbook — orchestrates the whole flow

---

## Scenario 6 — Provision Bare-Metal Nodes (New Cluster Growth)

**Trigger**: 8 new compute nodes arrived (hostnames `compute09`–`compute16`).
All have dual 100 Gbps NICs (ib0, ib1). Need to add them to the cluster.

**Desired outcome**:
1. Configure Warewulf DHCP/TFTP/NFS for PXE boot.
2. Import the base OS image (e.g., Rocky Linux 9).
3. Build the compute image with GPU drivers and MPI.
4. Configure the compute profile to use the image.
5. Add 8 nodes to Warewulf (hostname, MAC, IP, profile).
6. Build init overlay.
7. Power on nodes via IPMI; they PXE boot.
8. Verify all 8 nodes join Slurm successfully.

**HPC-Pilot evaluation**: MOSTLY COVERED (updated 2026-06-19)
- ✅ `hpc_warewulf_configure_dhcp` / `tftp` / `nfs` — server config
- ✅ `hpc_warewulf_image_import` / `_build` — image management
- ✅ `hpc_warewulf_profile_set` — profile config
- ✅ `hpc_warewulf_node_add_bulk` — add all 8 nodes in one call (replaces repeated single-add)
- ✅ `hpc_warewulf_overlay_build` — overlay build
- ✅ `hpc_warewulf_power_on` — power on via IPMI
- ✅ `provision-nodes` runbook — chains all steps into one composite workflow
- ❌ No "verify node joins Slurm" step — would need `hpc_slurm_node_status` polling
  after power-on to confirm Slurm registration

---

## Scenario 7 — Monitor and Respond to Cluster Health Incidents

**Trigger**: 2 AM on Saturday. Ops team page fires: `compute05` is unreachable,
Lustre OST `ost3` is degraded, and the login node is showing high load.

**Desired outcome**:
1. Check overall cluster health (`hpc_cluster_health_check`).
2. Pinpoint: `compute05` is DOWN, `ost3` is in "degraded" state, login node
   load > 50.
3. Check Lustre OST status for ost3.
4. Check disk mounts on compute05.
5. Escalate: If compute05 has mounts hanging, force-reboot; if OST is degraded,
   ticket storage team.

**HPC-Pilot evaluation**: MOSTLY COVERED (updated 2026-06-19)
- ✅ `hpc_cluster_health_check` — comprehensive health summary
- ✅ `hpc_storage_lustre_status` — Lustre OST/MDT health
- ✅ `hpc_storage_mounts` — mount status
- ✅ `hpc_logs_search` — journald search
- ✅ `health-incident-triage` runbook — chains health check, Lustre, mounts,
  and node check into one composite workflow
- ❌ No automated escalation or incident tracking (expected — external PagerDuty/etc.)

---

## Scenario 8 — User Request: Account Quota and Usage Inquiry

**Trigger**: User `alice` emails: "Can you tell me how many CPU/GPU hours
my group has used this quarter? I need to plan our next allocation renewal."

**Desired outcome**:
1. Look up `alice`'s Slurm association to find her account and QOS.
2. Query `sacct` for `alice`'s jobs this quarter.
3. Query fairshare (`sshare`) for priority position.
4. Generate a usage report via `sreport` for the account.
5. Respond with a clear summary: hours used, hours remaining, priority level.

**HPC-Pilot evaluation**: FULLY COVERED (updated 2026-06-19)
- ✅ `hpc_slurm_association_list` — find user's account/QOS
- ✅ `hpc_slurm_accounting` — job history
- ✅ `hpc_slurm_fairshare` — fairshare/priority
- ✅ `hpc_slurm_usage_report` — group usage reports
- ✅ `hpc_usage_vs_budget` — dedicated tool that queries `sacct` and
  `sacctmgr show qos` to compute used CPU/GPU hours vs budget allocation

---

## Scenario 9 — Partition Reconfiguration: Add a New GPU Partition

**Trigger**: 4 new A100 nodes need their own partition `gpu-a100` with
dedicated access for the "ai-ml" project. 24-hour max wall time.

**Desired outcome**:
1. Create the partition in Slurm.
2. Assign the 4 nodes to the partition.
3. Create a QOS `gpu-a100-qos` with 24h max wall and GPU limits.
4. Create a reservation for "ai-ml" project's dedicated time window.
5. Verify partition shows up in `scontrol show partition`.

**HPC-Pilot evaluation**: FULLY COVERED
- ✅ `hpc_slurm_partition_update` — create/update partition
- ✅ `hpc_slurm_qos_create` — create QOS with `GrpTRES` and `MaxTRESPU` support
- ✅ `hpc_slurm_reservation_create` — create reservation
- ✅ `hpc_slurm_partition_list` — verification

---

## Scenario 10 — Security: Audit Unauthorized Access Attempt

**Trigger**: Audit log shows repeated failed job cancellation attempts for
job `481516` by a user with operator role. The job belongs to another PI's group.

**Desired outcome**:
1. Query the audit log for all `hpc_slurm_job_cancel` calls targeting job `481516`.
2. Identify the actor, role, and timestamps.
3. Check if any cancellation succeeded.
4. Write a report of findings.

**HPC-Pilot evaluation**: MOSTLY COVERED (updated 2026-06-19)
- ✅ Audit logging exists (JSONL in `~/.hpc-pilot/logs/audit.jsonl`)
- ✅ RBAC prevents unauthorized cancellations (`hpc_slurm_job_cancel` ownership check)
- ✅ `hpc_audit_query` — search/filter audit logs by tool, actor, role, error status,
  time range; returns JSON lines newest first
- ❌ No alerting on repeated RBAC violations

---

## Scenario 11 — Upgrade Slurm Controller (Planned Maintenance)

**Trigger**: Slurm has a security patch. The controller needs an upgrade.
All jobs must complete or be preserved across the restart.

**Desired outcome**:
1. Notify users of planned downtime.
2. Hold all pending jobs to prevent new starts during update.
3. Drain running jobs with a reasonable deadline.
4. Stop slurmctld.
5. Upgrade Slurm packages.
6. Start slurmctld.
7. Verify cluster state (`scontrol ping`, node status).
8. Release held jobs.

**HPC-Pilot evaluation**: MOSTLY COVERED (updated 2026-06-19)
- ✅ `hpc_slurm_job_hold` — hold pending jobs
- ✅ `hpc_slurm_node_state` — drain nodes
- ✅ `hpc_slurm_service` — start/stop/restart/status slurmctld, slurmd, slurmdbd
- ✅ `scheduler-upgrade` runbook — chains stop → verify → start → verify → reconfigure
- ✅ `hpc_slurm_reconfigure` — reload config after upgrade
- ✅ `hpc_slurm_job_release` — release held jobs

---

## Scenario 12 — Storage Crisis: Filesystem Full

**Trigger**: Compute jobs fail with "No space left on device." `/scratch` is
at 98% usage. Users complain that writes are failing.

**Desired outcome**:
1. Check disk usage across all mount points.
2. Identify top directories consuming space in `/scratch`.
3. Check Lustre OST balance (if applicable).
4. Find large orphaned/cached data (old job working directories, staging data).
5. Clean up or identify owners.
6. Configure inode/mount monitoring.

**HPC-Pilot evaluation**: PARTIAL (updated 2026-06-19)
- ✅ `hpc_storage_mounts` — disk usage across mount points
- ✅ `hpc_storage_lustre_status` — Lustre health
- ✅ `hpc_storage_large_files` — find largest files under a path (e.g. /scratch),
  sorted by size with configurable minimum and limit
- ✅ `hpc_storage_quota_check` — check filesystem quotas via repquota
- ✅ `hpc_storage_scrub_orphans` — find orphaned job working directories older
  than a threshold (dry-run by default; deletion requires manual review)
- ❌ No "purge old data" workflow (automated deletion is intentionally avoided)

---

## Scenario 13 — New User Session: Interactive Login Node Setup

**Trigger**: A new researcher `dave` joins the "biochem" group and needs
SSH access to the login node with their SSH public key.

**Desired outcome**:
1. Create UNIX user account `dave` on the login node.
2. Add SSH public key to `~dave/.ssh/authorized_keys`.
3. Create Slurm association for `dave` → `biochem` account.
4. Add user to shared software environment groups.
5. Set up home directory with skel files.
6. Notify the user with login instructions.

**HPC-Pilot evaluation**: MOSTLY COVERED (updated 2026-06-19)
- ✅ `hpc_slurm_association_create` — Slurm association
- ✅ `hpc_system_user_add` — create UNIX account (useradd) with optional UID,
  groups, and shell
- ✅ `hpc_system_user_delete` — delete UNIX account (userdel)
- ✅ `hpc_system_user_group_add` — add user to supplementary groups (usermod -aG)
- ✅ `hpc_system_ssh_key_deploy` — deploy SSH public key to
  `~/.ssh/authorized_keys` with correct permissions
- ✅ `onboard-user` runbook — chains UNIX account → SSH key → Slurm association
  → groups into one composite workflow

---

## Scenario 14 — Job Performance Investigation

**Trigger**: A user reports that their 256-core MPI job is running 3× slower
than last week. Nothing changed in their application.

**Desired outcome**:
1. Find the job in the queue/accounting for the user.
2. Check the job's assigned nodes and resource allocation.
3. Check node CPU/memory state on those nodes.
4. Check InfiniBand link status for fabric health.
5. Check if the node was recently patched/drained (kernel version drift).
6. Compare against previous runs of the same job.

**HPC-Pilot evaluation**: MOSTLY COVERED (updated 2026-06-19)
- ✅ `hpc_slurm_job_status` — job details and nodes
- ✅ `hpc_slurm_node_status` — assigned node state
- ✅ `hpc_fabric_ib_link_status` — InfiniBand health
- ✅ `hpc_ansible_drift_check` — kernel/driver version drift
- ✅ `hpc_slurm_accounting` — historical job comparison
- ✅ `hpc_slurm_job_step_metrics` — per-job-step CPU, memory, and wall-time
  metrics via sacct
- ❌ No composite "perf investigation" runbook (investigations are too
  context-dependent to standardize)

---

## Scenario 15 — License Server Monitoring and CRITICAL Failure

**Trigger**: A licensed application (e.g., ABAQUS, MATLAB) fails for all
users. The FlexLM license server appears unreachable.

**Desired outcome**:
1. Check if the license server host is reachable (ping/SSH).
2. Check if FlexLM daemon is running on the license server.
3. Check FlexLM log for license checkouts/errors.
4. Restart the FlexLM daemon if needed.
5. Notify users that licensing is restored.

**HPC-Pilot evaluation**: NOT COVERED
- ❌ No license server management tools (FlexLM, RLM, LS-Dyna)
- ❌ No license usage query tools
- ❌ No license server restart capability
- ⚠️ `hpc_ansible_playbook_run` could run a wrapper playbook to restart the FlexLM
  daemon, but there is no built-in license awareness or dedicated tooling

---

## Scenario 16 — Rebalance Lustre OSTs

**Trigger**: One Lustre OST is 80% full while others are 20% full. Slower
writes on the full OST are creating I/O bottlenecks.

**Desired outcome**:
1. Check per-OST usage to confirm imbalance.
2. Enable Lustre QoS/throttling on overloaded OSTs.
3. Set OST stripe count on new files to better distribute.
4. Optionally run `lfs_migrate` on files on the full OST to redistribute.

**HPC-Pilot evaluation**: MOSTLY COVERED (updated 2026-06-19)
- ✅ `hpc_storage_lustre_status` — see per-OST usage
- ✅ `hpc_storage_lustre_balance` — per-OST usage report with balance
  analysis and optional ``lfs_migrate`` for files on overfull (>70%) OSTs
- ❌ No Lustre parameter adjustment tools (lctl set_param — dangerous to automate)

---

## Scenario 17 — Power/Cooling Emergency Response

**Trigger**: Data center cooling fails in rack 7. Temperature sensors show
40°C. Ops needs to shut down nodes in that rack before hardware damage.

**Desired outcome**:
1. Identify which nodes are in rack 7 (node naming convention or topology).
2. Gracefully drain running jobs from those nodes.
3. Power off nodes via IPMI (orderly shutdown).
4. After cooling is restored, power on nodes and verify they rejoin Slurm.
5. Check Lustre mounts and fabric links post-recovery.

**HPC-Pilot evaluation**: MOSTLY COVERED (updated 2026-06-19)
- ✅ `hpc_slurm_node_state` — drain nodes
- ✅ `hpc_warewulf_power_off` / `power_on` — IPMI power control
- ✅ `hpc_slurm_node_status` — verify nodes rejoin
- ✅ `emergency-shutdown` runbook — drains nodes → powers off → verifies down
  in one composite workflow with ``for_each`` over a comma-separated node list
- ❌ No data center topology awareness (rack position → node names)
- ❌ No temperature/PDU sensor data tools

---

## Scenario 18 — Login Node Load Spikes (Computation on Login)

**Trigger**: Login node load is at 80 and growing. Users running heavy
computation on the login node instead of submitting jobs.

**Desired outcome**:
1. Identify top processes by CPU/memory on the login node.
2. Identify users running those processes.
3. Send warnings to offending users.
4. Optionally kill user processes that violate policy.
5. Set up user-space cgroups to restrict future login-node computation.

**HPC-Pilot evaluation**: MOSTLY COVERED (updated 2026-06-19)
- ✅ `hpc_login_node_processes` — list top resource-consuming processes on
  the login node, sortable by CPU, memory, or PID
- ✅ `audit-login-node` runbook — runs process listing and presents findings
- ❌ No cgroup management tool for user resource limits

---

## Scenario 19 — Backup and Restore Configuration

**Trigger**: A misconfiguration breaks the Warewulf server. Need to restore
from backup. Regular config backups should exist.

**Desired outcome**:
1. Take a snapshot of current configuration (Slurm, Warewulf, Ansible).
2. Backup all configs to a safe location.
3. When needed, restore config from backup.
4. Verify the system works after restore.

**HPC-Pilot evaluation**: MOSTLY COVERED (updated 2026-06-19)
- ✅ Config is already versioned in git (overlay edits) and the Warewulf config
  has a managed copy with SHA256 tracking
- ✅ `hpc_warewulf_overlay_revert` — git-based overlay restore
- ✅ `hpc_config_backup` — snapshot Slurm config, partitions, reservations,
  associations, Warewulf nodes, accounts, and QOS to a timestamped backup
  directory (`~/.hpc-pilot/backups/<ts>/`)
- ❌ No scheduled backup automation

---

## Scenario 20 — Multi-Cluster Job Migration

**Trigger**: Cluster A is going down for maintenance. 50 running jobs and
120 pending jobs need to move to Cluster B for the weekend.

**Desired outcome**:
1. List all running jobs on Cluster A.
2. Identify which jobs can migrate (same software stack, same data access).
3. Notify affected users.
4. Cancel/requeue jobs on Cluster A (so they can restart on Cluster B).
5. Ensure Cluster B can handle the load.
6. After maintenance, handle the reverse migration.

**HPC-Pilot evaluation**: MOSTLY COVERED (updated 2026-06-19)
- ✅ `hpc_multi_query` — cross-cluster queries
- ✅ `hpc_slurm_queue` — list jobs
- ✅ `hpc_slurm_job_cancel` / `hpc_slurm_job_status` — per-cluster job ops
- ✅ `hpc_multi_migration_plan` — analyzes cross-cluster partition/QOS
  compatibility and job counts between source and target clusters
- ❌ No job dependency tracking for migration ordering

---

## Gap Analysis Summary

| Domain | Fully Covered | Partially | Minimal | Not Covered |
|--------|:-------------:|:---------:|:-------:|:-----------:|
| Node Triage & Recovery | 2 (S2, S5) | 0 | 0 | 0 |
| Cluster Provisioning | 0 | 1 (S6) | 0 | 0 |
| Allocation Management | 1 (S1) | 0 | 0 | 0 |
| User Management | 1 (S13) | 0 | 0 | 0 |
| Usage & Allocations | 0 | 1 (S8) | 0 | 0 |
| Software Management | 1 (S3) | 0 | 0 | 0 |
| Fairshare/Scheduling | 1 (S4) | 0 | 0 | 0 |
| Partition Config | 1 (S9) | 0 | 0 | 0 |
| Health/Security | 2 (S7, S10) | 0 | 0 | 0 |
| Slurm Maintenance | 1 (S11) | 0 | 0 | 0 |
| Storage | 2 (S12, S16) | 0 | 0 | 1 (S15) |
| Performance | 1 (S14) | 0 | 0 | 0 |
| Emergency Response | 1 (S17) | 0 | 0 | 0 |
| Login Node Management | 1 (S18) | 0 | 0 | 0 |
| Backup/Disaster | 1 (S19) | 0 | 0 | 0 |
| Multi-Cluster | 1 (S20) | 0 | 0 | 0 |

**Legend**:
- **Fully Covered**: All desired outcomes achievable with existing tools/runbooks
- **Partial**: Some steps covered, some missing; may require manual orchestration
- **Minimal**: Only basic observation possible; most actions can't be done
- **Not Covered**: No tooling exists for this domain

## Resolved Gaps (2026-06-19)

The following gaps were addressed in this update:

1. ✅ **S1: Group onboarding** — `GrpTRES` support added to QOS create/modify.
   New `onboard-group` runbook chains account creation → QOS → user associations.
   New `hpc_usage_vs_budget` tool for used-vs-allocated tracking.
   New `hpc_notify` tool for gateway notifications.
2. ✅ **S3: Software stack update** — New `hpc_warewulf_image_build_from_env`
   tool builds images with a concretized Spack environment baked in.
   New `hpc_job_submit_test` tool for validation test job.
3. ✅ **S6: Node provisioning** — `hpc_warewulf_node_add_bulk` for bulk adds.
   New `provision-nodes` runbook.
4. ✅ **S7: Health incident triage** — New `health-incident-triage` runbook.
5. ✅ **S8: Usage vs budget** — New `hpc_usage_vs_budget` tool comparing
   `sacct` usage against QOS `GrpTRES` limits with percentage and warning.
6. ✅ **S10: Audit querying** — New `hpc_audit_query` tool with filters.
7. ✅ **S11: Slurm maintenance** — New `hpc_slurm_service` tool.
   New `scheduler-upgrade` runbook.
8. ✅ **S12/S16: Storage** — New `hpc_storage_large_files`, `hpc_storage_quota_check`,
   `hpc_storage_scrub_orphans`, and `hpc_storage_lustre_balance` tools.
9. ✅ **S13: User account creation** — New `hpc_system_user_add`, `_delete`,
   `_group_add`, `_ssh_key_deploy` tools. New `onboard-user` runbook.
10. ✅ **S14: Job performance** — New `hpc_slurm_job_step_metrics` tool.
11. ✅ **S17: Emergency response** — New `emergency-shutdown` runbook.
12. ✅ **S18: Login node management** — New `hpc_login_node_processes` tool.
    New `audit-login-node` runbook.
13. ✅ **S19: Backup/config** — New `hpc_config_backup` tool.
14. ✅ **S20: Multi-cluster migration** — New `hpc_multi_migration_plan` tool.

## Remaining Gaps (not yet implemented)

1. **S15: License server** — No FlexLM/lmstat tooling (external to HPC-Pilot scope).
2. **S1: Budget enforcement** — No automatic enforcement when usage exceeds QOS
   limits (Slurm enforces at job submission time, but no proactive alerting).
3. **S10: RBAC alerting** — No rules engine for detecting repeated permission
   violations automatically.
4. **S19: Scheduled backups** — Requires cron/systemd timer (outside HPC-Pilot scope).
5. **S7: PagerDuty/incident tracking** — External system integration
   (outside HPC-Pilot scope).
