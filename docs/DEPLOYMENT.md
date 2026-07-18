# Deployment

How to run Atlas-MCP locally, and how to take it to production. For
day-2 operations (rollback, key rotation, scaling, incident triage) see
[`RUNBOOK.md`](./RUNBOOK.md); for the threat model see
[`SECURITY.md`](./SECURITY.md).

---

## Configuration

All configuration is environment-driven and documented in
[`.env.example`](../.env.example). Settings are typed and validated in
`src/atlas_mcp/config.py`. Grouped by component:

| Group | Key variables |
|-------|---------------|
| Transport | `ATLAS_TRANSPORT`, `ATLAS_HTTP_HOST`, `ATLAS_HTTP_PORT`, `ATLAS_STATELESS_MODE` |
| Auth | `ATLAS_AUTH_ISSUER`, `ATLAS_AUTH_AUDIENCE`, `ATLAS_AUTH_JWKS_URL`, `ATLAS_AUTH_REQUIRE_PKCE` |
| Policy | `ATLAS_POLICY_DEFAULT_DENY`, `ATLAS_POLICY_FILE` |
| Reliability | `ATLAS_CIRCUIT_BREAKER_*`, `ATLAS_RETRY_MAX_ATTEMPTS`, `ATLAS_ATBA_TOTAL_BUDGET_MS` |
| Rate limit | `ATLAS_RATE_LIMIT_DEFAULT_RPM`, `ATLAS_RATE_LIMIT_BURST`, `ATLAS_REDIS_URL` |
| Cache | `ATLAS_CACHE_L1_MAX_ITEMS`, `ATLAS_CACHE_L1_TTL_SECONDS`, `ATLAS_CACHE_L2_TTL_SECONDS` |
| Observability | `ATLAS_OTEL_ENDPOINT`, `ATLAS_SERVICE_NAME`, `ATLAS_METRICS_ENABLED`, `ATLAS_AUDIT_LOG_PATH` |
| Governance | `ATLAS_TENANT_HEADER`, `ATLAS_REQUIRE_TENANT`, `ATLAS_DESTRUCTIVE_TOOL_REQUIRES_APPROVAL` |
| Data plane | `ATLAS_POSTGRES_DSN`, `ATLAS_ELASTICSEARCH_URL`, `ATLAS_S3_*`, `ATLAS_VECTOR_DB_URL` |
| Agent layer | `ATLAS_MCP_URL`, `ATLAS_MCP_TOKEN`, `ATLAS_TENANT`, `ANTHROPIC_API_KEY`, `ATLAS_EMBEDDING_*` |

**Secrets** (DSNs with passwords, API keys, dev tokens) must come from a
secret manager, never from the ConfigMap or git.

---

## Local: Docker Compose

```bash
cp .env.example .env      # then edit ANTHROPIC_API_KEY etc.
docker compose up -d
docker compose ps
```

This starts the server plus its data and observability dependencies. Sidecar
configs live in [`deploy/`](../deploy/):

- `deploy/sql/init.sql` — schema, row-level security policies, and seed data
- `deploy/prometheus/prometheus.yml` — scrape targets
- `deploy/otel/config.yaml` — OpenTelemetry Collector pipeline

Smoke test:

```bash
curl -fsS http://localhost:8080/healthz
curl -fsS http://localhost:8080/readyz
curl -fsS http://localhost:8080/.well-known/mcp-server | jq .
```

---

## Container image

Two-stage build (`Dockerfile`): a builder compiles wheels, the runtime image
is slim, runs as a **non-root** user (uid 10001), ships a `HEALTHCHECK`
against `/healthz`, and uses `tini` as PID 1.

```bash
docker build -t atlas-mcp:local .
# Publish by digest, not a mutable tag:
docker push ghcr.io/your-org/atlas-mcp@sha256:<digest>
```

---

## Kubernetes

Manifests live in [`deploy/k8s/`](../deploy/k8s/):

