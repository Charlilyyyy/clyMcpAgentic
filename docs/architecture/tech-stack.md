# Technology Stack & Dependency Graph

Locked technology choices for the Acme Commerce MCP server and support
copilot. Versions listed here are the implementation target — changes require
an architecture review.

See also: [`overview.md`](./overview.md), [`data-plane.md`](./data-plane.md),
[`components.md`](./components.md).

---

## Stack Summary

| Layer | Technology | Version |
|-------|-----------|---------|
| **Language** | Python | 3.11+ |
| **Package manager** | pip + hatchling | — |
| **MCP protocol** | MCP SDK | ≥ 1.2.0 |
| **Web framework** | Starlette + Uvicorn | ≥ 0.37 / ≥ 0.30 |
| **Validation** | Pydantic v2 + Pydantic Settings | ≥ 2.7 / ≥ 2.3 |
| **Auth** | PyJWT + Authlib | ≥ 2.9 / ≥ 1.3 |
| **Database** | asyncpg → PostgreSQL 16 | ≥ 0.29 |
| **Search** | elasticsearch[async] 8 | ≥ 8.14 |
| **Object storage** | aioboto3 (S3 API) | ≥ 13.0 |
| **Vector DB** | Qdrant (HTTP client via httpx) | 1.11+ |
| **Cache / rate limit** | Redis 7 + hiredis | ≥ 5.0 |
| **Reliability** | tenacity + custom breaker/ATBA | ≥ 8.5 |
| **Tracing** | OpenTelemetry SDK + OTLP | ≥ 1.25 |
| **Metrics** | prometheus_client | ≥ 0.20 |
| **Logging** | structlog (JSON) | ≥ 24.2 |
| **Config** | PyYAML | ≥ 6.0 |
| **HTTP client** | httpx | ≥ 0.27 |
| **LLM (agent layer)** | Anthropic Messages API (Claude) | — |

---

## Python Dependencies (`pyproject.toml`)

### Runtime

```toml
[project]
name = "atlas-mcp"
requires-python = ">=3.11"

dependencies = [
  "mcp>=1.2.0",
  "starlette>=0.37",
  "uvicorn[standard]>=0.30",
  "httpx>=0.27",
  "pydantic>=2.7",
  "pydantic-settings>=2.3",
  "pyyaml>=6.0",
  "authlib>=1.3",
  "pyjwt[crypto]>=2.9",
  "asyncpg>=0.29",
  "redis[hiredis]>=5.0",
  "elasticsearch[async]>=8.14",
  "boto3>=1.34",
  "aioboto3>=13.0",
  "tenacity>=8.5",
  "opentelemetry-api>=1.25",
  "opentelemetry-sdk>=1.25",
  "opentelemetry-exporter-otlp>=1.25",
  "opentelemetry-instrumentation-starlette>=0.46b0",
  "prometheus-client>=0.20",
  "structlog>=24.2",
]
```

### Development

```toml
[project.optional-dependencies]
dev = [
  "pytest>=8.2",
  "pytest-asyncio>=0.23",
  "pytest-cov>=5.0",
  "ruff>=0.5",
  "mypy>=1.10",
]
```

### CLI entry points

| Command | Module | Purpose |
|---------|--------|---------|
| `atlas-mcp` | `atlas_mcp.server:main` | MCP server (stdio or HTTP) |
| `atlas-copilot` | `atlas_mcp.agents.cli:main` | Support copilot CLI |

---

## Infrastructure Versions (Local Dev)

Docker Compose stack for local development:

| Service | Image | Port |
|---------|-------|------|
| MCP server | `atlas-mcp:dev` (built) | 8080 |
| PostgreSQL | `postgres:16-alpine` | 5432 |
| Redis | `redis:7-alpine` | 6379 |
| Elasticsearch | `elasticsearch:8.14.0` | 9200 |
| Qdrant | `qdrant/qdrant:v1.11.0` | 6333 |
| MinIO | `minio/minio:RELEASE.2024-08-17` | 9000 / 9001 |
| OTel Collector | `otel/opentelemetry-collector-contrib:0.103.0` | 4317 |
| Jaeger | (via OTel collector) | 16686 |
| Prometheus | `prom/prometheus:v2.53.0` | 9090 |
| Grafana | `grafana/grafana:11.1.0` | 3000 |

---

## Component → Technology Map

