# HPC Pilot — Improvement Plan (Round 2)

This document is the **authoritative work plan** for AI coding agents picking up
the HPC Pilot code base after the round-1 plan (Phases A–G) was completed and
merged.

It captures:

1. A fresh evaluation of the current state, *after* Phases A–G shipped.
2. Concrete regressions and new bugs introduced or left over by the round-1
   work.
3. A phase-ordered improvement plan with crisp acceptance criteria so an agent
   can claim a single phase, ship it, and update the tracker.

**Document status:** Active. Round 2. Created 2026-06-20.
**Round-1 history (Phases A–G, all merged):** `88f076e`, `16c3957`, `91171d2`,
`c5c267a`, `216b802`, `0a15688`, `d08f88d`. See `git log --oneline`.

---

## 0. Instructions for AI agents working from this plan

Read this section **before** picking up any phase below.

### 0.1 How to claim and execute a phase

1. Pick the **lowest-numbered phase whose `Status` field is `Not started`** in
   §6 (Status Tracker). Earlier phases unblock later ones; do not skip ahead.
2. Mark the phase `In progress` in §6 with your run identifier and the date,
   then commit that status change directly on `main` as a `chore(plan):`
   commit **before** doing the actual work. This makes the claim visible to
   other agents in parallel.
3. Cut a feature branch named `phase2-<letter>-<short-slug>`, e.g.
   `phase2-h-ci-green`.
4. Implement the items listed under that phase. Stay inside the phase scope.
   If you find new issues, add them under §7 (Backlog) rather than expanding
   the active phase.
5. For each item you complete, satisfy the acceptance criteria printed at the
   end of the phase. **Run the full lint / type / test stack locally** before
   opening a PR (see §0.5).
6. Open a pull request titled `Phase <letter>: <phase name>`. Reference this
   document in the PR body. Include the per-item checklist from the phase as
   a task list in the PR description.
7. **After the PR is merged**, push and update §6:
   - Set `Status` to `Done`.
   - Fill in the merged commit SHA and PR URL.
   - Commit the status change directly on `main` as
     `chore(plan): mark phase <letter> done`.
8. Only then may the next agent claim the next phase.

### 0.2 Status-tracking rules (mandatory)

- **The Status Tracker in §6 is the single source of truth** for what is done.
  Do not infer state from `git log` alone — round-1 left a trail of phase
  commits and round-2 will too.
- Update §6 in **the same commit** that performs the state transition (claim
  or completion). Never update §6 from a long-lived feature branch — always
  commit status changes directly on `main` (or via a tiny fast-forward PR if
  branch protection requires it).
- If you abandon a phase, set `Status` back to `Not started` and add a
  one-line note under §6.1 "Phase notes" explaining why. Do not leave stale
  `In progress` entries.
- Each completed phase **must be pushed to GitHub** on the `main` branch
  before the next phase begins. The push step is part of completion, not
  optional.

### 0.3 Commit / push protocol per main phase

For every main phase (Phase H through Phase N):

```text
# 1. Claim
git checkout main && git pull
# edit §6: mark phase <X> "In progress", add your run id + date
git add docs/IMPROVEMENT_PLAN.md
git commit -m "chore(plan): claim phase <X>"
git push origin main

# 2. Work
git checkout -b phase2-<X>-<slug>
# ... implement ...
# run full lint/type/test stack locally — see §0.5
git push -u origin phase2-<X>-<slug>

# 3. PR + merge
gh pr create --title "Phase <X>: <name>" --body-file <prepared-body>
# after review/merge:

# 4. Mark done + push
git checkout main && git pull
# edit §6: Status -> Done, fill commit SHA + PR URL
git add docs/IMPROVEMENT_PLAN.md
git commit -m "chore(plan): mark phase <X> done"
git push origin main
```

**Never skip the final `git push origin main`.** A merged PR that hasn't
updated §6 leaves the next agent unable to know the phase is finished.

### 0.4 Scope discipline

- **Do not** refactor outside the phase boundary "while you're in there."
  Each phase is sized to fit one PR. Drift causes merge conflicts with
  parallel agents.
- **Do not** delete `hpc_pilot/tools/warewulf.py` (the shim) or the
  re-export tables in `hpc_pilot/tools/__init__.py` without an explicit phase
  covering it — many existing tests still patch those paths.
