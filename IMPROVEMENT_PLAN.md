# HPC Pilot — Improvement Plan

Audience: AI coding agents and contributors making changes to this repo.
Scope: Evaluation of `hpc_pilot/` v1.0.0 as it stands at HEAD (`main`, commit `759c7f2`), plus a prioritized work list with concrete file:line references.

Read this file before making changes. Each item below states the symptom, the
root cause, the fix, and the acceptance check. Do not bundle unrelated changes
in one commit — one item per PR unless explicitly grouped.

---

## 1. Snapshot of the current code

```
hpc_pilot/
├── __init__.py     # version + a dead main() helper
├── _hermes.py      # legacy stub — not imported anywhere
├── agent.py        # working Anthropic tool-use agent (TOOL_SCHEMAS + run_turn)
├── audit.py        # JSONL audit log + secret redaction
├── cli.py          # argparse entry; routes to subcommand handlers
├── config.py       # writes ~/.hpc-pilot/config.yaml on first run
├── gateway.py      # working Telegram + Discord async server
├── paths.py        # ~/.hpc-pilot/* path helpers
├── rbac.py         # Role enum + per-tool minimum-role map
└── tools.py        # subprocess wrappers (Slurm/Warewulf/Spack/Ansible)

tests/             # 112 passing tests, mostly unit + a few CLI integration
docs/              # ARCHITECTURE.md + DEPLOYMENT.md + README.md (stale)
```

What works (verified by reading code + running `pytest`):
- The Anthropic tool-use loop in `agent.py` (streaming and non-streaming paths,
  RBAC-checked tool dispatch, audit on every call).
- The Telegram and Discord clients in `gateway.py` (per-chat session,
  message chunking).
- All direct CLI subcommands: `health`, `nodes`, `queue`, `qos`, `warewulf`,
  `spack`, `ansible`, `version`.
- RBAC enforcement and JSONL audit log with secret redaction.
- Input validation that rejects shell-injection in node/QOS/user names.

What is broken or misleading is enumerated in §2 below.

---

## 2. Bugs to fix (do these first)

### B1. `hpc-pilot gateway --start` crashes with NameError

File: `hpc_pilot/cli.py:167-184` (`gateway_command`).
Symptom: `_nyi(...)` is called at line 171 but `_nyi` is not defined in this
module. `hpc-pilot gateway --start` (and `hpc-pilot gateway` with no flags)
will raise `NameError` at runtime.

Why it isn't caught: `tests/test_gateway.py` only exercises
`hpc_pilot.gateway.main` (the standalone `hpc-pilot-gateway` script), never the
`hpc-pilot gateway` subcommand routed through `cli.py`. So the test suite
passes despite the bug.

Fix: replace the stub body of `gateway_command` with delegation to the real
gateway module. The README documents `hpc-pilot gateway --start` as the
canonical entry point.

```python
def gateway_command(args: argparse.Namespace) -> int:
    from hpc_pilot.gateway import main as gateway_main
    argv: list[str] = []
    if getattr(args, "start", False):    argv.append("--start")
    if getattr(args, "stop", False):     argv.append("--stop")
    if getattr(args, "status", False):   argv.append("--status")
    if getattr(args, "setup", False):    argv.append("--setup")
    # Default: --start (mirrors gateway.py's own default)
    if not argv:
        argv = ["--start"]
    return gateway_main(argv)
```

Acceptance:
- Add `tests/test_cli.py::TestMain::test_main_gateway_setup_delegates` that runs
  `main(["gateway", "--setup"])` with `gateway.main` patched and asserts it was
  called with `["--setup"]`.
- Manually verify `hpc-pilot gateway --status` no longer raises.

### B2. `cli.gateway_command` advertises `[planned]` but the feature ships

File: `hpc_pilot/cli.py:440` and the per-subcommand `[planned]` help strings
for `chat`, `shell`, `tui`, `gateway`, `setup`, `cron`.
Symptom: Misleading user-facing help. Only `tui` and `cron` are actually
unimplemented. `chat`, `shell`, `gateway`, `setup` are wired and functional.

