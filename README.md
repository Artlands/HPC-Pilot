# hpc-agent

AI agent that configures and manages an HPC cluster (Warewulf, Ansible, Slurm, Spack).

This repository is the **scaffold + reference implementation** for the specs in
`./agent-specs/`. It implements the foundations (spec 00), the safety layer
(spec 01), and a growing set of Slurm, Ansible, Spack, Warewulf, and workflow surfaces.
`slurm.manage_qos` (spec 05 §1.2) remains the most complete reference implementation for
the full mutating-tool contract.

## What's implemented

| Component | Spec | File |
|-----------|------|------|
| Settings | 00 §7 | `hpc_agent/config/settings.py` |
| Error taxonomy | 00 §3.3 | `hpc_agent/tools/errors.py` |
| ToolResult | 00 §3.2 | `hpc_agent/tools/result.py` |
| `@tool` registry + risk tiers | 00 §3.1 | `hpc_agent/tools/base.py` |
| Command executor (allowlist, redaction) | 00 §4 | `hpc_agent/exec/runner.py` |
| Audit log | 00 §5 | `hpc_agent/exec/audit.py` |
| RBAC | 00 §6 | `hpc_agent/exec/rbac.py` |
| Diff model | 01 §2 | `hpc_agent/safety/diff.py` |
| Policy engine (YAML) | 01 §4 | `hpc_agent/safety/policy.py` |
| Safety gate | 01 §3 | `hpc_agent/safety/gate.py` |
| **State store ORM** | 00 §1.1 | `hpc_agent/state/models.py` |
| **Repositories** | 00 §1.2 | `hpc_agent/state/repos.py` |
| **DB / session mgmt** | 00 §1 | `hpc_agent/state/db.py` |
| **Alembic migrations** | 00 §1 | `migrations/` |
| **Plan / Step models** | 02 §3 | `hpc_agent/core/plan.py` |
| **Topological ordering** | 02 §4 | `hpc_agent/core/ordering.py` |
| **Planner (rule-based)** | 02 §2-3 | `hpc_agent/core/planner.py` |
| **Executor (+ resume)** | 02 §4-5 | `hpc_agent/core/executor.py` |
| **Plan store** | 02 §5 | `hpc_agent/core/planstore.py` |
| **Reference tool: manage_qos** | 05 §1.2 | `hpc_agent/tools/slurm.py` |
| **Slurm account/user tools** | 05 §1.1, §1.3 | `hpc_agent/tools/slurm.py` |
| **Slurm node/reconfigure/query tools** | 05 §2.3-2.4, §3 | `hpc_agent/tools/slurm.py` |
| **Ansible playbook composition** | 04 §2.1 | `hpc_agent/tools/ansible.py` |
| **Ansible inventory generation** | 04 §2.4 | `hpc_agent/tools/ansible.py` |
| **Ansible playbook linting** | 04 §2.2 | `hpc_agent/tools/ansible.py` |
| **Spack query tools** | 06 §1.7 | `hpc_agent/tools/spack.py` |
| **Spack compiler tools** | 06 §1.3 | `hpc_agent/tools/spack.py` |
| **Spack environment tools** | 06 §1.1 | `hpc_agent/tools/spack.py` |
| **Spack modules/views** | 06 §1.5-1.6 | `hpc_agent/tools/spack.py` |
| **Warewulf provisioning** | 03 §1 | `hpc_agent/tools/warewulf.py` |
| **Control plane (git)** | 00 §2, 01 §5 | `hpc_agent/state/configrepo.py` |
| **LLM client scaffold** | 02 §2 | `hpc_agent/core/llm.py` |
| **Workflows (core)** | 07 §1-9 | `hpc_agent/workflows/` |
| CLI (`tools`/`qos`/`plan`/`spack-*`) | 02 §7 | `hpc_agent/core/interaction.py` |
| Virtual cluster | 08 | `deploy/` |

Sample policy lives in `config_repo/policy/`.

## Quickstart