- **Do not** edit the auto-memory at
  `~/.claude/projects/-Users-jieli-Documents-Projects-HPC-Pilot/memory/` —
  that is the user's personal memory; the plan lives in this file only.
- If a phase's premise turns out to be wrong (a bug isn't real, an item is
  already done), document it in §6.1 Phase notes and either skip the item or
  close the phase early. Do not silently invent new work.

### 0.5 Verification expectations (the local "green" stack)

Before declaring any phase Done, all of the following must pass on the
phase branch:

```bash
pytest tests/                              # zero failures, zero hangs
ruff check hpc_pilot/ tests/ scripts/      # zero findings
black --check hpc_pilot/ tests/ scripts/   # zero reformatted files
mypy hpc_pilot/                            # zero errors (subject to Phase H scope)
python scripts/check_tool_names.py         # zero findings
python scripts/check_no_direct_subprocess.py
```

If `main` is already red on one of these checks when you start your phase,
treat fixing the relevant subset as part of Phase H. After Phase H ships,
later phases must not regress green.

### 0.6 Local test-suite hang guard

`pytest tests/` currently **hangs** on
`tests/test_cli.py::TestMain::test_main_no_command_returns_1` when the
`hermes` binary is installed on PATH, because the test calls `main([])`
which defaults to `chat_command` → `os.execvp("hermes", ...)`. Phase H
fixes this. Until then, when running the suite locally:

```bash
pytest tests/ --deselect tests/test_cli.py::TestMain::test_main_no_command_returns_1
```

Do **not** ship a workaround that disables the test; fix it as part of
Phase H.

---

## 1. Snapshot of current state (2026-06-20)

- **Size:** ~13,450 LOC across `hpc_pilot/` (40+ source files including the
  new submodules under `tools/warewulf/`, `tools/metrics/`,
  `tools/observability/`).
- **Tools registered:** 117 `@hpc_tool` decorators. README docstring still
  says 112; plugin docstring also says 112. Mismatch (B-stale).
- **Test status:** 385 tests collected. **The full suite hangs** mid-run on
  `test_main_no_command_returns_1` (see §0.6). Up through `test_cli.py`
  ~30 tests, then stall.
- **Lint:** ruff finds 157 issues (116 E501, 45 F401, 10 I001, 3 F821, 1
  F841). The F821 ones are real bugs.
- **Format:** black would reformat 39 files.
- **Types:** mypy `strict` reports 32 errors (mostly mypy's no-implicit-reexport
  on the round-1 warewulf split + a missing-return in the daemon).
- **CI claims to enforce all of the above**, but `main` is currently in a
  state where every CI step except pytest would be red. CI may be silently
  failing or being ignored.

### What round-1 actually delivered

| Phase | Title | Delivered |
|---|---|---|
| A | Regression triage | `test_safety.py` patches fixed, `SkillStep.risk_summary` added, `hpc_multi_query` rerouted, `check_no_direct_subprocess.py` lint added. |
| B | Audit/observability | Prometheus counters wired in `dispatch.invoke()` + `audit.log_audit()`, value-side secret redaction, file rotation, gateway session TTL/LRU. |
| C | Multi-cluster correctness | `tests/test_ssh_wrapping.py` added (but has F821 bugs), `host_key_check` plumbed, `hpc-pilot config reload` added. |
| D | Agent + chat memory | `HpcAgent._hermes_session_id` + `--resume` flag, `reset_session()`, `config get <key>` parses dotted keys, webui secret guard. |
| E | Tooling / modularization | Split `tools/system.py` into `users.py`, `services.py`, `storage.py`, `audit_tools.py`. Split `tools/warewulf.py` into `warewulf/{images,nodes,overlays,power,services}.py` (kept `warewulf.py` shim). Split `tools/metrics.py` into `tools/metrics/prometheus.py` + `tools/observability/{logs,gpu}.py`. CI coverage step added (`--cov-fail-under=75`). |
| F | Self-evolve hardening | Sandboxed pytest subprocess, staging directory, schema validation via `jsonschema`. |
| G | Operational polish | `hpc-pilot daemon` with PID files, `cron` + `tui` implemented, `--cluster` flag on every subparser, `curl` replaced with `urllib.request` in `tools/evolve.py`. systemd units shipped under `packaging/systemd/`. |

