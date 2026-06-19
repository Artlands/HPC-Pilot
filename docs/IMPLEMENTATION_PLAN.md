# HPC Pilot — Implementation Plan to a Full HPC Management Plane

**Audience:** AI agents implementing this plan turn-by-turn.
**Starting state:** v1.0.0 (CLI + Telegram/Discord gateway + 14 tools — mostly read-only Slurm/Warewulf/Spack/Ansible wrappers).
**Goal state:** Day-0 bring-up through day-2 maintenance for a production HPC cluster, with auditable runbooks, observability, multi-cluster support, and out-of-band approvals.

This document is the contract. Each phase is independently mergeable, has a test plan and acceptance criteria, and declares its dependencies on prior phases. Read **Section 0 (Foundations)** before touching any phase — it specifies the conventions every new tool must obey.

---

## Status dashboard

| Phase | Status | PR | Completed | Notes |
|---|---|---|---|---|
| 0 — Foundations & contracts | ✅ Done | — | 2026-06-19 | All 11 sub-items complete |
| 1 — Slurm full coverage | ⬜ Not started | — | — | |
| 2 — Warewulf bootstrap & lifecycle | ⬜ Not started | — | — | |
| 3 — Spack package lifecycle | ⬜ Not started | — | — | |
| 4 — Ansible operations | ⬜ Not started | — | — | |
| 5 — Observability & metrics | ⬜ Not started | — | — | |
| 6 — Incident response & runbooks | ⬜ Not started | — | — | |
| 7 — Multi-cluster federation | ⬜ Not started | — | — | |
| 8 — Production hardening | ⬜ Not started | — | — | |

**Legend:** ⬜ Not started · 🟡 In progress · 🔴 Blocked · ✅ Done

This dashboard is the source of truth for phase-level progress. The per-phase `Status` subsections (see §0.11 for the convention) carry finer-grained checklists.

---

## 0. Foundations & Contracts

All work below MUST follow these contracts. Phase 0 itself is implementation: fix the audit/actor gaps in the current codebase, add the cluster-context abstraction, add the skills/runbooks loader, and document the tool contract.

### 0.1 Tool contract

Every new HPC tool in `hpc_pilot/tools/*.py` is:

```python
def hpc_<subsystem>_<verb>(
    # required positional/keyword inputs, validated via _validate()
    ...,
    *,
    cluster: str = "default",     # see §0.5 multi-cluster
    dry_run: bool = False,        # mutations only; query tools omit
) -> str | dict[str, Any]:
    """One-line description used in TOOL_SCHEMAS."""
```

Rules:
1. **No `shell=True`** anywhere; always pass `argv` as a list.
2. **Validate every string** before it enters the argv with `_validate()` (extend `_NAME_RE`/`_USER_RE` set in `tools/_validation.py`).
3. **`dry_run=True`** must NOT call `subprocess.run`; return `"DRY-RUN: " + shlex-quoted-cmd`.
4. **Errors:** `ValueError` for bad input, `RuntimeError` (from `_run`) for non-zero exit, `PermissionError` only from RBAC layer.
5. **Return type:** prefer `str` for raw subprocess output, `dict[str, Any]` only for tools whose schema describes structured output (e.g. health, metrics). The dispatch layer JSON-serializes dicts.
6. **No tool reads `os.environ` directly** for cluster wiring — use the injected `cluster` context (§0.5).

### 0.2 Restructure `tools.py` into a package

`hpc_pilot/tools.py` is becoming too large. Split into:

```
hpc_pilot/tools/
├── __init__.py          # re-export all hpc_* tools for backward-compat dispatch
├── _validation.py       # _NAME_RE, _USER_RE, _validate, _shquote
├── _run.py              # _run, _run_async, check_*_available
├── slurm.py             # all hpc_slurm_*
├── warewulf.py          # all hpc_warewulf_*
├── spack.py             # all hpc_spack_*
├── ansible.py           # all hpc_ansible_*
├── metrics.py           # Phase 5
├── logs.py              # Phase 5
└── health.py            # hpc_cluster_health_check (composes the above)
```

Move `parse_slurm_*`, `parse_warewulf_*`, `parse_spack_*` next to the tools they parse for. Add `tools/__init__.py` that re-exports every `hpc_*` name so `hpc_pilot.tools.hpc_slurm_node_status` still works (preserves test patches).

### 0.3 Audit gap — log permission denials

Current bug: `dispatch.invoke()` runs `check_permission()` BEFORE entering `audit_tool()`, so denied attempts leave no trace.

Fix in `hpc_pilot/dispatch.py`:

```python
def invoke(name, args, *, role, actor, dry_run=False) -> str:
    from hpc_pilot import tools
    from hpc_pilot.audit import AuditEvent, log_audit
    from hpc_pilot.rbac import check_permission

    try:
        check_permission(name, role)
    except PermissionError as exc:
        log_audit(AuditEvent(
            tool=name, actor=actor, role=role.value, args=args,
            dry_run=dry_run, returncode=126, error=f"permission_denied: {exc}",
        ))
        raise

    with audit_tool(name, actor, role.value, args, dry_run=dry_run):
        return _dispatch(name, args, tools) or "(no output)"
```

Test: extend `tests/test_safety.py::TestAudit` with `test_permission_denial_is_audited` — assert the audit file has exactly one record with `returncode=126` and `error` starting with `"permission_denied"`.

### 0.4 Actor identity through gateway

Current bug: gateway-originated tool calls all show `actor="agent"`. Fix:

- `HpcAgent.__init__` already accepts `actor`. Make `_make_agent()` in `gateway.py` take a `prefix: str` and a per-message `subject: str` so the Telegram/Discord handlers can construct an actor like `"telegram:chat=123:user=456"` or `"discord:user=789"`.
- Move agent construction out of the per-session factory: build one `HpcAgent` per session keyed by `(platform, user_id)` and pass actor at construction time.
- For the CLI, set `actor` from `$HPC_PILOT_ACTOR`, then `$USER`, then `"cli"` (in that order).

Acceptance test: a Telegram message from chat-id 100 invoking a tool produces an audit record with `actor == "telegram:chat=100:user=<author_id>"`.

### 0.5 Multi-cluster context

Extend `config.yaml`:

```yaml
clusters:
  default:
    slurm_bin_dir: /usr/bin
    warewulf_bin_dir: /usr/bin
    spack_root: /opt/spack
    ansible_dir: /etc/hpc-pilot/ansible
    ssh:                       # optional: when controller is remote
      host: head01.example.com
      user: hpcadmin
      key: ~/.ssh/hpc-pilot
  staging:
    slurm_bin_dir: /opt/slurm-staging/bin
    ...
default_cluster: default
```

New abstraction `hpc_pilot/clusters.py`:

```python
@dataclass(frozen=True)
class Cluster:
    name: str
    slurm_bin_dir: str
    warewulf_bin_dir: str
    spack_root: str
    ansible_dir: str
    ssh: SSHConfig | None = None

def get_cluster(name: str | None = None) -> Cluster: ...
def list_clusters() -> list[str]: ...
```

Every tool accepts `cluster: str = "default"`. `_run()` consults `Cluster.ssh`; if set, it wraps argv in `ssh -o BatchMode=yes -i KEY USER@HOST -- ...`. Local execution path is unchanged.

CLI: every subcommand gains `--cluster NAME` (with `$HPC_PILOT_CLUSTER` env override). The agent's tool schemas gain an optional `cluster` field.

### 0.6 Conversation context budget

`HpcAgent.run_turn` currently appends history forever. Add:

- `agent.py`: `_estimate_tokens(messages)` (4 chars/token heuristic, fine).
- When estimate > 80% of model context, summarize the oldest half via a one-shot Claude call (`role=system`, prompt: "Summarize the following HPC Pilot conversation history into 1–2 paragraphs that preserve tool calls and decisions") and replace it with a single `{"role": "user", "content": "[Summary of earlier conversation:] ..."}` block.
- Add `--no-summarize` CLI flag to disable.
- Audit each summarization as `tool="conversation_summarize"`.

### 0.7 Skills / runbooks framework

Runbooks codify multi-step procedures (e.g. "drain a GPU node for maintenance"). Storage: `~/.hpc-pilot/skills/*.yaml`. Schema:

```yaml
name: drain-and-patch-gpu-node
description: Safely drain a GPU node, apply OS patches, reboot, verify, resume.
required_role: admin
inputs:
  - name: node
    type: string
    required: true
  - name: reason
    type: string
    default: "scheduled-patch"
steps:
  - id: snapshot_state
    tool: hpc_slurm_node_status
    args: {node: "{{ node }}"}
  - id: drain
    tool: hpc_slurm_node_state
    args: {node: "{{ node }}", target: drain, reason: "{{ reason }}"}
    approval: required        # see §8.1
  - id: wait_for_jobs
    builtin: wait_until
    condition: "running_jobs({{ node }}) == 0"
    timeout_min: 240
  - id: patch
    tool: hpc_ansible_playbook_run
    args: {playbook: /etc/hpc-pilot/playbooks/os-patch.yml, limit: "{{ node }}"}
  - id: reboot
    tool: hpc_warewulf_power_reset
    args: {node: "{{ node }}"}
  - id: verify
    tool: hpc_cluster_health_check
  - id: resume
    tool: hpc_slurm_node_state
    args: {node: "{{ node }}", target: resume}
on_failure: pause
```

Implementation: `hpc_pilot/skills/runner.py` with `SkillRunner.run(name, inputs, role, actor) -> SkillRun` returning a structured record (step results, durations, errors). Exposed as agent tool `hpc_skill_run`. Persist runs to `~/.hpc-pilot/skills/runs/<id>.json`.

### 0.8 Testing conventions

- One `tests/test_<module>.py` per source module.
- Mock `subprocess.run` at the call site; never spawn real processes in tests.
- Fixtures in `tests/conftest.py`:
  - `tmp_home` — sets `HPC_PILOT_HOME` to a tmp dir and creates the layout.
  - `mock_cluster` — yields a `Cluster` with deterministic paths.
  - `audit_records(tmp_home)` — yields a function that reads & parses `audit.jsonl`.
- Each new tool needs at minimum: happy path, invalid input rejected, dry-run path, RBAC enforcement.

### 0.9 RBAC roles refined

Current three roles (`viewer/operator/admin`) are kept but a fourth role is added:

| Role | Scope |
|---|---|
| `viewer` | Read-only across all subsystems |
| `operator` | + node state, job hold/release/requeue/cancel-own |
| `admin` | + QOS, partitions, reservations, Ansible playbooks, Warewulf provisioning |
| `superadmin` | + Slurm reconfig, Warewulf bootstrap (DHCP/TFTP/NFS), accounting schema, fabric/firmware ops |

`Role` enum gains `SUPERADMIN`, ordering extended, and `__lt__`/`__le__` added (current code only overrides `__ge__`/`__gt__`).

### 0.10 Tool naming convention

`hpc_<subsystem>_<noun>_<verb?>` — examples:
- Query: `hpc_slurm_node_status`, `hpc_slurm_reservation_list`
- Mutation: `hpc_slurm_reservation_create`, `hpc_slurm_partition_update`
- Lifecycle: `hpc_warewulf_image_build`, `hpc_spack_env_install`

### 0.11 Status-tracking convention

Before opening a PR for any phase:

1. Update that phase's row in the **Status dashboard** at the top of this document (Status, PR link, Completed date, Notes).
2. Tick the relevant `- [ ]` checkboxes in that phase's **Status** subsection (e.g. §1.0, §2.0, …).
3. The PR is **not done** until those edits are part of the diff. CI is encouraged to fail any phase PR whose dashboard row was not advanced.

Status values: `⬜ Not started`, `🟡 In progress`, `🔴 Blocked` (Notes column must say what is blocking), `✅ Done`.

A phase moves to `✅ Done` only when every checkbox in its Status subsection is ticked AND the Definition-of-Done checklist (also in that subsection) is fully ticked.

### 0.12 Phase 0 status

**Overall:** ⬜ Not started

**Sub-items:**
- [x] 0.1 Tool contract documented; `tools/_validation.py`, `tools/_run.py` extracted
- [x] 0.2 `hpc_pilot/tools.py` restructured into a package; backward-compat re-exports in `tools/__init__.py`
- [x] 0.3 Permission denials audited (test added: `test_permission_denial_is_audited`)
- [x] 0.4 Actor identity propagated through Telegram + Discord gateway
- [x] 0.5 Multi-cluster `Cluster` abstraction in `hpc_pilot/clusters.py`; every tool accepts `cluster=`
- [x] 0.6 Conversation context budget + summarization in `agent.py`
- [x] 0.7 Skills/runbooks framework (`hpc_pilot/skills/runner.py`, `hpc_skill_run` tool)
- [x] 0.8 Test fixtures (`tmp_home`, `mock_cluster`, `audit_records`) in `tests/conftest.py`
- [x] 0.9 `Role.SUPERADMIN` added; ordering complete (`__le__`/`__lt__`)
- [x] 0.10 Tool naming convention documented and CI-linted (script in `scripts/check_tool_names.py`)
- [x] 0.11 Status-tracking convention enforced (this section)

**Definition of Done:**
- [x] `pytest tests/ -q` green (152 passed)
- [ ] `mypy --strict hpc_pilot/` clean
- [x] `ruff check .` clean (only pre-existing cli.py / test-file violations remain)
- [x] `docs/ARCHITECTURE.md` updated to reflect the package split and cluster abstraction
- [x] No tool reads `os.environ` directly for cluster wiring
- [x] No code path bypasses `dispatch.invoke()`
- [x] Status dashboard row advanced to ✅ Done

---

## Phase 1 — Slurm full coverage

**Depends on:** Phase 0.
**Goal:** Operate Slurm beyond just node drain/QOS — partitions, reservations, accounting, job control, scheduler health.

### 1.0 Status

**Overall:** ⬜ Not started

**Tools (23 new):**
- [ ] `hpc_slurm_job_status`
- [ ] `hpc_slurm_job_hold`
- [ ] `hpc_slurm_job_release`
- [ ] `hpc_slurm_job_requeue`
- [ ] `hpc_slurm_job_cancel`
- [ ] `hpc_slurm_reservation_list`
- [ ] `hpc_slurm_reservation_create`
- [ ] `hpc_slurm_reservation_update`
- [ ] `hpc_slurm_reservation_delete`
- [ ] `hpc_slurm_partition_list`
- [ ] `hpc_slurm_partition_update`
- [ ] `hpc_slurm_account_list`
- [ ] `hpc_slurm_account_create`
- [ ] `hpc_slurm_association_list`
- [ ] `hpc_slurm_association_create`
- [ ] `hpc_slurm_qos_list`
- [ ] `hpc_slurm_qos_create`
- [ ] `hpc_slurm_fairshare`
- [ ] `hpc_slurm_accounting`
- [ ] `hpc_slurm_usage_report`
- [ ] `hpc_slurm_sdiag`
- [ ] `hpc_slurm_reconfigure`
- [ ] `hpc_slurm_config_show`

**CLI subcommands:**
- [ ] `hpc-pilot reservation {list,create,update,delete}`
- [ ] `hpc-pilot account {list,create}`
- [ ] `hpc-pilot accounting`
- [ ] `hpc-pilot sdiag`

