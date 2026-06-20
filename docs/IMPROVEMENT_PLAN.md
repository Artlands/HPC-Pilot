# HPC Pilot — Improvement Plan

This document is the **authoritative work plan** for AI coding agents picking up
the HPC Pilot code base. It captures (1) a frank evaluation of current state,
(2) concrete bugs found by code review, and (3) a phase-ordered improvement
plan with crisp acceptance criteria.

**Document status:** Active. Last updated 2026-06-19.

---

## 0. Instructions for AI agents working from this plan

Read this section **before** picking up any phase below.

### 0.1 How to claim and execute a phase

1. Pick the **lowest-numbered phase whose `Status` field is `Not started`** in
   §6 (Status Tracker). Earlier phases unblock later ones; do not skip ahead.
2. Mark the phase `In progress` in §6 with your run identifier and the date,
   then commit that status change as a `chore(plan):` commit on `main` *before*
   doing the actual work. This makes the claim visible to other agents.
3. Implement the items listed under that phase directly on `main`. Stay inside
   the phase scope — if you find new issues, add them under §7 (Backlog) rather
   than expanding the current phase.
4. For each item you complete, satisfy the acceptance criteria printed at the
   end of the phase. **Run the full test suite, ruff, black, mypy, and the
   tool-name linter** before committing.
5. After all items pass verification, commit the work with a descriptive message
   and push to `main`. Then update §6:
   - Set `Status` to `Done`.
   - Fill in the commit SHA.
   - Commit the status change on `main` as `chore(plan): mark phase <letter> done`.
6. Only then may the next agent claim the next phase.

### 0.2 Status-tracking rules (mandatory)

- **The Status Tracker in §6 is the single source of truth** for what is done.
  Do not infer state from `git log` alone — phases overlap.
- Update §6 in **the same commit** that performs the state transition (claim
  or completion). Never update §6 from a long-lived feature branch — always
  commit status changes directly on `main` (or via a tiny fast-forward PR if
  branch protection requires it).
- If you abandon a phase, set `Status` back to `Not started` and add a one-line
  note under §6.1 "Phase notes" explaining why. Do not leave stale `In progress`
  entries.
- Each completed phase **must be pushed to GitHub** on the `main` branch before
  the next phase begins. The push step is part of completion, not optional.

### 0.3 Commit / push protocol per main phase

For every main phase (Phase A through Phase G):

```text
# 1. Claim
git checkout main && git pull
# edit §6: mark phase <X> "In progress", add your run id + date
git add docs/IMPROVEMENT_PLAN.md
git commit -m "chore(plan): claim phase <X>"
git push origin main

# 2. Work (directly on main)
# ... implement ...
# run full test/lint/typecheck/tool-name suite locally
git add -A
git commit -m "Phase <X>: <description>"
git push origin main

# 3. Mark done
# edit §6: Status -> Done, fill commit SHA
git add docs/IMPROVEMENT_PLAN.md
git commit -m "chore(plan): mark phase <X> done"
git push origin main
```

### 0.4 Scope discipline

- **Do not** refactor outside the phase boundary "while you're in there." Each
  phase is sized as one commit. Drift causes conflicts with adjacent phases.
- **Do not** delete the backward-compat re-exports in
  `hpc_pilot/tools/__init__.py` without an explicit phase covering it — many
  tests patch those paths.
- **Do not** edit the auto-memory at
  `~/.claude/projects/-Users-jieli-Documents-Projects-HPC-Pilot/memory/`. That
  is the user's personal memory; the plan lives in this file only.
- If a phase's premise turns out to be wrong (a bug isn't real, an item is
  already done), document it in §6.1 Phase notes and either skip the item or
  close the phase early. Do not silently invent new work.

### 0.5 Verification expectations

Before declaring any phase Done, all of the following must pass on the
phase branch:

