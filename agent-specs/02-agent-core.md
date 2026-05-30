# 02 — Agent Core

The reasoning layer: turns natural-language intent into ordered, gated tool calls;
manages memory; drives the interaction loop; supports resumable (approval-pausing) plans.

---

## 1. Components

```
hpc_agent/core/
├── interaction.py   # CLI / chat / HTTP entry points
├── planner.py       # intent -> Plan (DAG of steps)
├── executor.py      # runs a Plan, honoring gates & resumption
├── memory.py        # cluster facts + runbook RAG
└── llm.py           # provider-agnostic LLM client w/ tool-calling
```

---

## 2. LLM client

`llm.py` wraps the model with tool-calling. It is provider-agnostic but the reference
implementation targets the Anthropic Messages API.

```python
class LLM:
    def plan(self, intent: str, context: PlanContext, tools: list[ToolSchema]) -> Plan: ...
    def reason_step(self, state: StepState) -> StepDecision: ...
```

- Tools are exposed as JSON schemas from the `@tool` registry (spec 00 §3.1).
- The system prompt injects: cluster summary (from memory), site naming conventions,
  the safety contract ("you never bypass approval; destructive ops are prohibited"), and
  the available tools. Pin model version in settings.

---

## 3. Plan model

A plan is a DAG of steps; the planner resolves dependencies (e.g. `provision_node` before
`add_node_to_partition`).

```python
class Step(BaseModel):
    id: str
    tool: str
    input: dict
    depends_on: list[str] = []
    status: str = "pending"     # pending|running|needs_approval|done|failed|skipped
    result: ToolResult | None = None

class Plan(BaseModel):
    id: str                     # == agent_run_id in audit
    intent: str
    actor: str
    steps: list[Step]
    state: str = "draft"        # draft|awaiting_approval|running|paused|done|failed
```

The planner must **always present the full plan (with per-step risk + diffs) before
executing anything when any step is MEDIUM/HIGH**. Read-only plans may execute directly.

---

## 4. Executor

```python
def run_plan(plan: Plan) -> Plan:
    for step in topological_order(plan.steps):
        if any dep failed: step.status = "skipped"; continue
        # always dry-run first to populate diff
        dry = call_tool(step.tool, {**step.input, "dry_run": True})
        gate = safety.evaluate(...)
        if gate.requires_approval and not gate.approved:
            step.status = "needs_approval"; plan.state = "paused"
            persist(plan); return plan                 # resumable (see §5)
        if gate.denied:
            step.status = "failed"; ...
        real = call_tool(step.tool, {**step.input, "dry_run": False})
        step.result = real; step.status = "done" if real.ok else "failed"
        if step.failed and step.critical: halt + offer revert of completed steps
    return plan
```

Failure policy: on a failed critical step, **stop forward progress** and surface a
`revert` option for already-completed mutating steps in this plan.

---

## 5. Resumable plans

Plans persist to a `plans` table. When paused for approval:
- The pending approval references `plan.id` + `step.id` + diff hash.
- On approval (via spec 01 backend), `resume_plan(plan_id, step_id, approver)` re-validates
  the diff is unchanged, marks the step approved, and continues `run_plan` from there.
- Approvals expire after `APPROVAL_TTL` (default 1h); expired -> step fails, plan paused.

---

## 6. Memory

Two stores:

1. **Structured cluster facts** — read directly from the state store (spec 00). The
   planner gets a compact `ClusterSummary` (node counts by role/state, partitions, QOS
   names, image inventory) injected each turn. Never let the LLM guess topology.
2. **Runbook RAG** — site docs, prior incident resolutions, naming conventions embedded in
   a vector store (`hpc_agent/core/memory.py`, pgvector). Retrieved snippets augment the
   planner prompt for "how does this site usually do X" questions.

Decision memory: completed plans + outcomes are summarized and stored so the agent can
reference "last time we rebuilt gpu image we pinned driver 550.x".

---

## 7. Interaction layer

- **CLI** (`hpc-agent <intent>` or REPL): primary operator interface; renders plans and
  diffs as text, prompts for approval inline.
- **HTTP API** (FastAPI): `POST /plans` (create from intent), `GET /plans/{id}`,
  `POST /plans/{id}/approve`. Auth maps caller -> RBAC role (spec 00 §6).
- **Chat** (optional Slack): same plan/approval flow with interactive buttons.

All three funnel into the same planner/executor; the interaction layer only handles I/O
and identity resolution (who is `actor`).

## 8. Acceptance criteria

- [ ] Planner produces a valid dependency-ordered Plan for "add 2 GPU nodes to partition gpu".
- [ ] A plan with a MEDIUM step pauses, persists, and resumes correctly after approval.
- [ ] Read-only intents ("show down nodes") execute with no approval and no mutation.
- [ ] Stale approval (diff changed or TTL expired) is rejected on resume.
- [ ] ClusterSummary is always sourced from state store, never hallucinated.
