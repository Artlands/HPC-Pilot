# 09 — Cluster Bootstrap (Day-0 Setup)

Initial bring-up of the Warewulf provisioning server. These tools configure the
bare-metal controller **before** any compute node can PXE-boot. They are a
prerequisite for all spec 03 provisioning tools.

Sudoers (`/etc/sudoers.d/hpcagent`):
```
hpcagent ALL=(root) NOPASSWD: /usr/bin/wwctl *
```

---

## 1. Prerequisites (operator responsibility)

Before invoking bootstrap tools, an operator must have:
1. Installed `warewulf` (or `warewulf-ohpc`) on the controller node.
2. Confirmed the management NIC (`mgmt_interface`) carries the provisioning network and
   that the controller holds the provisioning IP (`controller_ip`).
3. Ensured `firewalld`/`iptables` allows DHCP (67/UDP), TFTP (69/UDP), and NFS
   (2049/TCP) on the management interface. Client-network restriction for NFS is
   enforced by the `firewalld` role (spec 04), not by the NFS bootstrap tool.

These are one-time, human-executed steps. The agent validates Warewulf is present via
`warewulf.server_status` but does not perform OS-level installation.

---

## 2. Config-as-code model

Warewulf 4.x is driven by **`/etc/warewulf/warewulf.conf`** (YAML). The `wwctl configure
<service>` subcommands take **no positional options** — they read warewulf.conf and
(re)write the live dhcpd / TFTP / NFS configuration. Accordingly, each bootstrap tool:

1. Reads the managed copy of `warewulf.conf` at `$CONFIG_REPO/warewulf/warewulf.conf`.
2. Merges its section (`dhcp`, `tftp`, or `nfs`) into a copy.
3. If the merged config is **unchanged**, returns `OK` with an empty diff and runs
   nothing (idempotent no-op, spec 00 §3.4 step 3).
4. Otherwise builds a `Diff` whose `config_diff` is the unified diff of warewulf.conf,
   gates it (spec 01 §3), then on apply: stages + commits warewulf.conf to the config
   repo and runs `wwctl configure <service>`.

Revert (spec 01 §5 mechanism 1): restore the prior warewulf.conf commit and re-run
`wwctl configure <service>`. All three tools are therefore `reversible=True`.

---

## 3. Tools

### 3.1 `warewulf.server_status`
Risk: READ. Validates that the Warewulf server is installed and reports service state.
```python
class ServerStatusIn(BaseModel):
    pass
class ServerStatusOut(BaseModel):
    installed: bool
    running: bool
    version: str | None
    dhcp_configured: bool
    tftp_configured: bool
    nfs_configured: bool
```
Returns `ErrorKind.PRECONDITION` if the `wwctl` binary is not found. Otherwise runs
`wwctl server status` and best-effort parses the text into the service-state booleans
(the calling workflow reads `.running`/`.*_configured`).

### 3.2 `warewulf.configure_dhcp`
Risk: HIGH (controls network boot for all nodes). **Always requires approval** — both via
the HIGH risk tier (spec 01 §3) and an explicit policy rule (`config_repo/policy/warewulf.yaml`).
```python
class ConfigureDhcpIn(BaseModel):
    interface: str                    # provisioning NIC (recorded in audit/diff)
    range_start: str                  # first PXE lease, e.g. "192.168.122.100"
    range_end: str                    # last PXE lease, e.g. "192.168.122.200"
    controller_ip: str | None = None  # warewulf.conf ipaddr (provisioning IP)
    netmask: str = "255.255.255.0"
    dry_run: bool = True
```
warewulf.conf merge: sets `ipaddr`/`netmask` (when `controller_ip` given) and
`dhcp.{enabled, range start, range end}`. Command: `wwctl configure dhcp`.

> The default **gateway** for booted nodes is *not* a DHCP-service setting; it is a node
> network default carried on the Warewulf profile (`warewulf.define_profile` `network`,
> spec 03 §1.3). The bootstrap workflow passes any gateway there, not here.

### 3.3 `warewulf.configure_tftp`
Risk: MEDIUM. Enables PXE/iPXE delivery.
```python
class ConfigureTftpIn(BaseModel):
    enabled: bool = True
    dry_run: bool = True
```
warewulf.conf merge: sets `tftp.enabled`. Command: `wwctl configure tftp`.