```bash
pytest tests/                              # all green
ruff check hpc_pilot/ tests/ scripts/      # clean
black --check hpc_pilot/ tests/ scripts/   # clean
mypy hpc_pilot/                            # clean (or scoped per Phase A.A5)
python scripts/check_tool_names.py         # clean
# Phase A also adds:
python scripts/check_no_direct_subprocess.py
```

If a verification command was already broken on `main` when the phase started,
note that in the PR description and treat fixing it as part of the phase only
if the fix is in scope; otherwise file it in §7 Backlog.

---

## 1. Snapshot of current state (2026-06-19)

- **Size:** ~14,300 LOC across `hpc_pilot/` (35 modules), 357 tests collected.
- **Tools registered:** 119 `@hpc_tool` decorators across `tools/`. README still
  says 114, plugin says 93+ — these numbers drift.
- **Test status:** 354 pass, **3 fail** in `tests/test_safety.py` (regressions
  from the recent CLI split).
- **Lint/typecheck:** Ruff/Black/mypy(`strict`) wired in CI; `scripts/check_tool_names.py`
  enforces naming.

### What's working well

| Area | Notes |
|---|---|
| Canonical registry (`tools/_registry.py`) | Single `@hpc_tool` decorator drives schemas, RBAC and dispatch. |
| Safety layering | RBAC → rate limiter → out-of-band approval → audit-context-manager → dispatch is consistent. |
| Multi-cluster | `Cluster` dataclass + transparent SSH wrapping in `_run()` is clean, frozen, mtime-cached. |
| Audit sinks | Pluggable `FileSink` / `SyslogSink` / `HttpSink` with backpressure and secret-key redaction. |
| Self-evolve guardrails | AST whitelist + dangerous-call denylist; SUPERADMIN + out-of-band approval gate. |
| Skills DSL | YAML runbooks with templating, pause/resume, `wait_until` / `for_each` builtins. |

---

## 2. Concrete bugs found by review

These are referenced by ID (`B1` … `B14`) from the phase items below.

| ID | Location | Symptom |
|---|---|---|
| **B1** | `tests/test_safety.py:274,292,309` | `patch("hpc_pilot.cli._confirm")` and `hpc_pilot.cli.get_role` fail — those attrs moved to `_cli_base.py` / `rbac.py` after the recent CLI split. **3 failing tests on `main`.** |
| **B2** | `hpc_pilot/skills/runner.py:313` | Reads `step.risk_summary`, but `SkillStep` (lines 56-62) has no such field. Any skill step with `approval: required` raises `AttributeError`. |
| **B3** | `hpc_pilot/tools/multi.py:39-43` | `_query_single` calls `_dispatch()` directly. **`hpc_multi_query` is not audited**. RBAC is hand-rolled; audit context manager never entered. |
| **B4** | `tools/metrics.py:618,625,772,779`; `tools/ansible.py:82,215,224,435,497`; `tools/warewulf.py:153,744–921,1231`; `tools/system.py:864,884` | Direct `subprocess.run` bypasses `_run()` → ignores `Cluster.ssh` wrapping. **Tools silently run on the *local* host when targeting a remote cluster.** Violates the documented invariant. |
| **B5** | `hpc_pilot/metrics.py` | Prometheus counters defined but never `.inc()`d. `/metrics` returns empty series. |
| **B6** | `hpc_pilot/agent.py:139` | `self.summarize` stored but never read. `docs/ARCHITECTURE.md §Context budget` notes summarization regressed when delegation moved to a `hermes` subprocess. |
| **B7** | `hpc_pilot/agent.py:141-187` | `run_turn` shells out to `hermes chat -q` once per turn. History parameter is informational only — Telegram/Discord conversations have no actual memory across turns. |
| **B8** | `hpc_pilot/_cli_admin.py:171-177` | `config get <key>` ignores `<key>` and dumps the whole config. |
| **B9** | `hpc_pilot/webui.py:36-54` | Reuses `secret` var as both filesystem path and token value; on path error silently falls back to literal `"hpc-pilot-webui-fallback-secret-do-not-use-in-production"`. |
| **B10** | various docs | README says 93+/114 tools, registry has 119. `docs/IMPLEMENTATION_PLAN.md` deleted but referenced elsewhere. |
| **B11** | `hpc_pilot/audit.py:23` | Redaction only inspects argument *keys*. Secrets in argument *values* (e.g. inside `cmd` strings) leak into the audit log. |
| **B12** | `hpc_pilot/clusters.py` | `_invalidate_cluster_cache()` never called by config-edit commands. Long-running gateway picks up changes only via mtime; not exercised by tests. |
| **B13** | `hpc_pilot/tools/_run.py:32-43` | SSH command uses `-o BatchMode=yes -i <key>` but never sets `StrictHostKeyChecking` or `UserKnownHostsFile`. First-time connections may stall or weaken security depending on local config. |
| **B14** | CI workflow | mypy `strict=true` combined with heavy `Any`/dynamic introspection in `_registry.py`/`dispatch.py` — current CI is likely red on a clean checkout, or strict is aspirational only. Needs verification. |