| File | Contains |
|------|----------|
| `configmap.yaml` | Non-secret runtime config (`envFrom`) |
| `secret.example.yaml` | Secret **template** — do not apply literally; source from a secret manager |
| `deployment.yaml` | Deployment (3 replicas, rolling `maxUnavailable: 0`), hardened `securityContext`, liveness/readiness probes, ServiceAccount |
| `service.yaml` | ClusterIP Service, HorizontalPodAutoscaler (CPU 70%, 3–20), PodDisruptionBudget (`minAvailable: 2`) |

Apply:

```bash
kubectl apply -f deploy/k8s/configmap.yaml

# Secrets — pick ONE (never commit real values):
#  a) External Secrets Operator  → kind: ExternalSecret pulling from Vault/ASM/GSM
#  b) Sealed Secrets             → kubeseal < secret.yaml > sealed-secret.yaml (commit the sealed file)
#  c) dev/staging only           → kubectl create secret generic atlas-mcp-secrets --from-env-file=.env.production

kubectl apply -f deploy/k8s/deployment.yaml
kubectl apply -f deploy/k8s/service.yaml
kubectl rollout status deployment/atlas-mcp
```

Because the server is stateless, throughput scales roughly linearly with
replicas; Redis-backed rate limits and L2 cache stay correct across the fleet.

---

## AWS ECS (Fargate)

A task definition template is at
[`deploy/ecs/task-definition.json`](../deploy/ecs/task-definition.json). It
pulls secrets from AWS Secrets Manager via the `secrets` block, references the
image by ECR digest, and health-checks `/healthz`. Prefer **IAM roles for
tasks (IRSA-equivalent)** over static `AWS_ACCESS_KEY_ID` for S3 access.

```bash
aws ecs register-task-definition --cli-input-json file://deploy/ecs/task-definition.json
aws ecs update-service --cluster atlas --service atlas-mcp --task-definition atlas-mcp
```

---

## Local dev vs production swaps

| Local (docker-compose) | Production |
|------------------------|------------|
| Dev JWT issuer | WorkOS AuthKit / Auth0 / Keycloak |
| MinIO | AWS S3 / GCS / Azure Blob |
| Local Postgres | RDS / Cloud SQL / Supabase |
| Redis container | ElastiCache / MemoryDB / Upstash |
| Local OTel collector | Datadog / Honeycomb / Grafana Cloud |
| File-based audit log | Splunk / Chronicle / your SIEM |

---

## Production checklist

- [ ] `ATLAS_POLICY_DEFAULT_DENY=true` and `ATLAS_REQUIRE_TENANT=true`
- [ ] `ATLAS_DESTRUCTIVE_TOOL_REQUIRES_APPROVAL=true`
- [ ] Real OAuth issuer + JWKS; short `ATLAS_AUTH_ACCESS_TOKEN_TTL_SECONDS`
- [ ] Image pinned by digest; non-root, read-only rootfs (already default)
- [ ] Secrets from a manager, not ConfigMap/git
- [ ] `/metrics` restricted to the monitoring network
- [ ] Redis provisioned for rate limits + L2 cache + STM
- [ ] OTel endpoint pointed at your tracing backend
- [ ] Audit log shipped to your SIEM
- [ ] HPA + PDB applied; alerts on `atlas_circuit_state` and `atlas_rate_limited_total`
- [ ] CI green: `ruff check`, `ruff format --check`, `pytest` (see `.github/workflows/ci.yml`)

---

## Monitoring

Scrape `GET /metrics`. Key series:

| Metric | Use |
|--------|-----|
| `atlas_tool_calls_total{tool,status}` | traffic + error rate |
| `atlas_tool_latency_seconds` | latency SLOs |
| `atlas_cache_*` | cache hit ratio |
| `atlas_rate_limited_total{tool}` | hot tenants/tools |
| `atlas_circuit_state{tool}` | 0 closed / 1 half-open / 2 open |

Traces and the JSONL audit log share one `trace_id`; the copilot's `run_id`
joins agent logs to server traces.
