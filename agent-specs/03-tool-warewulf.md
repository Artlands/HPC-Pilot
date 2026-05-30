# 03 — Provisioning Tools (Warewulf)

Image builds (CPU + GPU), profiles, overlays, node provisioning. Targets Warewulf 4.x
(`wwctl`). All commands run via `run_command` (spec 00 §4) under scoped sudo.

Sudoers (`/etc/sudoers.d/hpcagent`):
```
hpcagent ALL=(root) NOPASSWD: /usr/bin/wwctl *
```

---

## 1. Tools

### 1.1 `warewulf.import_container`
Risk: MEDIUM. Imports a base OS container.
```python
class ImportContainerIn(BaseModel):
    name: str                       # image name in state store
    source: str                     # "docker://rockylinux:9" | OCI ref | chroot path
    dry_run: bool = True
class ImportContainerOut(BaseModel):
    image: str; layers: int
```
Command: `wwctl container import <source> <name> [--syncuser]`.
State: upsert `Image(name, base_os=source, kind=?, status="ready")`.

### 1.2 `warewulf.build_node_image`
Risk: MEDIUM (HIGH if reused by live nodes). The core build tool. Idempotent on
`spec_hash` = hash of (base, kind, package_list, kernel_args, gpu options).
```python
class BuildImageIn(BaseModel):
    name: str
    base_image: str                 # imported container name
    kind: Literal["compute_cpu","compute_gpu"]
    packages: list[str] = []        # extra rpms/debs
    kernel_args: str | None = None
    # GPU-only:
    nvidia_driver_version: str | None = None   # e.g. "550.90.07"
    cuda_version: str | None = None            # e.g. "12.4"
    enable_fabricmanager: bool = False         # NVSwitch/NVLink systems
    install_dcgm: bool = True
    dry_run: bool = True
class BuildImageOut(BaseModel):
    image: str; spec_hash: str; kernel_version: str
    driver_version: str | None; cuda_version: str | None; status: str
```

**Build procedure (executed inside the container via `wwctl container exec <name> --`):**

CPU path:
1. `dnf -y update && dnf -y install <base compute packages>` — kernel, `chrony`, `munge`,
   `slurm-slurmd`, `nfs-utils`, `node_exporter`, plus `inp.packages`.
2. Configure munge dir perms; enable `slurmd`, `chronyd` units (don't start — built offline).
3. Capture `uname -r` -> `kernel_version`.

GPU path (CPU path **plus**):
4. Install kernel-devel/headers matching the image kernel (driver build needs them).
5. Install NVIDIA driver `inp.nvidia_driver_version` (DKMS or precompiled). **Validate
   driver↔kernel ABI**: run the driver's installer in check mode; if mismatch, fail with
   `ErrorKind.PRECONDITION` and remediation "pin a kernel or a compatible driver".
6. Install CUDA toolkit `inp.cuda_version` (or runtime libs only if compute-only).
7. If `enable_fabricmanager`: install + enable `nvidia-fabricmanager` matching driver.
8. If `install_dcgm`: install `datacenter-gpu-manager`, enable `nvidia-dcgm`.
9. Record `driver_version`, `cuda_version` on the `Image` row.

Finalize (both): `wwctl container build <name>` to produce the bootable image; set
`Image.status="ready"`, `spec_hash`, `built_at`.

Dry-run: compute `spec_hash`; if an Image with that hash + `status=ready` exists, return a
no-op diff. Otherwise return a Diff listing the package/driver operations and the
`wwctl container build` command — **execute nothing**.

Validation gate before `status=ready`: boot-test the image on one node in a `maint`
reservation (optional flag `validate_boot=True`) and confirm `slurmd -C` and (GPU)
`nvidia-smi` succeed; spec 07 §3 wires this into the node-add workflow.

### 1.3 `warewulf.define_profile`
Risk: LOW. Creates/updates a Warewulf profile (the node class defaults).
```python
class DefineProfileIn(BaseModel):
    name: str                       # "cpu-default" | "gpu-default"
    image: str
    system_overlays: list[str] = ["wwinit","hosts","ssh.host_keys"]
    runtime_overlays: list[str] = ["hosts","ssh.authorized_keys","munge","slurm"]
    kernel_args: str | None = None
    network: dict | None = None     # netdev/netmask/gateway defaults
    dry_run: bool = True
```
Command: `wwctl profile set <name> --container <image> --runtime-overlays ... --system-overlays ...`.

### 1.4 `warewulf.manage_overlay`
Risk: MEDIUM. Manages overlay template files (these render per-node config: `slurm.conf`,
`hosts`, `munge.key`, ssh keys). Overlay sources live in `$CONFIG_REPO/warewulf/overlays/`.
```python
class ManageOverlayIn(BaseModel):
    overlay: str                    # "slurm" | "munge" | "hosts"
    files: dict[str,str]            # relpath -> template content (.ww templates)
    dry_run: bool = True
```
Procedure: stage files in config repo -> `configrepo.diff()` as the Diff -> on apply,
copy into `/var/lib/warewulf/overlays/<overlay>/` and `wwctl overlay build`. **Never put
secrets (munge.key) in git plaintext** — reference a path in the secret store; the overlay
template pulls it at build time.

### 1.5 `warewulf.assign_image_to_nodes`
Risk: MEDIUM. Sets node profile/image and registers node hardware in state + Warewulf.
```python
class AssignImageIn(BaseModel):
    nodes: list[str]                # hostnames or a wwgroup
    profile: str
    dry_run: bool = True
```
Command per node: `wwctl node set <node> --profile <profile>`. Update `Node.image_id`,
`Node.profile`. `blast_radius = len(nodes)`.

### 1.6 `warewulf.provision_node`
Risk: MEDIUM. Registers a new node (MAC/IP/netdev) for PXE/iPXE boot.
```python
class ProvisionNodeIn(BaseModel):
    hostname: str; mac: str; ip: str; netdev: str = "eth0"
    profile: str; role: NodeRole
    dry_run: bool = True
```
Command: `wwctl node add <hostname> --netdev <netdev> --hwaddr <mac> --ipaddr <ip> --profile <profile>`
then `wwctl overlay build <hostname>`. Insert `Node` row (`state=provisioning`).

### 1.7 `warewulf.rebuild_overlay`
Risk: LOW. `wwctl overlay build [node|--all]`. Used after any overlay/config change.

### 1.8 `warewulf.query_*` (READ)
`list_images`, `list_nodes`, `node_status` — wrap `wwctl container list`,
`wwctl node list -a`. Reconcile into state store.

---

## 2. CPU vs GPU image differences (summary table)

| Aspect | CPU image | GPU image |
|--------|-----------|-----------|
| Base packages | kernel, munge, slurmd, chrony, nfs | + kernel-devel, NVIDIA driver, CUDA, (fabricmanager), DCGM |
| Slurm gres | none | `gres.conf` for `gpu`, `Type` set |
| Validation | `slurmd -C` boots | + `nvidia-smi`, `dcgmi discovery -l` |
| spec_hash inputs | base+pkgs+kargs | + driver+cuda+fabricmanager flag |

## 3. Validation checklist

- `build_node_image` is idempotent; re-running it with the same inputs yields a no-op
  diff.
- GPU builds fail cleanly with `PRECONDITION` on driver/kernel ABI mismatch.
- Dry-run never invokes `wwctl container build` or `container exec`.
- `provision_node` -> `assign_image_to_nodes` -> `rebuild_overlay` produces a node that
  the virtual cluster can PXE boot.
- The Munge key is never written to the git config repo in plaintext.
