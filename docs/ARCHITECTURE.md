# Atlas-MCP Architecture

How the server is put together, why the request pipeline is ordered the way
it is, and what each of the twelve components does. Deeper per-topic
write-ups live under [`docs/architecture/`](./architecture/); this document
is the map.

- [`architecture/overview.md`](./architecture/overview.md) — layered context
- [`architecture/components.md`](./architecture/components.md) — per-component detail
- [`architecture/request-pipeline.md`](./architecture/request-pipeline.md) — the non-negotiable ordering
- [`architecture/data-plane.md`](./architecture/data-plane.md) — backend connections
- [`architecture/tech-stack.md`](./architecture/tech-stack.md) — Redis/OTel/Prometheus wiring

---

## Layered view

```
                       ┌───────────────────────────────────┐
   Agent layer         │  Planner · Retriever · Synth · Critic
   (agents/)           │  Orchestrator + MCP client
                       └───────────────┬───────────────────┘
                                       │  MCP (Streamable HTTP / stdio)
                       ┌───────────────▼───────────────────┐
   Transport edge      │  Tracing → Auth → Tenant middleware
   (transport/, auth/, │
    governance/)       └───────────────┬───────────────────┘
                                       │  call_tool envelope
                       ┌───────────────▼───────────────────┐
   Dispatch pipeline   │  validate → policy → rate-limit →
   (server.py)         │  cache → breaker → execute →
                       │  errors → audit/metrics
                       └───────────────┬───────────────────┘
                                       │
                       ┌───────────────▼───────────────────┐
   Data plane          │  Postgres · Elasticsearch · S3 · Qdrant
   (tools/clients/)     └───────────────────────────────────┘
```

Identity, governance, and observability are **crosscutting**: they touch
every layer rather than sitting at one.

---

## The request pipeline (and why the order is fixed)

Every tool call crosses two stages. Stage A runs at the HTTP edge for each
request to `/mcp`; Stage B runs inside `dispatch()` for each `call_tool`
(both stdio and HTTP land here).

| Order | Step | Module | Why here |
|-------|------|--------|----------|
| 1 | Tracing | `observability/tracing.py` | Starts first so every later decision (auth, cache hit, breaker state) can be attached to the span. |
| 2 | Auth | `auth/middleware.py`, `auth/oauth.py` | Tenant and scopes are derived from the authenticated principal; unauthenticated callers must not reach tenant logic. |
| 3 | Tenant pinning | `governance/tenant.py` | Sets `request.state.tenant` before the envelope is built. |
| 4 | Validation | `tools/base.py`, `validation/` | Reject malformed/adversarial payloads cheaply before spending anything on policy. |
| 5 | Policy | `auth/policy.py` | Deny-by-default authorization. Runs before rate limiting so a **denied call never consumes quota**. |
| 6 | Rate limit | `ratelimit/limiter.py` | Runs before cache so a banned caller cannot read cached tenant data. |
| 7 | Cache | `cache/l1.py`, `cache/manager.py` | Runs before the breaker so a **warm cache can still answer during a downstream outage**. |
| 8 | Circuit breaker + retry + ATBA | `reliability/` | Wraps only the real backend call; fast-fails when a downstream is known unhealthy. |
| 9 | Execute | `tools/{atomic,composed,workflow}/` | The actual work, tenant-scoped. |
| 10 | Error normalisation | `errors/framework.py` | All exceptions become SERF `ToolError` → MCP `ErrorData`; no raw tracebacks on the wire. |
| 11 | Audit + metrics | `observability/{audit,metrics}.py` | Records the final outcome (ok / denied / rate-limited / error) with a hashed args digest and the trace id. |

The anti-patterns table in
[`request-pipeline.md`](./architecture/request-pipeline.md#ordering-anti-patterns-do-not-do)
spells out what breaks if any two of these are swapped.

---

## The twelve components in depth

### 1. Transport & session (`server.py`, `transport/`)
Dual transport: **stdio** for local hosts, **Streamable HTTP** for remote.
Sessions are stateless (`ATLAS_STATELESS_MODE`) so any replica can serve any
request — shared state lives in Redis, not the process.

### 2. Authentication (`auth/`)
OAuth 2.1 resource-server model: bearer JWTs validated against a JWKS URL
with issuer/audience checks and short token TTLs. Delegation is captured via
the `act` claim so the audit trail records the human behind an agent.

### 3. Authorization & policy (`auth/policy.py`, `config/policy.yaml`)
YAML-driven, evaluated top-to-bottom, **deny-by-default**, deny rules beat
allow rules. Supports RBAC (subjects/roles), tenant-scoped ABAC (glob
resources like `tenant:*/docs/*`), and conditions (e.g. block SQL containing
`DROP`, block PII columns).

### 4. Tool registry & discovery (`tools/registry.py`)
Dynamic registration with capability metadata (scopes required, destructive
flag, cacheability). Exposed via `.well-known/mcp-server` and `list_tools`,
which filter to tools whose required scopes are a subset of the caller's.

### 5. Input validation (`validation/`)
`schemas.py` defines the call envelope; `adversarial.py` bounds size and
nesting depth, strips control characters, and rejects unknown fields. The
default posture is that **input is adversarial**, including LLM-produced args.

### 6. Tool execution (`tools/base.py`, `tools/{atomic,composed,workflow}/`)
Three levels: **atomic** (one backend each, via injectable
`tools/backends.py` protocols and `tools/clients/` implementations),
**composed** (deterministic chains like `semantic_search`, `hybrid_search`),
and **workflow** (multi-step fan-outs like `customer.build_context`).

### 7. Reliability (`reliability/`)
Per-tool three-state circuit breaker, retry with full-jitter exponential
backoff (only for `retryable` errors, never destructive tools), and
**Adaptive Timeout Budget Allocation** that divides a total budget across a
sequence of calls using historical p95 latency.

### 8. Rate limiting (`ratelimit/limiter.py`)
Token-bucket, per-tenant and per-tool, atomic refill via a Redis Lua script
in production (in-memory backend for tests). Expensive tools get tighter
quotas.

### 9. Caching (`cache/`)
Two tiers: L1 in-process LRU with TTL, L2 Redis shared across replicas.
`get_or_compute` provides single-flight stampede protection. Keys are
`atlas:{tenant}:{tool}:{sha256(args)}`, enabling targeted invalidation by
key, tool, or tenant.

### 10. Structured error framework — SERF (`errors/framework.py`)
Every failure is a `ToolError` subclass carrying a machine-readable `code`,
a `retryable` flag, and a human-actionable `hint`. Agents read these fields
to decide whether to retry, back off, or escalate.

### 11. Observability (`observability/`)
OpenTelemetry spans stitched from agent → server via one trace id,
Prometheus metrics (calls, latency, cache hit/miss, rate-limited, circuit
state), and a structured JSONL audit log that hashes arguments to avoid PII
leakage.

### 12. Governance & multi-tenancy (`governance/`)
Tenant isolation at the middleware layer, tenant-prefixed/filtered data
access in every tool, a human-in-the-loop approval gate for destructive
tools, and a per-tenant outbound HTTP allow-list (SSRF protection).

---

## Where the agent layer fits

The agent layer is a **client** of this pipeline, not a bypass. When the
Retriever calls a tool, the full Stage A + B pipeline runs for that call —
same auth, same policy, same rate limits. See
[`AGENT_SYSTEM.md`](./AGENT_SYSTEM.md).
