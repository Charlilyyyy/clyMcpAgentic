# User Personas

This document defines who interacts with the system, what each actor needs,
and how their goals map to product and platform requirements. Personas are
grounded in the Acme Commerce support scenario described in
[`problem-statement.md`](./problem-statement.md).

## Persona Overview

| Persona | Interacts with | Primary goal |
|---------|----------------|--------------|
| Tier-1 support agent | Copilot UI + MCP (indirect) | Answer routine tickets faster with grounded drafts |
| Tier-2 / escalation agent | Copilot UI + MCP (indirect) | Resolve complex cases with full cross-system context |
| Support team lead | Dashboards + audit logs | Monitor quality, cost, and compliance across the team |
| Platform engineer / SRE | MCP server, infra, runbooks | Keep the stack reliable, observable, and secure |
| Tenant admin | Policy config, approvals | Control who can call which tools and approve high-risk actions |
| Support copilot (AI agent) | MCP server (direct) | Retrieve data and draft replies within strict budgets |
| End customer | Help portal, chat, email | Get accurate answers quickly (never calls MCP directly) |

---

## 1. Tier-1 Support Agent — "Jordan"

**Role:** Front-line responder handling 40–60 tickets per day.

**Background:** Six months at Acme. Knows the help-centre macros by heart but
still struggles to find order history and open tickets across separate consoles.

**Goals:**
- Paste a customer question and receive a draft reply with linked facts.
- See citations (`[S1]`, `[S2]`) to verify claims before sending.
- Spend less than two minutes on lookup for a standard ticket.

**Frustrations today:**
- Four browser tabs open per ticket (CRM, orders DB, ticket search, policy wiki).
- Senior agents get pinged on Slack for "where do I find X?"
- Fear of promising a refund the system will not honour.

**Needs from the system:**
- One-click draft grounded in live customer data.
- Clear flag when a refund or exception needs manager approval.
- No exposure to raw SQL, JWT claims, or MCP wire format.

**Authorization profile:** `role:human_agent` — read access to customer
records, tickets, docs, and attachment storage for their tenant. No destructive
writes without an approval gate.

---

## 2. Tier-2 / Escalation Agent — "Sam"

**Role:** Handles refunds, shipping disputes, and policy exceptions escalated
from Tier-1.

**Background:** Three years at Acme. Deep policy knowledge but limited patience
for re-gathering context that Tier-1 should have attached.

**Goals:**
- Open an escalated ticket and immediately see merged customer context (orders,
  open tickets, relevant policy passages).
- Trust that the copilot did not invent order dates or refund amounts.
- Approve or edit a draft, then send with an audit trail.

**Frustrations today:**
- Escalation notes often lack order ids or tier information.
- Re-running the same four-system lookup wastes 10–15 minutes per case.
- No single view of "what did the AI already try?"

**Needs from the system:**
- Workflow tool that bundles profile + orders + tickets + docs in one call.
- Full retrieval trace (which tools, which arguments, what came back).
- Critic gate that blocks refund commitments without explicit approval.

**Authorization profile:** `role:human_agent` — same read scope as Jordan, plus
ability to trigger approval workflows for write actions (e.g. attachment upload).

---

## 3. Support Team Lead — "Morgan"

**Role:** Manages a pod of 8–12 agents. Reports on CSAT, handle time, and
escalation rate.

**Background:** Former Tier-2 agent. Cares about consistency and cost control
as much as speed.

**Goals:**
- See per-run token usage, tool-call counts, and error rates.
- Answer "who approved this refund draft?" from audit logs.
- Set expectations: copilot assists humans; it does not auto-close tickets.

**Frustrations today:**
- No visibility into shadow AI usage (agents pasting questions into public LLMs).
- Cannot measure whether AI drafts reduce handle time or increase rework.
- Worried about agents sending hallucinated policy quotes to VIP customers.

**Needs from the system:**
- Prometheus metrics and structured audit logs tied to `run_id`.
- Dashboards for latency, cache hit ratio, and approval-blocked drafts.
- Policy rules that deny PII columns and destructive SQL for copilot roles.

**Authorization profile:** Read-only access to observability and audit exports.
Does not call MCP tools directly in normal operations.

---

## 4. Platform Engineer / SRE — "Riley"

**Role:** Owns the MCP server deployment, data-plane connectivity, and
on-call rotation.

**Background:** Builds internal platforms. Has been burned by "quick AI demos"
that bypass auth and spray unconstrained HTTP from agent tool calls.

**Goals:**
- Horizontally scale the MCP server without sticky sessions.
- Trace a failed tool call end-to-end in Jaeger within one `trace_id`.
- Trip circuit breakers fast when Postgres or Elasticsearch is unhealthy.

