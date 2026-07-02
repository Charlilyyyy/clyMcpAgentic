# Architecture Overview

High-level design for the Acme Commerce support copilot and its MCP
infrastructure layer. This document establishes **what the system is**, how
the major layers relate, and the design principles that constrain every
later implementation decision.

Builds on the idea-definition set in [`../problem-statement.md`](../problem-statement.md),
[`../use-case.md`](../use-case.md), and [`../success-metrics.md`](../success-metrics.md).

---

## System Purpose

The system has two cooperating layers:

| Layer | Responsibility |
|-------|----------------|
| **MCP server** | Secure, governed tool surface over Acme's data plane |
| **Agent copilot** | Retrieves data and drafts cited replies for human support agents |

The MCP server is **infrastructure** — transport, auth, policy, tools,
reliability, observability. The agent copilot is the **product** — Planner,
Retriever, Synthesizer, Critic pipeline that human agents interact with.

Neither layer is optional for production. A copilot without a hardened MCP
server leaks data and burns cost. An MCP server without a bounded agent
layer is a demo waiting for a pager.

---

## Layered Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         HUMAN SUPPORT AGENT                             │
│              (pastes question, reviews draft, sends reply)              │
└─────────────────────────────────┬───────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      AGENT COPILOT (product layer)                      │
│  ┌──────────┐   ┌───────────┐   ┌─────────────┐   ┌─────────┐          │
│  │ Planner  │ → │ Retriever │ → │ Synthesizer │ → │ Critic  │          │
│  └──────────┘   └─────┬─────┘   └─────────────┘   └─────────┘          │
│                       │ MCP JSON-RPC (JWT + tenant + act.sub)           │
└───────────────────────┼─────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      MCP SERVER (infrastructure layer)                  │
│                                                                         │
│  Transport → Trace → Auth → Tenant → Validate → Policy → RateLimit      │
│           → Cache → CircuitBreaker → Execute → Errors → Audit           │
│                                                                         │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────────────────┐    │
│  │ Tool        │  │ Reliability  │  │ Observability + Governance  │    │
│  │ Registry    │  │ (breaker,    │  │ (OTel, Prometheus, audit,   │    │
│  │ + 3-level   │  │  retry, ATBA) │  │  approval gates, RLS)       │    │
│  │ hierarchy   │  │              │  │                             │    │
│  └─────────────┘  └──────────────┘  └─────────────────────────────┘    │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
          ┌─────────────────────┼─────────────────────┐
          ▼                     ▼                     ▼
   ┌─────────────┐      ┌─────────────┐      ┌─────────────┐
   │ PostgreSQL  │      │Elasticsearch│      │ S3 / MinIO  │
   │ (+ RLS)     │      │             │      │             │
   └─────────────┘      └─────────────┘      └─────────────┘
          │                     │                     │
          └─────────────────────┼─────────────────────┘
                                ▼
                    ┌─────────────────────┐
                    │ Vector store        │
                    │ (Qdrant)            │
                    └─────────────────────┘

          ┌─────────────────────┐      ┌─────────────────────┐
          │ Redis               │      │ OAuth IdP (external)│
          │ (cache, rate limit, │      │ (tokens, JWKS)      │
          │  session, approvals)│      │                     │
          └─────────────────────┘      └─────────────────────┘
```

---

## Major Subsystems

### 1. Agent copilot

Four single-purpose agents with narrow contracts:

- **Planner** — structured retrieval plan (JSON); no tool calls.
- **Retriever** — bounded MCP tool-calling loop (read-only allowlist).
- **Synthesizer** — draft reply with citation anchors from findings only.
- **Critic** — approve or request one revision; block unsafe commitments.

One shared `AgentRun` object carries token counts, tool calls, and `run_id`
across all agents for observability.

### 2. MCP server

Twelve production components organised as a **request pipeline** (detailed in
a later document). Every tool call traverses the full stack — nothing is
optional in production mode.

Key modules (planned package layout):

```
src/atlas_mcp/
├── server.py              # transport + dispatch entry
├── auth/                  # JWT validation + policy engine
├── governance/            # tenant pinning + approval gates
├── tools/                 # registry + atomic / composed / workflow
├── validation/            # shared schemas
├── reliability/           # circuit breaker, retry, ATBA
├── ratelimit/             # Redis token bucket
├── cache/                 # L1 + L2 cache
├── errors/                # structured error framework (SERF)
├── observability/         # tracing, metrics, audit
└── agents/                # copilot product layer
```

### 3. Data plane

Heterogeneous backends unified behind MCP tools:

| Backend | Primary data |
|---------|--------------|
| PostgreSQL | Customers, orders, tickets (tenant-scoped with RLS) |
| Elasticsearch | Full-text search over tickets and logs |
| S3-compatible storage | Invoices, attachments, evidence files |
| Vector store | Semantic search over help-centre and policy docs |

### 4. Cross-cutting platform services

| Service | Role |
|---------|------|
| **Redis** | Rate-limit buckets, L2 cache, optional session state, approval queue |
| **OAuth IdP** (external) | Login, consent, token issuance — **not** embedded in MCP server |
| **OTel collector** | Distributed traces fan-out to Jaeger / Datadog |
| **Prometheus** | Scrape `/metrics` for SLO dashboards |

---

## Identity Model

The MCP server is an **OAuth 2.1 resource server**. It validates JWTs; it
does not issue them.

```
  Human agent logs in ──► Authorization server (IdP)
                              │
                              ▼ issues JWT
  Agent copilot / MCP host ──► MCP resource server
                              │
                              validates: issuer, audience, expiry, JWKS
                              extracts:  sub, tenant, act.sub, scope
