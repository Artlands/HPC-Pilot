# HPC Pilot Architecture

## Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                        USER INTERFACE                                 │
│  ┌──────────────┐  ┌──────────────────────────────────────────────┐  │
│  │   CLI        │  │  Gateway                                     │  │
│  │  (argparse)  │  │  Telegram / Discord (per-session actor ID)   │  │
│  └──────────────┘  └──────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
              │                         │
              │                         └─ hpc_pilot/gateway.py
              │
              ▼
┌──────────────────────────────────────────────────────────────────────┐
│                   SAFETY LAYER                                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐   │
│  │  RBAC        │  │  Dry-run     │  │  Audit log               │   │
│  │  rbac.py     │  │  gate        │  │  audit.py                │   │
│  │ (4 roles)    │  │              │  │  (denials logged too)    │   │
│  └──────────────┘  └──────────────┘  └──────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────┘
              │
              ▼
┌──────────────────────────────────────────────────────────────────────┐
│                   DISPATCH  (dispatch.py)                             │
│  Registry-based dispatch: name → handler(args, tools) → str          │
│  Skill calls routed to skills/runner.py                              │
└──────────────────────────────────────────────────────────────────────┘
              │
              ▼
┌──────────────────────────────────────────────────────────────────────┐
│                   HPC PILOT TOOLS  (hpc_pilot/tools/)                │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │
│  │ slurm.py │ │warewulf  │ │ansible.py│ │ spack.py │ │health.py │  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘  │
│  ┌──────────────────┐  ┌──────────────────────────────────────────┐  │
│  │  _run.py         │  │  _validation.py                          │  │
│  │  (SSH-aware run) │  │  (_NAME_RE, _USER_RE, _validate)         │  │
│  └──────────────────┘  └──────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
              │
              ▼
┌──────────────────────────────────────────────────────────────────────┐
│                   CLUSTER CONTEXT  (clusters.py)                      │
│  Cluster(name, slurm_bin_dir, …, ssh: SSHConfig | None)              │
│  get_cluster(name) → Cluster  (reads clusters: section of config)    │
│  _run() wraps argv in ssh -o BatchMode=yes when Cluster.ssh set      │
└──────────────────────────────────────────────────────────────────────┘
              │
              ▼
     subprocess.run (shell=False, no injection)
```

## Module map

| File | Purpose |
|------|---------|
| `cli.py` | CLI entry point; command routing; RBAC check; audit context |
| `agent.py` | Claude-powered tool-use loop; `TOOL_SCHEMAS`; context budget |
| `gateway.py` | Telegram + Discord bot server; per-session actor identity |
| `dispatch.py` | Registry-based tool dispatch; audit-gap fix for denials |
| `clusters.py` | `Cluster` dataclass + `SSHConfig`; `get_cluster()`; `list_clusters()` |
| `tools/__init__.py` | Backward-compat re-exports; `import subprocess` for patches |
| `tools/_validation.py` | `_NAME_RE`, `_USER_RE`, `_validate`, `_shquote` |
| `tools/_run.py` | `_run()` with optional SSH wrapping; `check_*_available()` |
| `tools/slurm.py` | `hpc_slurm_*` tools + `parse_slurm_*` parsers |
| `tools/warewulf.py` | `hpc_warewulf_*` tools + `parse_warewulf_*` parsers |
| `tools/spack.py` | `hpc_spack_*` tools + `parse_spack_*` parsers |
| `tools/ansible.py` | `hpc_ansible_*` tools |
| `tools/health.py` | `hpc_cluster_health_check` (composes all subsystems) |
| `skills/__init__.py` | Skills package |
| `skills/runner.py` | `SkillRunner`, YAML skill loader, step execution, pause/resume |
| `skills/builtin/` | Built-in runbook YAML files |
| `paths.py` | Home-directory paths (`~/.hpc-pilot/…`) |
| `config.py` | Config loading; `DEFAULT_CONFIG` with `clusters:` section |
| `rbac.py` | `Role` enum (4 levels), `TOOL_MIN_ROLE`, `check_permission` |
| `audit.py` | `audit_tool` CM; `log_audit`; denial records with returncode=126 |

## Role hierarchy

```
viewer < operator < admin < superadmin
```

| Role | Scope |
|------|-------|
| `viewer` | Read-only across all subsystems |
| `operator` | + node state, job hold/release, run skills |
| `admin` | + QOS, partitions, Ansible playbooks, Warewulf provisioning |
| `superadmin` | + Slurm reconfig, Warewulf bootstrap (DHCP/TFTP/NFS), accounting schema |

## Multi-cluster support

Every tool accepts `cluster: str = "default"`.  `get_cluster(name)` resolves
the name to a `Cluster` object from `config.yaml`:

```yaml
clusters:
  default:
    slurm_bin_dir: /usr/bin
    spack_root: /opt/spack
  staging:
    slurm_bin_dir: /opt/slurm-staging/bin
    ssh:
      host: staging-head.example.com
      user: hpcadmin
      key: ~/.ssh/hpc-pilot
