# Data Plane, Transports & Identity

How the MCP server connects to backends, which transports it exposes, how
identity is split between resource server and authorization server, and how
the three-level tool hierarchy maps onto the data plane.

See also: [`overview.md`](./overview.md), [`request-pipeline.md`](./request-pipeline.md).

---

## Data Plane Overview

The data plane is Acme's heterogeneous storage layer — four backends, one
MCP tool surface. Agents never connect to backends directly; every access
path goes through the request pipeline.

```
                    ┌─────────────────────┐
                    │     MCP Server      │
                    │  (tool dispatch)    │
                    └──────────┬──────────┘
           ┌───────────────────┼───────────────────┐
           │                   │                   │
           ▼                   ▼                   ▼
    ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
    │ PostgreSQL  │     │Elasticsearch│     │  S3 / MinIO │
    │  :5432      │     │  :9200      │     │  :9000      │
    └─────────────┘     └─────────────┘     └─────────────┘
           │                   │                   │
           │            ┌──────┴──────┐            │
           │            ▼             │            │
           │     ┌─────────────┐      │            │
           └────►│   Qdrant    │◄─────┘            │
                 │  (vector)   │                   │
                 │  :6333      │                   │
                 └─────────────┘                   │

    ┌─────────────┐     ┌─────────────┐
    │   Redis     │     │ OAuth IdP   │
    │  :6379      │     │ (external)  │
    └─────────────┘     └─────────────┘
    platform services   (not data plane)
```

**Redis** and the **OAuth IdP** are platform services, not data-plane stores.
Redis backs rate limits, L2 cache, sessions, and approval queues. The IdP
issues tokens the MCP server validates.

---

## Backend Catalogue

### PostgreSQL — system of record

| Aspect | Detail |
|--------|--------|
| **Client** | asyncpg |
| **Local** | `postgresql://atlas:atlas@localhost:5432/atlas` |
| **Production swap** | AWS RDS, Cloud SQL, Supabase |
| **Primary data** | Customers, orders, tickets, documents |
| **Tenant isolation** | Row-Level Security (RLS) — see below |
| **Atomic tool** | `postgres.query` |
| **Timeout** | 5 s default |
| **Guards** | SELECT-only; deny `DROP`, `TRUNCATE`, `GRANT`, `REVOKE` via policy |

**Schema (planned):**

| Table | Key columns |
|-------|-------------|
| `customers` | `id`, `tenant_id`, `name`, `email`, `tier` |
| `orders` | `id`, `tenant_id`, `customer_id`, `status`, `total_cents` |
| `documents` | `id`, `tenant_id`, `title`, `body`, `url` |

**RLS pattern:**

```sql
-- Before every query the MCP server runs:
SET LOCAL app.tenant_id = 'acme';

-- Policy on each table:
CREATE POLICY tenant_isolation ON customers
    USING (tenant_id = current_setting('app.tenant_id', true));
```

RLS is defence-in-depth: even if a policy bug allows unfiltered SQL, the
database cannot return another tenant's rows.

---

### Elasticsearch — full-text search

| Aspect | Detail |
|--------|--------|
| **Client** | elasticsearch[async] 8.x |
| **Local** | `http://localhost:9200` |
| **Production swap** | Elastic Cloud, OpenSearch |
| **Primary data** | Ticket threads, operational logs, support notes |
| **Atomic tool** | `elasticsearch.search` |
| **Timeout** | 3 s default |
| **Index pattern** | `tickets-{tenant}` — tenant prefix in index name |

Used for keyword search over unstructured text. Composed tools may combine
ES results with vector results (`hybrid_search`).

---

### S3-compatible object storage

