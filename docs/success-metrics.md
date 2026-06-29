# Success Metrics

Measurable outcomes for the Acme Commerce support copilot and its MCP
infrastructure layer. These targets guide design decisions in later milestones
and define when the system is ready for staging.

Related context: [`problem-statement.md`](./problem-statement.md),
[`user-personas.md`](./user-personas.md).

---

## Metric Categories

| Category | What it answers |
|----------|-----------------|
| **Agent productivity** | Are human agents faster with grounded drafts? |
| **Grounding & safety** | Does the copilot avoid hallucinations and unapproved commitments? |
| **Copilot run budgets** | Is each answer bounded in time, tokens, and tool calls? |
| **MCP server SLOs** | Is the tool surface reliable under load? |
| **Security & governance** | Are auth, tenancy, and approval gates working? |
| **Cost control** | Is spend per ticket predictable and capped? |

---

## 1. Agent Productivity

These metrics matter to Jordan and Sam (support agents) and Morgan (team lead).

| Metric | Target | Measurement |
|--------|--------|-------------|
| Lookup time per routine ticket | **≤ 2 minutes** (down from ~8–12 min manual) | Time from question submit to draft displayed |
| Draft send rate | **≥ 70%** of copilot drafts sent with ≤ 3 edits | Agent feedback / edit-distance tagging |
| Escalation context completeness | **≥ 90%** of escalations include customer id + order refs | Audit of escalation notes pre/post launch |
| Handle time (Tier-1 median) | **≥ 25% reduction** vs baseline after 30 days | CS platform timestamps |
| Agent adoption | **≥ 80%** of Tier-1 agents use copilot daily by week 4 | Session logs |

**Definition of done for productivity:** A Tier-1 agent can paste a standard
refund-status question, receive a cited draft, verify sources, and send within
five minutes end-to-end on a warm cache path.

---

## 2. Grounding & Safety

These metrics protect Casey (end customer) and reduce rework for Morgan.

| Metric | Target | Measurement |
|--------|--------|-------------|
| Unsupported factual claims in sent replies | **0 tolerated** in audited sample | Post-send QA review (weekly sample n ≥ 50) |
| Citation coverage | **100%** of factual claims in approved drafts have `[Sn]` anchors | Automated citation parser on `CopilotResponse` |
| Refund/exception promises without approval | **0 tolerated** | Critic blocks + approval middleware audit |
| Critic approval rate (first pass) | **≥ 75%** on production-like eval set | `critic.approved` on golden questions |
| Hallucinated policy numbers (price, date, SLA) | **0 tolerated** | Critic flags + human eval on policy-heavy tickets |

**Grounding rule:** The synthesizer may only state facts present in retriever
findings. The critic rejects drafts that introduce order amounts, refund dates,
or policy thresholds not found in the retrieval trace.

**Approval gate rule:** Any draft that commits to a refund, discount, or policy
exception must surface `approved=False` or route through the governance
approval queue before a human sends it to the customer.

---

## 3. Copilot Run Budgets

Every `SupportCopilot.answer()` run must stay within explicit ceilings so cost
and latency remain predictable.

| Budget | Ceiling | Rationale |
|--------|---------|-----------|
| End-to-end copilot latency (p95) | **≤ 45 seconds** | Above ~90s agents abandon the tool (orchestrator design note) |
| End-to-end copilot latency (p50) | **≤ 20 seconds** | Feels interactive at the ticket console |
| Retriever tool-call iterations | **≤ 6** (hard cap) | Prevents confused LLM loops |
| Tool calls per run (total) | **≤ 8** | Planner (0) + retriever (≤6) + overhead |
| LLM tokens in per run | **≤ 24,000** | Cost ceiling for Claude-class models |
| LLM tokens out per run | **≤ 4,000** | Draft + critic + one revise pass |
| Revise loops | **≤ 1** | Second synthesizer pass only; no infinite critique |
| Draft length | **≤ 180 words** (synthesizer prompt); critic rejects **> 200** | Keeps replies concise for agents |

**Short-circuit paths** (missing customer id, zero findings) must complete in
**≤ 5 seconds** without calling MCP tools.

**Retrieval budget flag:** When `exceeded_budget=True`, the run still returns
findings gathered so far — it does not hang or retry indefinitely.

---

## 4. MCP Server SLOs

These targets apply to the tool surface Riley operates in production.