So the round-1 work is substantive and largely correct in shape. What it
**did not do** was keep the lint/type/test stack green through the
modularization. Round 2 is mostly about cleanup, hardening, and closing the
gaps in round-1's own deliverables.

---

## 2. Concrete bugs found by round-2 review

Referenced by ID (`R1` … `R12`) in the phase items below.

| ID | Location | Symptom |
|---|---|---|
| **R1** | `tests/test_cli.py::TestMain::test_main_no_command_returns_1` | `main([])` defaults to `chat_command` which calls `os.execvp("hermes", ...)`. When hermes is installed (which it is in `dev` extras), this **replaces the pytest process**, hanging or zombifying the suite. The full local test run never finishes. |
| **R2** | `hpc_pilot/skills/runner.py:450` (`_execute_for_each`) | References `ctx` which is not a parameter of the function and not in scope. **F821**: skill builtin `for_each` crashes the instant its `items` arg starts with `"ctx["`. Round-1 Phase A.2 fixed `risk_summary` but did not touch this. |
| **R3** | `tests/test_ssh_wrapping.py:14,23` | Function signatures `-> Any` but `Any` is never imported. **F821** at import time. Phase C's new test file was never CI-verified. |
| **R4** | `hpc_pilot/_cli_daemon.py:153` (`_daemon_start`) | Declared `-> int`. The grandchild path runs `_monitor_loop` (infinite while-True) and never returns, so mypy `[return]` error. Functional, but symptom of "type system doesn't model infinite loops." Tag the function with `-> NoReturn` on the grandchild branch via a helper, or assert-unreachable. |
| **R5** | `hpc_pilot/tools/warewulf.py` (8 lines) vs. `hpc_pilot/tools/warewulf/__init__.py` | Both exist. Python imports the package (`warewulf/__init__.py`) and the module file (`warewulf.py`) is **dead code**. It is loaded only if the package is removed. Confusing and broken-by-design. Either delete the shim or convert the package back into a single module with proper re-exports. |
| **R6** | `hpc_pilot/tools/__init__.py:155` (and `_cli_system.py:26`, `tools/health.py:254`) | After Phase E split, mypy `--no-implicit-reexport` (strict default) flags 27 `[attr-defined]` errors on names re-exported via `from hpc_pilot.tools.warewulf import (...)`. Either add an explicit `__all__` to each submodule or rewrite as `from .x import y as y`. |
| **R7** | `hpc_pilot/audit.py:355` | `from hpc_pilot.metrics import _HAS_PROMETHEUS, sink_errors_total, tool_calls_total` — `sink_errors_total` is imported but never `.inc()`'d. Round-1 Phase B.1 wired `tool_calls_total` and `denials_total` (via `_inc_metric` in dispatch.py) but missed the sink-error counter. Sink failures are silently swallowed without a metric tick. |
| **R8** | Ruff 116 × E501 | Many `description` strings in `@hpc_tool(schema=...)` exceed 100 cols. Phase E split made some of them worse. Either reformat or raise the ruff line-length cap to 120 (project convention is currently 100). |
| **R9** | Ruff 45 × F401 | Dead imports in `hpc_pilot/clusters.py` (`time`), `hpc_pilot/hermes_plugin/__init__.py` (`sys`, `Any`), `hpc_pilot/metrics.py` (`push_to_gateway`), `hpc_pilot/audit.py` (`sink_errors_total` — see R7), `hpc_pilot/tools/ansible.py` (`subprocess`, side-effect of A.4 not cleaning up the import), and many test files. Easy clean-up but masks real dead-code warnings. |
| **R10** | `hpc_pilot/tools/warewulf/__init__.py:7-9` | `import json`, `import os`, `import subprocess` are kept "for backward compat with tests that patch `hpc_pilot.tools.warewulf.subprocess`." Each is `# noqa: F401`. This pins the *implementation* shape to the *test* shape; round-1 Phase A.4 banned `subprocess.run` in tools/ but here it's still imported just for the patch. Reconsider. |
| **R11** | `README.md:226`, `hpc_pilot/hermes_plugin/__init__.py:3` | Still says "112 tools." Registry actually has 117 (count of `@hpc_tool` decorators). Round-1 Phase E.3 was supposed to autoregen; it did not. |
| **R12** | `hpc_pilot/agent.py` (`_parse_session_id`) | Assumes Hermes prints `session_id: <id>` on stderr. This is brittle and undocumented in Hermes. There is no test asserting the parse handles a missing line; the agent silently degrades to single-turn. Worth a contract test (mock stderr → assert id captured) or a structured-output flag from Hermes if it supports one. |

