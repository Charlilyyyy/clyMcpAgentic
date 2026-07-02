# Request Pipeline

Fixed middleware and dispatch ordering for every MCP tool call. The sequence
is **not negotiable** — each layer assumes the previous one has already run.

See also: [`components.md`](./components.md) for per-component detail,
[`overview.md`](./overview.md) for layered context.

---

## Two Pipeline Stages

A tool call crosses two distinct stages:

| Stage | Where | What runs |
|-------|-------|-----------|
| **A. HTTP / transport edge** | Starlette middleware + MCP session | Trace, auth, tenant pinning |
| **B. Tool dispatch** | `server.py` → `_dispatch()` | Validate, policy, rate limit, cache, breaker, execute, errors, audit |

Stage A applies to every HTTP request hitting `/mcp`. Stage B applies to
every `call_tool` invocation (stdio and HTTP both land here).

---

## Full Request Path

```
  Client (agent / MCP host)
       │
       ▼
┌──────────────────────────────────────────────────────────────────┐
│  STAGE A — Transport edge (HTTP only; stdio skips Starlette)     │
├──────────────────────────────────────────────────────────────────┤
│  [1]  Transport          server.py          Streamable HTTP / stdio
│  [11] Tracing            observability/tracing.py   OTel span START
│  [2]  Auth middleware    auth/middleware.py       JWT → Principal
│  [12] Tenant middleware  governance/tenant.py     tenant → request.state
└──────────────────────────────┬───────────────────────────────────┘
                               │
                               ▼
                    MCP call_tool handler
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│  STAGE B — Tool dispatch (_dispatch in server.py)                │
├──────────────────────────────────────────────────────────────────┤
│  [5]  Input validation   tools/base.py          Pydantic schema
│  [3]  Policy engine      auth/policy.py        deny-by-default
│  [8]  Rate limiter       ratelimit/limiter.py    Redis token bucket
│  [9]  Cache read         cache/manager.py        L1 → L2 → miss
│       │                                              │
│       │ (cache HIT) ─────────────────────────► return result
│       │ (cache MISS)
│       ▼
│  [7]  Circuit breaker    reliability/circuit_breaker.py
│  [6]  Tool execution     tools/{atomic,composed,workflow}/
│       │                      (retry + ATBA wrap execution)
│       ▼
│  [10] Error normalise    errors/framework.py     SERF → MCP wire
│  [11] Audit + metrics    observability/{audit,metrics}.py
└──────────────────────────────┬───────────────────────────────────┘
                               │
                               ▼
                         MCP response
                               │
                               ▼
                    OTel span END (trace_id in audit)
```

---

## Ordered Steps with Rationale

| Order | Component | Why this position |
|-------|-----------|-------------------|
| 1 | **Tracing first** | Every downstream layer adds span attributes (tool, tenant, cache hit, breaker state). If tracing starts late, decisions are invisible. |
| 2 | **Auth before tenant** | Tenant is derived from the authenticated `Principal`. Unauthenticated callers must not reach tenant logic. |
| 3 | **Tenant before dispatch** | `request.state.tenant` must be set before `call_tool` builds the envelope. |
| 4 | **Validation before policy** | Malformed payloads are rejected cheaply. Policy evaluation is wasted on bad JSON. |
| 5 | **Policy before rate limit** | A denied call must **not** consume quota. Ordering leak = attacker burns quota on denied actions. |
| 6 | **Rate limit before cache** | A cache hit for a banned caller is still a data leak. Check permission before serving cached data. |
| 7 | **Cache before circuit breaker** | Warm cache can answer during a downstream outage. Breaker only wraps actual backend calls. |
| 8 | **Breaker before execute** | Fast-fail when backend is known unhealthy. Avoid hammering a dead Postgres. |
| 9 | **Errors before response** | All exceptions normalise to SERF `ToolError` → MCP `ErrorData`. No raw tracebacks on the wire. |
| 10 | **Audit last (before return)** | Log final outcome: ok, denied, rate-limited, error code. Metrics incremented here. |