---

## 3. Architectural concerns (not bugs, tracked as tech debt)

1. **God-modules.** `tools/system.py` (1510 LOC), `tools/warewulf.py` (1294),
   `tools/slurm.py` (1066), `tools/metrics.py` (863). Each mixes domains.
2. **Dual dispatch surfaces.** CLI uses `invoke()`; chat agent uses Hermes
   subprocess; gateway uses `HpcAgent.run_turn`. Three paths, three sets of
   bugs (see B3, B4, B7).
3. **`tools/__init__.py` re-exports** are ~167 lines maintained for test
   patches. Decide whether this is the public surface or trim it.
4. **Self-evolve runs in-process.** AST whitelist is reasonable but there is
   no sandbox between "validated AST" and "imported into the live daemon."
5. **No structured SSH-path testing.** B4 is the visible symptom.
6. **No CI coverage report** despite `pytest-cov` being a dev dep.
7. **mypy `strict`** plus registry introspection creates friction. Either
   relax per-package or add explicit `Protocol`s.
8. **Unbounded gateway sessions.** `TelegramGateway.sessions` and
   `DiscordGateway.sessions` grow keyed by chat/user ID with no TTL or LRU.

---

## 4. Out of scope for this plan

These exist in the repo but are not addressed here. Add to §7 if you want to
schedule them:

- New tool subsystems (e.g. Kubernetes, Lustre HSM).
- Full TUI implementation (currently a stub).
- Provider migrations beyond what `hermes-agent` already supports.
- Documentation translation / localization.

---

## 5. Phase-ordered improvement plan

Each phase is independently shippable. Items are tagged with the bug IDs from
§2 where applicable.

### Phase A — Regression triage (P0)

**Goal:** Get `main` back to green and patch the load-bearing bugs that
undermine the project's "safe, audited, multi-cluster" promise.

- [ ] **A.1** Fix `test_safety.py` patch targets (B1). Either re-export
  `_confirm` and `get_role` from `hpc_pilot.cli`, or repoint the patches at
  `hpc_pilot._cli_slurm._confirm` / `hpc_pilot._cli_slurm.get_role`. Prefer
  re-export so the public CLI surface stays single-sourced.
- [ ] **A.2** Add `risk_summary: str = ""` field to `SkillStep` and parse it in
  `_parse_skill` (B2). Add a regression test that runs a skill with an
  `approval: required` step and asserts the approval record carries the
  step's `risk_summary`.
- [ ] **A.3** Route `hpc_multi_query` through `invoke()` (B3). Drop the
  hand-rolled `_check_rbac` path; the per-cluster call must call
  `invoke(tool, args | {"cluster": c}, role=..., actor=...)` so audit, RBAC,
  and rate-limiter all fire per cluster. Add a test asserting one audit
  record per cluster.