default_cluster: default
```

When `Cluster.ssh` is set, `_run()` wraps the argv in an SSH call with
`BatchMode=yes` and `ConnectTimeout=5`.  The local execution path is unchanged.

## Skills / runbooks

Skills are YAML files in `~/.hpc-pilot/skills/` or `hpc_pilot/skills/builtin/`.
The `SkillRunner` executes them step-by-step:

1. Validate inputs against the skill schema.
2. Check the caller's role against `skill.required_role`.
3. Execute each step via `dispatch.invoke()` (full RBAC + audit per step).
4. If a step has `approval: required`, pause and persist state.
5. Resume via `hpc_skill_run(resume_run_id=...)`.

Run records are persisted to `~/.hpc-pilot/skills/runs/<uuid>.json`.

## Audit log

Every tool invocation writes one JSON line to `~/.hpc-pilot/logs/audit.jsonl`:
- **Success**: `returncode=0`, `duration_ms`, no `error` field.
- **Tool error**: `returncode=1`, `error=<message>`.
- **Permission denied**: `returncode=126`, `error="permission_denied: ..."` — logged
  **before** the PermissionError is re-raised, so denials are never silent.
- **Secrets** (`token`, `key`, `password`, `secret`) in `args` are redacted to `***`.

## Context budget

`HpcAgent.run_turn()` calls `_maybe_summarize(messages)` before each API
request.  When `_estimate_tokens(messages)` exceeds 80% of the model's context
window (default 200 K tokens), the oldest half of history is summarized via a
one-shot Claude call and replaced with a single user message.  Each
summarization is audited as `tool="conversation_summarize"`.

## Data flow — CLI mutating command

```
User: hpc-pilot qos gpu --max-wall-min 2880 --apply --yes
         │
         ▼
cli.main() → qos_command()
         │
         ├─ check_permission("hpc_slurm_qos_modify", role)  [rbac.py]
         │         raises PermissionError if role < ADMIN
         │         (denial is audited with returncode=126 before raise)
         │
         ├─ (dry_run=False because --apply)
         │
         ├─ with audit_tool(tool, actor, role, args, dry_run=False)  [audit.py]
         │         writes ~/.hpc-pilot/logs/audit.jsonl on exit
         │
         └─ hpc_slurm_qos_modify("gpu", 2880, dry_run=False, cluster="default")
                   │
                   ├─ get_cluster("default") → Cluster  [clusters.py]
                   │
                   └─ _run(["sacctmgr", "--immediate", "modify", ...], cluster=cl)
                             │
                             └─ subprocess.run(shell=False)
```

## Home directory layout

```
~/.hpc-pilot/
├── config.yaml        # clusters:, model:, default_cluster: (auto-created)
├── .env               # Secrets (user provides)
├── auth.json          # {"role": "operator"}
├── skills/            # User-defined skill YAML files
│   └── runs/          # Skill run records (<uuid>.json)
├── sessions/          # Saved chat sessions (<id>.json)
├── jobs/              # Async job records (<uuid>.json)
└── logs/
    └── audit.jsonl    # Append-only audit log (JSONL)
```
