# 04 — Configuration Management Tools (Ansible)

Composes and applies configuration from a **curated, vetted role library** — the agent
never authors freeform tasks. All applies are `--check`-first.

Sudoers: the agent invokes `ansible-playbook` as `hpcagent`; privilege escalation to
target nodes uses Ansible `become` with a dedicated key, not local sudo.

---

## 1. Role library (`roles/`)

Pre-written, reviewed roles the agent selects from. Each role has documented variables and
is `ansible-lint`-clean. Minimum catalog:

| Role | Purpose | Key vars |
|------|---------|----------|
| `common` | hostname, timezone, base pkgs | `timezone` |
| `chrony` | time sync (critical for munge/slurm) | `ntp_servers` |
| `munge` | install + distribute munge key | `munge_key_ref` (secret store ref) |
| `slurm_client` | slurmd config, gres.conf | `slurm_conf_ref`, `gres` |
| `nfs_mounts` | mount /home, /scratch, /apps | `mounts[]` |
| `sssd_ldap` | directory auth | `ldap_uri`, `base_dn` |
| `firewalld` | scheduler/munge ports | `allowed_ports[]` |
| `node_exporter` | Prometheus metrics | `port` |
| `dcgm_exporter` | GPU metrics (GPU nodes) | — |
| `spack_view` | mount/link spack views & modules | `view_path` |

Adding a new role is a **human PR** to `roles/`, not an agent action.

---

## 2. Tools

### 2.1 `ansible.compose_playbook`
Risk: LOW (writes a playbook file, applies nothing).
```python
class ComposePlaybookIn(BaseModel):
    name: str
    target_group: str               # inventory group, e.g. "compute_gpu"
    roles: list[str]                # must all exist in roles/ (validated)
    vars: dict[str, object] = {}    # validated against each role's arg spec
    dry_run: bool = True
class ComposePlaybookOut(BaseModel):
    playbook_path: str; resolved_roles: list[str]
```
Procedure: validate every role exists and that `vars` satisfy roles' `meta/argument_specs`.
Render a playbook from a Jinja template into `$ANSIBLE_DIR/playbooks/<name>.yml`, stage in
config repo. Reject unknown roles/vars (`ErrorKind.PRECONDITION`).

### 2.2 `ansible.lint_playbook`
Risk: READ. Runs `ansible-lint <playbook>` and `ansible-playbook --syntax-check`.
Must pass before any apply (`run_playbook` calls it internally and refuses on failure).

### 2.3 `ansible.run_playbook`
Risk: MEDIUM (HIGH if `target_group` resolves to > MAX_BLAST_RADIUS nodes).
```python
class RunPlaybookIn(BaseModel):
    playbook: str                   # name or path under $ANSIBLE_DIR
    limit: str | None = None        # further restrict hosts
    extra_vars: dict = {}
    dry_run: bool = True            # maps to --check --diff
class RunPlaybookOut(BaseModel):
    changed_hosts: list[str]; ok: int; changed: int; failed: int; unreachable: int
```
Procedure:
1. `lint_playbook` (refuse on failure).
2. Resolve hosts from inventory; `blast_radius = host count`.
3. Build Diff from a `--check --diff` run; the per-host changes populate `Diff.changes`.
4. Dry-run returns that Diff. Apply runs without `--check`, parses the JSON callback
   (`ANSIBLE_STDOUT_CALLBACK=json`) into `RunPlaybookOut`.
5. Record inverse where meaningful (config revert via git); note Ansible applies are
   reversible only by re-applying prior config — record the pre-change config commit.

### 2.4 `ansible.manage_inventory`
Risk: LOW. Generates/updates inventory from the **state store** (source of truth), not by
hand. Groups: `login`, `compute_cpu`, `compute_gpu`, `controller`. Writes
`$ANSIBLE_DIR/inventory/hosts.yml`, stages in config repo.
```python
class ManageInventoryIn(BaseModel):
    regenerate: bool = True
    dry_run: bool = True
```

### 2.5 `ansible.manage_secret` (READ/LOW)
Wraps the secret backend (Ansible Vault or external store). The agent **references**
secrets by id (`munge_key_ref`); it never prints or commits secret material. Provides
`ensure_secret_present(ref)` checks only.

---

## 3. Conventions

- `ANSIBLE_HOST_KEY_CHECKING=False` only inside the virtual test cluster; production uses
  managed known_hosts.
- All playbooks idempotent by construction (roles use proper modules, not `command`).
- `--diff` output is captured but secrets are redacted before entering the audit log.

## 4. Acceptance criteria

- [ ] `compose_playbook` rejects a non-existent role and an unknown var.
- [ ] `run_playbook` refuses to apply if `ansible-lint` fails.
- [ ] Dry-run produces a per-host Diff via `--check` and applies nothing.
- [ ] `manage_inventory` output exactly reflects state-store node roles.
- [ ] No secret value ever appears in a committed file or audit event.