| Component | Primary libraries | External service |
|-----------|-------------------|------------------|
| 1 Transport | `mcp`, `starlette`, `uvicorn` | — |
| 2 Authentication | `pyjwt`, `authlib`, `httpx` (JWKS fetch) | OAuth IdP |
| 3 Authorization | `pyyaml` | `config/policy.yaml` |
| 4 Tool registry | `mcp` SDK types | — |
| 5 Validation | `pydantic` | — |
| 6 Tool execution | `asyncpg`, `elasticsearch`, `aioboto3`, `httpx` | PG, ES, S3, Qdrant |
| 7 Reliability | `tenacity` + custom Python | — |
| 8 Rate limiting | `redis[hiredis]` + Lua script | Redis |
| 9 Caching | `redis[hiredis]` + in-process LRU | Redis |
| 10 Errors | `mcp` error types + custom SERF | — |
| 11 Observability | `opentelemetry-*`, `prometheus_client`, `structlog` | OTel collector, Prometheus |
| 12 Governance | `redis`, `asyncpg` (RLS) | Redis, PostgreSQL |
| Agent copilot | `httpx` (MCP client), Anthropic API | MCP server, Claude |

---

## Dependency Graph

### Layer dependencies (build order)

```
                    ┌─────────────────┐
                    │  config.py      │
                    │  (pydantic-     │
                    │   settings)     │
                    └────────┬────────┘
                             │
         ┌───────────────────┼───────────────────┐
         ▼                   ▼                   ▼
  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
  │ errors/     │    │ validation/ │    │ observability│
  └──────┬──────┘    └──────┬──────┘    └──────┬──────┘
         │                  │                   │
         └────────┬─────────┴─────────┬─────────┘
                  ▼                   ▼
           ┌─────────────┐    ┌─────────────┐
           │ auth/       │    │ reliability/│
           └──────┬──────┘    └──────┬──────┘
                  │                   │
                  ▼                   ▼
           ┌─────────────┐    ┌─────────────┐
           │ governance/ │    │ ratelimit/  │
           └──────┬──────┘    │ cache/      │
                  │           └──────┬──────┘
                  └────────┬─────────┘
                           ▼
                    ┌─────────────┐
                    │ tools/      │
                    │ (registry + │
                    │  atomic/    │
                    │  composed/  │
                    │  workflow/) │
                    └──────┬──────┘
                           ▼
                    ┌─────────────┐
                    │ server.py   │
                    └──────┬──────┘
                           ▼
                    ┌─────────────┐
                    │ agents/     │  (depends on server via MCP client)
                    └─────────────┘
```

### Runtime request dependencies

```
Transport
    └── requires: Auth, Tenant (middleware)
            └── call_tool
                    └── requires: Registry, Validation, Policy
                            └── requires: RateLimiter (Redis)
                                    └── requires: Cache (Redis)
                                            └── requires: CircuitBreaker
                                                    └── requires: Tool backends
                                                            ├── asyncpg → PostgreSQL
                                                            ├── elasticsearch → ES
                                                            ├── aioboto3 → S3
                                                            └── httpx → Qdrant / HTTP
```

### External service dependencies

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  MCP Server  │────►│  PostgreSQL  │     │  OAuth IdP   │
│              │────►│  Redis       │     │  (JWKS)      │
│              │────►│  Elasticsearch│    └──────────────┘
│              │────►│  S3 / MinIO  │
│              │────►│  Qdrant      │
│              │────►│  OTel Coll.  │
└──────┬───────┘     └──────────────┘
       │
       │ MCP JSON-RPC
       ▼