| Aspect | Detail |
|--------|--------|
| **Client** | aioboto3 |
| **Local** | MinIO at `http://localhost:9000` (minioadmin / minioadmin) |
| **Production swap** | AWS S3, GCS, Azure Blob |
| **Primary data** | Invoices, shipping labels, ticket attachments |
| **Atomic tools** | `s3.get_object` (read), `s3.put_object` (write — approval gated) |
| **Timeout** | 3 s read / 5 s write |
| **Key convention** | `tenant/{tenant_id}/docs/*`, `tenant/{tenant_id}/tickets/*` |
| **Read ceiling** | 1 MiB default, 10 MiB max per request |

Write access is restricted to `role:human_agent` on `attachments/*` paths.
Copilot read role can access `docs/*` and `tickets/*` only.

---

### Vector store (Qdrant)

| Aspect | Detail |
|--------|--------|
| **Client** | httpx (REST API) |
| **Local** | `http://localhost:6333` |
| **Production swap** | Qdrant Cloud, Pinecone, pgvector |
| **Primary data** | Embedded help-centre articles, policy passages |
| **Atomic tools** | `vector.search`, `embeddings.encode` |
| **Timeout** | 2 s search / 10 s embed |
| **Collection** | Per-tenant collection or tenant filter in payload |

Powers semantic search for policy questions ("what is the refund window for
Gold tier?") where keyword search alone fails.

---

## Platform Services (Not Data Plane)

### Redis

| Use | Detail |
|-----|--------|
| Rate-limit buckets | Lua-atomic token bucket per `(tenant, tool)` |
| L2 cache | Shared across horizontally scaled replicas |
| Session state | Streamable HTTP stateless mode — session in Redis if needed |
| Approval queue | Pending destructive-action approvals |

Local: `redis://localhost:6379/0`

### OAuth Authorization Server (external)

The MCP server **never** stores user passwords or issues login sessions.

| Responsibility | Owner |
|----------------|-------|
| Login, MFA, consent UI | IdP (WorkOS AuthKit, Auth0, Keycloak, Descope) |
| Client registration, PKCE | IdP |
| Access token issuance | IdP |
| JWT validation (JWKS) | MCP server (`auth/oauth.py`) |
| Tool-level authorization | MCP server (`auth/policy.py`) |

---

## Identity Separation

```
┌─────────────────────────────────────────────────────────────────────┐
│                    AUTHORIZATION SERVER (IdP)                       │
│                                                                     │
│  • User login + consent                                             │
│  • OAuth 2.1 + PKCE                                                   │
│  • Issues JWT access tokens (short-lived, ~15 min)                    │
│  • Embeds: sub, tenant, act.sub, scope, jti                         │
│                                                                     │
│  Endpoints: /.well-known/openid-configuration, /oauth/token, JWKS   │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ JWT (Bearer)
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    RESOURCE SERVER (MCP Server)                     │
│                                                                     │
│  • Validates JWT signature against IdP JWKS                         │
│  • Checks issuer, audience, expiry                                  │
│  • Builds Principal { subject, delegator, tenant, scopes }          │
│  • Enforces YAML policy per tool call                                 │
│  • Never issues its own tokens                                        │
└─────────────────────────────────────────────────────────────────────┘
```

### JWT claims the MCP server requires

| Claim | Example | Purpose |
|-------|---------|---------|
| `sub` | `agent:support-copilot` | Agent / service identity |
| `act.sub` | `user:jordan@acme.com` | Human who authorised the run |
| `tenant` | `acme` | Multi-tenancy scope |
| `scope` | `tool:postgres:read tool:vector:read` | Tool permissions |
| `jti` | `uuid` | Revocation + audit correlation |

### Dev vs production identity

| Environment | Token source |
|-------------|--------------|
| Local Docker Compose | Dev JWT issuer or static `dev-token` with known claims |
| Staging / production | Real IdP — WorkOS AuthKit, Auth0, or Keycloak |

---

## Transport Design

Two transports from day one; same tool registry and pipeline for both.

### stdio — local development

| Aspect | Detail |
|--------|--------|
| **Use case** | Claude Desktop, Cursor, local CLI |
| **Entry** | `atlas-mcp` with `ATLAS_TRANSPORT=stdio` |
| **Auth** | Host-provided token or dev principal |
| **Scaling** | Single process, single user |
| **Session** | Process-bound |

### Streamable HTTP — remote deployment

| Aspect | Detail |
|--------|--------|
| **Use case** | Production, multi-user, K8s / ECS |
| **Endpoint** | `POST http://host:8080/mcp` |
| **Auth** | `Authorization: Bearer <JWT>` + `X-Tenant-Id` (optional override) |
| **Scaling** | Horizontal — stateless replicas behind load balancer |
| **Session** | Stateless mode; Redis if session data required |

### Stateless session requirement

```
  Load balancer
       │
       ├──► Replica A  ──┐
       ├──► Replica B  ──┼──► any replica handles any request
       └──► Replica C  ──┘
                │
                ▼
           Redis (shared state only)
```

No sticky sessions. Any replica can serve any MCP request. This is the
production requirement for Kubernetes rollouts and autoscaling.

### MCP host configuration (Cursor / Claude Desktop)

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

## Three-Level Tool Hierarchy

Agents see one flat tool list; internally tools are organised in three levels.

```
┌─────────────────────────────────────────────────────────────────┐
│  WORKFLOW (Level 3) — domain procedures, one agent-facing call  │
│                                                                 │
│  customer.build_context                                         │
│    ├── postgres: customer profile + recent orders               │
│    ├── elasticsearch: open tickets                              │
│    └── vector: relevant policy docs for question                │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ composes
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  COMPOSED (Level 2) — deterministic multi-atomic chains         │
│                                                                 │
│  semantic_search  = embeddings.encode → vector.search           │
│  hybrid_search    = elasticsearch.search ∥ vector.search        │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ composes
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  ATOMIC (Level 1) — one backend, one primitive                  │
│                                                                 │
│  postgres.query │ elasticsearch.search │ s3.get_object          │
│  s3.put_object  │ vector.search        │ embeddings.encode      │
│  http.fetch                                                       │
└─────────────────────────────────────────────────────────────────┘
```

### Tool catalogue

| Level | Tool name | Backend(s) | Typical use |
|-------|-----------|------------|-------------|
| Atomic | `postgres.query` | PostgreSQL | Ad-hoc read queries |
| Atomic | `elasticsearch.search` | Elasticsearch | Keyword ticket search |
| Atomic | `s3.get_object` | S3 | Read invoice / attachment |
| Atomic | `s3.put_object` | S3 | Upload attachment (approval) |
| Atomic | `vector.search` | Qdrant | Semantic doc search |
| Atomic | `embeddings.encode` | Embeddings API | Vectorise query text |
| Atomic | `http.fetch` | External HTTP | Allowlisted API calls |
| Composed | `semantic_search` | Embed + Qdrant | Policy / help-centre lookup |
| Composed | `hybrid_search` | ES + Qdrant | Best-of keyword + semantic |
| Workflow | `customer.build_context` | PG + ES + Qdrant | First call on new ticket |

### Retriever allowlist (copilot)

The support copilot retriever may call:

- `customer.build_context`
- `semantic_search`, `hybrid_search`
- `postgres.query`, `elasticsearch.search`, `vector.search` (read atomics)

Explicitly **not** on allowlist: `s3.put_object`, `http.fetch`, write tools.

Server-side policy enforces the same restrictions — belt and suspenders.

### Why three levels?

| Benefit | Explanation |
|---------|-------------|
| Fewer agent errors | One `customer.build_context` replaces 4 separate calls |
| Lower token cost | Less tool-selection reasoning per ticket |
| Atomic escape hatch | Power users and edge cases still have primitives |
| Independent timeouts | Workflow tool gets 15 s; atomics get 2–5 s each |

---

## Tenant Data Boundaries

Isolation enforced at three layers:

| Layer | Mechanism |
|-------|-----------|
| **JWT** | `tenant` claim scopes every request |
| **Policy** | Rules match `tenant:<id>/*` resources |
| **Postgres RLS** | `app.tenant_id` session variable |
| **S3 keys** | `tenant/{tenant_id}/...` prefix |
| **ES indices** | `tickets-{tenant}` naming |
| **Vector collections** | Per-tenant collection or payload filter |
| **Cache keys** | Hash includes tenant — no cross-tenant hits |

### Sample tenants (local dev seed)

| Tenant | Sample customer | Sample order |
|--------|-----------------|--------------|
| `acme` | CUST-1001 (Gold) | o_9002 (refund_pending) |
| `globex` | CUST-2001 (Gold) | o_9101 (delivered) |

Cross-tenant access attempts must return empty results or policy denial —
never another tenant's data.

---

## Outbound HTTP Allowlist

File: `config/http_allowlist.yaml`

Agents cannot fetch arbitrary URLs. Each tenant has an explicit hostname list.

```yaml
acme:
  - api.stripe.com
  - api.sendgrid.com
  - "*.acme.internal"
```

Prompt injection that convinces the retriever to call `http://evil.com` hits
policy denial before any network request leaves the server.

---

## Local vs Production Mapping

| Concern | Local (Docker Compose) | Production |
|---------|------------------------|------------|
| MCP endpoint | `http://localhost:8080/mcp` | HTTPS behind ingress / CDN |
| Postgres | Container `:5432` | RDS / Cloud SQL |
| Elasticsearch | Container `:9200` | Elastic Cloud / OpenSearch |
| Object storage | MinIO `:9000` | AWS S3 |
| Vector DB | Qdrant `:6333` | Qdrant Cloud / managed |
| Redis | Container `:6379` | ElastiCache / Upstash |
| Tracing | OTel → Jaeger `:16686` | Datadog / Honeycomb / Grafana Cloud |
| Metrics | Prometheus `:9090` | Managed Prometheus / Grafana Cloud |
| Auth | Dev JWT / static token | WorkOS / Auth0 / Keycloak |
| Audit log | `/var/log/atlas/audit.jsonl` | Splunk / SIEM |

---

## Connection Configuration (planned)

Centralised in `src/atlas_mcp/config.py` via Pydantic Settings:

| Setting | Env var | Default |
|---------|---------|---------|
| Postgres DSN | `ATLAS_POSTGRES_DSN` | `postgresql://atlas:atlas@localhost:5432/atlas` |
| Elasticsearch URL | `ATLAS_ELASTICSEARCH_URL` | `http://localhost:9200` |
| S3 endpoint | `ATLAS_S3_ENDPOINT` | `http://localhost:9000` (MinIO) |
| S3 bucket | `ATLAS_S3_BUCKET` | `atlas-mcp-data` |
| Vector DB URL | `ATLAS_VECTOR_DB_URL` | `http://localhost:6333` |
| Redis URL | `ATLAS_REDIS_URL` | `redis://localhost:6379/0` |
| JWKS URL | `ATLAS_AUTH_JWKS_URL` | IdP endpoint |
| Transport | `ATLAS_TRANSPORT` | `http` |

No component reads `os.environ` directly — all settings flow through
`get_settings()`.

---

## Review Checklist

| Item | Status |
|------|--------|
| Four data-plane backends chosen and scoped | Done |
| Redis + IdP distinguished from data plane | Done |
| stdio + stateless Streamable HTTP defined | Done |
| Resource server vs authorization server separation | Done |
| Three-level tool hierarchy with catalogue | Done |
| RLS + multi-layer tenant isolation | Done |
| Local → production swap table | Done |
| Outbound HTTP allowlist pattern | Done |

Next: [`tech-stack.md`](./tech-stack.md) — locked technology versions and
dependency graph.