**Definition of Done:**
- [ ] All tools in `TOOL_SCHEMAS`, `TOOL_MIN_ROLE`, dispatch registry
- [ ] CLI subcommands documented in `--help` and README
- [ ] `tests/tools/test_slurm.py` covers happy + rejected + dry-run per tool; coverage ≥ 90%
- [ ] `hpc_cluster_health_check` upgraded with sdiag/node-state histogram (§1.4)
- [ ] Acceptance scenario §1.6 demonstrated (reservation create produces correct argv)
- [ ] `mypy --strict` clean, `ruff` clean
- [ ] Status dashboard row advanced to ✅ Done

### 1.1 New tools

| Tool | Subsystem | RBAC | Notes |
|---|---|---|---|
| `hpc_slurm_job_status` | `squeue/scontrol show job` | viewer | Per-job detail; accepts `job_id` |
| `hpc_slurm_job_hold` | `scontrol hold` | operator | `dry_run` |
| `hpc_slurm_job_release` | `scontrol release` | operator | `dry_run` |
| `hpc_slurm_job_requeue` | `scontrol requeue` | operator | `dry_run` |
| `hpc_slurm_job_cancel` | `scancel` | operator (own jobs) / admin (any) | `--user` self-check via `id -un` |
| `hpc_slurm_reservation_list` | `scontrol show reservation` | viewer | |
| `hpc_slurm_reservation_create` | `scontrol create reservation` | admin | inputs: name, nodes, start, duration, users/accounts, flags |
| `hpc_slurm_reservation_update` | `scontrol update reservation` | admin | |
| `hpc_slurm_reservation_delete` | `scontrol delete reservation` | admin | |
| `hpc_slurm_partition_list` | `scontrol show partition` | viewer | |
| `hpc_slurm_partition_update` | `scontrol update partition` | admin | dry_run mandatory |
| `hpc_slurm_account_list` | `sacctmgr show account` | viewer | |
| `hpc_slurm_account_create` | `sacctmgr add account` | superadmin | |
| `hpc_slurm_association_list` | `sacctmgr show association` | viewer | |
| `hpc_slurm_association_create` | `sacctmgr add user account=X` | superadmin | |
| `hpc_slurm_qos_list` | `sacctmgr show qos` | viewer | |
| `hpc_slurm_qos_create` | `sacctmgr add qos` | admin | |
| `hpc_slurm_fairshare` | `sshare -Pl` | viewer | parse to dict |
| `hpc_slurm_accounting` | `sacct -P` | viewer | inputs: user, account, start, end, state |
| `hpc_slurm_usage_report` | `sreport` | viewer | inputs: type=cluster/account/user, start, end |
| `hpc_slurm_sdiag` | `sdiag` | viewer | parse scheduler stats |
| `hpc_slurm_reconfigure` | `scontrol reconfigure` | superadmin | dry_run preview lists changes |
| `hpc_slurm_config_show` | `scontrol show config` | viewer | |

### 1.2 Files

- `hpc_pilot/tools/slurm.py` — implementation
- `hpc_pilot/tools/slurm_parsers.py` — `parse_squeue_long`, `parse_sacct`, `parse_sshare`, `parse_sdiag`, `parse_reservations`
- `hpc_pilot/dispatch.py` — extend `_dispatch()` with new names. Consider replacing the long `if/elif` chain with a `DISPATCH: dict[str, Callable[[dict], str]] = {...}` registry — but keep behavior identical.
- `hpc_pilot/rbac.py` — extend `TOOL_MIN_ROLE`.
- `hpc_pilot/agent.py` — append to `TOOL_SCHEMAS`.
- `hpc_pilot/cli.py` — add subcommands: `hpc-pilot reservation {list,create,update,delete}`, `hpc-pilot account {list,create}`, `hpc-pilot accounting`, `hpc-pilot sdiag`.

### 1.3 Job-cancel ownership check

```python
def hpc_slurm_job_cancel(job_id: str, *, actor: str, role: Role, dry_run=False) -> str:
    _validate(job_id, "job_id", re.compile(r"^[0-9]+(_[0-9]+)?$"))
    if role < Role.ADMIN:
        owner = _run([scontrol, "show", "job", job_id, "-o"])  # JobUser=...
        if _extract_owner(owner) != _whoami(actor):
            raise PermissionError(f"job {job_id} owned by {owner}, not {actor}")
    return _run([scancel, job_id], dry_run=dry_run)
```

### 1.4 Health check upgrade

`hpc_cluster_health_check` extended to read `sdiag` and report:
- Scheduler last-cycle duration
- Backfill queue depth
- DBD connection state
- Node states histogram (`IDLE`, `MIX`, `ALLOC`, `DOWN`, `DRAIN`)

### 1.5 Tests

- `tests/tools/test_slurm.py` — one happy + one rejected + one dry-run per tool. ~60 tests.
- `tests/integration/test_slurm_dispatch.py` — every new tool name dispatches without error when subprocess is mocked.

### 1.6 Acceptance criteria

- All new tools callable through CLI and agent with correct RBAC.
- `hpc-pilot reservation create maintenance --nodes node[01-04] --start now --duration 4h --users root` produces a sensible `scontrol create reservation ...` argv.
- `pytest tests/tools/test_slurm.py -q` is green; coverage of `hpc_pilot/tools/slurm.py` ≥ 90%.

---

## Phase 2 — Warewulf bootstrap & node lifecycle

**Depends on:** Phase 0.
**Goal:** Restore the bare-metal provisioning surface that was dropped in v1.0.0. The agent must be able to bring up a node from PXE to first Slurm registration.

### 2.0 Status

**Overall:** ⬜ Not started

**Tools (20 new + 1 revert helper):**
- [ ] `hpc_warewulf_image_import`
- [ ] `hpc_warewulf_image_build` (with `spec_hash` cache, §2.2)
- [ ] `hpc_warewulf_image_delete`
- [ ] `hpc_warewulf_node_show`
- [ ] `hpc_warewulf_node_add`
- [ ] `hpc_warewulf_node_set`
- [ ] `hpc_warewulf_node_delete`
- [ ] `hpc_warewulf_profile_list`
- [ ] `hpc_warewulf_profile_set`
- [ ] `hpc_warewulf_overlay_list`
- [ ] `hpc_warewulf_overlay_edit` (managed git config repo, §2.3)
- [ ] `hpc_warewulf_overlay_build`
- [ ] `hpc_warewulf_overlay_revert`
- [ ] `hpc_warewulf_configure_dhcp` (idempotent, §2.4)
- [ ] `hpc_warewulf_configure_tftp`
- [ ] `hpc_warewulf_configure_nfs`
- [ ] `hpc_warewulf_server_status`
- [ ] `hpc_warewulf_power_status`
- [ ] `hpc_warewulf_power_on`
- [ ] `hpc_warewulf_power_off`

**Skills & policy:**
- [ ] `bootstrap-cluster.yaml` skill (§2.5, 10 steps CPU / 12 GPU)
- [ ] `~/.hpc-pilot/policy/warewulf.yaml` policy file (§2.6)
- [ ] DHCP `wwctl configure` runs only when warewulf.conf changed
- [ ] External-edit detection on `warewulf.conf` (sha256 mismatch warning)

**Definition of Done:**
- [ ] `tests/tools/test_warewulf.py` ≥ 40 tests including image-hash determinism
- [ ] `bootstrap-cluster` skill demonstrated on a Vagrant rig (integration test)
- [ ] Acceptance scenarios §2.8 demonstrated
- [ ] `mypy --strict` clean, `ruff` clean
- [ ] Status dashboard row advanced to ✅ Done