┌──────────────┐     ┌──────────────┐
│ Agent        │────►│ Anthropic    │
│ Copilot      │     │ Claude API   │
└──────────────┘     └──────────────┘
```

**Startup order (Docker Compose):** Postgres, Redis, Elasticsearch healthy →
Qdrant, MinIO, OTel started → MCP server last.

---

## Technology Decisions & Rationale

| Choice | Why | Alternatives rejected |
|--------|-----|----------------------|
| **Python 3.11+** | MCP SDK, async/await, team familiarity | Node (weaker MCP ecosystem), Go (slower agent iteration) |
| **Starlette** | Lightweight ASGI, MCP HTTP transport fits naturally | FastAPI (extra ceremony for MCP-only server) |
| **Pydantic v2** | Validation is a core component; v2 is fast | Manual dict checks, marshmallow |
| **asyncpg** | Best async Postgres driver for Python | psycopg3 (viable; asyncpg chosen for maturity) |
| **Redis** | Shared state for rate limit + cache across replicas | In-process only (breaks horizontal scale) |
| **OpenTelemetry** | Vendor-neutral tracing; industry standard | Custom trace IDs only |
| **Prometheus** | Pull-based metrics; Grafana ecosystem | StatsD (less structured) |
| **tenacity** | Battle-tested retry; we wrap with ATBA budget | Hand-rolled retry loops |
| **Custom circuit breaker** | Per-tool state; simple 3-state machine | Hystrix (Java), resilience4j (not Python) |
| **YAML policy** | Human-readable; auditable diffs | OPA/Rego (powerful but steep learning curve) |
| **Qdrant** | Simple HTTP API; good local Docker story | Pinecone (hosted-only), pgvector (fewer features) |
| **Anthropic Claude** | Strong tool-use and instruction following | OpenAI (viable swap), local models (quality gap) |

---

## Dev Tooling

| Tool | Purpose | Config |
|------|---------|--------|
| **ruff** | Lint + format | `line-length = 100`, `target-version = py311` |
| **mypy** | Static type checking | Strict on public APIs |
| **pytest** | Unit + integration tests | `asyncio_mode = auto` |
| **pytest-cov** | Coverage reporting | Focus on policy, errors, breaker |
| **hatchling** | Build backend | `src/atlas_mcp` wheel layout |

### Test scope (planned)

| Test file | Property verified |
|-----------|-------------------|
| `test_policy.py` | Deny-by-default, deny-beats-allow, glob matching |
| `test_errors.py` | SERF wire format, retryable flag |
| `test_circuit_breaker.py` | closed → open → half-open transitions |

---

## Repository Layout (Implementation Target)

```
.
├── pyproject.toml
├── docker-compose.yml
├── Dockerfile
├── .env.example
├── config/
│   ├── policy.yaml
│   └── http_allowlist.yaml
├── deploy/
│   ├── sql/init.sql
│   ├── otel/config.yaml
│   └── prometheus/prometheus.yml
├── docs/
│   ├── problem-statement.md
│   ├── user-personas.md
│   ├── success-metrics.md
│   ├── non-goals.md
│   ├── use-case.md
│   └── architecture/
│       ├── overview.md
│       ├── components.md
│       ├── request-pipeline.md
│       ├── data-plane.md
│       └── tech-stack.md          ← this file
├── src/atlas_mcp/
│   └── ...                        (per components.md)
└── tests/
```

---

## Environment Variables (Key)

| Variable | Component | Example |
|----------|-----------|---------|
| `ATLAS_TRANSPORT` | Transport | `http` or `stdio` |
| `ATLAS_HTTP_PORT` | Transport | `8080` |
| `ATLAS_STATELESS_MODE` | Transport | `true` |
| `ATLAS_AUTH_JWKS_URL` | Auth | `https://auth.example/.well-known/jwks.json` |
| `ATLAS_POSTGRES_DSN` | Data plane | `postgresql://atlas:atlas@localhost:5432/atlas` |
| `ATLAS_REDIS_URL` | Cache + rate limit | `redis://localhost:6379/0` |
| `ATLAS_ELASTICSEARCH_URL` | Data plane | `http://localhost:9200` |
| `ATLAS_S3_ENDPOINT` | Data plane | `http://localhost:9000` |
| `ATLAS_VECTOR_DB_URL` | Data plane | `http://localhost:6333` |
| `ATLAS_OTEL_ENDPOINT` | Observability | `http://localhost:4317` |
| `ANTHROPIC_API_KEY` | Agent layer | `sk-ant-...` |
| `ATLAS_MCP_URL` | Agent client | `http://localhost:8080` |
| `ATLAS_MCP_TOKEN` | Agent client | Bearer JWT |

Full list documented in `.env.example` during scaffolding.

---

## Architecture Completion Checklist

| Exit criterion | Document | Status |
|----------------|----------|--------|
| Architecture doc with request-path diagram | `request-pipeline.md` | Done |
| Twelve components mapped with ownership | `components.md` | Done |
| Middleware ordering fixed and rationale | `request-pipeline.md` | Done |
| Transports chosen (stdio + Streamable HTTP) | `data-plane.md` | Done |
| Data plane chosen (PG, ES, S3, vector) | `data-plane.md` | Done |
| Resource server vs IdP separation | `data-plane.md` | Done |
| Three-level tool hierarchy | `data-plane.md` | Done |
| Component dependency graph agreed | This file | Done |
| Tech stack locked (Python 3.11+, MCP, Redis, OTel, Prometheus) | This file | Done |

**Architecture design is complete.** Next step: project foundation and repo
scaffolding (`pyproject.toml`, `src/` layout, Docker Compose, config files).

---

## What We Deliberately Did Not Adopt

| Technology | Reason |
|------------|--------|
| LangGraph / CrewAI | Linear four-agent flow is sufficient |
| Celery / RabbitMQ | Redis covers queue needs for approvals |
| GraphQL | MCP tools are the API surface |
| Kubernetes in local dev | Docker Compose is enough for development |
| ORM (SQLAlchemy) | Raw asyncpg + RLS session vars is simpler and safer |

See [`../non-goals.md`](../non-goals.md) for full product and scope boundaries.