Fix: Remove the `[planned]` prefix from the help strings for `chat`, `shell`,
`gateway`, and `setup`. Keep it on `tui` and `cron`.

Acceptance: `hpc-pilot --help` should not mention `[planned]` for shipping
subcommands.

### B3. `_hermes.py` is dead code that lies about the agent layer

File: `hpc_pilot/_hermes.py` (all of it).
Symptom: The module says "AI agent is not yet implemented" and prints that to
stderr when called, but the agent IS implemented in `hpc_pilot/agent.py`.
Nothing imports `_hermes`.

Fix: Delete `hpc_pilot/_hermes.py`. Update `docs/ARCHITECTURE.md` to remove the
`_hermes.py` row (line 43) and the "Planned agent layer" section (lines
141-146).

Acceptance: `grep -r _hermes hpc_pilot/ docs/ tests/` returns no matches.
All tests still pass.

### B4. `hpc_pilot/__init__.py:main()` is dead

File: `hpc_pilot/__init__.py:14-21`.
Symptom: Defines a `main()` that does an `argv` length check with identical
branches, then delegates to `cli.main`. `pyproject.toml` exposes
`hpc-pilot = "hpc_pilot.cli:main"`, so this wrapper is never called.

Fix: Delete the function and the trailing `if __name__ == "__main__"` block.
Keep only `__version__ = "1.0.0"` (and the module docstring).

Acceptance: `hpc-pilot version` still works; test suite green.

### B5. `hpc_warewulf_bootstrap` issues a command that does not exist

File: `hpc_pilot/tools.py:179-182`.
Symptom: The tool runs `wwctl node bootstrap <node>`. Warewulf 4.x does not
have a `node bootstrap` subcommand. A real bare-metal provisioning workflow
needs `wwctl node add`, `wwctl overlay build`, `wwctl image build`, and
network-side PXE boot.

Fix options (pick one in this order):
1. Preferred — implement a real provisioning entry point. Rename the tool to
   `hpc_warewulf_provision_node` and accept `mac`, `ip`, `profile` arguments.
   Build the command as `wwctl node add <name> --netname default --hwaddr <mac>
   --ipaddr <ip> --profile <profile>` followed by `wwctl overlay build <name>`.
   Update RBAC entry, `TOOL_SCHEMAS`, and the dispatch branch in
   `agent.py::_call_tool`.
2. Minimal — if you cannot implement provisioning in this PR, change the
   subcommand to a reboot trigger (`wwctl power reset <node>`) and rename the
   tool to `hpc_warewulf_power_reset`. Document that PXE happens as a side
   effect.

Do NOT keep the current fabricated command. It will crash on any real cluster.

Acceptance:
- The chosen tool maps to a real `wwctl` subcommand documented at
  https://warewulf.org/docs/main/contents/control.html.
- Dry-run output prints the correct command.
- RBAC entry updated to match the new tool name.

### B6. `mypy --strict` does not pass

File: `pyproject.toml:54-57` sets `strict = true`; running `mypy hpc_pilot/`
returns 60+ errors. Most come from the Anthropic SDK's typed response objects
(`agent.py:404-422`) and from one undefined name (`_nyi` in `cli.py:171`,
already covered by B1).

Fix:
- After B1, the `_nyi` error vanishes.
- For the Anthropic typing errors, either:
  - Narrow each block in `agent.py` with `isinstance(block, TextBlock)` /
    `isinstance(block, ToolUseBlock)` (import the types from `anthropic.types`
    inside `TYPE_CHECKING` to avoid runtime cost), OR
  - Loosen mypy via `[[tool.mypy.overrides]] module = "hpc_pilot.agent"`
    setting `disallow_untyped_calls = false` and casting the SDK return type
    to a `Protocol` you control.
- Remove the two `# type: ignore` lines that mypy reports as unused
  (`agent.py:241`, `cli.py:509`).

Acceptance: `mypy hpc_pilot/` exits 0 (or with at most documented overrides).

### B7. `__init__.py` exports an unused `Optional`

File: `hpc_pilot/__init__.py:11`. Trivially dead. Remove on the same pass as
B4.