- [ ] **A.4** Ban direct `subprocess.run` in `hpc_pilot/tools/` outside
  `_run.py` and `evolve.py` (B4). Add `scripts/check_no_direct_subprocess.py`
  and wire into CI. Convert offenders in `tools/metrics.py`,
  `tools/ansible.py`, `tools/warewulf.py`, `tools/system.py` to `_run(...)`.
  For pipelines (`tail | grep`, `journalctl | grep`), do the filtering in
  Python on `_run()` output rather than spawning a second local process.
- [ ] **A.5** Verify mypy actually passes on a clean checkout (B14). If it
  doesn't, scope `strict` via `[[tool.mypy.overrides]]` per-module so CI does
  not silently false-green.

**Acceptance criteria:**

- `pytest tests/ -q` — 0 failures.
- `mypy hpc_pilot/` — 0 errors (or explicitly scoped via overrides documented
  in the PR).
- `ruff check`, `black --check`, `python scripts/check_tool_names.py`,
  `python scripts/check_no_direct_subprocess.py` — all clean.
- Manual: instantiate a `Cluster` with `ssh=...`, invoke each previously
  offending tool with `monkeypatch.setattr(subprocess, "run", ...)` and
  assert the captured argv begins with `["ssh", "-o", "BatchMode=yes", ...]`.

---

### Phase B — Audit / observability integrity (P1)

**Goal:** Make `/metrics` non-empty and ensure the audit log is actually
trustworthy.

- [ ] **B.1** Wire Prometheus counters (B5). In `audit.log_audit()` and
  `dispatch.invoke()`, increment `tool_calls_total`,  `denials_total`,
  `sink_errors_total` and observe `tool_call_duration_seconds`. Gate via
  `_HAS_PROMETHEUS`. Add `tests/test_metrics.py` that drives the dispatch
  path and asserts counter deltas.
- [ ] **B.2** Value-side secret redaction (B11). Extend `_redact` to scrub
  regex-matched tokens in argument *values*, not just keys. Cover at minimum:
  `sk-[A-Za-z0-9_-]{20,}`, `ghp_[A-Za-z0-9]{36}`, `xox[abprs]-[A-Za-z0-9-]{10,}`,
  `Bearer\s+[A-Za-z0-9._-]+`. Add tests with positive and negative cases.
- [ ] **B.3** Audit log rotation. `prune_audit_log()` exists but is only
  invoked manually. Add a size-bounded rotation in `FileSink.write` (rotate at
  configurable cap, default 100 MB → `audit.jsonl.1`, `.2`, retain 5).
  Configurable via `audit.rotation` in `config.yaml`.
- [ ] **B.4** Gateway session TTL/LRU. Bound `TelegramGateway.sessions` and
  `DiscordGateway.sessions` (default max 500, idle 30 min). Wire
  `active_gateway_sessions` gauge.

**Acceptance criteria:**

- `curl http://127.0.0.1:8000/metrics` after one CLI command produces
  non-empty series for `hpc_tool_calls_total`.
- Synthetic 200 MB audit-write test shows rotation kicked in and only 5 files
  remain.
- `tests/test_audit_sinks.py` covers value-side redaction.

---

### Phase C — Multi-cluster correctness (P1)

**Goal:** Make the SSH/remote-cluster path testable and pin its security
posture.

- [ ] **C.1** SSH integration tests. Use `monkeypatch` to stub
  `subprocess.run` and assert the exact argv `_run()` produces when
  `Cluster.ssh` is set: BatchMode, ConnectTimeout, optional ControlPath,
  shquote of remote args, timeout extension.
- [ ] **C.2** `StrictHostKeyChecking` policy (B13). Add
  `ssh.host_key_check: yes|accept-new|no` (default `accept-new`) and
  `ssh.known_hosts: <path>` to the `Cluster`/`SSHConfig` schema; thread
  through to `_run()`. Document in `DEPLOYMENT.md`.
