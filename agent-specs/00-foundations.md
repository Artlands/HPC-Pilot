# 00 — Foundations

Implements the shared substrate every other spec depends on: the state store, the typed
tool framework, the command executor, RBAC, the audit log, and config-as-code.

---

## 1. State store

The state store is the **desired-state source of truth**. Live cluster state is reconciled
against it (spec 07 §6). Use PostgreSQL via SQLAlchemy 2.x ORM. Provide Alembic migrations.

### 1.1 Schema

```python
# hpc_agent/state/models.py
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from datetime import datetime
import enum

class Base(DeclarativeBase): ...

class NodeRole(str, enum.Enum):
    LOGIN = "login"; COMPUTE_CPU = "compute_cpu"; COMPUTE_GPU = "compute_gpu"; CONTROLLER = "controller"

class NodeState(str, enum.Enum):
    UNKNOWN="unknown"; PROVISIONING="provisioning"; UP="up"; DRAINED="drained"; DOWN="down"; MAINT="maint"

class Node(Base):
    __tablename__ = "nodes"
    id: Mapped[int] = mapped_column(primary_key=True)
    hostname: Mapped[str] = mapped_column(unique=True, index=True)
    mac: Mapped[str | None]
    ip: Mapped[str | None]
    role: Mapped[NodeRole]
    state: Mapped[NodeState] = mapped_column(default=NodeState.UNKNOWN)
    image_id: Mapped[int | None] = mapped_column(...)        # FK images.id
    profile: Mapped[str | None]                              # warewulf profile name
    gpu_count: Mapped[int] = mapped_column(default=0)
    gpu_model: Mapped[str | None]
    cpu_count: Mapped[int] = mapped_column(default=0)
    mem_mb: Mapped[int] = mapped_column(default=0)
    features: Mapped[str | None]                             # comma list, mirrors slurm Features
    partitions: Mapped[list["PartitionMember"]] = relationship(back_populates="node")
    updated_at: Mapped[datetime]

class Image(Base):
    __tablename__ = "images"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    base_os: Mapped[str]                                     # e.g. "rockylinux:9"
    kind: Mapped[NodeRole]                                   # cpu vs gpu image
    spec_hash: Mapped[str]                                   # hash of build spec (idempotency)
    cuda_version: Mapped[str | None]
    driver_version: Mapped[str | None]
    kernel_version: Mapped[str | None]
    status: Mapped[str]                                      # building|ready|failed|deprecated
    built_at: Mapped[datetime | None]

class Partition(Base):
    __tablename__ = "partitions"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    is_default: Mapped[bool] = mapped_column(default=False)
    max_time_min: Mapped[int | None]
    default_qos: Mapped[str | None]

class PartitionMember(Base):
    __tablename__ = "partition_members"
    partition_id: Mapped[int] = mapped_column(primary_key=True)   # FK
    node_id: Mapped[int] = mapped_column(primary_key=True)        # FK
    node: Mapped["Node"] = relationship(back_populates="partitions")

class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    parent: Mapped[str | None]
    organization: Mapped[str | None]
    description: Mapped[str | None]

class QOS(Base):
    __tablename__ = "qos"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    priority: Mapped[int | None]
    max_wall_min: Mapped[int | None]
    max_jobs_pu: Mapped[int | None]
    max_tres: Mapped[str | None]                            # "cpu=128,gres/gpu=8"
    grp_tres: Mapped[str | None]

class UserAssoc(Base):
    __tablename__ = "user_assocs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user: Mapped[str] = mapped_column(index=True)
    account: Mapped[str]
    qos_list: Mapped[str]                                   # comma list
    default_qos: Mapped[str | None]
    fairshare: Mapped[int | None]
    __table_args__ = (UniqueConstraint("user", "account"),)
```

### 1.2 Repository pattern