```bash
pip install -e ".[dev]"

# list registered tools + JSON schemas (for LLM tool-calling)
hpc-agent tools

# enable durable operation tracking in SQLite for local testing
HPC_AUDIT_DB_URL=sqlite+pysqlite:////tmp/hpc-agent-audit.sqlite hpc-agent audit-init
HPC_AUDIT_SINK=db HPC_AUDIT_DB_URL=sqlite+pysqlite:////tmp/hpc-agent-audit.sqlite \
  hpc-agent qos gpu --op modify --max-wall-min 2880 --apply
HPC_AUDIT_SINK=db HPC_AUDIT_DB_URL=sqlite+pysqlite:////tmp/hpc-agent-audit.sqlite \
  hpc-agent audit-log --result-status ok

# start an interactive Claude Code / OpenCode-style operator shell
hpc-agent shell
# or launch the split-pane terminal UI
hpc-agent tui
# inside the shell:
#   give alice 48 hours of wall time on the gpu qos
#   /run
#   /approve
#   /tools
#   /help

# dry-run a QOS wall-time extension (needs a sacctmgr on PATH; see tests for stubbing)
HPC_CONFIG_REPO=$PWD/config_repo hpc-agent qos gpu --op modify --max-wall-min 2880
# add --apply to actually execute (gated by policy)

# other Slurm operations follow the same dry-run/apply contract
hpc-agent account research --op modify --grp-tres cpu=512
hpc-agent assoc alice research --qos-add gpu
hpc-agent set-limits qos --name gpu --max-wall-min 2880
hpc-agent node-state gpu01 drain --reason maintenance
hpc-agent node-status --node gpu01
hpc-agent queue --user alice --partition gpu
hpc-agent job-accounting --user alice --start 2026-05-01 --end 2026-05-02
hpc-agent usage-report --user alice --account research --start 2026-05-01 --end 2026-05-02
hpc-agent show-assoc --user alice --account research
hpc-agent diag
hpc-agent reservation maint-gpu create --nodes gpu01 --start 2026-06-01T01:00:00 --duration-min 60
hpc-agent reconfigure

# validate a curated Ansible playbook before apply
hpc-agent manage-inventory
hpc-agent compose-playbook site --target-group compute_cpu --roles common
hpc-agent lint-playbook /etc/hpc-agent/ansible/playbooks/site.yml
hpc-agent run-playbook /etc/hpc-agent/ansible/playbooks/site.yml
hpc-agent check-secret munge/key

# inspect Spack environments and specs without building anything
hpc-agent spack-envs
hpc-agent spack-find gpu-stack
hpc-agent spack-spec "openmpi@5 +cuda"

# manage Spack compilers (find/add, dry-run unless --apply)
hpc-agent spack-compilers --op find --scope site
hpc-agent spack-compilers --op add --scope site --path /opt/gcc/bin --apply

# generate Spack modulefiles
hpc-agent spack-modules gpu-stack --module-type lmod
# manage Spack environments (create/add/remove specs, dry-run unless --apply)
hpc-agent spack-env my-env --op create
hpc-agent spack-env my-env --op add_specs --specs "gcc@13" --specs "openmpi"
hpc-agent spack-env my-env --op remove_specs --specs "gcc@13" --apply

hpc-agent spack-modules gpu-stack --module-type lmod --apply

# create Spack filesystem view
hpc-agent spack-view gpu-stack

# manage Spack buildcache
hpc-agent spack-buildcache push /path/to/mirror

# install packages
hpc-agent spack-install gpu-stack
hpc-agent spack-buildcache update_index /path/to/mirror
hpc-agent spack-buildcache add_mirror /path/to/mirror --apply
hpc-agent spack-view gpu-stack --prefix /opt/modules --apply

# build a plan from a natural-language intent, then optionally execute it
hpc-agent plan "give alice 48 hours of wall time on the gpu qos"
hpc-agent plan "extend the normal qos wall time to 2 days" --apply

# create/update the production state schema
HPC_DB_URL=postgresql+psycopg://hpcagent@localhost/hpc_agent alembic upgrade head
# or pass an explicit URL without touching the environment
alembic -x db_url=sqlite+pysqlite:////tmp/hpc-agent-state.sqlite upgrade head
```

## How to add the next tool

Copy the structure of `manage_qos` in `hpc_agent/tools/slurm.py`. Every mutating tool
follows the spec 00 §3.4 execution contract in order:

1. Define a Pydantic `*In` model; decorate the function with `@tool(name=..., risk=...,
   domain=..., blast_radius=...)`.
2. Open an audit event.
3. Read current state (via `run_command` with `-P`/`--json`; parse structurally).
4. Compute the delta; return an idempotent no-op if there's nothing to change.
5. Build a `Diff` (changes + redacted command preview + blast radius + reversibility).
6. Call `safety_gate.evaluate(...)`.
7. Honor `dry_run`, `denied`, and `needs_approval` before executing.
8. Snapshot config (if the tool edits config files) before mutating.
9. Execute via `run_command`.
10. Record the inverse command(s) for revert, commit the audit event, upsert state.
11. Return a `ToolResult`.

Tests for a new tool should mock `run_command` (see `tests/unit/test_manage_qos.py`) and
cover: dry-run mutates nothing, in-policy auto-apply, out-of-policy deny, idempotent
no-op, not-found precondition, and inverse-command recording.

## Now implemented since the initial scaffold

- State store ORM + repositories + Alembic initial migration (spec 00 §1); mutating Slurm
  tools upsert desired-state rows when the schema has a corresponding row.
- Durable SQL audit operation log with `audit_events` and `audit_commands`, plus
  `hpc-agent audit-init`, `audit-log`, and `audit-show` for tracking applied operations.
- Config-repo git wrapper with audited git operations and rollback primitives
  (spec 00 §2, 01 §5).
- Plan/Step models, topological ordering, rule-based planner, and the executor with
  pause-for-approval and diff-revalidated resume (spec 02 §3-5).
- Slurm account creation/modification, user association management, node drain/resume/down,
  maintenance reservations, controller reconfigure, node status, queue, job accounting,
  usage reporting, diagnostics, and association query tools (spec 05).
- Ansible curated-role playbook composition plus lint/syntax validation tools
  state-store inventory generation, and lint-gated playbook dry-run/apply
  plus secret-reference checks that never expose secret material (spec 04 §2.1-2.5).
- Spack read-only environment/spec query tools for safe software inventory and
  concretization previews (spec 06 §1.7).
- Spack compiler management (`find`/`add`, low-risk, `site`/`env` scope, spec 06 §1.3).
- Spack environment management (`create`/`add_specs`/`remove_specs`, spec 06 §1.1).
- Spack modulefile generation (Lmod/Tcl) and filesystem views (spec 06 §1.5-1.6).
- Approval backends include CLI, mock, and API-pending behavior (spec 01 §3).
- Policy evaluation includes YAML assertions, blast-radius checks, and the sample
  blackout-window rule (spec 01 §4).

## Quality gates (all green)

```bash
ruff check .             # lint
black --check .          # formatting
mypy hpc_agent tests     # strict type check (84 source files)
pytest tests/unit        # 158 unit tests
```

### Progress

| Component | Status | Spec |
|-----------|--------|------|
| **Core foundations** | ✅ Implemented | §00-02 |
| **Slurm tools** | ✅ Broad implementation, strongest coverage | §05 |
| **Ansible tools** | ✅ Implemented and unit-tested | §04 |
| **Spack tools** | ✅ CLI/tool surface implemented; deeper concretize/install fidelity remains future work | §06 |
| **Warewulf tools** | ✅ CLI/tool surface implemented; image-build internals remain reference-level | §03 |
| **Workflows** | ✅ Plan builders implemented | §07 |

The repo is ready for mocked unit development and local CLI dry-runs. It is not yet a
complete production implementation of every acceptance criterion in specs 03-08.

## Still to implement (enhancements, not blockers)

- Full LLM planner integration for open-ended natural language intent processing
- Slack approval backend
- Virtual cluster + integration/eval suites (spec 08: deploy/, tests/integration/, tests/evals/)
- More complete Spack environment semantics: config-repo `spack.yaml`/`spack.lock` edits,
  concretize-on-dry-run, and lockfile diffs
- More complete Warewulf image-build internals and state persistence

Running: `ruff check .` ✅ | `black --check .` ✅ | `mypy hpc_agent tests` ✅ |
`pytest tests/unit` ✅ (158 tests)
