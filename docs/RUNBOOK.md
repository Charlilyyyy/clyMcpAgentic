# Atlas-MCP Operations Runbook

Operational procedures for the on-call engineer. Every command assumes
`kubectl` is pointed at the production cluster and namespace
(`kubectl config set-context --current --namespace=atlas`).

The server is **stateless** (`ATLAS_STATELESS_MODE=true`): shared state
(rate-limit buckets, L2 cache) lives in Redis. Pods can be killed, scaled,
and rolled at will without data loss.

---

## 1. Deploy a new version

```bash
# Always deploy by immutable digest, never :latest.
kubectl set image deployment/atlas-mcp \
  atlas-mcp=ghcr.io/your-org/atlas-mcp@sha256:<digest>

kubectl rollout status deployment/atlas-mcp --timeout=120s
```

Post-deploy smoke test (should all return 200 / valid JSON):

```bash
kubectl run smoke --rm -it --image=curlimages/curl --restart=Never -- \
  sh -c 'curl -fsS http://atlas-mcp/healthz && curl -fsS http://atlas-mcp/readyz'
```

Rolling strategy is `maxUnavailable: 0`, so a bad image never reduces
capacity below the current replica count — the rollout simply stalls.

## 2. Roll back

```bash
kubectl rollout undo deployment/atlas-mcp          # previous revision
kubectl rollout undo deployment/atlas-mcp --to-revision=<n>
kubectl rollout status deployment/atlas-mcp
```

Rollback is always safe: the app carries no schema migrations of its own,
and Redis state is forward/backward compatible across adjacent versions.

## 3. Rotate keys and secrets

Secrets are injected via `envFrom: secretRef`. Rotation is a two-step,
zero-downtime process:

```bash
# 1. Update the secret (via your secret manager / ExternalSecret / kubeseal).
kubectl create secret generic atlas-mcp-secrets \
  --from-env-file=.env.production --dry-run=client -o yaml | kubectl apply -f -

# 2. Restart pods so they pick up the new values.
kubectl rollout restart deployment/atlas-mcp
```

- **JWT signing keys (JWKS):** no restart needed — keys are fetched from
  `ATLAS_AUTH_JWKS_URL` and cached with TTL. Publish the new key to the
  JWKS endpoint *before* retiring the old one so in-flight tokens verify.
- **Anthropic / embedding keys:** rotate the secret, then rollout restart.
- **Postgres/S3 credentials:** create the new credential, update the
  secret, rollout restart, then revoke the old credential.

## 4. Scale replicas

```bash
# Manual override (autoscaler resumes control after the next interval):
kubectl scale deployment/atlas-mcp --replicas=8

# Inspect the autoscaler decision:
kubectl get hpa atlas-mcp
kubectl describe hpa atlas-mcp
```

Because state is external, throughput scales roughly linearly with
replicas until Redis or a downstream data store becomes the bottleneck —
watch `atlas_tool_calls_total` rate vs. Redis CPU.

## 5. Drain a stuck circuit breaker

When a downstream (Postgres, ES, S3, vector) is failing, the per-tool
circuit breaker opens and short-circuits calls with `circuit_open`
(SERF `retryable=true`, `recovery_seconds`).

Verify from metrics:

```bash
# 2 == open, 1 == half-open, 0 == closed
curl -fsS http://atlas-mcp/metrics | grep atlas_circuit_state
```

To force-clear after the downstream recovers, roll the pods (breaker state
is in-process, so a restart resets it):

```bash
kubectl rollout restart deployment/atlas-mcp
```

Do **not** raise `ATLAS_CIRCUIT_BREAKER_FAILURE_THRESHOLD` to "fix" an open
breaker — that removes the protection that is keeping the failing
downstream from being overwhelmed.

## 6. Incident triage checklist

1. `kubectl get pods -l app.kubernetes.io/name=atlas-mcp` — any CrashLoop/OOM?
2. `/readyz` failing but `/healthz` OK → a dependency (Redis/DB) is down.
3. Spike in `atlas_rate_limited_total` → a tenant is hot; check per-tenant
   quotas before widening global limits.
4. Spike in `atlas_tool_calls_total{status="error"}` → check
   `atlas_circuit_state` and downstream health.
5. Correlate with traces via the OTel `trace_id` printed in structured
   logs and the audit log (`ATLAS_AUDIT_LOG_PATH`).

## 7. Approval queue for destructive tools

Destructive tools (e.g. `s3.put`) require human approval when
`ATLAS_DESTRUCTIVE_TOOL_REQUIRES_APPROVAL=true`. If approvals appear stuck,
confirm the approval store (Redis) is reachable; pending requests surface
to callers as `PendingApprovalError` and are safe to retry after approval.