Expose one repository class per aggregate (`NodeRepo`, `ImageRepo`, `SlurmRepo`, …) in
`hpc_agent/state/repos.py`. Each provides `get`, `list`, `upsert`, `delete`, and
domain queries (e.g. `NodeRepo.by_role(role)`). All tool functions read/write state only
through repositories — never raw SQL in tool code.

---

## 2. Config-as-code

All managed config lives in a git repo (`$CONFIG_REPO`, default `/etc/hpc-agent/config`):
`slurm/slurm.conf`, `warewulf/overlays/**`, `spack/envs/**/spack.yaml`, `ansible/**`.

`hpc_agent/state/configrepo.py` wraps git:

```python
class ConfigRepo:
    def read(self, relpath: str) -> str: ...
    def stage(self, relpath: str, content: str) -> None: ...      # writes to working tree
    def diff(self) -> str: ...                                    # unified diff of staged changes
    def commit(self, message: str, author: str) -> str: ...       # returns commit sha
    def snapshot(self) -> str: ...                                # tag current HEAD, return ref
    def rollback(self, ref: str) -> None: ...                     # git reset --hard to ref
```

Rule: **mutating tools stage + commit config changes**; the commit sha is recorded in the
audit log so any change is revertible (spec 01 §5).

---

## 3. Tool framework

### 3.1 Tool definition

Every tool is a function decorated with `@tool`, declaring typed I/O. The decorator
registers it for the planner (spec 02) and auto-generates the JSON schema for LLM
tool-calling.

```python
# hpc_agent/tools/base.py
from pydantic import BaseModel
from typing import Callable, TypeVar
from enum import Enum

class Risk(str, Enum):
    READ = "read"          # no mutation, auto-run
    LOW = "low"            # reversible, in-policy auto-run allowed
    MEDIUM = "medium"      # mutation, approval unless within policy bounds
    HIGH = "high"          # destructive/wide blast radius, always approval

I = TypeVar("I", bound=BaseModel); O = TypeVar("O", bound=BaseModel)

def tool(*, name: str, risk: Risk, domain: str,
         blast_radius: Callable[[BaseModel], int] = lambda _: 1):
    """Registers a tool. `blast_radius` returns #entities affected, for cap enforcement."""
    ...
```

### 3.2 ToolResult (uniform return type)

```python
class ToolStatus(str, Enum):
    OK="ok"; DRY_RUN="dry_run"; NEEDS_APPROVAL="needs_approval"; DENIED="denied"; ERROR="error"

class ToolResult(BaseModel):
    status: ToolStatus
    data: dict | None = None
    diff: "Diff | None" = None          # spec 01 §2
    audit_id: str | None = None
    config_commit: str | None = None
    error: "ToolError | None" = None
```

### 3.3 Error taxonomy

```python
class ErrorKind(str, Enum):
    PRECONDITION="precondition"     # state not as required (e.g. node not drained)
    COMMAND_FAILED="command_failed" # underlying CLI returned nonzero
    POLICY_DENIED="policy_denied"
    NOT_FOUND="not_found"
    CONFLICT="conflict"             # idempotency / concurrent change
    TIMEOUT="timeout"
    UNKNOWN="unknown"

class ToolError(BaseModel):
    kind: ErrorKind
    message: str          # human-readable, safe to show
    detail: str | None    # stderr / traceback, for logs only
    remediation: str | None
```

### 3.4 Tool execution contract (pseudocode every tool follows)

```
def some_tool(inp) -> ToolResult:
    1. validate inp (Pydantic already did types; check semantic preconditions)
    2. read current state (repo + live query)
    3. compute delta; if no delta -> return OK (idempotent no-op)
    4. build Diff
    5. gate = safety.evaluate(tool_meta, inp, diff)      # spec 01 §3
    6. if inp.dry_run:        return ToolResult(DRY_RUN, diff=diff)
    7. if gate.requires_approval and not gate.approved:
                              return ToolResult(NEEDS_APPROVAL, diff=diff)
    8. if gate.denied:        return ToolResult(DENIED, error=...)
    9. snapshot = configrepo.snapshot()  (if touches config)
   10. execute via run_command(...)
   11. commit config; update state repo; write audit (spec 00 §5)
   12. return ToolResult(OK, data=..., audit_id=..., config_commit=...)
```

