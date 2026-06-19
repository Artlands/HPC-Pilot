# HPC Pilot Documentation

## Contents

- [ARCHITECTURE.md](ARCHITECTURE.md) — Technical architecture, safety model, module map
- [DEPLOYMENT.md](DEPLOYMENT.md) — Installation, configuration, production setup

## Quick reference

```bash
pip install hpc-pilot

hpc-pilot health                          # cluster health check
hpc-pilot nodes [NODE]                    # Slurm node status
hpc-pilot queue [--user U] [--partition P]# job queue
hpc-pilot qos NAME [--max-wall-min N]     # QOS (dry-run by default)
hpc-pilot qos NAME --apply [--yes]        # apply QOS change
hpc-pilot warewulf                        # Warewulf node list
hpc-pilot spack list|find ENV|compilers   # Spack queries
hpc-pilot ansible PLAYBOOK [--apply]      # Ansible playbook
hpc-pilot version                         # version info
```

## Safety model summary

| Default | With `--apply` | With `--apply --yes` |
|---------|---------------|----------------------|
| Dry-run (prints command) | Prompts `[y/N]` | Executes directly |

See [ARCHITECTURE.md](ARCHITECTURE.md) for RBAC roles and audit logging.
