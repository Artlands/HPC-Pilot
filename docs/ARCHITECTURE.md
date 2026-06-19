# HPC Pilot Architecture

## Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                        USER INTERFACE                                 │
│  ┌──────────────┐  ┌──────────────────────────────────────────────┐  │
│  │   CLI        │  │  Gateway                                     │  │
│  │  (argparse)  │  │  Telegram / Discord                          │  │
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
│  └──────────────┘  └──────────────┘  └──────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────┘
              │
              ▼
┌──────────────────────────────────────────────────────────────────────┐
│                   HPC PILOT TOOLS  (tools.py)                        │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────────────────┐  │
│  │ Slurm    │ │Warewulf  │ │ Ansible  │ │ Spack                  │  │
│  └──────────┘ └──────────┘ └──────────┘ └────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
              │
              ▼
     subprocess.run (shell=False, no injection)
```

## Module map

| File | Purpose |
|------|---------|
| `cli.py` | CLI entry point; command routing; RBAC check; audit context |
| `agent.py` | Claude-powered tool-use loop; `TOOL_SCHEMAS`; `run_chat_loop` |
| `gateway.py` | Telegram + Discord bot server; per-session history |
| `tools.py` | HPC tool functions; `_run` helper; `parse_slurm_nodes` |
| `paths.py` | Home-directory paths (`~/.hpc-pilot/…`) |
| `config.py` | Default config generation |
| `rbac.py` | `Role` enum, `TOOL_MIN_ROLE`, `check_permission`, `get_role` |
| `audit.py` | `audit_tool` context manager; `log_audit` → `audit.jsonl` |

## Data flow — CLI mutating command

```
User: hpc-pilot qos gpu --max-wall-min 2880 --apply --yes
         │
         ▼
cli.main() → qos_command()
         │
         ├─ check_permission("hpc_slurm_qos_modify", role)  [rbac.py]
         │         raises PermissionError if role < ADMIN
         │
         ├─ (dry_run=False because --apply)
         │
         ├─ with audit_tool(tool, actor, role, args, dry_run=False)  [audit.py]
         │         opens ~/.hpc-pilot/logs/audit.jsonl on exit
         │
         └─ hpc_slurm_qos_modify("gpu", 2880, dry_run=False)  [tools.py]
                   │
                   └─ _run(["sacctmgr", "--immediate", "modify", ...])
                             │
                             └─ subprocess.run(shell=False)
```

## Safety gates

### 1. Dry-run by default

Every mutating tool function has a `dry_run: bool = False` parameter.  When
`dry_run=True` the resolved command is returned as a string prefixed with
`"DRY-RUN: "` without calling `subprocess.run`.  CLI commands default to
`dry_run=True`; `--apply` flips it to `False`.

### 2. RBAC

```python
# rbac.py
TOOL_MIN_ROLE = {
    "hpc_slurm_node_status": Role.VIEWER,   # read-only
    "hpc_slurm_qos_modify":  Role.ADMIN,    # dangerous
    "hpc_ansible_playbook_run": Role.ADMIN, # dangerous
    ...
}
```

Role is read from `$HPC_PILOT_ROLE` env var → `~/.hpc-pilot/auth.json` →
default `VIEWER`.

### 3. Approval prompt

For ADMIN-level commands with `--apply`, the user is prompted `[y/N]` unless
`--yes` is also supplied (for non-interactive scripts).

### 4. Audit log

Every tool invocation writes one JSON line to
`~/.hpc-pilot/logs/audit.jsonl`. Fields: `ts`, `actor`, `role`, `tool`,
`args` (secrets redacted), `dry_run`, `returncode`, `duration_ms`, `error`.
I/O errors in audit writing are silently discarded so a full disk never
blocks cluster operations.

## Input validation

`tools.py` validates user-supplied strings before building the subprocess
argv:

- Node names, QOS names, partition names, user names: must match
  `^[a-zA-Z0-9][a-zA-Z0-9_\[\],.-]*$` (reject leading `-` flag injection).
- Unknown filter keys in `hpc_slurm_queue` are rejected.
- Empty playbook path is rejected.
- Invalid node state targets are rejected.

## Error surfacing

The `_run(cmd)` helper raises `RuntimeError` on non-zero exit codes, including
the command's stderr. CLI handlers catch `RuntimeError → exit 1`,
`ValueError → exit 2`.

## Home directory layout

```
~/.hpc-pilot/
├── config.yaml      # Main configuration (auto-created on first run)
├── .env             # Secrets (not auto-created; user provides)
├── auth.json        # {"role": "operator"}
├── skills/          # (planned)
├── sessions/        # (planned)
└── logs/
    └── audit.jsonl  # Append-only audit log
```

## Agent layer

`agent.py` implements the Claude-powered tool-use loop. Every tool in
`TOOL_SCHEMAS` maps 1-to-1 to a function in `tools.py`. The dispatch in
`HpcAgent._execute_tool` applies RBAC and audit before calling the tool,
so all agent-originated calls are subject to the same safety gates as CLI
calls.