### 3.4 `warewulf.configure_nfs`
Risk: MEDIUM. Configures NFS exports so PXE-booted nodes can mount `/home`, `/scratch`,
and Spack software trees.
```python
class ConfigureNfsIn(BaseModel):
    exports: list[str] = ["/home", "/scratch", "/opt/spack"]
    export_options: str = "rw,sync,no_root_squash"
    dry_run: bool = True
```
warewulf.conf merge: sets `nfs.enabled` and `nfs.exports` (a list of
`{path, "export options"}`). Command: `wwctl configure nfs`. Client-network restriction
is handled by the `firewalld` role (spec 04), not encoded here.

---

## 4. Bootstrap Workflow (`workflows/bootstrap_cluster.py`)

Orchestrates a full day-0 bring-up. See spec 07 for the workflow convention.

```python
def build(
    mgmt_interface: str,
    dhcp_range_start: str,
    dhcp_range_end: str,
    base_os: str,                       # "docker://rockylinux:9"
    cpu_image_name: str,
    *,
    controller_ip: str | None = None,
    netmask: str = "255.255.255.0",
    gateway: str | None = None,         # routed to the node profile, not DHCP
    gpu_image_name: str | None = None,  # skips GPU steps when None
    gpu_driver_version: str | None = None,
    gpu_cuda_version: str | None = None,
    nfs_exports: list[str] | None = None,
    actor: str,
) -> Plan
```

Ordered steps (GPU steps 8 and 10 are emitted only when `gpu_image_name` is set):

| # | Step id | Tool | Depends on |
|---|---------|------|------------|
| 1 | `check-server` | `warewulf.server_status` (READ — gates the rest) | — |
| 2 | `configure-dhcp` | `warewulf.configure_dhcp` | 1 |
| 3 | `configure-tftp` | `warewulf.configure_tftp` | 1 |
| 4 | `configure-nfs` | `warewulf.configure_nfs` | 1 |
| 5 | `start-server` | `ansible.run_playbook(warewulf_server)` | 2, 3, 4 |
| 6 | `import-base-os` | `warewulf.import_container` | 5 |
| 7 | `build-cpu-image` | `warewulf.build_node_image` (CPU) | 6 |
| 8 | `build-gpu-image` | `warewulf.build_node_image` (GPU) *(optional)* | 6 |
| 9 | `define-cpu-profile` | `warewulf.define_profile` (cpu-default) | 7 |
| 10 | `define-gpu-profile` | `warewulf.define_profile` (gpu-default) *(optional)* | 8 |
| 11 | `build-overlays` | `warewulf.rebuild_overlay` (all overlays) | 9 (or 10) |
| 12 | `generate-inventory` | `ansible.manage_inventory` | 11 |

So the CPU-only path is **10 steps**; the full GPU path is **12 steps**.

Because steps 2–4 depend on `check-server`, a failed `server_status` (e.g. `wwctl`
absent) cascades to skip the rest (executor dependency rule, spec 02 §4) — the READ
prereq effectively gates the mutating steps without itself being marked critical. Steps
2–11 are critical; a failure there halts forward progress and offers revert of completed
mutating steps.

The `warewulf_server` Ansible role (step 5) is part of the curated role library; see
spec 04 §1.

---

## 5. Validation checklist

- `server_status` returns `ErrorKind.PRECONDITION` if the `wwctl` binary is absent.
- `configure_dhcp` dry-run never runs `wwctl`; the diff shows the warewulf.conf change
  and a `wwctl configure dhcp` command preview.
- `configure_dhcp` always pauses for approval (HIGH risk + explicit policy rule), even
  for an ADMIN.
- `configure_nfs` stages `warewulf.conf` in the config repo and commits on apply.
- Re-running any configure tool with unchanged inputs returns `OK` with an empty diff
  and invokes no `wwctl configure` command (idempotent no-op).
- `bootstrap_cluster` returns a valid dependency-ordered Plan: 10 steps CPU-only,
  12 steps for the full-GPU path; GPU steps are omitted when `gpu_image_name` is `None`.
- A `gateway` passed to `bootstrap_cluster` lands on the node profile's `network`
  defaults, not on `configure_dhcp`.
