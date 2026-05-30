# HPC Pilot Documentation

Use this index to find the right document for the job you are doing.

## Start Here

- [README.md](README.md): project overview, installation, interfaces, core concepts,
  configuration, and developer workflow.
- [QUICK_START.md](QUICK_START.md): fastest path to a local SQLite-backed development
  setup.
- [USER_GUIDE.md](USER_GUIDE.md): operator and developer guide with command examples,
  audit logging, policies, RBAC, and troubleshooting.

## Operations

- [USER_GUIDE.md](USER_GUIDE.md#interfaces): CLI, shell, and TUI usage.
- [USER_GUIDE.md](USER_GUIDE.md#audit-log): durable operation tracking.
- [USER_GUIDE.md](USER_GUIDE.md#slurm-operations): Slurm operations.
- [USER_GUIDE.md](USER_GUIDE.md#ansible-operations): Ansible operations.
- [USER_GUIDE.md](USER_GUIDE.md#spack-operations): Spack operations.
- [USER_GUIDE.md](USER_GUIDE.md#warewulf-operations): Warewulf tool surface.
- [USER_GUIDE.md](USER_GUIDE.md#policies): policy files and approval behavior.

## Development

- [README.md](README.md#development): checks and tool-extension workflow.
- [USER_GUIDE.md](USER_GUIDE.md#developer-workflow): implementation checklist for new
  tools.
- [agent-specs/README.md](agent-specs/README.md): design reference for core contracts and
  domain tools.

## Deployment and Integration Testing

- [deploy/README.md](deploy/README.md): virtual-cluster overview and current deploy files.
- [deploy/VM-README.md](deploy/VM-README.md): libvirt XML definitions and VM roles.

## Design Reference

The files in `agent-specs/` are product and engineering reference documents. They are not
release notes or progress trackers.

- [00-foundations.md](agent-specs/00-foundations.md): state store, repositories, config
  repo, tool framework, runner, audit, RBAC, settings.
- [01-safety-and-governance.md](agent-specs/01-safety-and-governance.md): diffs,
  approval gates, policy evaluation, rollback, blast radius.
- [02-agent-core.md](agent-specs/02-agent-core.md): plan model, planner, executor,
  resumable plans, interaction layer.
- [03-tool-warewulf.md](agent-specs/03-tool-warewulf.md): Warewulf provisioning tools.
- [04-tool-ansible.md](agent-specs/04-tool-ansible.md): Ansible composition and apply
  tools.
- [05-tool-slurm.md](agent-specs/05-tool-slurm.md): Slurm account, QOS, node, partition,
  reservation, and reporting tools.
- [06-tool-spack.md](agent-specs/06-tool-spack.md): Spack environment, install,
  buildcache, compiler, module, and view tools.
- [07-workflows.md](agent-specs/07-workflows.md): multi-tool workflow plan builders.
- [08-testing.md](agent-specs/08-testing.md): unit, integration, virtual-cluster, and eval
  strategy.

## Command Groups

| Area | Commands |
|------|----------|
| Core | `tools`, `shell`, `tui`, `plan` |
| Audit | `audit-init`, `audit-log`, `audit-show` |
| Slurm | `qos`, `account`, `assoc`, `set-limits`, `show-assoc`, `node-status`, `queue`, `job-accounting`, `usage-report`, `diag`, `node-state`, `reservation`, `reconfigure` |
| Ansible | `compose-playbook`, `manage-inventory`, `lint-playbook`, `run-playbook`, `check-secret` |
| Spack | `spack-envs`, `spack-find`, `spack-spec`, `spack-compilers`, `spack-env`, `spack-buildcache`, `spack-modules`, `spack-view`, `spack-install` |

Run `hpc-pilot --help` for the current command list.
