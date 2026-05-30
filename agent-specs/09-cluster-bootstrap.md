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
1. Installed `warewulf-ohpc` (or equivalent) on the controller node.
2. Confirmed the management NIC (`mgmt_interface`) carries the provisioning VLAN.
3. Ensured `firewalld`/`iptables` allows DHCP (67/UDP), TFTP (69/UDP), and NFS
   (2049/TCP) on the management interface.

These are one-time, human-executed steps. The agent validates they are complete
via `warewulf.server_status` but does not perform OS-level installation.

---

## 2. Tools

### 2.1 `warewulf.server_status`
Risk: READ. Validates that the Warewulf server is installed and running.
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
Command: `wwctl server status`. Parse structured output to fill `ServerStatusOut`.
Returns `ErrorKind.PRECONDITION` if `wwctl` is not found; always succeeds otherwise
(even if services are not running — the calling workflow checks `.running`).

### 2.2 `warewulf.configure_dhcp`
Risk: HIGH (affects network boot for all nodes). Configures Warewulf's built-in
DHCP server for the management network.
```python
class ConfigureDhcpIn(BaseModel):
    interface: str                    # management NIC, e.g. "eth0"
    range_start: str                  # first PXE IP, e.g. "10.1.0.100"
    range_end: str                    # last PXE IP, e.g. "10.1.0.254"
    router: str | None = None         # gateway for PXE network
    dry_run: bool = True
```
Command: `wwctl configure dhcp --interface <if> --range-start <ip> --range-end <ip>
[--router <gw>]`. After applying, writes a config-repo record at
`warewulf/dhcp.yaml` (documents the chosen range for audit).

### 2.3 `warewulf.configure_tftp`
Risk: MEDIUM. Configures and enables the TFTP server used for PXE boot.
```python
class ConfigureTftpIn(BaseModel):
    interface: str | None = None      # bind address (default: all)
    dry_run: bool = True
```
Command: `wwctl configure tftp [--interface <if>]`.

### 2.4 `warewulf.configure_nfs`
Risk: MEDIUM. Configures NFS exports so PXE-booted nodes can mount `/home`,
`/scratch`, and Spack software trees over the management network.
```python
class ConfigureNfsIn(BaseModel):
    exports: list[str] = ["/home", "/scratch", "/opt/spack"]
    network: str | None = None        # allowed network CIDR, e.g. "10.1.0.0/24"
    dry_run: bool = True
```
Command: `wwctl configure nfs [--export <path>] [--cidr <net>]` (one invocation
per export path). Writes `/etc/exports` snippet to config repo at
`warewulf/nfs_exports.conf`.

---

## 3. Bootstrap Workflow (`workflows/bootstrap_cluster.py`)

Orchestrates a full day-0 bring-up. See spec 07 for the workflow convention.

```python
def build(
    mgmt_interface: str,
    dhcp_range_start: str,
    dhcp_range_end: str,
    dhcp_router: str | None,
    base_os: str,          # "docker://rockylinux:9"
    cpu_image_name: str,
    gpu_image_name: str | None,
    nfs_exports: list[str],
    nfs_network: str | None,
    *,
    actor: str,
) -> Plan
```

Ordered steps:

| # | Tool | Depends on |
|---|------|------------|
| 1 | `warewulf.server_status` (READ — validate prereqs) | — |
| 2 | `warewulf.configure_dhcp` | 1 |
| 3 | `warewulf.configure_tftp` | 1 |
| 4 | `warewulf.configure_nfs` | 1 |
| 5 | `ansible.run_playbook(warewulf_server)` | 2, 3, 4 |
| 6 | `warewulf.import_container` (base OS) | 5 |
| 7 | `warewulf.build_node_image` (CPU image) | 6 |
| 8 | `warewulf.build_node_image` (GPU image, if requested) | 6 |
| 9 | `warewulf.define_profile` (cpu-default) | 7 |
| 10 | `warewulf.define_profile` (gpu-default, if GPU image built) | 8 |
| 11 | `warewulf.manage_overlay` (standard overlays) | 9 (or 10) |
| 12 | `ansible.manage_inventory` (generate initial inventory) | 11 |

Critical steps: 2–11. If any critical step fails, halt and offer revert of
completed mutating steps.

---

## 4. Validation checklist

- `server_status` returns `ErrorKind.PRECONDITION` if `wwctl` binary is absent.
- `configure_dhcp` dry-run never invokes the command; shows correct argv preview.
- `configure_dhcp` is always ADMIN-only (HIGH risk requires explicit approval).
- `configure_nfs` stages `/etc/exports` content in the config repo and commits.
- `bootstrap_cluster` returns a valid Plan with 12 steps for the full-GPU path.
- `bootstrap_cluster` skips GPU steps 8 and 10 when `gpu_image_name` is `None`.
- Rerunning bootstrap (all step inputs unchanged) produces no-op diffs for all
  idempotent tools and skips DHCP/TFTP/NFS if already configured.
