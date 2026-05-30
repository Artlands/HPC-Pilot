# 07 — Management Workflows

Composite, multi-tool procedures the planner assembles. Each is implemented as a workflow
function in `hpc_agent/workflows/` that returns a `Plan` (spec 02 §3) — it does **not**
execute directly; it produces ordered steps the executor runs with normal gating.

Convention: every workflow function signature is
`def build(<typed args>, actor: str) -> Plan`. Steps reference tools from specs 03–06.

---

## 1. Onboard user
`workflows/onboard_user.py`
Args: `user, account, qos_list, default_qos, fairshare, create_home=True, quota_gb`.
Steps:
1. `slurm.show_assoc(user)` (READ) — skip if already exists.
2. For each QOS not present: `slurm.manage_qos(op=create…)` (usually pre-existing).
3. `slurm.extend_account(name=account, op=modify-or-create)` if account missing.
4. `slurm.manage_user_assoc(op=create, user, account, qos_list, default_qos, fairshare)`.
5. If `create_home`: `ansible.run_playbook(playbook="user_home", extra_vars={user,quota_gb})`
   (role creates home dir, sets quota, ldap/sssd entry if applicable).
6. Verify: `slurm.show_assoc(user)` reflects the new assoc.

## 2. Extend account / QOS (the core requested task)
`workflows/extend_allocation.py`
Args: `target (user|account|qos), field, value` (e.g. wall, gpu TRES, cpu TRES, job limit).
Steps:
1. Read current (`show qos`/`show assoc`).
2. `slurm.manage_qos` or `slurm.set_limits` modify with only the changed field.
3. Policy decides auto vs approval (spec 01 §4). Apply, record inverse.
4. Verify new value; report before/after to operator.

## 3. Add compute node (CPU or GPU) end-to-end
`workflows/add_node.py`
Args: `hostname, mac, ip, role(compute_cpu|compute_gpu), partition, image, gres?`.
Steps:
1. `warewulf.assign_image_to_nodes` / ensure profile exists (`define_profile`).
2. `warewulf.provision_node(hostname, mac, ip, profile, role)`.
3. `warewulf.rebuild_overlay(node)`.
4. **Boot + validate** (out-of-band power-on or operator action): poll
   `warewulf.node_status` until up; run `ansible.run_playbook(common,chrony,munge,slurm_client[,dcgm_exporter])`
   limited to the node.
5. Validate: ansible ad-hoc `slurmd -C`; GPU → `nvidia-smi`, `dcgmi discovery -l`.
6. `slurm.add_node_to_partition(node, partition, features, gres)` → `scontrol reconfigure`.
7. `slurm.node_state(node, resume)`.
8. Reconcile state store.
GPU vs CPU differences are carried by `role`/`image`/`gres` and the extra `dcgm_exporter`
role + nvidia validation.

## 4. Node maintenance / patch / rebuild
`workflows/node_maintenance.py`
Args: `node, action(patch|rebuild_image), new_image?`.
Steps:
1. `slurm.manage_reservation(create, MAINT, node)` (optional) or
   `slurm.node_state(node, drain, reason)`.
2. Wait until node idle (`slurm.queue` shows no running jobs on node).
3. patch: `ansible.run_playbook(patch, limit=node)`; or rebuild:
   `warewulf.assign_image_to_nodes(node, new_profile)` + `rebuild_overlay` + reboot.
4. Re-validate (as §3 step 5).
5. `slurm.node_state(node, resume)`; delete reservation.

## 5. Rolling image update across a group
`workflows/rolling_update.py`
Args: `group, new_image, batch_size=2`.
Steps: chunk the group into batches ≤ `batch_size`; for each batch run §4 sequentially,
halting on any validation failure. Blast-radius caps (spec 01 §6) force approval for large
groups.

## 6. Reconciliation (state ↔ live)
`workflows/reconcile.py` — runs on a schedule and on demand.
Steps (all READ): `warewulf.query_*`, `slurm.node_status`, `slurm.show_assoc`,
`spack.query_*`. Compute drift between state store (desired) and live (actual). Output a
**drift report**; propose corrective Plans but never auto-apply mutations from reconcile.

## 7. Health monitoring & triage
`workflows/health_triage.py` — scheduled.
Detect:
- Nodes `down`/`drained` (`slurm.node_status`): inspect Reason.
- GPU faults: `dcgm_exporter`/`dcgmi` ECC/XID errors.
- Munge desync / time skew (chrony): common slurmd failure cause.
- Filesystem/quota alerts (node_exporter, df).
- Controller health (`sdiag`, slurmdbd reachability).
For each finding, attach a suggested remediation Plan (e.g. restart slurmd via ansible,
re-sync munge key, drain+rebuild) gated normally. Auto-remediation only for LOW-risk,
explicitly policy-allowed actions; everything else surfaces to an operator.

## 8. Usage & allocation reporting
`workflows/reporting.py` — `slurm.usage_report` + `slurm.job_accounting` aggregated per
account/user over a period; render a table/CSV. READ-only, fully autonomous.

## 9. Offboard user
`workflows/offboard_user.py`
Steps: archive usage (`sreport`), `slurm.manage_user_assoc` remove (modify, not delete of
account), reclaim quota via ansible. **Permanent deletion is prohibited** — assoc removal
+ archival only; data deletion is flagged for a human.

## 10. Validation checklist

- Each workflow returns a valid dependency-ordered `Plan`; workflows do not execute side
  effects at build time.
- `add_node` works for both CPU and GPU nodes on the virtual cluster, ending with the node
  `idle` in its partition.
- `extend_allocation` for an in-policy wall-time bump runs without approval; an
  out-of-policy change pauses for approval.
- `reconcile` reports injected drift and proposes, but does not auto-apply, a fix.
- `node_maintenance` drains before touching a node and resumes after validation.
- `offboard_user` performs no permanent deletion.