### 2.1 New tools

| Tool | Underlying | RBAC | Notes |
|---|---|---|---|
| `hpc_warewulf_image_list` (exists) | `wwctl image list` | viewer | keep |
| `hpc_warewulf_image_import` | `wwctl image import` | admin | source URI + name |
| `hpc_warewulf_image_build` | `wwctl image build` | admin | computes SHA256 spec_hash; supports CPU + GPU container exec steps |
| `hpc_warewulf_image_delete` | `wwctl image delete` | admin | |
| `hpc_warewulf_node_list` (exists) | `wwctl node list` | viewer | keep |
| `hpc_warewulf_node_show` | `wwctl node show <name>` | viewer | structured parse |
| `hpc_warewulf_node_add` | `wwctl node add` | admin | inputs: name, mac, ipaddr, profile |
| `hpc_warewulf_node_set` | `wwctl node set` | admin | dry_run mandatory |
| `hpc_warewulf_node_delete` | `wwctl node delete` | admin | |
| `hpc_warewulf_profile_list` | `wwctl profile list` | viewer | |
| `hpc_warewulf_profile_set` | `wwctl profile set` | admin | dry_run |
| `hpc_warewulf_overlay_list` | `wwctl overlay list` | viewer | |
| `hpc_warewulf_overlay_edit` | edits managed config repo + `wwctl overlay build` | admin | see §2.3 |
| `hpc_warewulf_overlay_build` | `wwctl overlay build` | operator | rebuild only |
| `hpc_warewulf_configure_dhcp` | rewrites `/etc/warewulf/warewulf.conf` + `wwctl configure dhcp` | superadmin | see §2.4 |
| `hpc_warewulf_configure_tftp` | same pattern | superadmin | |
| `hpc_warewulf_configure_nfs` | same pattern | superadmin | |
| `hpc_warewulf_server_status` | `wwctl server status` + `systemctl is-active warewulfd` | viewer | |
| `hpc_warewulf_power_status` | `wwctl power status <n>` | viewer | |
| `hpc_warewulf_power_on/off/reset` | `wwctl power *` | admin | dry_run; `reset` already exists |

### 2.2 Image build with spec_hash

```python
def hpc_warewulf_image_build(
    name: str,
    base: str,                       # e.g. "rockylinux:9"
    exec_steps: list[str],           # shell commands run inside the container
    *,
    gpu: bool = False,               # adds CUDA driver install steps
    cluster: str = "default",
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Build a Warewulf container image. Returns
        {"name": ..., "spec_hash": "<sha256>", "size_mb": ..., "log_path": ...}
    spec_hash is SHA256 over (base + sorted exec_steps + gpu flag), so identical
    inputs produce identical hashes and we can skip rebuilds.
    """
```

Build directory: `~/.hpc-pilot/warewulf/builds/<name>/<spec_hash>/`. If a build with the same hash exists, return cached metadata (no rebuild). Tag the resulting `wwctl image` with the hash in its description field.

### 2.3 Overlay edit contract

Overlays stage files in the **managed config repo** (`~/.hpc-pilot/warewulf/overlays/<overlay>/`) before invoking `wwctl overlay build`. The tool:

1. Acquires a file lock on the overlay dir.
2. Writes new/updated files (validated paths — no `..` traversal).
3. Commits to a git repo at the overlay dir root (auto-init on first use).
4. Runs `wwctl overlay build <overlay>`.
5. Returns `{"overlay": ..., "files_changed": [...], "commit": "<sha>", "rebuild_returncode": 0}`.

This gives reversibility: `hpc_warewulf_overlay_revert` (operator) checks out a prior commit and rebuilds.

### 2.4 DHCP/TFTP/NFS configuration

Warewulf 4.x `wwctl configure <svc>` takes NO flags — it reads `/etc/warewulf/warewulf.conf`. The tool:

1. Reads the **managed** `~/.hpc-pilot/warewulf/warewulf.conf` (a copy with our edits).
2. Applies typed updates (e.g. `dhcp.range_start`, `dhcp.range_end`, `dhcp.template`).
3. If unchanged → no-op, no `wwctl` call.
4. If changed → commit to git, copy to `/etc/warewulf/warewulf.conf` (atomic write), run `wwctl configure <svc>`.
5. **Never** invent flags like `--cidr`, `--range-start`. (The earlier project memory flagged this as a past mistake.)

Default gateway lives on the Warewulf profile (`hpc_warewulf_profile_set network=...`), NOT in the DHCP service config.

### 2.5 Day-0 bootstrap workflow

Skill `bootstrap-cluster.yaml` (consumed by Phase 0.7 runner):

```yaml
name: bootstrap-cluster
required_role: superadmin
inputs:
  - {name: cidr, type: string, required: true}
  - {name: image_base, type: string, default: "rockylinux:9"}
  - {name: gpu, type: boolean, default: false}
  - {name: node_count, type: integer, required: true}
steps:
  - hpc_warewulf_configure_tftp:    {}
  - hpc_warewulf_configure_nfs:     {}
  - hpc_warewulf_configure_dhcp:    {range_start: "...", range_end: "...", template: "default"}
  - hpc_warewulf_image_import:      {name: base, source: "{{ image_base }}"}
  - hpc_warewulf_image_build:       {name: compute, base: base, exec_steps: [...], gpu: "{{ gpu }}"}
  - hpc_warewulf_profile_set:       {name: default, image: compute, network: {gateway: "..."}}
  - {builtin: for_each, var: i, range: [1, "{{ node_count }}"], do: [
       {hpc_warewulf_node_add: {name: "node{{ i }}", mac: "...", ipaddr: "..."}}
     ]}
  - hpc_warewulf_overlay_build:     {overlay: wwinit}
  - hpc_warewulf_server_status:     {}
```

10 steps for CPU-only, 12 with GPU (extra exec steps + verification).

### 2.6 Policy file

`~/.hpc-pilot/policy/warewulf.yaml` declares default-approval behavior:

```yaml
hpc_warewulf_image_build:
  auto_approve_when:
    blast_radius: "<= 1 node"      # image not yet assigned to nodes
hpc_warewulf_configure_dhcp:
  approval: always_required
hpc_warewulf_node_add:
  auto_approve_when:
    cluster: staging
```

The dispatch layer consults policy before invoking; if approval required, it triggers the Phase 8.1 out-of-band approval workflow.

### 2.7 Tests

`tests/tools/test_warewulf.py` — 40+ tests covering image hash determinism, overlay git commit, DHCP idempotency (no `wwctl` call when conf unchanged), node add validation.

### 2.8 Acceptance criteria

- `hpc-pilot skill run bootstrap-cluster --cidr 10.0.0.0/24 --node-count 4` brings up a cluster end-to-end on a Vagrant test rig.
- Building the same image spec twice returns the cached `spec_hash` without re-running container builds.
- Editing `warewulf.conf` outside HPC Pilot is detected (sha256 mismatch on read) and surfaced as a warning, not silently overwritten.

---

## Phase 3 — Spack package lifecycle

**Depends on:** Phase 0.
**Goal:** Move beyond read-only Spack queries to env management, installs, and binary mirror operations.

### 3.0 Status

**Overall:** ⬜ Not started

**Tools (13 new):**
- [ ] `hpc_spack_env_create`
- [ ] `hpc_spack_env_delete`
- [ ] `hpc_spack_env_concretize` (lockfile diff, §3.3)
- [ ] `hpc_spack_env_install` (async via `jobs.py`, §3.2)
- [ ] `hpc_spack_env_status`
- [ ] `hpc_spack_install_spec`
- [ ] `hpc_spack_uninstall`
- [ ] `hpc_spack_mirror_list`
- [ ] `hpc_spack_mirror_add`
- [ ] `hpc_spack_buildcache_push`
- [ ] `hpc_spack_buildcache_update_index`
- [ ] `hpc_spack_module_refresh`
- [ ] `hpc_spack_compiler_find`