---

## Stage A — Transport Edge Detail

### Applicable routes

| Route | Auth required | Pipeline |
|-------|---------------|----------|
| `POST /mcp` | Yes | Full Stage A + B |
| `GET /.well-known/mcp-server` | No | Discovery metadata only |
| `GET /healthz` | No | Liveness probe |
| `GET /readyz` | No | Readiness (Redis, registry loaded) |
| `GET /metrics` | No | Prometheus scrape (restrict in prod) |

### Middleware stack (Starlette)

```python
middleware = [
    Middleware(TracingMiddleware),   # [11] span begins
    Middleware(AuthMiddleware),      # [2]  Bearer JWT → Principal
    Middleware(TenantMiddleware),    # [12] tenant → request.state
]
```

**stdio transport:** Starlette middleware is bypassed. Auth context is
injected via environment token / dev principal for local hosts. Production
stdio usage should still carry a valid JWT where the host supports it.

### Principal extraction

After auth middleware, `request.state` carries:

| Field | Source |
|-------|--------|
| `subject` | JWT `sub` |
| `tenant` | JWT `tenant` claim (overridable by admin impersonation header) |
| `scopes` | JWT `scope` (space-separated) |
| `actor` | JWT `act.sub` — human who delegated to the agent |

---

## Stage B — Dispatch Detail

Pseudocode for `_dispatch(envelope)`:

```python
async def _dispatch(envelope: ToolCallEnvelope):
  # [5] Validate
  tool = registry.get(envelope.tool)
  validated_args = tool.validate(envelope.arguments)

  # [3] Policy
  policy.check(
    subject=envelope.caller,
    tenant=envelope.tenant,
    action=f"tool:{envelope.tool}",
    resource=f"tenant:{envelope.tenant}/*",
    context=validated_args,
  )

  # [8] Rate limit
  await limiter.acquire(envelope.tenant, envelope.tool)

  # [9] Cache read
  cache_key = tool.cache_key(envelope.tenant, validated_args)
  if cached := await cache.get(cache_key):
    metrics.cache_hit.labels(tool=envelope.tool).inc()
    audit.log(outcome="cache_hit", ...)
    return cached

  # [7] Circuit breaker + [6] Execute (+ retry inside breaker)
  breaker = breakers.for_tool(envelope.tool)
  with metrics.latency.labels(tool=envelope.tool).time():
    result = await breaker.call(tool.execute, envelope.tenant, validated_args)

  # [9] Cache write-through
  if tool.cacheable:
    await cache.set(cache_key, result, ttl=tool.cache_ttl_seconds)

  # [11] Audit + metrics
  metrics.calls_total.labels(tool=envelope.tool, status="ok").inc()
  audit.log(outcome="ok", args_hash=hash_args(validated_args), ...)
  return result
```

---

## Failure Paths at Each Layer

| Layer | Failure | HTTP / MCP result | Retryable? |
|-------|---------|-------------------|------------|
| Auth | Missing / invalid JWT | 401 Unauthorized | Yes (refresh token) |
| Tenant | Missing tenant claim | 400 Bad Request | No |
| Validation | Schema violation | `ValidationError` | No |
| Policy | Rule deny | `PolicyError` | No |
| Rate limit | Quota exhausted | `RateLimitError` + `retry_after_seconds` | Yes (after delay) |
| Cache | Miss | Continue to breaker | — |
| Circuit breaker | Open | `UpstreamError` fast-fail | Yes (after recovery window) |
| Execution | Backend timeout / 5xx | `UpstreamError` | Yes (if idempotent read) |
| Approval gate | Destructive without approval | `PendingApprovalError` | No (human action required) |
| Error framework | Any `ToolError` | MCP `ErrorData` with `code`, `hint` | Per `retryable` flag |