---

## 3. Design problems to address next

### D1. Two duplicate code paths for "RBAC → audit → execute"

The CLI and the agent each re-implement the same prelude. CLI does it
inline in every `*_command` function (`cli.py:189-205, 209-230, 233-260, …`).
The agent does it inside `_execute_tool` (`agent.py:340-350`).

Fix: introduce one helper, e.g. `hpc_pilot.dispatch.invoke(name, args, *,
role, actor, dry_run) -> str`, that does RBAC + audit + dispatch and returns
the string result. Have both CLI handlers and `HpcAgent._execute_tool` call it.

Acceptance: `grep "check_permission" hpc_pilot/cli.py` returns nothing
(it's centralized). All existing tests still pass without modification.

### D2. `config.py::DEFAULT_CONFIG` advertises options that are never read

File: `hpc_pilot/config.py:8-62`.
Symptom: The default YAML writes `model.default`, `agent.max_turns`,
`terminal.backend`, `hpc.slurm_bin_dir`, `gateway.platforms.*`,
`toolsets.*`, `memory.*`, `cron.*`, `checkpoints.*`, `security.redact_secrets`.
Of these, **only `model.default` is implicitly relevant** — and even that is
read from `HPC_PILOT_MODEL` env, not the YAML.

Fix: strip the default config down to the keys that actually drive behavior
today. Currently that is essentially nothing — so the right move is one of:
1. Stop writing the file (delete the helper and the `init_config()` call
   sites). Tell users to set environment variables.
2. Keep `config.yaml` but cut it down to ~10 lines (model + bin paths) and
   actually wire those keys into `tools.py` and `agent.py`. If you take this
   path, add a `Config` dataclass loaded from YAML and pass it down to the
   tool helpers via a single `load_config()` call.

Either is fine; option 2 is preferred because the README already documents
config-driven bin paths. Do not leave the file as it is.

Acceptance: every key in the resulting `DEFAULT_CONFIG` is referenced
somewhere in `hpc_pilot/`. Add a test that loads the file and asserts that
each top-level key is consumed.

### D3. CLI shim names (`get_hermes_home`, `get_config_path`, `ensure_home_dir`)

File: `hpc_pilot/cli.py:37-47` and tests that pin them
(`tests/test_cli.py:15-60`, `tests/test_gateway.py:12-43`).
Symptom: These exist only so older tests don't break. They are leftover
from a prior project name ("Hermes").

Fix: rename to `home_dir`, `config_file`, `ensure_home` in cli.py; rewrite the
three test classes (`TestGetHermesHome`, `TestGetConfigPath`,
`TestEnsureHomeDir`) to call the new names; keep one-line deprecation
shims for `get_hermes_home` etc. that just `warnings.warn(... DeprecationWarning)`
and delegate, so external callers (if any) do not break.

Acceptance: `grep -r get_hermes_home hpc_pilot/` returns only the deprecated
shim. New tests cover the new names.

### D4. Optional `tools.registry` integration in `tools.py:21-25` and 350-568

The block beneath "Hermes tool registry (register if available)" registers
every tool with an external `tools.registry` import that is no longer a
declared dependency of this project (README and DEPLOYMENT call HPC Pilot
"standalone"). 200+ lines of code that never run.

Fix: delete the import attempt at `tools.py:21-25` and the whole "register if
available" block at lines 350-568. If a future integration is needed, build
it on top of the real `TOOL_SCHEMAS` in `agent.py:34-205` instead of a parallel
registration list.

Acceptance: `tools.py` shrinks to ~350 lines. `pyproject.toml` does not need
to change. Tests pass.

### D5. Gateway default role is VIEWER — bot refuses most commands

File: `hpc_pilot/gateway.py:51-54` (`_make_agent`); roles in
`hpc_pilot/rbac.py:46-61`. When users start the gateway without setting
`HPC_PILOT_ROLE`, the agent runs as VIEWER, so any Telegram/Discord user
who asks "drain node X" is denied. The README implies admin works, but the
default is restrictive.

Fix:
1. In `gateway.py::main()` (after `_load_env()`), if `HPC_PILOT_ROLE` is
   unset, emit a clear warning to stderr and fall back to VIEWER.
2. Add a `--role` CLI argument to `hpc-pilot-gateway` and to
   `hpc-pilot gateway --start` that overrides the env var.
3. Document the default in README and DEPLOYMENT.

Do NOT silently default to ADMIN — that turns the public bot into a foot-gun.

Acceptance: starting the gateway with no `HPC_PILOT_ROLE` prints
`Warning: HPC_PILOT_ROLE not set; defaulting to viewer (read-only).` Tests
patched to check stderr contents.

### D6. No allowlist on Telegram / Discord users

File: `hpc_pilot/gateway.py:85-176`. Anyone who knows the bot token can DM
the bot and run viewer-level tools immediately, and operator/admin tools if
the gateway is started with a privileged role. This is a real security gap.

Fix: add two env vars, `HPC_PILOT_TELEGRAM_ALLOWED_CHAT_IDS` and
`HPC_PILOT_DISCORD_ALLOWED_USER_IDS`, each accepting a comma-separated list
of IDs. In the relevant `_handle_message`/`on_message` handlers, drop messages
from senders not on the list with a single audit-logged "denied" line. Add a
unit test using `MagicMock` for `update.effective_chat.id`.

Acceptance: a hostile chat ID receives no answer and exactly one
`gateway_access_denied` audit entry is written.

### D7. `run_turn` has no max-iteration safety

File: `hpc_pilot/agent.py:380-432`. The `while True` loop relies on Claude
emitting `stop_reason != "tool_use"` to exit. A misbehaving model can loop
forever, burning the API budget. `config.py:18` even sets
`agent.max_turns: 90` but the code does not read it.

Fix: add a parameter `max_iterations: int = 25` to `run_turn` and break with
a clear message if exceeded. Surface it as a config key
(`agent.max_iterations`) once D2 is resolved.

Acceptance: a unit test using a `_tool_response`-only mock chain that never
ends asserts the loop breaks after `max_iterations` calls.

### D8. Default subprocess timeout is too short for cluster operations

File: `hpc_pilot/tools.py:45` — default `timeout=30`. Some Slurm
operations on busy controllers take >30s. `scontrol show node` on a 1000-node
cluster can be slow.

Fix: raise the default to 60 in `_run`. Override per-tool as needed:
- `hpc_slurm_node_status` for "all nodes": 90
- `hpc_slurm_queue`: 60 (today)
- `hpc_spack_find`: keep 60
- `hpc_warewulf_bootstrap` (or its replacement): 300 (today)
- `hpc_ansible_playbook_run`: 600 (today is 300; ansible runs across many hosts)

Acceptance: a test exercises `_run`'s default and per-call timeouts via a
`subprocess.run` patch that asserts the `timeout=` kwarg.

---

## 4. Quality-of-life improvements

### Q1. `--json` flag for tabular CLI subcommands

`nodes`, `queue`, `warewulf`, `spack` should accept `--json` and emit
machine-readable output for scripting. Today only `health` does. Implementation:
parse the subprocess stdout with the existing `parse_slurm_nodes` (or write
analogous parsers) and `json.dumps` it.

### Q2. Conversation history persistence in the CLI chat loop

File: `hpc_pilot/agent.py:447-488`. The `run_chat_loop` discards history on
exit. Persist it to `~/.hpc-pilot/sessions/<timestamp>.json` on `exit`/Ctrl-D
and add `hpc-pilot chat --resume <session-id>` (and `--list-sessions`).

### Q3. Token usage accounting

After every `messages.create` / `messages.stream` call in `agent.py`, log
`response.usage.input_tokens` and `output_tokens` to the audit record (extend
`AuditEvent` with optional `usage` field). This makes cost auditable.

### Q4. Retries on transient Anthropic errors

Wrap the API calls in `run_turn` with `tenacity` or a manual backoff for
`anthropic.RateLimitError` and `anthropic.APIConnectionError`. 3 attempts,
exponential backoff starting at 1s.

### Q5. Helpful chat-loop output

When the agent calls a tool, today the CLI prints `[→ tool_name]` with no
args or result. Show `(args: {...})` and either truncate the result to N
chars or hide behind `--verbose`. This makes interactive use less opaque.

### Q6. Documentation pass

After B3/D4 are merged:
- `docs/ARCHITECTURE.md`: remove the `_hermes.py` row and the "Planned agent
  layer" section; add the actual agent and gateway architecture diagrams.
- `docs/DEPLOYMENT.md`: remove the line "Gateway won't start: The AI agent /
  gateway layer is not yet implemented" — false today.
- `README.md`: under "Planned", remove `cron`/`tui` only if you implement
  them; remove any other implemented features still listed as "planned".

### Q7. Unit tests for the patched bugs

For each of B1-B7, add at least one unit test that fails before the fix and
passes after. List them in the PR description.

---

## 5. Future scope (do not start without a separate spec)

These are intentionally out of scope for the bug-fix and design-cleanup
passes above. They are listed only so future agents do not rediscover them.

- **Real bare-metal provisioning surface**: image build, overlay management,
  DHCP/TFTP/NFS bootstrap, profile management. The auto-memory snapshot at
  `~/.claude/projects/.../memory/project_hpc_pilot.md` references these as
  "complete" in a prior project tree, but the current `hpc_pilot/tools.py`
  has none of them. Treat that memory as stale until verified.
- **Policy / blast-radius gates**: spec talked about
  `config_repo/policy/warewulf.yaml`. Not present here. Out of scope.
- **Web UI on port 8000** (README, "Planned" section). Out of scope.
- **TUI via Textual** (`hpc-pilot tui`). Out of scope.
- **Scheduled cron monitoring** (`hpc-pilot cron`). Out of scope.

If a future task asks for any of these, request a separate spec document
before coding.

---

## 6. House rules for agents working in this repo

1. Make one logical change per commit. Tests for that change go in the same
   commit. Commit messages: imperative present tense, ≤72-char subject.
2. Do not add `# type: ignore` to silence a real error; either fix the type or
   document why with one line.
3. Do not bypass `--no-verify`, `--no-gpg-sign`, or any pre-commit hook.
4. Run the full test suite (`pytest tests/`) before declaring a task done.
   The current baseline is 112 passing tests; do not regress that count.
5. When editing `tools.py`, never construct subprocess argv from an
   un-validated string. Use `_validate()` against `_NAME_RE` or `_USER_RE` for
   any user-supplied token before it enters argv.
6. Subprocess calls must use `shell=False` (the default for list-form argv).
   Never call `os.system`, `subprocess.Popen(..., shell=True)`, or string
   concatenation into a shell command.
7. Any new audit fields go through `AuditEvent`; do not write directly to the
   audit log file.
8. When adding a new tool to `TOOL_SCHEMAS`, also add: an `_call_tool` branch
   in `agent.py`, an entry in `rbac.TOOL_MIN_ROLE`, and a unit test in
   `tests/test_tools.py` or `tests/test_agent.py`.
9. Prefer extending an existing module over creating a new file. The current
   8-module layout is small enough to keep flat.
10. When something is unclear — for example, the right Warewulf command for
    B5 — flag it in the PR description with a question rather than guessing.

---

## 7. Suggested ordering

If you can only do a few of these, do them in this order:

1. **B1** (gateway NameError) — production-breaking, 10 minutes.
2. **B3 + B4 + B7** (delete dead code) — clears confusion.
3. **B5** (Warewulf bootstrap) — currently lies to users about what it does.
4. **B6** (mypy errors) — unlocks `mypy --strict` in CI.
5. **D5 + D6** (gateway security defaults + allowlist) — required before any
   production deployment.
6. **D2** (config file actually drives behavior, or stop writing it).
7. **D4** (delete dead Hermes registry block) — large code reduction.
8. **D1** (centralize RBAC+audit+dispatch) — improves testability of any
   future feature.
9. **D7 + D8** — defensive hardening.
10. The Q* items, as time allows.

End of plan.