**Frustrations today:**
- Agent frameworks that hide outbound HTTP and skip SSRF allowlists.
- No standard error format — retries hammer dead backends.
- Tenant isolation enforced only in application code, not at the database.

**Needs from the system:**
- Streamable HTTP in stateless mode, health/readiness probes.
- Middleware ordering: trace → auth → tenant → validate → policy → rate limit
  → cache → circuit breaker → execute → errors → audit.
- Row-Level Security in Postgres as defence-in-depth for tenant data.

**Authorization profile:** Infrastructure credentials for deploy and break-glass.
Not a routine MCP tool caller.

---

## 5. Tenant Admin — "Alex"

**Role:** Acme's internal admin for tool policies, OAuth clients, and
approval configuration.

**Background:** Security-conscious. Reviews which service accounts and human
roles can access which tools per tenant.

**Goals:**
- Edit YAML policy rules without redeploying the entire server.
- Restrict the support copilot to an explicit tool allowlist.
- Enable impersonation only for audited internal tooling.

**Frustrations today:**
- All-or-nothing API keys shared across teams.
- No deny-by-default policy engine — permissions grow until something leaks.
- High-risk actions (refunds, S3 writes) lack a human-in-the-loop gate.

**Needs from the system:**
- Deny-by-default policy with glob resources and deny-beats-allow precedence.
- Separate OAuth resource server (MCP) from authorization server (IdP).
- Approval middleware for actions that mutate customer state or issue refunds.

**Authorization profile:** `role:internal_admin` — can configure policies,
manage tenant impersonation scopes, and review approval queues.

---

## 6. Support Copilot — "Atlas Copilot" (AI Agent)

**Role:** Automated actor that calls MCP tools on behalf of a human agent.
Implemented as Planner → Retriever → Synthesizer → Critic pipeline.

**This is not a human user**, but it is a first-class principal in auth and
audit logs. Every tool call carries:

- `sub` — the copilot service identity (`role:support_copilot`).
- `act.sub` — the human agent who initiated the run (Jordan, Sam, etc.).
- `tenant` — the Acme tenant id (e.g. `acme`).
- `scope` — tool-level permissions granted by the IdP.

**Goals:**
- Retrieve only what the plan requires, within a bounded tool-call budget
  (default max 6 iterations).
- Produce a draft reply with citation anchors, never invent facts.
- Stop and flag when the critic detects an unsupported refund promise.

**Constraints (non-negotiable):**
- Client-side tool allowlist **and** server-side policy enforcement.
- Token, time, and tool-call ceilings per run.
- All actions logged with actor delegation for compliance.

**Authorization profile:** `role:support_copilot` — read tools only;
no `DROP`, no PII columns, no unapproved writes.

---

## 7. End Customer — "Casey"

**Role:** Acme shopper who opens a ticket about a delayed refund, wrong item,
or account question.

**Background:** Expects a reply within hours, not days. May be on a loyalty
tier that demands white-glove treatment.

**Goals:**
- Accurate answer the first time.
- No contradictory information across email and chat.
- Refunds processed only when policy allows — not because an AI promised one.

**Relationship to the system:**
- Casey **never** calls the MCP server or copilot API directly.
- Casey interacts only with human agents (or future customer-facing channels
  that remain out of scope for v1).
- Every improvement in Jordan's draft quality and lookup speed flows through
  to Casey as faster, more consistent support.

**Needs (indirect):**
- Human-reviewed replies before send.
- Grounded policy citations agents can double-check.
- Audit trail if a dispute arises later.

---

## Persona → System Boundary Map

```
  End customer (Casey)
        │
        ▼ submits ticket
  Human agent (Jordan / Sam)
        │
        ▼ asks question, reviews draft
  Support copilot (AI)
        │
        ▼ MCP tool calls (JWT + tenant + act.sub)
  MCP server ──► Postgres · Elasticsearch · S3 · Vector store
        │
        ▼ metrics + audit
  Team lead (Morgan) · Platform (Riley) · Admin (Alex)
```

## Design Implications

| Persona | Drives requirement for |
|---------|------------------------|
| Jordan, Sam | Copilot CLI/UI, citations, approval flags on drafts |
| Morgan | Audit logs, run summaries, cost/token metrics |
| Riley | Stateless HTTP, circuit breakers, OTel, health probes |
| Alex | YAML policy, deny-by-default, approval gates, tenant pinning |
| Atlas Copilot | Tool allowlist, bounded retriever loop, structured errors |
| Casey | Human-in-the-loop send — no autonomous customer-facing replies in v1 |
