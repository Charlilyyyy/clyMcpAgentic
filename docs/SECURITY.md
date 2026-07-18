# Atlas-MCP Security Review

A threat-model walkthrough of the attack surface and the mitigations in the
codebase. Each section names the risk, the control, and where it lives.

## Trust boundaries

```
Untrusted            Semi-trusted                 Trusted
─────────            ────────────                 ───────
LLM output   ──▶     Agent layer (agents/)  ──▶   Atlas-MCP server  ──▶  Data plane
customer text        MCP client                   auth → policy →         (Postgres, ES,
                                                   validation →            S3, Qdrant)
                                                   dispatch
```

The core assumption: **the LLM is untrusted**. Anything it emits (tool
names, arguments, SQL) is treated as adversarial input and must pass the
same server-side gates as any external caller.

## 1. AuthN / AuthZ

- **Risk:** unauthenticated or over-privileged tool calls.
- **Control:** OAuth 2.1 / JWT bearer validated against JWKS
  (`auth/`), then a deny-by-default policy engine (`auth/policy.py`,
  `config/policy.yaml`). Absence of an allow rule is a denial; deny rules
  beat allow rules.
- **Verified by:** `tests/test_oauth.py`, `tests/test_policy.py`, and
  `tests/test_agent_server_integration.py` (an agent's un-allowed tool call
  is rejected server-side even though the client allow-list permitted it —
  belt and suspenders).

## 2. Multi-tenant isolation

- **Risk:** tenant A reading tenant B's data.
- **Control:** tenant enforced in middleware (`governance/tenant.py`);
  Postgres runs `SET LOCAL app.tenant_id`; ES/vector queries inject a
  mandatory tenant filter; S3 keys are tenant-prefixed. Impersonation
  requires an explicit `tenant:*:impersonate` scope.
- **Verified by:** `tests/test_tool_postgres.py`,
  `tests/test_tool_search_storage.py`, `tests/test_tool_vector_http.py`.

## 3. SSRF (outbound HTTP)

- **Risk:** the `http.fetch` tool coerced into hitting internal metadata
  endpoints (169.254.169.254) or other tenants' hosts.
- **Control:** per-tenant outbound allow-list
  (`governance/http_allowlist.py`) enforced before any request leaves the
  process; policy also scopes `tool:http:fetch`.
- **Verified by:** `tests/test_http_allowlist.py`.

## 4. SQL injection / destructive SQL

- **Risk:** LLM- or user-crafted SQL performing writes or dropping tables.
- **Control:** the Postgres tool rejects anything that is not a single
  `SELECT`/`WITH` and forbids `INSERT/UPDATE/DELETE/DROP/TRUNCATE/…`
  (`tools/atomic/postgres.py`); a policy deny rule additionally blocks
  `DROP/TRUNCATE/GRANT/REVOKE`; parameters are always bound, never
  interpolated.
- **Verified by:** `tests/test_tool_postgres.py`.

## 5. Adversarial / malformed input

- **Risk:** oversized payloads, deeply nested JSON, unknown fields, control
  characters used to exhaust resources or smuggle data.
- **Control:** `validation/adversarial.py` bounds size/depth, strips
  control characters, and rejects unknown fields (strict Pydantic models)
  *before* policy evaluation, so cheap rejections happen first.
- **Verified by:** `tests/test_validation.py`.

## 6. Prompt injection

- **Risk:** retrieved content or customer text instructing the agent to
  call destructive tools or exfiltrate data.
- **Controls (defense in depth):**
  - The Retriever has a **client-side allow-list** of read-only tools; it
    can never name a destructive tool, and the request never reaches the
    wire (`agents/retriever.py`).
  - Even if it did, **server-side policy** would deny it.
  - The Planner does **not** call tools; customer-id extraction is done in
    code (regex), never trusted from the model (`agents/planner.py`).
  - The Critic blocks unsupported claims and unapproved refund/exception
    commitments before a draft is surfaced (`agents/critic.py`).
  - Destructive tools additionally require **human approval**
    (`governance/approval.py`).
- **Verified by:** `tests/test_agents_retriever.py`,
  `tests/test_agents_critic_memory.py`,
  `tests/test_agent_server_integration.py`, `tests/test_approval.py`.

## 7. Secrets handling

- **Risk:** credentials leaking into images, logs, or git.
- **Control:** secrets injected via env from a secret manager
  (`deploy/k8s/secret.example.yaml`, `deploy/ecs/task-definition.json`);
  audit logs **hash** tool arguments rather than storing them
  (`observability/audit.py`); the container runs non-root with a read-only
  root filesystem.

## 8. Denial of service

- **Risk:** a single tenant exhausting shared capacity.
- **Control:** per-tenant/per-tool token-bucket rate limiting
  (`ratelimit/limiter.py`), adaptive timeout budgets
  (`reliability/atba.py`), and circuit breakers to shed load from failing
  downstreams (`reliability/circuit_breaker.py`).
- **Verified by:** `tests/test_rate_limiter.py`,
  `tests/test_load_concurrency.py`, `tests/test_circuit_breaker.py`.

## Residual risks / follow-ups

- JWKS is cached with a TTL; a compromised signing key is valid until its
  token expiry — keep `ATLAS_AUTH_ACCESS_TOKEN_TTL_SECONDS` short.
- The in-process circuit-breaker state is per-pod; a fleet-wide view would
  require exporting breaker state to a shared store.
- Long-term memory writes (`agents/memory.py`) are intentionally not wired
  to a destructive MCP tool yet; do so only behind the approval gate.