```

Critical claims:

| Claim | Meaning |
|-------|---------|
| `sub` | Service identity (e.g. `support_copilot`) |
| `act.sub` | Human who authorised the run (audit delegation) |
| `tenant` | Multi-tenancy scope (e.g. `acme`) |
| `scope` | Tool permissions (e.g. `tool:postgres:read`) |

---

## Transport Strategy

Two transports from day one:

| Transport | Use case |
|-----------|----------|
| **stdio** | Local development — Claude Desktop, Cursor |
| **Streamable HTTP** | Remote deployment — horizontally scaled replicas |

Streamable HTTP runs in **stateless mode**: any replica handles any request.
Session state (if needed) lives in Redis, not in process memory. This
eliminates sticky-session requirements for Kubernetes / ECS rollouts.

---

## Tool Surface Strategy

Three-level hierarchy exposed to agents:

```
  Workflow     customer.build_context     (one call, multi-backend fan-out)
      │
  Composed     semantic_search, hybrid_search   (deterministic chains)
      │
  Atomic       postgres, elasticsearch, s3, vector, http, embeddings
```

Higher-level tools reduce erroneous calls and token waste. Atomic tools
remain available for edge cases and power users.

---

## Design Principles

| Principle | Implication |
|-----------|-------------|
| **Deny by default** | No tool call succeeds without explicit policy allow |
| **Agent input is adversarial** | Validate before policy; never trust LLM-generated args |
| **Ordering is load-bearing** | Middleware sequence is fixed and documented (not rearrangeable) |
| **Belt and suspenders** | Client-side tool allowlist + server-side policy enforcement |
| **Bounded agents** | Hard caps on iterations, tokens, revise loops, and latency |
| **Observable by default** | Every call gets trace id, metrics, and audit line |
| **Fail closed on auth** | Unauthenticated or cross-tenant requests are rejected |
| **Cache warm during outage** | Cache sits before circuit breaker so hits survive backend failure |
| **Human sends the reply** | Copilot drafts; agents approve and send (see [`../non-goals.md`](../non-goals.md)) |

---

## Requirements Traceability

| Idea-definition requirement | Architectural answer |
|----------------------------|------------------------|
| Multi-system lookup pain | Unified MCP tool surface over four backends |
| Grounded replies | Retriever findings-only synthesis + critic gate |
| Cost bounds | Rate limits, ATBA, agent token/tool ceilings |
| Approval for refunds | Governance approval middleware + critic |
| Audit "who called what" | `act.sub` + immutable JSONL audit log |
| Multi-tenant isolation | JWT tenant claim + policy globs + Postgres RLS |
| Production reliability | Circuit breakers, retry, structured errors, cache |

---

## Document Map

Subsequent architecture documents in this folder:

| Document | Contents |
|----------|----------|
| **overview** (this file) | Layers, subsystems, principles |
| `components.md` | Twelve production components in detail |
| `request-pipeline.md` | Middleware order and rationale |
| `data-plane.md` | Backends, transports, identity separation |
| `tech-stack.md` | Locked technology choices + dependency graph |

---

## Open Decisions (resolved in later docs)

The following are **fixed by design** and will be expanded in the remaining
architecture documents in this folder:

- [ ] Full component catalogue with ownership
- [ ] Request-path diagram with per-layer module mapping
- [ ] Data-plane connection patterns and RLS strategy
- [ ] Tech stack version locks and dependency graph

Once those documents are approved, implementation scaffolding (package
layout, Docker Compose, config files) can begin.