**Infrastructure:**
- [ ] `hpc_pilot/jobs.py` async job table (`hpc_job_status`, `hpc_job_logs` tools)
- [ ] Job records persisted to `~/.hpc-pilot/jobs/<id>.json`
- [ ] Build logs streamed to `~/.hpc-pilot/logs/spack/<env>/<ts>.log`

**Definition of Done:**
- [ ] `tests/tools/test_spack.py` covers lockfile diff and job-id generation
- [ ] Acceptance scenarios §3.5 demonstrated (async install returns run id)
- [ ] `mypy --strict` clean, `ruff` clean
- [ ] Status dashboard row advanced to ✅ Done

### 3.1 New tools

| Tool | Underlying | RBAC | Notes |
|---|---|---|---|
| `hpc_spack_env_list` (exists) | `spack env list` | viewer | keep |
| `hpc_spack_env_create` | `spack env create` | admin | name + optional manifest path |
| `hpc_spack_env_delete` | `spack env remove` | admin | |
| `hpc_spack_env_concretize` | `spack -e <env> concretize -f` | admin | returns lockfile diff |
| `hpc_spack_env_install` | `spack -e <env> install --no-checksum=false` | admin | streams build log to `~/.hpc-pilot/logs/spack/<env>/<ts>.log` |
| `hpc_spack_env_status` | parses `spack -e <env> spec` | viewer | |
| `hpc_spack_find` (exists) | `spack find -lN -d -e <env>` | viewer | keep |
| `hpc_spack_install_spec` | `spack install <spec>` | admin | single-spec install outside env |
| `hpc_spack_uninstall` | `spack uninstall --dependents` | admin | dry_run mandatory |
| `hpc_spack_mirror_list` | `spack mirror list` | viewer | |
| `hpc_spack_mirror_add` | `spack mirror add` | superadmin | |
| `hpc_spack_buildcache_push` | `spack buildcache push` | admin | requires GPG key path arg |
| `hpc_spack_buildcache_update_index` | `spack buildcache update-index` | admin | |
| `hpc_spack_module_refresh` | `spack module lmod refresh -y` | admin | |
| `hpc_spack_compilers` (exists) | `spack compilers` | viewer | keep |
| `hpc_spack_compiler_find` | `spack compiler find` | admin | |

### 3.2 Long-running install handling

`spack install` can run for hours. Pattern:

```python
def hpc_spack_env_install(env: str, *, cluster="default", dry_run=False) -> dict:
    """
    Returns: {"run_id": "<uuid>", "status": "started", "log_path": "..."}
    The actual subprocess runs in the background; callers poll
    hpc_spack_install_status(run_id) for progress.
    """
```

New module `hpc_pilot/jobs.py` — lightweight job table at `~/.hpc-pilot/jobs/<id>.json` with fields `{cmd, started_at, pid, status, returncode, log_path}`. `hpc_job_status(id)` and `hpc_job_logs(id, tail=200)` are exposed as tools. Reused later for Ansible runs.

### 3.3 Lockfile diff

`hpc_spack_env_concretize` returns:

```python
{"env": "...", "added": [...], "removed": [...], "changed": [...], "lockfile_sha256": "..."}
```

Achieved by snapshotting `spack.lock` before/after and diffing the `concrete_specs` map.

### 3.4 Tests

`tests/tools/test_spack.py` — mock subprocess; verify env-name validation, lockfile diff calculation, job-id generation.

### 3.5 Acceptance criteria

- `hpc-pilot spack install myenv` returns a run id and exits; `hpc-pilot job status <id>` shows live status.
- Agent can answer "what changed in the gpu-stack env after I added cuda@12?" by calling `hpc_spack_env_concretize` and explaining the diff.

---

## Phase 4 — Ansible operations

**Depends on:** Phase 0, Phase 3 (jobs.py).
**Goal:** Make Ansible a first-class drift-detection and remediation surface, not a "run-this-yaml" wrapper.

### 4.0 Status

**Overall:** ⬜ Not started

**Tools (7 new + 1 refactor):**
- [ ] `hpc_ansible_playbook_run` refactored to use `jobs.py` for async execution
- [ ] `hpc_ansible_playbook_check` (`--check --diff`, structured per-host diff)
- [ ] `hpc_ansible_playbook_list`
- [ ] `hpc_ansible_role_list`
- [ ] `hpc_ansible_inventory_from_truth` (Slurm + Warewulf → inventory, §4.2)
- [ ] `hpc_ansible_drift_check`
- [ ] `hpc_ansible_vault_decrypt` (never logs plaintext)
- [ ] `hpc_ansible_run_history`

**Curated drift playbooks (`hpc_pilot/playbooks/drift/`):**
- [ ] `slurm-config-drift.yml`
- [ ] `chrony-sync-drift.yml`
- [ ] `mount-drift.yml`
- [ ] `kernel-version-drift.yml`

**Definition of Done:**
- [ ] Inventory generation is byte-identical for identical inputs
- [ ] `tests/tools/test_ansible.py` covers drift JSON parsing
- [ ] Acceptance scenarios §4.5 demonstrated (drift summary as Markdown table)
- [ ] `mypy --strict` clean, `ruff` clean
- [ ] Status dashboard row advanced to ✅ Done

### 4.1 New tools

| Tool | RBAC | Notes |
|---|---|---|
| `hpc_ansible_playbook_run` (exists) | admin | now uses `jobs.py` for async |
| `hpc_ansible_playbook_check` | admin | `--check --diff`; returns structured diff per host |
| `hpc_ansible_playbook_list` | viewer | enumerates `ansible_dir/playbooks/*.yml` with header metadata |
| `hpc_ansible_role_list` | viewer | enumerates `ansible_dir/roles/*` |
| `hpc_ansible_inventory_generate` (exists) | viewer | keep |
| `hpc_ansible_inventory_from_truth` | admin | NEW — builds inventory from Slurm + Warewulf as source of truth |
| `hpc_ansible_drift_check` | operator | runs a curated drift-detection playbook; returns per-host status |
| `hpc_ansible_vault_decrypt` | admin | wraps `ansible-vault view`; never logs plaintext |
| `hpc_ansible_run_history` | viewer | reads `~/.hpc-pilot/logs/ansible/*.json` |

### 4.2 Inventory from truth

`hpc_ansible_inventory_from_truth(cluster="default")`:

1. Reads `wwctl node list` → set of provisioned nodes with profile.
2. Reads `scontrol show nodes` → set of Slurm-known nodes with partition/feature.
3. Produces YAML inventory:
   ```yaml
   all:
     children:
       gpu_nodes: {hosts: {node03: {}, node04: {}}}   # by feature=gpu
       cpu_nodes: {hosts: {...}}
       partitions:
         interactive: {hosts: {...}}
   ```
4. Writes to `<ansible_dir>/inventory/generated.yml` with header comment "generated by HPC Pilot at <ts>; do not edit".
5. Returns diff vs. previous version.

### 4.3 Curated drift playbooks

Ship in-repo under `hpc_pilot/playbooks/drift/`:

- `slurm-config-drift.yml` — compares `/etc/slurm/slurm.conf` to a canonical hash.
- `chrony-sync-drift.yml` — verifies NTP offset < 100ms across all nodes.
- `mount-drift.yml` — verifies `/home`, `/scratch`, `/apps` are mounted with expected options.
- `kernel-version-drift.yml` — flags nodes whose `uname -r` differs from the image's expected version.

