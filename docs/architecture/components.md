# Twelve Production Components

Catalogue of every load-bearing component in the MCP server infrastructure
layer. Each component owns a single concern, lives in its own module, and
can be read, replaced, or extended independently.

See also: [`overview.md`](./overview.md) for layered context.
Request ordering is detailed in [`request-pipeline.md`](./request-pipeline.md).

---

## Component Index

| # | Component | Module | Owner |
|---|-----------|--------|-------|
| 1 | Transport & Session | `server.py` | Platform |
| 2 | Authentication | `auth/oauth.py`, `auth/middleware.py` | Security |
| 3 | Authorization & Policy | `auth/policy.py` | Security |
| 4 | Tool Registry & Discovery | `tools/registry.py` | Platform |
| 5 | Input Validation | `validation/schemas.py`, `tools/base.py` | Platform |
| 6 | Tool Execution Engine | `tools/{atomic,composed,workflow}/` | Data / Platform |
| 7 | Reliability | `reliability/` | Platform / SRE |
| 8 | Rate Limiting & Quotas | `ratelimit/limiter.py` | Platform |
| 9 | Caching | `cache/manager.py` | Platform |
| 10 | Structured Error Framework | `errors/framework.py` | Platform |
| 11 | Observability | `observability/` | SRE |
| 12 | Governance & Multi-Tenancy | `governance/` | Security / Platform |

**Ownership key:**
- **Platform** — core MCP server engineering
- **Security** — auth, policy, tenant isolation, approval gates
- **SRE** — tracing, metrics, audit, on-call runbooks
- **Data** — backend connectors inside atomic tools

---

## 1. Transport & Session Layer

**Module:** `src/atlas_mcp/server.py`

**Responsibility:** Accept MCP protocol traffic, bootstrap the application,
route `list_tools` / `call_tool` to the dispatch pipeline, expose health and
discovery endpoints.

| Capability | Detail |
|------------|--------|
| stdio transport | Local dev — Claude Desktop, Cursor |
| Streamable HTTP | Remote deployment at `/mcp` |
| Stateless sessions | Any replica handles any request; no sticky routing |
| Session backing store | Redis when session state is required |
| Health probes | `/healthz` (liveness), `/readyz` (readiness) |
| Discovery | `/.well-known/mcp-server` (unauthenticated metadata) |
| Metrics scrape | `/metrics` for Prometheus |

**Dependencies:** Starlette, Uvicorn, MCP SDK.

**Does not:** Validate JWTs (delegates to auth middleware), execute tools
directly (delegates to dispatch handler).

---

## 2. Authentication

**Modules:** `src/atlas_mcp/auth/oauth.py`, `src/atlas_mcp/auth/middleware.py`

**Responsibility:** Validate bearer tokens on every request. Extract a
`Principal` with subject, tenant, scopes, and delegated actor.

| Capability | Detail |
|------------|--------|
| JWT validation | Issuer, audience, expiry, signature via JWKS |
| Resource-server model | MCP server never issues tokens |
| MCP claims | `tenant`, `act.sub` (RFC 8693), `scope` |
| Failure mode | Fail closed — 401 on missing or invalid token |

**Dependencies:** External OAuth IdP (WorkOS, Auth0, Keycloak, etc.), PyJWT,
Authlib.

**Does not:** Handle login UI, consent screens, or client registration.

---

## 3. Authorization & Policy Engine

**Module:** `src/atlas_mcp/auth/policy.py`

**Responsibility:** Deny-by-default access control for every tool call.
Evaluate YAML rules against subject, tenant, action, resource, and context.

| Capability | Detail |
|------------|--------|
| Policy file | `config/policy.yaml` (hot-reloadable) |
| Rule precedence | Deny beats allow; absence of allow = deny |
| Resource convention | `tenant:<id>/<path>` glob matching |
| Condition DSL | `sql_contains_any`, `pii_fields` (narrow, auditable) |
| Tool scopes | `tool:postgres:read`, `tool:s3:write`, etc. |

**Example roles from policy:**

| Role | Typical access |
|------|----------------|
| `role:support_copilot` | Read tools; no PII columns; no destructive SQL |
| `role:human_agent` | Read + scoped S3 write (attachments) |
| `role:analyst` | Analytics-scoped Postgres read only |
| `role:internal_admin` | Tenant impersonation (audited) |

**Dependencies:** Component 2 (Principal), PyYAML.

**Does not:** Rate-limit or cache (runs before both).

---

## 4. Tool Registry & Discovery

**Module:** `src/atlas_mcp/tools/registry.py`

**Responsibility:** Maintain the in-memory index of registered tools. Serve
`list_tools` and resolve `call_tool` to the correct executor.

| Capability | Detail |
|------------|--------|
| Dynamic registration | Tools register at startup with metadata |
| Scope visibility filter | Callers only see tools their scopes permit |
| Capability metadata | Name, description, input schema, required scopes |
| Well-known endpoint | Publishes server capabilities without auth |

**Dependencies:** Component 2 (scopes for visibility), Component 5 (schemas).

**Does not:** Execute tools or enforce policy (delegates downstream).