---

## 3. Architectural concerns (not bugs, tracked as tech debt)

1. **CI is silently broken.** The workflow runs `ruff`, `black`, `mypy`,
   `pytest`, `--cov-fail-under=75`, and tool-name lint, but `main` would fail
   the first three. Either CI hasn't run, or the team is ignoring red
   builds. Either way, Phase H must restore CI as a load-bearing signal.
2. **`tools/warewulf.py` shim is technical-debt theatre.** It only exists for
   test patching, but tests can patch the package just as well. Same pattern
   leaking into `tools/metrics.py` (1-liner re-export) and
   `tools/__init__.py` (180 lines of re-exports).
3. **Daemon detaches via `os.fork()` twice.** Pragmatic but untestable — calling
   `_daemon_start` inside pytest forks the pytest process. There is no unit
   test for `daemon_command`; the systemd unit is the only integration test
   surface, and it's not exercised in CI. We need a `--foreground` flag or a
   strategy-pattern abstraction.
4. **Cron and TUI are blocking loops in the CLI process.** Fine for a
   foreground tool, but the test suite has no coverage of them. They share
   nearly all logic with `_monitor_loop` in `_cli_daemon.py` — duplication
   that should be consolidated.
5. **`hpc_pilot/metrics.py` no-op shim** uses class-level `_Noop` as both
   metric and registry. When `prometheus_client` is absent, `inc()`/`observe()`
   are no-ops but `labels(**)` returns `self`, which is fine. But
   `clusters_total.set_function(...)` is referenced nowhere — dead capability.
6. **The session-id parsing in `HpcAgent._parse_session_id`** is the only thing
   binding round-1 Phase D's multi-turn fix to reality. If Hermes ever changes
   that log line, the gateway silently loses memory and no test catches it.

---

## 4. Out of scope for this plan

Listed so an agent doesn't drift into them. Add to §7 if you want them
scheduled.

- New tool subsystems (Kubernetes, Lustre HSM, BeeGFS, OpenLDAP).
- Full chat history persistence in the WebUI.
- A real provider abstraction layer (round-1 D.1 picked the "cheap" option).
- Documentation translation / localization.

---

## 5. Phase-ordered improvement plan (Round 2)

