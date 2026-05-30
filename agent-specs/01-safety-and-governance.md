# 01 — Safety & Governance

The guardrail layer. Sits between tools and execution. Implements diffs, the approval
gate, the policy engine, blast-radius caps, and rollback.

---

## 1. Responsibilities

For every mutating tool call, before execution, decide one of: **auto-run**, **require
approval**, or **deny** — and after execution, guarantee a revert path.

---

## 2. Diff model

A `Diff` is a structured, human-renderable preview of what a tool *would* change. Tools
build it in step 4 of the execution contract (spec 00 §3.4).

```python
class Change(BaseModel):
    target: str            # "qos/normal", "node/gpu01", "image/gpu-rocky9"
    field: str | None      # "max_wall_min"
    before: str | None
    after: str | None
    op: str                # create|modify|delete|build|drain|resume

class Diff(BaseModel):
    changes: list[Change]
    config_diff: str | None        # unified git diff if config files change
    commands_preview: list[list[str]]   # argv that WOULD run (redacted)
    blast_radius: int              # #entities affected
    reversible: bool
    revert_hint: str | None
    def render(self) -> str: ...   # pretty text for CLI/chat approval prompt
```

When `dry_run=True`, the tool returns `ToolResult(status=DRY_RUN, diff=diff)` and executes
nothing. Dry-run for each domain maps to a real no-op check:
- Slurm: `sacctmgr -i ... ` is NOT used; instead read current via `sacctmgr show ... -P`
  and compute the delta. For `slurm.conf` edits, validate with `slurmctld -t -f <tmp>`.
- Warewulf: `wwctl <...> --help`/inventory read; never call `build` in dry-run.
- Ansible: `ansible-playbook --check --diff`.
- Spack: `spack spec` / `spack concretize` (no install).

---

## 3. Approval gate / policy evaluation

```python
class Gate(BaseModel):
    requires_approval: bool
    denied: bool
    reason: str | None
    approved: bool = False
    approver: str | None = None

def evaluate(tool_meta, inp, diff: Diff, actor_role: Role) -> Gate:
    # 1. RBAC: if not authorize(actor_role, capability) -> denied
    # 2. policy engine: run all matching policy rules (see §4); any DENY -> denied
    # 3. blast radius: if diff.blast_radius > MAX_BLAST_RADIUS_AUTO -> requires_approval
    # 4. risk tier:
    #      READ                -> auto
    #      LOW                 -> auto if in-policy
    #      MEDIUM              -> auto only if a policy rule explicitly AUTO-allows; else approval
    #      HIGH                -> always approval
    # 5. maintenance window / blackout checks (§4)
```

### Approval backends (`APPROVAL_BACKEND`)

`hpc_agent/safety/approval.py` exposes `request_approval(gate, diff, actor) -> Gate`:
- `cli`: print `diff.render()`, read y/N from operator.
- `slack`: post message with Approve/Deny buttons; block until response or timeout.
- `api`: create a pending-approval record; tool returns `NEEDS_APPROVAL` and the
  workflow resumes when approved (see spec 02 §5 resumable plans).

Approvals are single-use, bound to a specific `audit_id` + diff hash. A changed diff
invalidates a prior approval.

---

## 4. Policy engine

Declarative YAML rules in `$CONFIG_REPO/policy/*.yaml`, evaluated top-down.

```yaml
# policy/slurm.yaml
- id: qos-wall-cap
  match: { tool: "slurm.manage_qos" }
  assert:
    max_wall_min: { "<=": 4320 }          # nobody gets > 3 days via the agent
  on_violation: deny
  message: "QOS wall time above 3d requires manual sacctmgr by an admin."

- id: qos-extend-autoallow
  match: { tool: "slurm.manage_qos", op: "modify" }
  assert:
    max_wall_min: { "<=": 2880 }
    max_tres.gpu: { "<=": 16 }
  effect: auto                            # within these bounds, no human approval

- id: account-create-approval
  match: { tool: "slurm.extend_account", op: "create" }
  effect: require_approval

# policy/global.yaml
- id: blackout-window
  match: { risk: ["medium","high"] }
  when: { time_in: "Mon 02:00-04:00 America/Chicago" }   # backup window
  on_violation: deny
  message: "Blocked during nightly backup window."

- id: node-blast-cap
  match: { domain: "warewulf" }
  assert: { blast_radius: { "<=": 8 } }
  on_violation: require_approval
```

Engine spec:
- `match` filters by tool/domain/risk/op (all keys ANDed).
- `assert` checks fields of the tool input or diff via comparison ops (`<=`, `>=`, `==`,
  `in`, `regex`). Dotted paths index into nested input (e.g. `max_tres.gpu` parses the
  TRES string).
- `effect: auto` upgrades an in-policy MEDIUM action to auto-run.
- `effect: require_approval` / `on_violation: deny|require_approval`.
- Site admins edit YAML; engine hot-reloads on change. Unit-test every rule.

---

## 5. Rollback

Two revert mechanisms:

1. **Config revert** (Slurm conf, overlays, spack.yaml, playbooks): every mutating tool
   snapshots `configrepo.snapshot()` before editing and records the commit. `revert(audit_id)`
   does `configrepo.rollback(pre_snapshot)` then re-applies the live action that makes
   config effective (e.g. `scontrol reconfigure`, `wwctl overlay build`).
2. **State revert** for actions without config files (e.g. `sacctmgr` rows): each tool
   records an **inverse command** in the audit event (`revert_argv`). `revert(audit_id)`
   replays the inverse (e.g. modify QOS back to prior values; resume a drained node).

```python
def revert(audit_id: str, actor: str) -> ToolResult: ...
```

Irreversible ops (`Diff.reversible=False`, e.g. permanent deletes) are **prohibited** per
spec 00; the agent refuses and instructs the human to do them manually.

---

## 6. Blast-radius enforcement

`blast_radius` from the `@tool` decorator (spec 00 §3.1) is recomputed against the actual
input. Exceeding `MAX_BLAST_RADIUS_AUTO` forces approval; exceeding a hard per-domain cap
(policy) denies and tells the operator to batch the action.

## 7. Acceptance criteria

- [ ] Every mutating tool returns a populated `Diff` in dry-run mode and executes nothing.
- [ ] Policy YAML loads, hot-reloads, and `evaluate()` returns correct Gate for a table of
      fixture cases (auto / approval / deny).
- [ ] An approved action whose diff later changes is re-gated (approval invalidated).
- [ ] `revert(audit_id)` restores prior config commit AND prior live state for a sample
      QOS-modify and a node-drain.
- [ ] Blackout-window rule denies a MEDIUM action inside the window, allows outside.