---

## 5. Input Validation

**Modules:** `src/atlas_mcp/validation/schemas.py`, `src/atlas_mcp/tools/base.py`

**Responsibility:** Parse and validate every tool-call envelope and per-tool
argument payload before policy evaluation or backend access.

| Capability | Detail |
|------------|--------|
| Envelope schema | Tool name, tenant, trace id, actor delegation |
| Per-tool Pydantic models | Type constraints, enums, regex, bounds |
| Threat model | Agent-generated input treated as adversarial |
| Guard examples | SELECT-only SQL, HTTPS-only URLs, customer id regex |
| Error output | `ValidationError` with one-line actionable hint |

**Dependencies:** Pydantic v2.

**Does not:** Authorize (runs before policy) or hit backends.

---

## 6. Tool Execution Engine

**Modules:** `src/atlas_mcp/tools/base.py`, `tools/atomic/`, `tools/composed/`,
`tools/workflow/`

**Responsibility:** Execute validated, authorized tool calls against backends.
Three-level hierarchy:

| Level | Examples | Character |
|-------|----------|-----------|
| **Atomic** | `postgres.query`, `elasticsearch.search`, `s3.get`, `vector.search`, `http.fetch`, `embeddings.encode` | One backend, one primitive |
| **Composed** | `semantic_search`, `hybrid_search` | Deterministic multi-atomic chain |
| **Workflow** | `customer.build_context` | Domain procedure behind one tool name |

**Shared tool contract (`base.py`):**

```
validate(args) → execute(tenant, args) → normalise(result) → audit hook
```

Each tool declares: Pydantic input model, timeout_ms, required scopes,
retryable flag.

**Dependencies:** Data plane backends, Component 7 (breaker/retry wraps execution).

**Does not:** Handle transport or auth (assumes Principal already attached).

---

## 7. Reliability

**Modules:** `src/atlas_mcp/reliability/circuit_breaker.py`,
`reliability/retry.py`, `reliability/atba.py`

**Responsibility:** Survive downstream failures without cascading outages.
Bound end-to-end latency across tool chains.

| Sub-component | Behaviour |
|---------------|-----------|
| **Circuit breaker** | Per-tool state machine: closed → open → half-open |
| **Retry** | Exponential backoff + jitter; only when `retryable=True` |
| **ATBA** | Adaptive Timeout Budget Allocation — 30s total per agent turn, split across calls by observed p95 |

**Breaker defaults:**

| Setting | Value |
|---------|-------|
| Failure threshold | 5 consecutive retryable errors |
| Recovery window | 30 seconds before half-open probe |

**Dependencies:** Redis (optional breaker state), Component 10 (error classification).

**Does not:** Rate-limit (runs after cache, before execute).

---

## 8. Rate Limiting & Quotas

**Module:** `src/atlas_mcp/ratelimit/limiter.py`

**Responsibility:** Protect backends and control cost via per-tenant,
per-tool token buckets.

| Capability | Detail |
|------------|--------|
| Algorithm | Redis Lua script — atomic check-and-consume |
| Key | `(tenant, tool)` |
| Defaults | 60 RPM, burst 20 |
| Denied calls | Policy rejections do **not** consume quota |
| Error response | `RateLimitError` with `retry_after_seconds` |

**Dependencies:** Redis.

**Does not:** Cache responses (runs before cache read).

---

## 9. Caching

**Module:** `src/atlas_mcp/cache/manager.py`

**Responsibility:** Reduce backend load and improve latency for repeated
identical tool calls.

| Tier | Backing | TTL (default) |
|------|---------|---------------|
| L1 | In-process LRU | 60 seconds |
| L2 | Redis | 600 seconds |

| Capability | Detail |
|------------|--------|
| Key format | Hash of `(tool, tenant, args)` — no cross-tenant collision |
| Stampede prevention | Redis NX lock on cache miss |
| Write policy | Write-through on successful execution |
| Hit short-circuit | Return cached result before circuit breaker |

**Dependencies:** Redis.

**Does not:** Cache policy-denied or unauthenticated requests (never reached).

---

## 10. Structured Error Framework (SERF)

**Module:** `src/atlas_mcp/errors/framework.py`

**Responsibility:** Normalise all failures to machine-readable MCP wire format.

| Field | Purpose |
|-------|---------|
| `code` | Stable error identifier (e.g. `es_timeout`, `policy_denied`) |
| `retryable` | Whether the agent should retry |
| `hint` | One-line guidance for the LLM or operator |
| `context` | Structured metadata (no raw PII) |

**Error taxonomy (planned):**

| Class | Examples | Retryable |
|-------|----------|-----------|
| `ValidationError` | Bad args, regex fail | No |
| `AuthError` | Expired token | Yes (refresh) |
| `PolicyError` | Denied by rule | No |
| `RateLimitError` | Quota exceeded | Yes (after delay) |
| `UpstreamError` | Backend timeout, 5xx | Yes |
| `PendingApprovalError` | Write needs human OK | No |

**Dependencies:** MCP SDK error types.

**Does not:** Log audit lines (runs before audit; audit captures outcome).

---

