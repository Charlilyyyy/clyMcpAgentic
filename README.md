<div align="center">

# Atlas-MCP — Production-Grade MCP Server + Agentic System

### A reference implementation of an MCP server designed to actually ship

*Multi-tenant · Authenticated · Observable · Rate-limited · Cached · Circuit-broken · Governed*

</div>

---

## What this is

Most MCP tutorials end with a `@tool` decorator that returns `"hello world"`.
That is fine for a demo. It is not what ships.

**Atlas-MCP** is a reference implementation of an MCP server built to run in
production: multi-tenant, authenticated, observable, rate-limited, cached,
circuit-broken, and governed. It exposes a company's heterogeneous data layer
(Postgres, Elasticsearch, S3, vector DB) to AI agents as a single, secure tool
surface, and ships with a **four-agent support copilot**
(Planner → Retriever → Synthesizer → Critic) that uses it end to end.

The codebase is organised around **twelve components** that keep showing up on
the 3 AM pager when teams skip them. Each lives in its own module and can be
read, replaced, or extended independently.

---

## The 12 components

| # | Component | Lives in | What it gives you |
|---|-----------|----------|-------------------|
| 1 | Transport & Session Layer | `server.py`, `transport/` | stdio for local, Streamable HTTP for remote, stateless horizontal scale |
| 2 | Authentication | `auth/oauth.py`, `auth/middleware.py` | OAuth 2.1 + PKCE, short-lived JWTs, JWKS validation |
| 3 | Authorization & Policy | `auth/policy.py`, `config/policy.yaml` | Tool-level RBAC, tenant-scoped ABAC, deny-by-default |
| 4 | Tool Registry & Discovery | `tools/registry.py` | Dynamic toolsets, `.well-known` capability metadata |
| 5 | Input Validation | `validation/schemas.py`, `validation/adversarial.py` | Strict Pydantic schemas, adversarial input as the default threat model |
| 6 | Tool Execution Engine | `tools/base.py`, `tools/{atomic,composed,workflow}/` | Three-level hierarchy: atomic / composed / workflow |
| 7 | Reliability | `reliability/` | Circuit breaker (closed→open→half-open), retry w/ jitter, ATBA |
| 8 | Rate Limiting & Quotas | `ratelimit/limiter.py` | Redis token-bucket (Lua-atomic), per-tenant + per-tool |
| 9 | Caching | `cache/l1.py`, `cache/manager.py` | Two-tier (L1 in-process, L2 Redis), stampede prevention |
| 10 | Structured Error Framework | `errors/framework.py` | Machine-readable errors with `retryable` + `hint` (SERF) |
| 11 | Observability | `observability/` | OpenTelemetry traces, Prometheus metrics, audit logs |
| 12 | Governance & Multi-Tenancy | `governance/` | Tenant isolation, approval gates, outbound HTTP allow-listing |

On top of these sits the **agent layer** (`agents/`): a four-agent support
copilot driven through the same authenticated, policy-gated tool surface.

---

## Quick start

### Prerequisites
- Docker & Docker Compose
- Python 3.11+ (to run the CLI locally)
- An Anthropic API key (for the agent layer)

### 1. Configure

```bash
cp .env.example .env
```

Set at minimum:
- `ANTHROPIC_API_KEY` — for the agent layer
- `ATLAS_AUTH_JWKS_URL` — your OAuth 2.1 provider's JWKS endpoint (or leave the default for dev)

### 2. Bring up the stack

```bash
docker compose up -d
```

| Service | URL | What it is |
|---------|-----|------------|
| MCP Server | `http://localhost:8080/mcp` | Streamable HTTP endpoint |
| Discovery | `http://localhost:8080/.well-known/mcp-server` | Unauthenticated capability metadata |
| Metrics | `http://localhost:8080/metrics` | Prometheus scrape target |
| Health | `http://localhost:8080/healthz`, `/readyz` | Liveness / readiness probes |

### 3. Run the support copilot CLI

```bash
pip install -e .

export ATLAS_MCP_URL=http://localhost:8080
export ATLAS_MCP_TOKEN=dev-token
export ATLAS_TENANT=acme
export ANTHROPIC_API_KEY=sk-ant-...

atlas-copilot "Why was the refund on order o_9002 for CUST-1001 delayed?"
```

You will see the four agents run end to end, the final draft printed with
`[S1][S2]` citations, and a trace summary including token counts, tool calls,
and the `run_id` that ties back to your tracing UI.

### 4. Connect from Claude Desktop / Cursor

```json
{
  "mcpServers": {
    "atlas-mcp": {
      "type": "http",
      "url": "http://localhost:8080/mcp",
      "headers": {
        "Authorization": "Bearer ${ATLAS_MCP_TOKEN}",
        "X-Tenant-Id": "acme"
      }
    }
  }
}
```

---

## Tech stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11+ |
| Web framework | Starlette + Uvicorn |
| MCP SDK | `mcp` |
| Auth | PyJWT / Authlib (OAuth 2.1 resource server) |
| Validation | Pydantic v2 + Pydantic Settings |
| Database | asyncpg (PostgreSQL with RLS) |
| Search | Elasticsearch (async client) |
| Vector DB | Qdrant |
| Object storage | aioboto3 (MinIO / S3) |
| Cache + rate limits | Redis |
| Tracing | OpenTelemetry SDK + OTLP exporter |
| Metrics | prometheus_client |
| Logging | structlog (JSON) |
| LLM | Anthropic Messages API (Claude) |

---

## Testing

```bash
pip install -e ".[dev]"
ruff check src tests && ruff format --check src tests
pytest -v
```

The suite covers the load-bearing safety properties end to end: policy
(default-deny, deny-beats-allow), SERF error semantics, circuit-breaker
transitions, validation, rate-limiter isolation and cache single-flight
under concurrency (`tests/test_load_concurrency.py`), and the full agent
pipeline over the live MCP stack (`tests/test_agent_server_integration.py`).

---

## Documentation

- [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md) — the 12 components and the request pipeline in depth
- [`docs/AGENT_SYSTEM.md`](./docs/AGENT_SYSTEM.md) — the four-agent orchestrator, contracts, budgets, approval flows
- [`docs/DEPLOYMENT.md`](./docs/DEPLOYMENT.md) — production deployment (Docker, Kubernetes, ECS, secrets)
- [`docs/RUNBOOK.md`](./docs/RUNBOOK.md) — deploy, rollback, key rotation, scaling, breaker drain
- [`docs/SECURITY.md`](./docs/SECURITY.md) — threat model and mitigations
- [`CONTRIBUTING.md`](./CONTRIBUTING.md) — how to add a new tool, policy rule, or agent role

---

## License

MIT.
