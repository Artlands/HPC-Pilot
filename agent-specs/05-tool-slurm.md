# 05 — Scheduler Tools (Slurm)

The highest-frequency management surface: accounts, QOS, user associations, partitions,
node states. Targets Slurm 23.x+ with `slurmdbd`. Wraps `sacctmgr`, `scontrol`, `sinfo`,
`squeue`, `sacct`, `sreport`.

Sudoers:
```
hpcagent ALL=(slurm) NOPASSWD: /usr/bin/sacctmgr, /usr/bin/scontrol
```
(Run accounting/control commands as the `slurm` user, not root.)

**Parsing rule:** always query with parseable output (`-P` for `sacctmgr`, `--json` for
`scontrol`/`squeue` where available) — never screen-scrape human tables.

**Dry-run rule:** never pass `-i` (immediate) in dry-run. Compute the delta by reading
current state with `show ... -P`, diff against requested, and return a `Diff`. For
`slurm.conf` edits, validate with `slurmctld -t -f <tmpfile>` before applying.

---

## 1. Account & QOS

### 1.1 `slurm.extend_account`
Risk: MEDIUM (create) / LOW (modify). Create or modify an account in the hierarchy.
```python
class ExtendAccountIn(BaseModel):
    name: str
    op: Literal["create","modify"]
    parent: str | None = None
    organization: str | None = None
    description: str | None = None
    # optional association-level limits set on the account:
    grp_tres: str | None = None        # "cpu=512,gres/gpu=32"
    max_wall_min: int | None = None
    dry_run: bool = True
```
Commands:
- create: `sacctmgr -i add account <name> parent=<parent> Organization=... Description=...`
- modify: `sacctmgr -i modify account <name> set <fields>`
- limits: `sacctmgr -i modify account <name> set GrpTRES=... MaxWall=...`
Inverse (for revert): capture prior values from `sacctmgr show account <name> -P` and
build a `modify ... set <prior>` command (create's inverse is `delete account`, which is
**prohibited** auto — flag for manual).
State: upsert `Account`.

### 1.2 `slurm.manage_qos`
Risk: MEDIUM. **This is the core "extend a user's QOS" capability.** Create/modify a QOS.
```python
class ManageQOSIn(BaseModel):
    name: str
    op: Literal["create","modify"]
    priority: int | None = None
    max_wall_min: int | None = None        # MaxWall
    max_jobs_pu: int | None = None         # MaxJobsPerUser
    max_tres: str | None = None            # MaxTRES, e.g. "cpu=128,gres/gpu=8"
    max_tres_pu: str | None = None         # MaxTRESPerUser
    grp_tres: str | None = None            # GrpTRES
    flags: list[str] = []                  # e.g. ["DenyOnLimit"]
    dry_run: bool = True
```
Commands:
- create: `sacctmgr -i add qos <name> set Priority=... MaxWall=... MaxTRES=... ...`
- modify: `sacctmgr -i modify qos <name> set <changed fields only>`
Minute->Slurm time: convert `max_wall_min` to `D-HH:MM:SS`.
Policy hook: `policy/slurm.yaml` caps `max_wall_min` / `max_tres.gpu` (spec 01 §4); an
in-bounds modify auto-runs, out-of-bounds requires approval or is denied.
Inverse: prior QOS values via `show qos <name> -P`.
State: upsert `QOS`.

### 1.3 `slurm.manage_user_assoc`
Risk: MEDIUM. Add/modify a user's association: account, allowed QOS, default QOS,
fairshare. Covers onboarding and "extend this user with access to the high QOS".
```python
class ManageUserAssocIn(BaseModel):
    user: str
    account: str
    op: Literal["create","modify"]
    qos_list: list[str] | None = None      # full allowed set (replaces)
    qos_add: list[str] | None = None       # additive (qos+=)
    default_qos: str | None = None
    fairshare: int | None = None
    dry_run: bool = True
```
Commands:
- create: `sacctmgr -i add user <user> account=<account> [DefaultQOS=...] [Fairshare=...]`
  then set qos: `sacctmgr -i modify user <user> account=<account> set qos=<list>`
- add a QOS: `sacctmgr -i modify user <user> account=<account> set qos+=<q>`
- modify: targeted `set` of changed fields.
Precondition: every QOS in the request must already exist (`show qos -P`); else
PRECONDITION error suggesting `manage_qos` first.
Inverse: prior assoc from `show assoc user=<user> account=<account> -P`.
State: upsert `UserAssoc`.

### 1.4 `slurm.set_limits`
Risk: MEDIUM. Convenience wrapper to set TRES/job/wall limits on an existing
account/user/qos association without recreating it. Delegates to the appropriate
`sacctmgr modify ... set`.

---

## 2. Partitions & nodes