## 11. Observability

**Modules:** `src/atlas_mcp/observability/tracing.py`,
`observability/metrics.py`, `observability/audit.py`

**Responsibility:** Make the system debuggable at 3 AM. Three pillars:

### Tracing (OpenTelemetry)

- Span begins at transport entry (first middleware).
- Attributes: tool, tenant, cache outcome, circuit state.
- Export: OTLP → collector → Jaeger / Datadog.

### Metrics (Prometheus)

| Instrument | Labels |
|------------|--------|
| `calls_total` | tool, status |
| `latency_seconds` | tool |
| `cache_hit_total` | tool |

Label by **tool**, not tenant (cardinality control).

### Audit log (JSONL)

One line per call: timestamp, tenant, caller `sub`, delegator `act.sub`,
tool, `args_hash` (never raw args), trace id, outcome.

**Dependencies:** OTel SDK, prometheus_client, structlog.

**Does not:** Store long-term metrics (Prometheus/Grafana own retention).

---

## 12. Governance & Multi-Tenancy

**Modules:** `src/atlas_mcp/governance/tenant.py`,
`governance/approval.py`

**Responsibility:** Enforce tenant isolation and human-in-the-loop gates for
high-risk actions.

| Capability | Detail |
|------------|--------|
| Tenant middleware | Pin `request.state.tenant` from Principal |
| Impersonation | `X-Tenant-Id` header override — `role:internal_admin` only |
| Approval gate | Destructive tools create Redis-backed pending approval |
| Postgres RLS | `current_setting('app.tenant_id')` — defence in depth |
| Outbound HTTP allowlist | `config/http_allowlist.yaml` per tenant |

**Approval flow:**

```
destructive tool call → PendingApprovalError → human dashboard
                              │
                              ▼ approved
                         tool executes with approval_id
```

**Dependencies:** Redis (approval queue), Component 3 (policy), PostgreSQL RLS.

**Does not:** Issue JWTs or define policy rules (reads policy file).

---

## Component Interaction Matrix

Which components call or depend on which:

```
        1  2  3  4  5  6  7  8  9 10 11 12
    1   -  →  ·  →  ·  ·  ·  ·  ·  ·  →  →
    2   ·  -  →  ·  ·  ·  ·  ·  ·  ·  ·  →
    3   ·  ←  -  ·  ←  ·  ·  ·  ·  ·  ·  ·
    4   ·  ←  ·  -  →  →  ·  ·  ·  ·  ·  ·
    5   ·  ·  ·  ·  -  →  ·  ·  ·  →  ·  ·
    6   ·  ·  ·  ←  ←  -  ←  ·  ·  →  →  ←
    7   ·  ·  ·  ·  ·  →  -  ·  ·  ←  →  ·
    8   ·  ·  ·  ·  ·  ·  ·  -  ·  →  →  ·
    9   ·  ·  ·  ·  ·  →  ·  ·  -  ·  →  ·
   10   ·  ·  ·  ·  ·  ·  ·  ·  ·  -  →  ·
   11   ·  ←  ·  ·  ·  ·  ·  ·  ·  ·  -  ·
   12   ·  ←  ←  ·  ·  ·  ·  ·  ·  ·  ·  -

→ = depends on / invokes
← = depended on by
· = no direct dependency
```

---

## Agent Layer (Product — Outside the Twelve)

The four-agent copilot (`src/atlas_mcp/agents/`) is **not** one of the twelve
MCP server components. It is a **consumer** of the MCP server:

| Agent | Calls MCP? | Component interaction |
|-------|------------|----------------------|
| Planner | No | None |
| Retriever | Yes | Hits full pipeline via `AtlasMCPClient` |
| Synthesizer | No | None |
| Critic | No | None |

The retriever's client-side allowlist mirrors server policy — belt and
suspenders against prompt injection.

---

## Component → File Map (Implementation Target)

```
src/atlas_mcp/
├── server.py                     # 1  Transport
├── auth/
│   ├── oauth.py                  # 2  Authentication
│   ├── middleware.py             # 2  Authentication
│   └── policy.py                 # 3  Authorization
├── tools/
│   ├── registry.py               # 4  Registry
│   ├── base.py                   # 5  Validation + 6  Execution base
│   ├── atomic/                   # 6  Atomic tools
│   ├── composed/                 # 6  Composed tools
│   └── workflow/                 # 6  Workflow tools
├── validation/
│   └── schemas.py                # 5  Validation
├── reliability/                  # 7  Reliability
├── ratelimit/limiter.py          # 8  Rate limiting
├── cache/manager.py              # 9  Caching
├── errors/framework.py           # 10 Errors
├── observability/                # 11 Observability
├── governance/                   # 12 Governance
└── agents/                       # Product layer (consumer)
```

---

## Review Checklist

| Item | Status |
|------|--------|
| All twelve components named and scoped | Done |
| Module ownership assigned | Done |
| Dependencies between components documented | Done |
| Agent layer distinguished from server components | Done |
| Implementation file map agreed | Done |

Request-path ordering and per-layer rationale: [`request-pipeline.md`](./request-pipeline.md).
