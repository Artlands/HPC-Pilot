# 06 — System Software Tools (Spack)

Reproducible system/application software via Spack environments, arch-aware for CPU vs GPU
nodes, with binary build caches and module generation. Targets Spack 0.22+.

The agent operates Spack as the `spack` service user under `$SPACK_ROOT`. Environments are
version-controlled: every env is a directory under `$CONFIG_REPO/spack/envs/<name>/` with
`spack.yaml` (+ committed `spack.lock`).

Sudoers: none — Spack runs unprivileged into a shared prefix the agent owns.

---

## 1. Tools

### 1.1 `spack.manage_environment`
Risk: LOW (edits spack.yaml) / MEDIUM (concretize+install via `apply`).
```python
class ManageEnvIn(BaseModel):
    name: str
    op: Literal["create","add_specs","remove_specs","set_config"]
    specs: list[str] = []           # "gcc@13.2.0", "openmpi@5 +cuda", "fftw %gcc@13"
    target_arch: str | None = None  # "x86_64_v3" | "zen4" ; GPU envs add cuda_arch
    config: dict = {}               # compilers, packages.yaml prefs, view path
    dry_run: bool = True
class ManageEnvOut(BaseModel):
    env: str; spec_count: int; lockfile_changed: bool
```
Procedure:
1. create: `spack env create <name>` then write `spack.yaml` into the config-repo env dir.
2. add/remove: edit the `specs:` list in `spack.yaml`.
3. **Concretize** (always, even dry-run): `spack -e <name> concretize -f` → produces/updates
   `spack.lock`. The lockfile diff IS the `Diff` (shows resolved versions/variants/deps).
4. Dry-run stops here (nothing installed). Apply proceeds to `install_packages`.
Commit `spack.yaml` + `spack.lock` to config repo.

### 1.2 `spack.install_packages`
Risk: MEDIUM. Builds/installs concretized specs (long-running).
```python
class InstallIn(BaseModel):
    env: str
    use_buildcache: bool = True     # pull binaries first
    jobs: int = 16
    dry_run: bool = True
```
Procedure: dry-run = `spack -e <env> install --fake --no-checksum` or report the concretized
DAG only (no build). Apply = `spack -e <env> install -j <jobs> [--use-buildcache auto]`.
Stream long output; enforce a generous timeout (e.g. 6h) and heartbeat to the audit log.
On success, optionally push to buildcache (§1.4).
**Arch correctness:** GPU envs must concretize with the GPU node target + `cuda_arch=<sm>`;
the tool refuses to install a GPU env onto CPU-only build hosts unless a matching build
host is available (PRECONDITION).

### 1.3 `spack.manage_compilers`
Risk: LOW. `spack compiler find` / add a compiler; write to env or site `compilers.yaml`.
```python
class ManageCompilersIn(BaseModel):
    op: Literal["find","add"]; path: str | None = None
    scope: Literal["site","env"] = "site"; env: str | None = None
    dry_run: bool = True
```

### 1.4 `spack.manage_buildcache`
Risk: MEDIUM. Push/update a binary mirror to speed multi-node deploys.
```python
class BuildcacheIn(BaseModel):
    op: Literal["push","update_index","add_mirror"]
    mirror: str                     # path or URL
    env: str | None = None
    signing_key_ref: str | None = None   # GPG key id from secret store
    dry_run: bool = True
```
Commands: `spack buildcache push <mirror> <specs>`, `spack buildcache update-index <mirror>`,
`spack mirror add <name> <url>`. Signing key referenced, never embedded.

### 1.5 `spack.generate_modules`
Risk: LOW. Produce Lmod/Tcl modulefiles for an env so users can `module load`.
```python
class GenModulesIn(BaseModel):
    env: str; module_type: Literal["lmod","tcl"] = "lmod"
    dry_run: bool = True
```
Command: `spack -e <env> module lmod refresh --delete-tree -y`. Ensure module root is on
nodes via the `spack_view` Ansible role (spec 04).

### 1.6 `spack.create_view`
Risk: LOW. Filesystem view for an env: `spack -e <env> env view enable <prefix>` /
`spack view symlink <prefix> <specs>`.

### 1.7 `spack.query_*` (READ)
`list_envs`, `find` (`spack -e <env> find -P`), `spec` (`spack spec -I <spec>` to preview
concretization without building). Reconcile installed software inventory into state if
tracked.

---

## 2. Conventions

- **One env per purpose** (e.g. `core-tools`, `mpi-stack`, `gpu-stack`), each pinned via
  committed `spack.lock` for reproducibility.
- Never `spack install` loose specs outside an env on shared systems.
- Prefer buildcache pulls; only build from source when no binary matches the concretized
  hash. Record provenance (lockfile hash) in the audit event.
- GPU vs CPU: maintain separate envs/targets; the planner picks the env matching the node
  role when wiring software to a node group (spec 07 §3 step: apply `spack_view` role).

## 3. Acceptance criteria

- [ ] `manage_environment` always concretizes and surfaces the `spack.lock` diff as the Diff.
- [ ] Dry-run of `install_packages` builds nothing.
- [ ] GPU env refuses to build on a CPU-only host (PRECONDITION) and succeeds on a GPU host.
- [ ] `spack.yaml` + `spack.lock` are committed together on every env change.
- [ ] Generated Lmod modules are loadable on a virtual-cluster node.
- [ ] Buildcache signing key is referenced by id, never written into config/audit.