### 2.1 `slurm.manage_partition`
Risk: MEDIUM (HIGH on delete — delete is **prohibited** auto). Edits partition definition
in `slurm.conf` (config-as-code).
```python
class ManagePartitionIn(BaseModel):
    name: str
    op: Literal["create","modify"]
    nodes: list[str] | None = None         # node names or ranges
    default: bool | None = None
    max_time_min: int | None = None
    default_qos: str | None = None
    allow_qos: list[str] | None = None
    state: Literal["UP","DOWN","DRAIN"] | None = None
    dry_run: bool = True
```
Procedure: read `slurm/slurm.conf` from config repo, edit the `PartitionName=<name> ...`
line (or append), validate with `slurmctld -t -f <tmp>`, stage diff. On apply: commit,
copy conf to controller (via overlay rebuild or direct), `scontrol reconfigure`.
State: upsert `Partition` + `PartitionMember`.

### 2.2 `slurm.add_node_to_partition`
Risk: MEDIUM. Adds a node (already provisioned, spec 03) to a partition + sets its
NodeName line (CPUs, RealMemory, Gres, Features) in `slurm.conf`.
```python
class AddNodeToPartitionIn(BaseModel):
    node: str
    partition: str
    features: list[str] = []               # -> Features=
    gres: str | None = None                # "gpu:a100:8" for GPU nodes
    dry_run: bool = True
```
Procedure: ensure `NodeName=<node> CPUs=.. RealMemory=.. [Gres=..] Features=..` present
(read hw facts from `Node` row), add node to the partition's `Nodes=` list, validate,
apply, `scontrol reconfigure`. For GPU nodes ensure `gres.conf`/`GresTypes` consistent.

### 2.3 `slurm.node_state`
Risk: MEDIUM. Drain/resume/down/undrain a node for maintenance.
```python
class NodeStateIn(BaseModel):
    node: str
    target: Literal["drain","resume","down","undrain"]
    reason: str | None = None              # required for drain/down
    dry_run: bool = True
```
Commands: `scontrol update NodeName=<node> State=DRAIN Reason="<reason>"` etc.
Drain is graceful (lets running jobs finish). Inverse of `drain` is `resume`; recorded for
revert. State: update `Node.state`.

### 2.4 `slurm.reconfigure`
Risk: LOW. `scontrol reconfigure`. Called automatically after conf edits; also exposed
standalone.

### 2.5 `slurm.manage_reservation`
Risk: MEDIUM. Create/delete maintenance reservations.
```python
class ManageReservationIn(BaseModel):
    name: str; op: Literal["create","delete"]
    nodes: list[str] | None = None; start: str | None = None
    duration_min: int | None = None; users: list[str] | None = None
    flags: list[str] = ["MAINT","IGNORE_JOBS"]
    dry_run: bool = True
```
Commands: `scontrol create reservation ...` / `scontrol delete reservation=<name>`.

---

## 3. Reporting / queries (READ — auto-run)

| Tool | Wraps | Returns |
|------|-------|---------|
| `slurm.node_status` | `sinfo --json` / `scontrol show node --json` | per-node state, reason |
| `slurm.queue` | `squeue --json` | jobs, states, pending reasons |
| `slurm.job_accounting` | `sacct -P --json` | completed-job records |
| `slurm.usage_report` | `sreport cluster/user Utilization -P` | usage by account/user |
| `slurm.show_assoc` | `sacctmgr show assoc -P` | associations (for reconcile) |
| `slurm.diag` | `scontrol show config`, `sdiag` | controller health |

All reconcile relevant rows into the state store (spec 07 §6).

---

## 4. Worked example — "extend user alice's wall time to 48h on the gpu QOS"

Planner emits:
1. `slurm.show_assoc(user=alice)` (READ) — confirm alice is on `gpu` QOS.
2. `slurm.manage_qos(name="gpu", op="modify", max_wall_min=2880, dry_run=True)` → Diff:
   `qos/gpu MaxWall 1-00:00:00 → 2-00:00:00`.
3. Policy `qos-extend-autoallow` allows ≤2880 → auto-run; else approval.
4. Apply; record inverse (`MaxWall=1-00:00:00`); audit. No reconfigure needed (QOS is in
   slurmdbd, not slurm.conf).

## 5. Validation checklist

- `manage_qos` modify changes only specified fields; dry-run shows exact before/after.
- `manage_user_assoc` returns `PRECONDITION` if a requested QOS does not exist.
- `slurm.conf` edits are rejected if `slurmctld -t` validation fails.
- `node_state drain` records a working `resume` inverse for revert.
- Queries parse `--json` or `-P` output rather than human tables.
- Account and QOS deletes are never auto-executed; they are prohibited and flagged to a
  human operator.
