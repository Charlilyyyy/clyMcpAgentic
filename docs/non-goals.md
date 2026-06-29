# Non-Goals

Explicit boundaries for what this project will **not** build, optimise for,
or compromise on. Non-goals prevent scope creep, keep the architecture
honest, and stop demo shortcuts from masquerading as production readiness.

Related context: [`problem-statement.md`](./problem-statement.md),
[`success-metrics.md`](./success-metrics.md).

---

## How to Use This Document

When a feature request or design debate arises, check here first. If it
matches a non-goal, the answer is **no for v1** — not "maybe later without
writing it down." Deferred items belong in a separate roadmap, not as silent
assumptions.

---

## 1. Product & User Experience

### Not a customer-facing chatbot

The copilot assists **human support agents** behind the scenes. End customers
(Casey) never interact with the MCP server or agent pipeline directly in v1.

- No public chat widget that auto-replies to shoppers.
- No unsupervised email auto-send.
- Every customer-facing message still goes through a human review step.

### Not an auto-refund or auto-exception engine

The system drafts replies; it does **not** execute financial or policy
actions on its own.

- No automatic refund initiation.
- No automatic discount codes or loyalty credits.
- No bypass of manager approval for exceptions.
- Drafts that promise refunds must be blocked or flagged by the critic and
  governance layer.

### Not a ticket auto-closer

The copilot reduces lookup time and drafting effort. It does not change
ticket status, assign ownership, or mark issues resolved without explicit
human action in the CS platform.

### Not a replacement for support agents

Goal is augmentation, not headcount reduction through full automation.
Tier-1 and Tier-2 agents remain the accountable decision-makers.

---

## 2. Technical Scope

### Not a hello-world MCP demo

A single `@tool` returning `"hello world"` is useful for learning MCP; it is
not this project.

We are not optimising for:
- Fewest lines of code.
- Fastest time-to-first-tool-call without auth.
- A README that only shows a decorated function.

Every component (transport, auth, policy, validation, reliability, rate
limits, cache, errors, observability, governance) is in scope because
production teams need all of them.

### Not a generic agent framework

This is a **reference implementation** for one domain (Acme Commerce support)
on one protocol (MCP). We are not building:

- LangGraph, CrewAI, or AutoGen orchestration layers.
- A plug-in marketplace for arbitrary third-party agents.
- A no-code agent builder UI.

The four-agent pipeline (Planner → Retriever → Synthesizer → Critic) is
deliberately linear with at most one revise loop — not a general-purpose
state machine.

### Not a custom LLM or embedding model

We consume hosted models (e.g. Anthropic Claude for agents, an embeddings
API for vector search). Training, fine-tuning, and model hosting are
out of scope.

### Not a bespoke authorization server

MCP runs as an **OAuth 2.1 resource server** validating JWTs from an
external IdP (WorkOS, Auth0, Keycloak, etc.). We do not ship a full identity
provider, user directory, or SSO product.

### Not multi-cloud abstraction for its own sake

The data plane uses concrete backends: PostgreSQL, Elasticsearch, S3-compatible
storage, Qdrant (or similar vector store), Redis. We document production swap
patterns (RDS, ElastiCache, etc.) but do not build a cloud-agnostic ORM or
storage facade unless a real portability requirement appears.

---

## 3. Agent & Memory Behaviour

### Not unbounded agent loops

- Retriever: hard cap of **6** tool-call iterations.
- Critic revise: **at most 1** extra synthesizer pass.
- No infinite planner–retriever–critic cycles "until good enough."

A confused model must stop and return partial findings or a safe short-circuit
message — not burn tokens until timeout.

### Not persistent customer memory in v1

Long-term vector memory for customers (remembering preferences across sessions)
is **deferred** until approval gates and audit trails are proven in production.

Short-term session context may exist, but the copilot does not silently
accumulate durable profiles about customers without explicit governance design.

### Not prompt-invented tool schemas

Tool descriptions come from the server's `list_tools` output — not
auto-generated schemas hallucinated into the LLM prompt. The MCP registry is
the single source of truth for what tools exist and what they accept.

---

## 4. Security & Compliance

### Not security-through-obscurity

- No shared API keys across tenants.
- No "dev mode" that skips auth in production builds.
- No optional tenant header — tenancy is required when `require_tenant` is on.

### Not trust-the-agent input validation

Agent-generated tool arguments are treated as **adversarial**. Validation runs
before policy and execution. We do not assume the LLM will only produce safe
SQL, URLs, or file paths.

### Not unconstrained outbound HTTP

Agents do not get a generic `fetch(any_url)` capability. Outbound HTTP is
allowlisted per tenant. SSRF protection is non-negotiable — not a later hardening task.

### Not SOC 2 / HIPAA certification as a deliverable

The architecture supports audit logs, tenant isolation, and policy enforcement
that *enable* compliance programmes, but formal certification, legal review,
and data-processing agreements are organisational work outside this codebase.

---

## 5. Operations & Scale

### Not a managed SaaS offering

This repo ships runnable artifacts (Docker Compose, deployment docs, K8s
patterns) — not a hosted multi-tenant SaaS product with billing, signup, and
SLA-backed uptime guarantees from the maintainers.

### Not real-time streaming to end users

v1 returns a complete `CopilotResponse` when the run finishes. Token-by-token
streaming UX for agents is a nice-to-have, not a requirement.

### Not global active-active on day one

Stateless HTTP enables horizontal scale within a region. Cross-region
failover, conflict resolution, and geo-routed data planes are future ops
concerns — not v1 goals.

### Not eliminating all external dependencies

Production assumes managed Postgres, Redis, search, object storage, an IdP,
and an LLM API. The goal is to **survive** dependency failure (circuit
breakers, caches, structured errors) — not to remove dependencies entirely.

---

## 6. Documentation & Process

### Not documentation-after-the-fact only

Architecture decisions and trade-offs are written down **before** or **alongside**
implementation — not as a post-launch cleanup. Docs are part of the deliverable,
not a stretch goal.

### Not collapsing documentation into a single README

Problem statement, personas, success metrics, non-goals, and use-case brief
stay as separate artifacts. We do not fold everything into one unstructured
README and call it done.

---

## 7. Quick Reference — "Is This In Scope?"

| Request | In scope? |
|---------|-----------|
| Draft a cited reply for a human agent | **Yes** |
| Auto-send reply to customer without review | **No** |
| Process refund via tool call without approval | **No** |
| MCP tool that reads Postgres with tenant RLS | **Yes** |
| Single `@tool` demo without auth | **No** |
| LangGraph multi-agent state machine | **No** |
| Circuit breaker on Elasticsearch calls | **Yes** |
| Remember customer preferences forever (LTM) | **No** (v1) |
| JWT validation against external JWKS | **Yes** |
| Build our own OAuth IdP | **No** |
| Rate limit per tenant per tool | **Yes** |
| Public customer-facing AI chat widget | **No** |

---

## 8. What Happens When Someone Proposes a Non-Goal

1. **Name it** — link to the relevant section in this document.
2. **Capture the real need** — often a non-goal masks a valid underlying pain
   (e.g. "auto-refund" → faster approval workflow for Sam).
3. **Redirect** — solve the pain within scope (approval gates, workflow tools,
   better drafts) rather than expanding into a non-goal.
4. **Revisit deliberately** — if a non-goal must change, update this file in
   the same pull request that changes behaviour. Silent scope expansion is
   how production systems fail.