`hpc_ansible_drift_check(which: str = "all")` runs them via `ansible-playbook --check --diff`, parses the JSON output (`ANSIBLE_STDOUT_CALLBACK=json`), and returns a per-host result.

### 4.4 Tests

`tests/tools/test_ansible.py` — mock subprocess, mock filesystem; verify inventory generation determinism, drift parsing.

### 4.5 Acceptance criteria

- Asking the agent "are any nodes drifted?" triggers `hpc_ansible_drift_check`, returns a Markdown table of host × playbook × status.
- Inventory regeneration is idempotent (same input → byte-identical output).

---

## Phase 5 — Observability & metrics

**Depends on:** Phase 0.
**Goal:** Give the agent real telemetry: Prometheus, slurm sdiag, GPU/fabric/storage health, log triage.

### 5.0 Status

**Overall:** ⬜ Not started

**Tools (12 new):**
- [ ] `hpc_metrics_prometheus_query`
- [ ] `hpc_metrics_prometheus_alerts`
- [ ] `hpc_metrics_node_summary`
- [ ] `hpc_gpu_nvidia_smi`
- [ ] `hpc_gpu_dcgm_diag`
- [ ] `hpc_storage_lustre_status`
- [ ] `hpc_storage_mounts`
- [ ] `hpc_fabric_ib_link_status`
- [ ] `hpc_logs_slurmctld_tail`
- [ ] `hpc_logs_slurmd_tail`
- [ ] `hpc_logs_dmesg_xid`
- [ ] `hpc_logs_search`

**Infrastructure:**
- [ ] `httpx` dependency added; Prometheus client in `hpc_pilot/tools/metrics.py`
- [ ] `observability` block added to `config.yaml`
- [ ] `_redact_log_line()` helper; > 10 KB log output summarized before LLM exposure
- [ ] `hpc_cluster_health_check` v2 (§5.3) — fabric/storage/gpu/alerts integrated

**Definition of Done:**
- [ ] Parser snapshot tests for `ibstatus`, `lctl`, `dcgm diag`
- [ ] Property test: redactor never emits a `_SECRET_RE` match
- [ ] Acceptance scenarios §5.6 demonstrated (CPU util query, XID diagnosis)
- [ ] `mypy --strict` clean, `ruff` clean
- [ ] Status dashboard row advanced to ✅ Done

### 5.1 Tools

| Tool | Source | RBAC | Notes |
|---|---|---|---|
| `hpc_metrics_prometheus_query` | Prom HTTP API | viewer | `query`, `start`, `end`, `step` |
| `hpc_metrics_prometheus_alerts` | `/api/v1/alerts` | viewer | active alerts |
| `hpc_metrics_node_summary` | derived | viewer | per-node CPU/mem/GPU util over last N min |
| `hpc_gpu_nvidia_smi` | `nvidia-smi -q -x` | operator (remote SSH to node) | per-GPU temp, util, ECC errors |
| `hpc_gpu_dcgm_diag` | `dcgmi diag -r 1` | admin | runs short diagnostic |
| `hpc_storage_lustre_status` | `lctl get_param` | operator | OST/MDT state |
| `hpc_storage_mounts` | `mount` + `df` | viewer | per-mount free/used |
| `hpc_fabric_ib_link_status` | `ibstatus` / `ibstat` | operator | link rate, state, errors |
| `hpc_logs_slurmctld_tail` | reads `/var/log/slurm/slurmctld.log` | operator | tail + regex filter |
| `hpc_logs_slurmd_tail` | per-node `journalctl -u slurmd` (SSH) | operator | |
| `hpc_logs_dmesg_xid` | per-node `dmesg \| grep -i xid` | operator | GPU XID errors |
| `hpc_logs_search` | `journalctl --since=... \| grep` | operator | with regex validation |

### 5.2 Prometheus config

Extend `config.yaml`:

```yaml
observability:
  prometheus:
    url: http://prometheus.internal:9090
    timeout_sec: 10
  loki:                            # optional, Phase 5.2
    url: http://loki.internal:3100
```

Use `httpx` (new dep). Cache `/api/v1/labels` for 5 min to short-circuit autocompletion-style queries.

### 5.3 Cluster health upgrade (v2)

`hpc_cluster_health_check` extended:

```python
{
  "timestamp": "...",
  "overall": "healthy|degraded|critical",
  "components": {
    "slurm":     {"status": "...", "controller_responsive": true, "sdiag": {...}, "down_nodes": [...]},
    "warewulf":  {"status": "...", "server_active": true, "stale_overlays": [...]},
    "fabric":    {"status": "...", "links_down": [...]},
    "storage":   {"status": "...", "lustre_evictions_last_hour": 0},
    "gpu":       {"status": "...", "xid_errors_last_hour": 0},
    "metrics":   {"status": "...", "prometheus_reachable": true, "alerts_firing": 2},
  },
  "issues": ["..."],
  "recommendations": ["..."]
}
```

### 5.4 Log redaction

Slurm/syslog tails may contain user-supplied paths and env. `_redact_log_line()` strips:
- Anything matching `password=`, `token=`, `secret=` (re-use `_SECRET_RE`).
- Email addresses (configurable).
- Output is **never** raw-passed to the LLM if > 10 KB — summarize via heuristics first (count, top N by frequency).

### 5.5 Tests

- Mock httpx responses for Prometheus.
- Snapshot tests for parsers (`parse_ibstatus`, `parse_lctl_state`, `parse_dcgm_diag`).
- Property: redactor never emits a line containing a `_SECRET_RE` match.

### 5.6 Acceptance criteria

- "Show me CPU utilization on node[01-04] for the past hour" → agent calls `hpc_metrics_prometheus_query` with a sensible PromQL and returns a Markdown table.
- "Why is node03 in DRAIN?" → agent calls `hpc_slurm_node_status` → sees `Reason=...XID...` → calls `hpc_logs_dmesg_xid` → returns a diagnosis with cited XID code.

---

## Phase 6 — Incident response & runbooks

**Depends on:** Phase 0.7 (skill runner), Phase 1, Phase 5.
**Goal:** Codify the on-call playbook so the agent can drive incident response with auditable steps.

### 6.0 Status

**Overall:** ⬜ Not started

**Built-in skills (`hpc_pilot/skills/builtin/`):**
- [ ] `drain-and-patch-node.yaml`
- [ ] `triage-node-down.yaml`
- [ ] `triage-gpu-xid.yaml` (with XID code lookup table)
- [ ] `triage-fabric-flap.yaml`
- [ ] `triage-scheduler-stuck.yaml`
- [ ] `rolling-reboot-partition.yaml`
- [ ] `postmortem-collect.yaml`

**Framework features:**
- [ ] `hpc-pilot skill list` enumerates built-in + `~/.hpc-pilot/skills/`
- [ ] `hpc_skill_describe(name)` agent tool
- [ ] `hpc_skill_run(name, inputs)` agent tool
- [ ] Pause/resume across HPC Pilot restarts (state in `~/.hpc-pilot/skills/runs/<id>.json`)
- [ ] `on_failure: pause` honored mid-runbook
- [ ] Postmortem report template populated from audit log + sacct + LLM summarization

**Definition of Done:**
- [ ] `tests/skills/test_runner.py` — happy/pause-resume/failure/RBAC
- [ ] Acceptance scenarios §6.6 demonstrated (DOWN node triggers triage skill end-to-end)
- [ ] Skill `required_role` enforced before any step
- [ ] `mypy --strict` clean, `ruff` clean
- [ ] Status dashboard row advanced to ✅ Done

### 6.1 Built-in skills

Ship in `hpc_pilot/skills/builtin/`:

| Skill | Trigger | Steps |
|---|---|---|
| `drain-and-patch-node` | scheduled maintenance | snapshot → drain → wait jobs → patch → reboot → verify → resume |
| `triage-node-down` | `NodeState=DOWN` detected | sdiag → slurmctld tail → slurmd tail → dmesg → fabric link → recommendation |
| `triage-gpu-xid` | XID error in dmesg | identify XID code → consult lookup table → dcgm diag → recommend drain/RMA |
| `triage-fabric-flap` | link state oscillation | ibstatus → counter delta over 60s → recommend port reset or cable swap |
| `triage-scheduler-stuck` | sdiag last-cycle > 60s | sdiag → backfill queue depth → slurmctld errors → suggest reconfigure |
| `rolling-reboot-partition` | OS upgrade | for each node in partition: drain → wait → reboot → verify → resume |
| `postmortem-collect` | post-incident | gather slurmctld logs, sacct of affected jobs, sdiag snapshot, into `~/.hpc-pilot/incidents/<id>/` |

### 6.2 Skill discovery

`hpc-pilot skill list` shows all skills (built-in + user-defined in `~/.hpc-pilot/skills/`). Agent tool `hpc_skill_describe(name)` returns the YAML for the LLM to reason about; `hpc_skill_run(name, inputs)` executes.

### 6.3 Pause/resume

Skills can `pause` between steps (waiting for human approval — see Phase 8.1) or `on_failure: pause` to stop the runbook mid-flight. `hpc-pilot skill resume <run_id>` continues from the paused step. State stored in `~/.hpc-pilot/skills/runs/<id>.json`.

### 6.4 Postmortem report generation

`postmortem-collect` skill produces `~/.hpc-pilot/incidents/<id>/REPORT.md` from a template with: timeline (built from audit log), affected nodes/jobs (from sacct), root cause (LLM-summarized from logs), action items (LLM-suggested).

### 6.5 Tests

`tests/skills/test_runner.py` — happy path, pause-resume, failure mid-step, input templating, RBAC at skill level (skill `required_role` enforced before any step runs).

### 6.6 Acceptance criteria

- A node going DOWN triggers (via Phase 5.X alert hook) `triage-node-down`, which runs to completion and posts a structured incident report to the audit log.
- All skill runs are resumable across HPC Pilot restarts.

---

## Phase 7 — Multi-cluster federation

**Depends on:** Phase 0.5 (cluster context), Phase 1, Phase 2.
**Goal:** One HPC Pilot instance manages multiple clusters (prod / staging / dev), executing remotely via SSH.

### 7.0 Status

**Overall:** ⬜ Not started

**Items:**
- [ ] `_run()` honors `Cluster.ssh` (wraps argv in SSH with `BatchMode=yes`, §7.1)
- [ ] ControlMaster opt-in via `cluster.ssh.control_path`
- [ ] `hpc_multi_query(tool, args, clusters)` agent tool with partial-success semantics
- [ ] System prompt addendum about cluster context (§7.3)
- [ ] CLI: `--cluster NAME` on every subcommand; `--cluster all` aggregates
- [ ] `$HPC_PILOT_CLUSTER` env var override honored

**Definition of Done:**
- [ ] `tests/test_clusters.py` covers SSH argv composition + multi-query aggregation
- [ ] Acceptance scenarios §7.5 demonstrated (`--cluster all health` + idle-GPU comparison)
- [ ] No tool reads `os.environ` directly for cluster wiring (regression check)
- [ ] `mypy --strict` clean, `ruff` clean
- [ ] Status dashboard row advanced to ✅ Done

### 7.1 Remote execution

`_run()` honors `Cluster.ssh`:

```python
def _run(cmd, *, cluster: Cluster, timeout=60, dry_run=False) -> str:
    if cluster.ssh is None:
        return _run_local(cmd, timeout, dry_run)
    ssh_cmd = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
        "-i", os.path.expanduser(cluster.ssh.key),
        f"{cluster.ssh.user}@{cluster.ssh.host}",
        "--", *map(shlex.quote, cmd),
    ]
    return _run_local(ssh_cmd, timeout + 5, dry_run)
```

ControlMaster is opt-in via `cluster.ssh.control_path`.

### 7.2 Cross-cluster query aggregation

New tool `hpc_multi_query(tool: str, args: dict, clusters: list[str])`:

- Runs `tool` in parallel against each cluster (asyncio thread pool).
- Returns `{cluster_name: result_or_error}`.
- RBAC: each cluster checked independently; failures partial-success.

### 7.3 Cluster targeting in agent

System prompt addendum:

> Cluster context: the user is currently focused on `{cluster}`. When the user
> says "the staging cluster" or "both clusters", call `hpc_multi_query` or set
> `cluster=` explicitly on each tool call. Always echo which cluster a result
> came from when summarizing.

CLI: `hpc-pilot --cluster staging nodes`, `hpc-pilot --cluster all health`.

### 7.4 Tests

`tests/test_clusters.py` — config parsing, SSH argv composition (mocked), multi-query aggregation.

### 7.5 Acceptance criteria

- `hpc-pilot --cluster all health` returns a summary table comparing prod/staging.
- Agent answers "which cluster has more idle GPUs right now?" correctly by calling both clusters.

---

## Phase 8 — Production hardening

**Depends on:** all prior phases (most items can be done in parallel within Phase 8).

### 8.0 Status

**Overall:** ⬜ Not started

**Sub-items (can ship independently):**
- [ ] 8.1 Out-of-band approval workflow (Slack / PagerDuty / email; `hpc-pilot approve <id>`)
- [ ] 8.2 Gateway daemon (PID file, `--stop`, systemd unit at `packaging/systemd/`)
- [ ] 8.3 Pluggable audit sinks (`FileSink`, `SyslogSink`, `HttpSink`); per-sink failure isolation
- [ ] 8.4 Web UI (FastAPI on :8000; `/chat`, `/audit`, `/skills`, `/approvals`, `/clusters/<n>/health`)
- [ ] 8.5 Secrets via Vault (lazy fetch, in-memory cache, hourly refresh)
- [ ] 8.6 Conversation summarization wired into `agent.py` for real-world load

**Definition of Done:**
- [ ] `tests/test_approvals.py` covers pending → approved → resumed, expiry, reject
- [ ] `tests/test_gateway_daemon.py` covers PID lifecycle
- [ ] `tests/test_audit_sinks.py` proves one-sink-fails-others-succeed
- [ ] `tests/test_webui.py` smoke tests via FastAPI test client
- [ ] Acceptance scenarios §8.8 demonstrated (Telegram → Slack approval → execute → audit chain)
- [ ] Open questions in the "Open questions" section answered before this phase starts
- [ ] `mypy --strict` clean, `ruff` clean
- [ ] Status dashboard row advanced to ✅ Done

### 8.1 Out-of-band approval workflow

For tools/skill steps marked `approval: required`:

1. Tool call enters `APPROVAL_PENDING` state, returns immediately with a request id.
2. `hpc_pilot/approvals.py` writes a request to `~/.hpc-pilot/approvals/<id>.json`.
3. Notification channels (configurable):
   - Slack webhook → posts message with Approve / Reject buttons (uses `interactivity` block).
   - PagerDuty → opens an "informational" incident.
   - Email → SMTP with a signed approval token.
4. Approver decisions hit `hpc-pilot approve <id> [--reject]` (CLI) or a small HTTPS receiver embedded in the gateway (Phase 8.4).
5. On approval, the tool/skill resumes; on reject or 24h timeout, it aborts.

Schema:

```python
@dataclass
class ApprovalRequest:
    id: str                          # uuid
    tool: str
    args: dict[str, Any]
    requester_actor: str
    requester_role: str
    cluster: str
    risk_summary: str                # LLM-generated 1-paragraph "what this will do"
    created_at: float
    expires_at: float
    status: Literal["pending", "approved", "rejected", "expired"]
    approver: str | None
    decided_at: float | None
```

Audit entries on creation, decision, and execution-after-approval.

### 8.2 Gateway daemon

- `hpc-pilot gateway --start` writes a PID file at `~/.hpc-pilot/gateway.pid`.
- `hpc-pilot gateway --stop` sends `SIGTERM` to that PID, waits up to 10s, escalates to `SIGKILL`.
- Ship `packaging/systemd/hpc-pilot-gateway.service`:
  ```ini
  [Service]
  Type=simple
  User=hpc-pilot
  EnvironmentFile=/etc/hpc-pilot/gateway.env
  ExecStart=/usr/bin/hpc-pilot gateway --start
  Restart=on-failure
  RestartSec=10s
  ```
- `--status` checks the PID file and verifies the process is alive (signal 0).

### 8.3 Audit log shipping

`hpc_pilot/audit.py` gains a pluggable sink:

```python
class AuditSink(Protocol):
    def write(self, record: dict[str, Any]) -> None: ...

class FileSink(AuditSink): ...        # current behavior
class SyslogSink(AuditSink): ...      # facility=LOG_LOCAL5
class HttpSink(AuditSink): ...        # POST to Splunk HEC / Elastic / OTel
```

Configured via:

```yaml
audit:
  sinks:
    - {type: file, path: ~/.hpc-pilot/logs/audit.jsonl}
    - {type: syslog}
    - {type: http, url: ..., headers: {Authorization: "Splunk ..."}}
```

All sinks called in order; one failure does not block others. Sinks must not raise.

### 8.4 Web UI

`hpc-pilot webui --start` launches a FastAPI app at `:8000`:

- `/chat` — same Claude tool-use loop as CLI, but with a browser-friendly UI (server-sent events for streaming).
- `/audit` — paginated audit log viewer with filters.
- `/skills` — list, run, view in-flight skill runs.
- `/approvals` — pending approval queue with Approve/Reject buttons (for §8.1).
- `/clusters/<name>/health` — health dashboard.

Auth: SSO via OIDC (env vars `HPC_PILOT_OIDC_*`), with role mapped from the OIDC `groups` claim.

### 8.5 Secrets management

Replace `.env` with optional Vault integration:

```yaml
secrets:
  backend: vault                     # or "env" (default)
  vault:
    addr: https://vault.example.com
    role_id_path: /etc/hpc-pilot/role_id
    secret_id_path: /etc/hpc-pilot/secret_id
    secret_paths:
      anthropic_api_key: secret/hpc-pilot/anthropic
      telegram_bot_token: secret/hpc-pilot/telegram
```

Lazy fetch + cache in-memory only; never write to disk. Refresh hourly.

### 8.6 Conversation summarization tuning

(Already specced in Phase 0.6 — actually wire it up here when the agent is real-world-tested at scale.)

### 8.7 Tests

- `tests/test_approvals.py` — pending → approved → resumed; expiry; reject.
- `tests/test_gateway_daemon.py` — PID file lifecycle.
- `tests/test_audit_sinks.py` — one-sink-fails-others-succeed property.
- `tests/test_webui.py` — FastAPI test client smoke tests.

### 8.8 Acceptance criteria

- An admin can run a destructive operation from Telegram, an SRE approves in Slack within 5 min, and the operation completes — entire chain captured in audit log.
- Gateway survives `systemctl restart hpc-pilot-gateway`.
- Web UI lists in-flight skill runs in real time.

---

## Cross-cutting acceptance: end-to-end scenarios

These must work end-to-end after all phases ship:

1. **Bring-up.** "Bootstrap a 16-node CPU cluster on the 10.0.0.0/24 subnet using Rocky 9." → bootstrap-cluster skill runs, returns a health-check pass.
2. **Reservation.** "Reserve node[01-04] for the FOO project from Monday 9am for 24 hours." → `hpc_slurm_reservation_create` invoked with parsed dates.
3. **Triage.** "node07 is DOWN, what happened?" → agent runs triage-node-down skill → identifies cause from logs → recommends drain+RMA or reboot.
4. **Maintenance.** "Patch gpu_nodes partition and roll through them safely." → rolling-reboot-partition skill on `gpu_nodes`, with Slack approval between each node.
5. **Capacity report.** "How much GPU time did the chem account use last month?" → `hpc_slurm_usage_report` parsed and explained.
6. **Drift.** "Are all compute nodes running the right kernel?" → `hpc_ansible_drift_check kernel-version-drift` → table of host × actual vs. expected.
7. **Cross-cluster.** "Compare scheduler health on prod vs. staging." → `hpc_multi_query hpc_slurm_sdiag prod,staging` → side-by-side.
8. **Audit accountability.** Every action above appears in `audit.jsonl` with actor, role, cluster, args, returncode, AND any denied attempts.

---

## Sequencing & parallelism

Tier 1 (no dependencies beyond Phase 0):
- Phase 1 (Slurm)
- Phase 2 (Warewulf)
- Phase 3 (Spack)

Tier 2 (depends on Tier 1 outputs):
- Phase 4 (Ansible — needs Phase 3's jobs.py)
- Phase 5 (Observability)

Tier 3:
- Phase 6 (Runbooks — composes Phases 1, 4, 5)
- Phase 7 (Multi-cluster — relies on Phase 0.5 plumbing maturing under Phases 1–2)

Tier 4:
- Phase 8 (Hardening — touches everything)

Recommended order for a single implementer: 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8. Phases 1–3 can be parallelized across agents.

---

## Per-phase Definition of Done

A phase is **done** when:

1. All new tools registered in `TOOL_SCHEMAS`, `TOOL_MIN_ROLE`, and the dispatch table.
2. All new CLI subcommands documented in `--help` and the root README.
3. `pytest tests/ -q` is green; coverage of new modules ≥ 85%.
4. `mypy --strict hpc_pilot/` passes.
5. `ruff check .` is clean.
6. `docs/ARCHITECTURE.md` updated where the architecture changed.
7. The phase's acceptance-criteria scenarios are demonstrated either by an integration test (against a containerized Slurm/Warewulf rig in `tests/integration/`) or by a screencast linked from the phase's PR description.
8. No `subprocess.run(..., shell=True)` introduced anywhere.
9. No tool reads `os.environ` directly for cluster wiring (must go through `Cluster`).
10. No new code path bypasses the `dispatch.invoke()` funnel (every tool call goes through RBAC + audit).

---

## Out of scope (explicitly)

- Replacing Slurm, Warewulf, Spack, or Ansible.
- A non-Claude LLM backend. (The agent loop is Anthropic-specific by design — porting to other vendors is a separate effort.)
- Job submission UX (`sbatch` wrappers). HPC Pilot is for **administrators**, not job-submitting users. Read-only job queries are in scope; submitting jobs is not.
- Replacing Prometheus/Grafana. We query them; we don't replace them.
- Cost/billing dashboards. Could be added later via `sreport` parsing but not part of this plan.

---

## Open questions for the human owner

(Answer before Phase 8 starts.)

1. **Approval channel of record.** Slack vs. PagerDuty vs. both? Drives 8.1 dependency surface.
2. **Secret backend.** Vault, AWS Secrets Manager, or stay on `.env`? Drives 8.5.
3. **Web UI auth.** OIDC provider — Okta? Google Workspace? Drives 8.4.
4. **Container runtime for Warewulf builds.** podman vs. docker vs. apptainer? Drives Phase 2.2 build steps.
5. **Audit retention.** Days on local disk before shipping? Drives 8.3 rotation policy.