- [ ] **C.3** Per-cluster observability config (extends
  `tools/metrics.py:_cluster_prometheus_url` already started). Add
  `clusters.<name>.observability.prometheus.url` to the documented config and
  add cluster-scoped `SecretsManager` lookups for Prometheus auth.
- [ ] **C.4** Reload-on-edit (B12). Add `hpc-pilot config reload` that calls
  `_invalidate_cluster_cache()` and resets `_SINKS_LOADED` and the rate
  limiter. Document.

**Acceptance criteria:**

- New test file `tests/test_ssh_wrapping.py` covering 4+ argv shapes.
- `hpc-pilot config reload` exits 0 and subsequent commands pick up edited
  `config.yaml` without process restart (covered by a test that edits the
  config file mid-test).

---

### Phase D — Agent + chat memory (P2)

**Goal:** Multi-turn conversations actually remember context. Fix small
correctness bugs in the same area.

- [ ] **D.1** Make `HpcAgent` carry multi-turn state (B7). Two options;
  pick one and document why:
  - *Cheap:* if `hermes chat` supports a session id, thread it through
    `run_turn` so subsequent turns reuse the same session.
  - *Right:* switch the gateway/agent path to call the provider SDK directly
    with `tools=TOOL_SCHEMAS` and route tool calls through `dispatch.invoke()`.
    Keep `hermes chat` only for the interactive `hpc-pilot chat` exec path.
- [ ] **D.2** Token-budget summarization (B6). Re-implement the summarize
  loop the docs already acknowledge regressed. Wire `self.summarize` or
  remove it.
- [ ] **D.3** `config get <key>` actually parses dotted keys (B8). Add tests.
- [ ] **D.4** WebUI secret (B9): refuse to start when the fallback would be
  triggered unless `HPC_PILOT_WEBUI_INSECURE=1`. Print a clear error.

**Acceptance criteria:**

- Manual: a 3-turn Telegram conversation referencing prior context produces
  coherent answers.
- Synthetic 50 KB transcript test passes the summarizer without exceeding
  context window.
- WebUI refuses insecure start without the override flag; the override path is
  logged at WARN level.

---

### Phase E — Tooling & modularization (P2)

**Goal:** Make the code base easier to navigate and harder to silently break.

- [ ] **E.1** Split god-modules:
  - `tools/system.py` → `tools/users.py`, `tools/services.py`, `tools/storage.py`, `tools/audit_tools.py`.
  - `tools/warewulf.py` → `tools/warewulf/images.py`, `nodes.py`, `overlays.py`, `power.py`, `services.py`.
  - `tools/metrics.py` → `tools/metrics/prometheus.py`, `tools/observability/logs.py`, `tools/observability/gpu.py`.
  Keep the backward-compat re-exports in `hpc_pilot/tools/__init__.py`.
- [ ] **E.2** CI coverage: `pytest --cov=hpc_pilot --cov-report=xml --cov-fail-under=75`.
- [ ] **E.3** Doc cleanup (B10): regenerate tool counts from the registry in
  `README.md`, `docs/ARCHITECTURE.md` and `hpc_pilot/hermes_plugin/__init__.py`
  docstrings. Decide whether to recreate `docs/IMPLEMENTATION_PLAN.md` (point
  it at this document) or remove the references.
- [ ] **E.4** Remove `HpcAgent.summarize` if Phase D didn't use it.

**Acceptance criteria:**

- No module under `hpc_pilot/` exceeds 700 LOC.
- CI uploads `coverage.xml` as an artifact; `--cov-fail-under=75` enforced.
- Tool count in README matches `len(get_registry())`.

---

### Phase F — Self-evolve hardening (P3)

**Goal:** Make self-evolve safe enough to leave on by default for trusted
operators.