| Metric | Target | Notes |
|--------|--------|-------|
| Tool call availability | **99.5%** monthly | Excludes planned maintenance |
| Tool call latency p50 | **≤ 300 ms** | Cache hit path |
| Tool call latency p95 | **≤ 2,000 ms** | Cache miss, all backends healthy |
| Tool call latency p99 | **≤ 8,000 ms** | Includes composed/workflow tools |
| Error rate (5xx / structured upstream errors) | **≤ 1%** of calls | Excludes policy denials (4xx-class) |
| Cache hit ratio (steady state) | **≥ 40%** for read-heavy tools | Measured per tool via Prometheus |
| Circuit breaker trip recovery | **≤ 30 s** half-open probe | Matches `circuit_breaker_recovery_seconds` |
| Stateless replica interchangeability | **100%** of requests routable to any replica | No sticky-session requirement |

### Per-tool timeout budgets (ATBA-aligned)

| Tool class | Default timeout |
|------------|-----------------|
| Atomic reads (Postgres, ES, vector, S3) | 2–5 s each |
| Composed search | 8–10 s |
| Workflow `customer.build_context` | 15 s |
| Single agent request total (ATBA) | **30 s** server-side budget |

---

## 5. Security & Governance

| Metric | Target | Measurement |
|--------|--------|-------------|
| Unauthenticated tool calls accepted | **0** | Auth middleware rejection count |
| Cross-tenant data access | **0 incidents** | RLS audits + policy deny logs |
| Policy-denied calls consuming rate-limit quota | **0** | Ordering: policy before rate limit |
| PII column reads by copilot role | **0** | Policy rule `no-pii-columns-for-support-agents` |
| Destructive SQL attempts | **0 successful** | Deny rule `no-one-can-drop-tables` |
| Outbound HTTP to non-allowlisted hosts | **0** | SSRF allowlist enforcement |
| Audit log completeness | **100%** of tool calls logged with tenant, actor, `act.sub`, outcome | JSONL audit pipeline |
| Trace joinability | **100%** of copilot `run_id`s join to OTel `trace_id` | Spot-check in Jaeger |

### Rate limiting defaults

| Parameter | Value |
|-----------|-------|
| Default requests per minute (per tenant + tool) | 60 RPM |
| Burst capacity | 20 tokens |
| Denied-by-policy calls | Do not decrement bucket |

---

## 6. Cost Control

| Metric | Ceiling | Notes |
|--------|---------|-------|
| LLM cost per copilot run (p95) | **≤ $0.15** | Model-dependent; track via token counts |
| LLM cost per ticket (median) | **≤ $0.08** | Assumes one copilot run per ticket |
| Monthly MCP infra per 1k tickets | **≤ $50** | Redis, compute, observability overhead |
| Max concurrent copilot runs per tenant | **20** | Protects shared backends during spikes |

**Cost visibility:** Every `CopilotResponse.to_dict()` must include
`tokens_in`, `tokens_out`, and `tool_calls` so Morgan can attribute spend to
teams and tenants without a separate billing integration in v1.

---

## 7. Observability Requirements

Metrics are not optional — they are part of the definition of success.

| Signal | Required instrumentation |
|--------|--------------------------|
| Latency histogram | Per-tool `atlas_mcp_latency_seconds` |
| Call counter | `atlas_mcp_calls_total{tool, status}` |
| Cache hits | `atlas_mcp_cache_hit_total{tool}` |
| Breaker state | Per-backend open/half-open/closed gauge |
| Audit trail | Immutable JSONL: who, what, when, tenant, outcome |
| Distributed trace | OTel span per middleware layer; trace starts first |

**SLO review cadence:** Weekly for error rate and latency; monthly for cost
and adoption metrics.

---

## 8. Launch Readiness Checklist

The idea milestone is complete when all of the following are documented and
agreed — not merely aspirational:

- [ ] Productivity targets with baseline comparison method
- [ ] Grounding and approval-gate rules with zero-tolerance items called out
- [ ] Copilot run budgets (latency, tokens, tool calls, revise loops)
- [ ] MCP server SLOs with p50/p95/p99 latency tiers
- [ ] Security metrics tied to policy rules and audit completeness
- [ ] Cost ceilings per run and per ticket
- [ ] Observability signals mapped to Prometheus / OTel / audit log

---

## 9. Example Golden-Path Acceptance Scenario

**Input (agent question):**

> Why was the refund on order o_9002 for CUST-1001 delayed?

**Expected outcomes:**

| Check | Pass criterion |
|-------|----------------|
| Latency | Full run completes in ≤ 45s (p95 budget) |
| Tool calls | `customer.build_context` or equivalent; ≤ 6 retriever iterations |
| Draft | Mentions order o_9002 with cited facts only |
| Citations | At least one `[S1]` anchor linked to retrieval finding |
| Approval | No unconditional refund promise if policy requires escalation |
| Audit | `run_id` present; joins to server trace |
| Cost | `tokens_in + tokens_out` within per-run ceilings |

This scenario becomes a regression test in later milestones; the numbers here
are the acceptance thresholds it must meet.