**Rule:** Policy denials and validation failures never increment rate-limit
counters or write cache entries.

---

## Cache Hit Short-Circuit

```
call_tool
   │
   ▼
validate ──► policy ──► rate_limit
   │
   ▼
cache.get(key)
   │
   ├── HIT ──► audit(cache_hit) ──► return cached JSON
   │           (breaker NOT consulted)
   │           (backend NOT contacted)
   │
   └── MISS ──► breaker ──► execute ──► cache.set ──► audit(ok) ──► return
```

Cache keys: `SHA256(tool + tenant + canonical_json(args))`.

Tenant isolation: keys always include tenant — no cross-tenant collision.

---

## `list_tools` vs `call_tool`

| Handler | Stage B steps | Notes |
|---------|---------------|-------|
| `list_tools` | Registry visibility filter only | Returns tools whose scopes ⊆ caller scopes |
| `call_tool` | Full pipeline | Every step in Stage B runs |

`list_tools` does not hit rate limiter or cache — it is cheap metadata.
It still requires auth in production so anonymous callers cannot enumerate
the attack surface.

---

## Agent Copilot Request Path

When the Retriever calls MCP, the full server pipeline runs per tool
invocation:

```
Human question
     │
     ▼
Planner (no MCP)
     │
     ▼
Retriever iteration 1..6
     │
     ├── AtlasMCPClient.call_tool("customer.build_context", ...)
     │        │
     │        └──► FULL Stage A + B pipeline (this document)
     │
     ├── AtlasMCPClient.call_tool("semantic_search", ...)
     │        └──► FULL pipeline again
     │
     ▼
findings[] ──► Synthesizer ──► Critic ──► CopilotResponse
```

One copilot answer may trigger multiple full pipeline traversals (one per
tool call). ATBA divides the 30-second server budget across those calls.

---

## Trace Propagation

```
HTTP request
     │
     ▼
trace_id = W3C traceparent (or generated)
     │
     ├──► OTel root span: "mcp.request"
     │         │
     │         ├── child span: "auth.validate"
     │         ├── child span: "policy.check"
     │         ├── child span: "ratelimit.acquire"
     │         ├── child span: "cache.get"  (attribute: hit=true|false)
     │         └── child span: "tool.execute" (attribute: tool, tenant)
     │
     └──► audit.jsonl line includes trace_id
               │
               └──► CopilotResponse.run_id joins agent log ↔ server trace
```

---

## Ordering Anti-Patterns (Do Not Do)

| Wrong order | Consequence |
|-------------|-------------|
| Rate limit before policy | Denied calls burn quota |
| Cache before rate limit | Banned caller reads cached tenant data |
| Circuit breaker before cache | Cache hits skipped during outage |
| Auth after tenant | Tenant derived from unauthenticated request |
| Audit before error normalise | Inconsistent error codes in logs |
| Validation after policy | Policy evaluates garbage payloads |
| Tracing after auth | Auth failures missing from distributed trace |

---

## Implementation Checklist

| Item | Module | Status |
|------|--------|--------|
| Tracing middleware first in stack | `observability/tracing.py` | Design agreed |
| Auth → tenant middleware order | `auth/`, `governance/tenant.py` | Design agreed |
| Dispatch: validate → policy → limit → cache → breaker → execute | `server.py` | Design agreed |
| SERF wraps all `ToolError` at handler boundary | `errors/framework.py` | Design agreed |
| Audit + metrics on ok, cache_hit, and error paths | `observability/` | Design agreed |
| Policy deny skips rate limit | `server.py` ordering | Design agreed |

---

## Related Documents

| Document | Contents |
|----------|----------|
| [`data-plane.md`](./data-plane.md) | Backend connections inside tool execution |
| [`tech-stack.md`](./tech-stack.md) | Redis, OTel, Prometheus wiring |
| [`../success-metrics.md`](../success-metrics.md) | Latency SLOs this pipeline must meet |