- [ ] **F.1** Sandboxed execution. Generated tools currently run in-process.
  Move the post-generation `pytest` step into a subprocess in a fresh venv (or
  a restricted subinterpreter / no-network). Document the threat model.
- [ ] **F.2** Pre-import staging. Before adding
  `from hpc_pilot.tools.evolved.X import X` to `tools/__init__.py`, write the
  candidate under `evolved/staging/`. Require a separate
  `hpc_self_evolve_promote` step before it lands in the loaded namespace.
  The existing `hpc_self_evolve_create_pr` covers part of this — coordinate.
- [ ] **F.3** Schema validation. Run the generated `input_schema` through
  `jsonschema.Draft202012Validator.check_schema`.

**Acceptance criteria:**

- A generated tool with `os.system("rm -rf /")` is caught both by the AST
  whitelist (existing) *and* the new sandbox (defense in depth).
- A generated tool with a malformed JSON schema is rejected before it touches
  the registry.

---

### Phase G — Operational polish (P3)

**Goal:** Make HPC Pilot operate as a service, not just a CLI.

- [ ] **G.1** Daemon mode (`hpc-pilot daemon`) for gateway + webui + scheduled
  health, with a `systemd` unit shipped under `packaging/`.
- [ ] **G.2** Implement or remove the `cron` and `tui` subcommands — they
  currently print "not yet implemented."
- [ ] **G.3** Surface `--cluster` in every subcommand `--help`, not just at
  the top-level parser.
- [ ] **G.4** Replace the curl-based GitHub PR call in `tools/evolve.py:499`
  with `urllib.request` to match the rest of the code base.

**Acceptance criteria:**

- `systemctl start hpc-pilot` starts gateway + webui on a clean VM with only
  `pip install hpc-pilot` and a populated `~/.hpc-pilot/.env`.
- `hpc-pilot <any-subcommand> --help` mentions `--cluster`.

---

## 6. Status Tracker

**This table is the source of truth for phase completion. Update it as part of
every claim. Push the change to `main`.**

| Phase | Title | Priority | Status | Claimed by | Claim date | Commit SHA |
|---|---|---|---|---|---|---|
| A | Regression triage | P0 | Done | claude-code | 2026-06-19 | d08f88d |
| B | Audit / observability integrity | P1 | Done | claude-code | 2026-06-19 | d08f88d |
| C | Multi-cluster correctness | P1 | Done | claude-code | 2026-06-19 | 0a15688 |
| D | Agent + chat memory | P2 | Done | claude-code | 2026-06-19 | 216b802 |
| E | Tooling & modularization | P2 | Done | claude-code | 2026-06-19 | c5c267a |
| F | Self-evolve hardening | P3 | Done | claude-code | 2026-06-19 | 91171d2 |
| G | Operational polish | P3 | Not started |  |  |  |

Allowed values for `Status`: `Not started`, `In progress`, `Blocked`, `Done`.

### 6.1 Phase notes

Free-form notes per phase. Use this to record blockers, scope changes, or
reasons a phase was abandoned. Keep entries short and dated.

> _(no entries yet)_

---

## 7. Backlog

New issues discovered while executing a phase go here, not into the active
phase. Each entry must include a short title, the reporting agent's identifier,
and the date. The maintainer schedules them into a future phase.

> _(no entries yet)_

---

## 8. Recommended sequencing for parallel agents

1. **One agent** picks up Phase A end-to-end (this gets `main` healthy).
2. After A merges, **fan out** B, C, D as independent branches — they touch
   largely disjoint files.
3. **E** waits for A–C to keep merge conflicts manageable.
4. **F, G** can run in parallel with E.

The most important phase to land first is **Phase A**, because the failing
safety tests (B1), the silent multi-cluster regression (B4), and the
un-audited multi-query path (B3) together undermine the project's headline
value proposition: safe, audited cluster operations across multiple sites.