---

## 4. Command executor

The single chokepoint for all shell. No tool calls `subprocess` directly.

```python
# hpc_agent/exec/runner.py
class CommandSpec(BaseModel):
    argv: list[str]                 # never a shell string; no shell=True
    cwd: str | None = None
    timeout_s: int = 120
    input_text: str | None = None
    redact: list[str] = []          # substrings to mask in logs (secrets)

class CommandResult(BaseModel):
    rc: int; stdout: str; stderr: str; duration_s: float

def run_command(spec: CommandSpec, *, actor: str, audit_id: str) -> CommandResult:
    # 1. assert spec.argv[0] in ALLOWLIST (wwctl, sacctmgr, scontrol, ansible-playbook,
    #    spack, sinfo, squeue, sacct, sreport, munge, git, ...)
    # 2. resolve to absolute path
    # 3. run with subprocess.run(shell=False), capture, enforce timeout
    # 4. log argv (redacted), rc, durations to audit
    # 5. NEVER raise on nonzero rc — return result; caller classifies
```

**Privilege:** the agent runs as service account `hpcagent`. Privileged commands are
invoked via `sudo` entries scoped to exact binaries in `/etc/sudoers.d/hpcagent`
(documented per tool spec). No blanket `sudo`.

---

## 5. Audit log

Append-only table `audit_events` (separate DB or WORM storage). Every tool invocation and
every `run_command` writes an event.

```python
class AuditEvent(BaseModel):
    id: str                          # uuid
    ts: datetime
    actor: str                       # requesting human identity
    agent_run_id: str                # ties multi-step plans together
    tool: str
    risk: str
    input: dict                      # redacted
    decision: str                    # auto|approved-by:<who>|denied
    diff_summary: str | None
    commands: list[dict]             # argv(redacted), rc, duration
    result_status: str
    config_commit: str | None
```

Audit writes must succeed before a mutating action is reported `OK`; if audit write fails,
fail the tool (`ErrorKind.UNKNOWN`) and do not proceed.

---

## 6. RBAC

```python
class Role(str, Enum):
    VIEWER="viewer"; OPERATOR="operator"; ADMIN="admin"

# capability = "domain.tool", e.g. "slurm.manage_qos"
ROLE_CAPS: dict[Role, set[str]] = {
    Role.VIEWER:  {"*.query*", "*.list*", "*.status*"},
    Role.OPERATOR:{"slurm.*", "warewulf.rebuild_overlay", "ansible.run_playbook", ...},
    Role.ADMIN:   {"*"},
}
def authorize(actor_role: Role, capability: str) -> bool: ...   # glob match
```

Authorization is checked in the safety layer (spec 01 §3) before approval evaluation.

---

## 7. Settings

`hpc_agent/config/settings.py` via `pydantic-settings`, env-overridable:

```
HPC_DB_URL, HPC_AUDIT_DB_URL, CONFIG_REPO, SLURM_BIN_DIR, WW_BIN_DIR,
SPACK_ROOT, ANSIBLE_DIR, APPROVAL_BACKEND (cli|slack|api),
DRY_RUN_DEFAULT=true, MAX_BLAST_RADIUS_AUTO=4
```

## 8. Validation checklist

- Alembic migrations create all tables; `pytest` fixtures seed a sample cluster.
- `@tool` registry returns valid JSON schema for each registered tool.
- `run_command` rejects any binary not on the allowlist and redacts secrets in logs.
- Every `run_command` produces exactly one linked audit command entry.
- RBAC glob matcher passes unit tests for all role/capability combos.
