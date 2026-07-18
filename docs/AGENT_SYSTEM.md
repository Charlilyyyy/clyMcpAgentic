# Agent System

Atlas-MCP ships with a **support copilot**: a four-agent pipeline that turns a
customer question into a grounded, cited draft reply for a human agent to
review. It lives in [`src/atlas_mcp/agents/`](../src/atlas_mcp/agents/).

The design principle throughout: **the LLM is untrusted**. Agents are small,
single-purpose, and every action they take is re-checked by the server's
authenticated, policy-gated pipeline (see [`ARCHITECTURE.md`](./ARCHITECTURE.md)).
There is no heavy framework here — just direct LLM calls with clear contracts
and a bounded orchestrator.

---

## The flow

```
  customer question
        │
        ▼
   ┌─────────┐   plan (JSON)     ┌───────────┐  findings[]   ┌─────────────┐  draft   ┌────────┐
   │ Planner │ ────────────────▶ │ Retriever │ ────────────▶ │ Synthesizer │ ───────▶ │ Critic │
   └─────────┘                   └───────────┘               └─────────────┘          └────────┘
        │ no tools                    │ MCP tool calls              │ grounded only         │
        │ code-side id extraction     │ (bounded loop, allow-list)  │ in findings           │
        │                             │ full server pipeline        │                       │
        └──────────── customer_id missing → short-circuit           approve ◀───────────────┤
                                                                    revise (one shot) ───────┘
```

At most **one** revise loop: a draft that cannot pass the critic after one
revision is usually missing information the retriever never found. Looping
further just burns tokens and latency.

---

## The four agents

### Planner (`planner.py`)
Turns the question into a short JSON plan of information needs. It **never
calls tools** and **never answers**. Critically, the customer id is extracted
**in code** (a regex), not trusted from the model — the prompt explicitly
forbids inventing one, and if none is present the planner sets
`customer_id_required=true` so the orchestrator can short-circuit.

Contract:
```json
{ "needs": [{"id": "n1", "description": "...", "priority": 1}],
  "customer_id_required": true, "notes": "" }
```

### Retriever (`retriever.py`)
The only agent that touches the outside world. It runs a **bounded**
tool-calling loop (default `max_iterations=6`) via the MCP client:

```
while not done and iterations < cap:
    ask the LLM which tool to call with what arguments
    call the tool via the MCP client
    append the observation to the running conversation
```

Two layers of safety:
- **Client-side allow-list** (`_ALLOWED_TOOLS`): only read-only composed /
  workflow / atomic retrieval tools. A destructive tool name never reaches
  the wire.
- **Server-side policy**: even if the allow-list were bypassed, the server's
  deny-by-default policy rejects the call. (`test_agent_server_integration.py`
  proves both layers.)

Each tool result becomes a `Finding` (source, summary, raw value). SERF error
fields (`retryable`, `hint`) are surfaced back into the prompt so the LLM can
adapt.

### Synthesizer (`synthesizer.py`)
Drafts a reply **grounded only in the retriever's findings**, citing them with
`[S1]`, `[S2]` anchors. It must not invent facts and must flag any
refund/exception as needing human approval rather than committing to it.

### Critic (`critic.py`)
The gatekeeper. Emits `approve` or `revise` with issues. It blocks drafts that
assert unsupported facts, promise refunds/discounts/exceptions without
approval, contradict a finding, or exceed length limits.

Contract:
```json
{ "verdict": "approve" | "revise", "issues": ["..."], "revision_hints": "..." }
```

---

## Orchestrator (`orchestrator.py`)

`SupportCopilot.answer(question)` wires the four agents, sharing one
`AgentRun` so token counts and tool-call counts are accounted in one place.
It returns a `CopilotResponse` (draft, approval, citations, plan, retrieval
stats, critic verdict, and per-run token/tool totals).

Short-circuits:
- **Missing customer id** when required → a polite ask, no retrieval.
- **No findings** → an honest "couldn't find anything" rather than a guess.

---

## Budgets and ceilings

| Budget | Where | Default |
|--------|-------|---------|
| Retriever iterations | `RetrieverAgent(max_iterations=...)` | 6 |
| Revise loops | orchestrator | 1 |
| Per-call token cap | `Agent._complete_text(max_tokens=...)` | per-agent |
| Server-side time budget | `reliability/atba.py` (ATBA) | `ATLAS_ATBA_TOTAL_BUDGET_MS` (30s) |
| Per-tenant/tool rate limit | `ratelimit/limiter.py` | configurable |

The server budgets (ATBA, rate limits, breakers) apply to **every** tool call
the retriever makes, so a runaway agent is bounded by the platform, not just
by its own loop cap.

---

## Approval flow for destructive actions

When `ATLAS_DESTRUCTIVE_TOOL_REQUIRES_APPROVAL=true`, any destructive tool
(e.g. `s3.put`) routes through `governance/approval.py`. The agent cannot
complete the action itself; it receives a `PendingApprovalError` (a SERF
error) and surfaces it. The critic independently blocks drafts that *promise*
such actions before approval. Two independent gates, by design.

---

## The LLM seam and testing

`agents/base.py` defines a tiny `LLMProtocol` (`complete(system, messages,
max_tokens)`). Swapping Anthropic for another provider — or a test double —
means changing one class. Tests use `tests/agent_fakes.py::FakeLLM`, which
returns queued or responder-computed responses, so the entire pipeline runs
deterministically with no network:

- `test_agents_planner_synth.py`, `test_agents_critic_memory.py` — per-agent contracts
- `test_agents_retriever.py` — loop bound, allow-list, MCP client parsing
- `test_agents_orchestrator.py` — happy path, short-circuits, revise loop
- `test_agent_server_integration.py` — the orchestrator over the **live** MCP
  stack, asserting server-side policy denial degrades gracefully

---

## Memory (`memory.py`)

- **Short-term memory** — per-session conversation buffer in Redis, TTL'd and
  trimmed to the most recent turns. Gives conversational follow-up.
- **Long-term memory** — durable customer facts written back to the vector
  store. Intentionally **not** wired to a destructive MCP tool yet; when it is,
  it must go through the approval gate like any other write. No back doors
  into the tenant-isolated data plane.

---

## CLI (`cli.py`)

`atlas-copilot "<question>"` drives the copilot against a running server.
Reads `ATLAS_MCP_URL`, `ATLAS_MCP_TOKEN`, `ATLAS_TENANT`, and
`ANTHROPIC_API_KEY` from the environment, prints the draft plus a JSON trace
summary (tokens, tool calls, `run_id`).
