# Use Case & Problem Brief

End-to-end description of what goes into the support copilot, what comes out,
and a one-page summary that ties the idea-definition documents together.

| Document | Role |
|----------|------|
| [`problem-statement.md`](./problem-statement.md) | Why we are building this |
| [`user-personas.md`](./user-personas.md) | Who uses it |
| [`success-metrics.md`](./success-metrics.md) | How we measure success |
| [`non-goals.md`](./non-goals.md) | What we explicitly will not build |
| **This file** | What question goes in, what artifact comes out |

---

## Primary Use Case

**Title:** Human agent drafts a grounded customer reply with AI assistance.

**Actor:** Tier-1 support agent (Jordan) at Acme Commerce.

**Trigger:** A customer ticket arrives asking about an order, refund, or
policy question. Jordan pastes the customer's message (or a paraphrase) into
the copilot alongside any known identifiers.

**Outcome:** Jordan receives a cited draft reply, verifies facts against
sources, edits if needed, and sends the message to the customer through the
normal CS platform. The copilot never sends mail on its own.

---

## Input → Output Contract

### Input

| Field | Required | Example |
|-------|----------|---------|
| `question` | Yes | `"Why was the refund on order o_9002 for CUST-1001 delayed?"` |
| `tenant` | Yes | `"acme"` |
| `actor` (human agent id) | Yes | JWT `act.sub` — who initiated the run |
| `customer_id` | Preferred | Extracted from question or supplied by agent |

**Input qualities we expect:**
- Natural language, often messy (pasted from email or chat).
- May include order ids (`o_9002`), customer ids (`CUST-1001`), or neither.
- No structured SQL, MCP JSON, or tool names from the human agent.

### Output (`CopilotResponse`)

| Field | Type | Purpose |
|-------|------|---------|
| `draft` | string | Reply text for the human to review and send |
| `approved` | bool | `true` if critic passed; `false` if human must handle carefully |
| `citations` | list[string] | Source anchors (`[S1]`, `[S2]`) mapped to retrieval findings |
| `run_id` | string | Joins copilot run to MCP server audit + OTel trace |
| `tokens_in` / `tokens_out` | int | Cost attribution per run |
| `tool_calls` | int | How many MCP tools the retriever invoked |
| `plan` | object | What information the planner decided to gather |
| `retrieval` | object | Findings, iterations used, budget-exceeded flag |
| `critic` | object | Approval verdict, issues, revision hints |

**Output the human actually uses:** `draft` + `citations` + `approved` flag.
**Output ops and compliance use:** `run_id`, token counts, full `to_dict()`
payload for observability.

---

## End-to-End Flow

```
  Customer ticket
        │
        ▼
  Human agent pastes question ──────────────────────────────┐
        │                                                  │
        ▼                                                  │
  ┌─────────────┐    JSON plan (no tools)                  │
  │   Planner   │──► needs: orders, tickets, policy       │
  └─────────────┘                                          │
        │                                                  │
        ▼                                                  │
  ┌─────────────┐    MCP tool calls (≤ 6 iterations)      │
  │  Retriever  │──► customer.build_context, search, …     │
  └─────────────┘         │                                │
        │                 ▼                                │
        │            MCP server ──► Postgres · ES · S3 · Vector
        │                 │                                │
        ▼                 ▼                                │
  findings[] ◄────────────┘                                  │
        │                                                  │
        ▼                                                  │
  ┌──────────────┐   draft + [S1][S2] citations           │
  │ Synthesizer  │──────────────────────────┐             │
  └──────────────┘                          │             │
        │                                   ▼             │
        ▼                            ┌──────────┐         │
  ┌──────────┐   approve / revise    │  Critic  │         │
  │ (revise  │◄──────────────────────│          │         │
  │  ≤ 1×)   │                       └──────────┘         │
  └──────────┘                                            │
        │                                                  │
        ▼                                                  │
  CopilotResponse ──► human reviews ──► send to customer ◄┘
```

### Step-by-step behaviour

1. **Planner** reads the question and emits a structured plan of information
   needs. It does not call tools. A regex also extracts `CUST-####` ids as a
   safeguard.

2. **Retriever** is the only agent that calls MCP tools. It runs a bounded
   loop: ask the LLM which tool next → call via authenticated client → append
   result → repeat until done or budget hit. Typical first call:
   `customer.build_context` (profile + orders + tickets + relevant docs).

3. **Synthesizer** drafts a reply (≤ 180 words) using **only** retriever
   findings. Citations are embedded as `[S1]`, `[S2]`.

4. **Critic** approves or requests one revision. It blocks unsupported facts,
   refund promises without approval, and drafts over 200 words. At most **one**
   revise pass — then the draft returns with `approved=False` if still failing.

5. **Human agent** reads the draft, checks citations, edits tone, and sends.
   If `approved=False`, Sam (Tier-2) or a manager may take over.

---

## Example Walkthrough

### Question in