Phases are lettered `H`…`N` (continuing from round-1's A–G) and are
independently shippable. Items are tagged with the R-IDs from §2.

### Phase H — Make CI a real signal again (P0)

**Goal:** Every check in `.github/workflows/ci.yml` is green on `main`. No
hangs, no F821, no implicit-reexport violations.

- [ ] **H.1** Fix `test_main_no_command_returns_1` (R1). Either:
  - Pass an explicit subcommand so `chat_command` is never reached, **or**
  - Add a `--no-exec` / dependency-injection seam to `run_chat_loop` and have
    the test patch it.
  Whichever you pick, document it in the PR. The acceptance bar is:
  `pytest tests/` completes and returns 0 in < 60 s on a machine with the
  `hermes` binary installed.
- [ ] **H.2** Fix `for_each` `ctx` reference (R2). Pass `ctx` through
  `_execute_for_each`'s signature (and update `_execute_step` to forward it).
  Add a regression test with a skill that uses `items: "ctx[some_step_id]"`.
- [ ] **H.3** Fix `tests/test_ssh_wrapping.py` F821s (R3). Add
  `from typing import Any`. Verify the file is actually exercised.
- [ ] **H.4** Fix mypy `[return]` on `_daemon_start` (R4). Refactor the
  grandchild branch into a helper typed `-> NoReturn` (since `_monitor_loop`
  is an infinite while-True).
- [ ] **H.5** Resolve the `warewulf.py` vs `warewulf/` collision (R5).
  Recommended: delete the shim file; ensure `from hpc_pilot.tools.warewulf
  import *` still works via the package's `__init__.py`. Update tests that
  patch `hpc_pilot.tools.warewulf.<name>`.
- [ ] **H.6** Resolve mypy implicit-reexport violations (R6). For each split
  submodule (`warewulf/`, `metrics/`, `observability/`, plus
  `users.py`/`services.py`/`storage.py`/`audit_tools.py`), add an explicit
  `__all__` and rewrite the public-symbol re-exports in
  `hpc_pilot/tools/__init__.py` as `from x import y as y` so mypy's
  `no-implicit-reexport` accepts them.
- [ ] **H.7** Remove unused imports (R9): start with `hpc_pilot/clusters.py`,
  `hpc_pilot/audit.py:355`, `hpc_pilot/hermes_plugin/__init__.py`,
  `hpc_pilot/metrics.py:13`, `hpc_pilot/tools/ansible.py:9`.
  `ruff check --fix --select F401` does most of this; review the diff.
- [ ] **H.8** Decide on line-length (R8). Either: (a) raise the ruff
  `line-length` to 120 and document why, or (b) reformat the 116 E501s
  (mostly long `description=` strings in `@hpc_tool` schemas — splitting them
  across lines is straightforward). Prefer (a) for ergonomics; the round-1
  schema descriptions read better on one line.
- [ ] **H.9** Run `black hpc_pilot/ tests/ scripts/` and commit the result.

**Acceptance criteria:**

- `ruff check hpc_pilot/ tests/ scripts/` → 0 findings.
- `black --check hpc_pilot/ tests/ scripts/` → 0 reformatted.
- `mypy hpc_pilot/` → 0 errors.
- `pytest tests/` completes in < 60 s with 0 failures.
- `python scripts/check_tool_names.py`,
  `python scripts/check_no_direct_subprocess.py` → 0 findings.
- CI workflow run on the PR branch is green.

---

### Phase I — Trustworthy observability (P1)

**Goal:** Every audit and sink event has a metric. The session-id parser is
contract-tested.

- [ ] **I.1** Wire `sink_errors_total` (R7). Each `AuditSink.write()`
  swallow path increments the counter labelled with the sink type. Make sure
  one failing sink does **not** prevent other sinks from being recorded;
  that's the current contract, just add the metric.
- [ ] **I.2** Add `tools/observability/health_history.py` (or reuse cron log)
  to expose recent health-check results as Prometheus gauges
  (`hpc_health_overall`, `hpc_health_issues_total`).
- [ ] **I.3** Contract-test `HpcAgent._parse_session_id` (R12). Cover:
  Hermes prints "session_id: abc" → captured; absent line → `None`; multiple
  lines → first wins; trailing whitespace; corrupted output. If Hermes has a
  `--print-session-id` or JSON output mode, switch to that instead and pin
  the version of `hermes-agent` in `pyproject.toml`.
- [ ] **I.4** Add a smoke test that calls `dispatch.invoke()` once and
  asserts `tool_calls_total{tool="...",status="ok"}` incremented. The
  Round-1 B.1 test already verified `denials_total` and
  `tool_call_duration_seconds`; close the gap on the success counter.

**Acceptance criteria:**

- `tests/test_metrics.py` covers all four counters
  (`tool_calls_total`, `denials_total`, `sink_errors_total`,
  `tool_call_duration_seconds`).
- New `tests/test_agent_session.py` covers session-id parsing.
- `/metrics` exposes `hpc_sink_errors_total` after a forced sink failure
  (e.g., HttpSink to an unreachable URL).

---

### Phase J — Daemon and long-running tools are testable (P1)

**Goal:** `daemon`, `cron`, `tui` are exercised by pytest, not just by
running them by hand.

- [ ] **J.1** Add a `--foreground` flag to `hpc-pilot daemon` that skips the
  double-fork. Used by tests and ad-hoc debugging. Default behavior
  unchanged.
- [ ] **J.2** Extract the shared "health-check-on-an-interval" core out of
  `_cli_chat.cron_command` and `_cli_daemon._monitor_loop` into one helper
  in `hpc_pilot/monitor.py` with an injectable clock and exit predicate.
  Write tests against the helper. Cron and daemon both call it.
- [ ] **J.3** TUI test (R-tui): collect-only test asserting the imports
  work when `rich` is installed and the `[tui]` extras are added to
  `pyproject.toml` (currently `hpc_pilot[tui]` is referenced in the error
  message but not defined as an extras group).
- [ ] **J.4** Add `pyproject.toml` `[project.optional-dependencies] tui = ["rich>=13.0"]`.
- [ ] **J.5** Integration test for `hpc-pilot daemon --foreground` running
  three iterations of the monitor loop (use the injectable clock), then
  asserting the gateway and webui subprocess spawn paths were called with
  the right argv. Use `subprocess.Popen` patches; do not actually spawn.

**Acceptance criteria:**

- Tests cover `cron`, `tui` (collect-only), and `daemon --foreground`.
- `_cli_chat.py`'s `cron_command` and `_cli_daemon.py`'s `_monitor_loop`
  share a single implementation.
- `pip install '.[tui]'` works.

---

### Phase K — Documentation truthfulness (P2)

**Goal:** Numbers in the docs match the registry. No dangling references to
deleted files.

- [ ] **K.1** Generate the tool count programmatically (R11) at doc-build
  time, or add a CI step that runs
  `python -c "from hpc_pilot.tools._registry import get_registry; print(len(get_registry()))"`
  and substitutes into the README. Simpler: a `scripts/check_tool_count.py`
  that reads the README + plugin docstring and fails if either number is
  stale. Wire into CI.
- [ ] **K.2** Re-author `docs/ARCHITECTURE.md` to reflect the round-1 split:
  - `tools/system.py` no longer holds 1,510 lines — list the post-split layout.
  - `tools/warewulf.py` is a shim/being-deleted (depending on H.5 outcome).
  - The "Context budget" note is now obsolete after Phase D — replace.
- [ ] **K.3** Remove or rewrite the deleted `docs/IMPLEMENTATION_PLAN.md`
  references in the auto-memory and `README.md` if any survive (search:
  `grep -rn IMPLEMENTATION_PLAN .`).
- [ ] **K.4** Add a `docs/DEVELOPING.md` explaining the
  claim/branch/merge/push protocol from §0 so external contributors can
  follow the same workflow.

**Acceptance criteria:**

- `scripts/check_tool_count.py` is in CI and currently green.
- `docs/ARCHITECTURE.md` describes the actual file layout, not the
  pre-split one.

---

### Phase L — Backward-compat re-export hygiene (P2)

**Goal:** `hpc_pilot/tools/__init__.py` stops carrying 180 lines of
"this exists only because a test patches it." Decide what's actually public
and what's an implementation detail.

- [ ] **L.1** Inventory which re-exports are referenced by:
  - external code (none expected — there is no other consumer),
  - the Hermes plugin (`hpc_pilot/hermes_plugin/__init__.py`),
  - tests (`grep -rn "hpc_pilot.tools.<name>" tests/`),
  - other internal modules.
  Build the inventory in a scratch file in the PR description.
- [ ] **L.2** For tests that patch `hpc_pilot.tools.X.subprocess` or
  `hpc_pilot.tools.X._run`, repoint to the canonical
  `hpc_pilot.tools._run._run` / `hpc_pilot.tools.<submodule>._run` so the
  shim imports can be removed.
- [ ] **L.3** Delete the `noqa: F401` re-exports from
  `hpc_pilot/tools/__init__.py` that no caller uses. Keep an `__all__` that
  reflects the genuine public API.
- [ ] **L.4** Delete `hpc_pilot/tools/warewulf.py` and `hpc_pilot/tools/metrics.py`
  shims if H.5/H.6 didn't already.

**Acceptance criteria:**

- `hpc_pilot/tools/__init__.py` is ≤ 80 lines.
- All tests still pass without `# noqa: F401` re-export hacks.

---

### Phase M — Self-evolve sandbox audit (P3)

**Goal:** Round-1 F.1 added "sandboxed pytest in a subprocess." Validate that
the sandbox actually denies network and filesystem escape.

- [ ] **M.1** Document the sandbox threat model in `docs/SELF_EVOLVE.md`
  (new file). What's blocked: AST whitelist, dangerous-call list, schema
  validation, pytest in a subprocess. What's **not** blocked today: filesystem
  reads outside the project, environment variable access, `~/.aws/credentials`
  reads, etc.
- [ ] **M.2** Add a test that generates a tool whose test code attempts
  `urlopen("http://example.com")`. The sandboxed pytest must fail-fast (or
  fail-deterministically) without making the network call.
- [ ] **M.3** Add a test that generates a tool whose test code attempts
  `open("/etc/passwd")`. The sandbox must deny the read or the test must
  document why it's tolerated.
- [ ] **M.4** Move the staging directory out of the package tree
  (`evolved/staging/`) and into `~/.hpc-pilot/staging/` so that generated
  code never participates in `pip install` imports. Update the promote step.

**Acceptance criteria:**

- `docs/SELF_EVOLVE.md` exists and lists each threat plus its mitigation.
- Two new tests in `tests/tools/test_evolve_sandbox.py` cover the network
  and filesystem-escape attempts.

---

### Phase N — Provider abstraction (P3)

**Goal:** Replace the brittle Hermes-subprocess delegation with a typed
provider interface. This is the largest phase; estimate one week of agent
time.

- [ ] **N.1** Define `hpc_pilot/agent/provider.py` with a
  `ChatProvider` `Protocol`: `def turn(self, user_message: str,
  tool_schemas: list[dict], on_tool_call: Callable, history: list[Msg])
  -> tuple[str, list[Msg]]`.
- [ ] **N.2** Implement `HermesSubprocessProvider` (current behavior, kept
  as default).
- [ ] **N.3** Implement `AnthropicSDKProvider` using the Anthropic SDK
  directly with `tools=TOOL_SCHEMAS` and routing tool calls through
  `dispatch.invoke()`. This is the path round-1 D.1 didn't take.
- [ ] **N.4** Provider is selected via `~/.hpc-pilot/config.yaml` →
  `model.provider: hermes | anthropic_sdk | …`. Default remains `hermes`.
- [ ] **N.5** Add `tests/test_agent_provider.py` with provider-agnostic
  contract tests, run against both implementations.
- [ ] **N.6** When `AnthropicSDKProvider` is selected, the multi-turn
  conversation memory and tool-use loop are managed entirely in-process —
  no Hermes binary needed. Confirm this works end-to-end in a manual run.

**Acceptance criteria:**

- Both providers pass the same contract tests.
- Selecting `model.provider: anthropic_sdk` and chatting via Telegram
  produces a coherent 3-turn conversation referencing prior context.
- The HermesSubprocessProvider's `_parse_session_id` heuristic is the only
  place that depends on Hermes's stderr format; everything else is provider-
  neutral.

---

## 6. Status Tracker

**This table is the source of truth for round-2 phase completion. Update it
as part of every claim and every merge. Push the change to `main`.**

| Phase | Title | Priority | Status | Claimed by | Claim date | Merge SHA | PR |
|---|---|---|---|---|---|---|---|
| H | Make CI a real signal again | P0 | Not started |  |  |  |  |
| I | Trustworthy observability | P1 | Not started |  |  |  |  |
| J | Daemon and long-running tools are testable | P1 | Not started |  |  |  |  |
| K | Documentation truthfulness | P2 | Not started |  |  |  |  |
| L | Backward-compat re-export hygiene | P2 | Not started |  |  |  |  |
| M | Self-evolve sandbox audit | P3 | Not started |  |  |  |  |
| N | Provider abstraction | P3 | Not started |  |  |  |  |

Allowed values for `Status`: `Not started`, `In progress`, `Blocked`, `Done`.

### 6.1 Phase notes

Free-form notes per phase. Use this to record blockers, scope changes, or
reasons a phase was abandoned. Keep entries short and dated.

> _(no entries yet — round 2 just started)_

---

## 7. Backlog

New issues discovered while executing a round-2 phase go here, not into the
active phase. Each entry must include a short title, the reporting agent's
identifier, and the date. The maintainer schedules them into a future phase.

> _(no entries yet)_

---

## 8. Recommended sequencing for parallel agents

1. **Phase H runs alone first.** Until CI is green, every other phase will
   step on something H touches.
2. After H merges, **fan out** I, J, K, L as independent branches — they
   touch largely disjoint files (audit/metrics; daemon/monitor; docs;
   tools/__init__).
3. **M and N** can run in parallel with the others. N is the most invasive
   and should be the last to merge if there is contention.

The most important phase is **H**, because without a real CI signal every
future agent is shipping blind. Round-1 demonstrated the failure mode: seven
phases shipped, none of them re-verified the green stack at the end.