```
Why was the refund on order o_9002 for CUST-1001 delayed?
```

### Plan out (abbreviated)

```json
{
  "needs": [
    {"id": "n1", "description": "customer tier and order o_9002 status", "priority": 1},
    {"id": "n2", "description": "refund processing timeline policy", "priority": 1}
  ],
  "customer_id": "CUST-1001",
  "customer_id_required": true
}
```

### Retrieval (typical tools)

| Step | Tool | What it returns |
|------|------|-----------------|
| 1 | `customer.build_context` | Profile, recent orders incl. o_9002, open tickets |
| 2 | `semantic_search` | Help-centre passage on refund processing times |

### Draft out (illustrative)

```
Hi — I checked order o_9002 for your account. The refund was initiated on
March 12 but is still in "processing" because your bank's settlement window
is 5–7 business days for Gold-tier accounts [S1]. Our policy states refunds
appear within that window after warehouse receipt confirmation [S2]. If you
do not see it by March 21, we can escalate with our payments team — that
would need a supervisor to approve a manual trace.
```

### Citations

| Anchor | Source finding |
|--------|----------------|
| `[S1]` | Order row: `o_9002`, status `refund_processing`, tier `gold` |
| `[S2]` | Vector doc: "Refunds — Gold tier settlement 5–7 business days" |

### Human action

Jordan verifies `[S1]` against the orders console, confirms the policy quote
in `[S2]`, removes nothing material, and sends. No auto-refund triggered.

---

## Secondary Use Cases

| Use case | Input sketch | Output artifact |
|----------|--------------|-----------------|
| **Policy-only question** | "What is Acme's return window for electronics?" | Draft citing help-centre doc; no customer id required |
| **Escalation handoff** | Tier-2 opens copilot on forwarded ticket | Same `CopilotResponse` + full retrieval trace for audit |
| **Missing customer id** | "Why is my refund delayed?" (no id) | Short-circuit message asking agent to supply `CUST-####` |
| **Zero findings** | Valid id but no matching records | Safe message: double-check id; no invented order data |
| **High-risk draft** | Question implying immediate refund | Critic sets `approved=False`; governance flag for manager |
| **MCP-only tool call** | Another internal service calls `hybrid_search` | Structured JSON tool result (no natural-language draft) |

---

## One-Page Problem Brief

### The problem

Acme support agents answer tickets by querying **four separate systems**
(Postgres, Elasticsearch, S3, vector store) per ticket. Context-switching is
slow, error-prone, and hard to audit. Generic AI chatbots hallucinate facts
and promise refunds without approval.

### The solution

Two layers:

1. **MCP server** — production-grade tool surface over Acme's data plane:
   authenticated, tenant-scoped, rate-limited, cached, circuit-broken,
   observable, and governed by YAML policy.

2. **Support copilot** — four-agent pipeline (Planner → Retriever →
   Synthesizer → Critic) that retrieves authoritative data and drafts
   cited replies for **human agents** to review and send.

### Who benefits

| Who | Benefit |
|-----|---------|
| Tier-1 / Tier-2 agents | Faster lookups, grounded drafts, clear approval flags |
| Support managers | Token/cost visibility, audit trail per `run_id` |
| Platform / SRE | Stateless HTTP, structured errors, OTel traces |
| Tenant admins | Deny-by-default policy, tool allowlists, approval gates |
| End customers | Faster, more consistent answers (via better human replies) |

### Success at a glance

| Dimension | Target |
|-----------|--------|
| Lookup time (routine ticket) | ≤ 2 minutes |
| Copilot latency (p95) | ≤ 45 seconds |
| Unsupported claims in sent replies | 0 tolerated |
| Auto-refunds without approval | 0 tolerated |
| Retriever tool iterations | ≤ 6 hard cap |
| MCP availability | 99.5% monthly |

Full numbers: [`success-metrics.md`](./success-metrics.md).

### Explicit boundaries

- Not a customer-facing chatbot.
- Not an auto-refund engine.
- Not a hello-world `@tool` demo.
- Not unbounded agent loops or persistent customer memory in v1.

Full list: [`non-goals.md`](./non-goals.md).

### What happens next

With the problem, personas, metrics, non-goals, and use case documented,
the next work designs the system architecture (layered components, middleware
order, tech stack) before any repository scaffolding or code.

---

## Idea Definition — Completion Checklist

| Item | Status | Where |
|------|--------|-------|
| Problem statement and operational pain | Done | [`problem-statement.md`](./problem-statement.md) |
| User personas and auth profiles | Done | [`user-personas.md`](./user-personas.md) |
| Measurable success metrics | Done | [`success-metrics.md`](./success-metrics.md) |
| Explicit non-goals | Done | [`non-goals.md`](./non-goals.md) |
| Agent use case: input → output artifact | Done | This document |
| One-page problem brief | Done | Above |

**Approval gate:** Stakeholder sign-off on this brief unlocks architecture
and implementation work.
