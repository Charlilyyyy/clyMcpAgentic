# Contributing to Atlas-MCP

Thanks for extending the system. This guide covers the three most common
changes — **adding a tool, a policy rule, or an agent role** — plus the
local workflow every change must pass.

---

## Local workflow

```bash
pip install -e ".[dev]"

ruff check src tests          # lint
ruff format src tests         # auto-format (CI runs --check)
pytest -q                     # full suite
```

CI (`.github/workflows/ci.yml`) runs `ruff check`, `ruff format --check`, and
`pytest`. All three must be green. Keep tests narrow and property-focused —
assert the behaviour that matters (isolation, ordering, bounds), not
implementation detail.

Conventions:
- Type-annotate public APIs; keep modules single-purpose.
- Comments explain **why**, not what. No narration.
- New tools/policies/agents ship **with tests**.

---

## Add a new tool

Tools live in `src/atlas_mcp/tools/` across three levels: `atomic/` (one
backend), `composed/` (deterministic chains), `workflow/` (multi-step
fan-outs). Backends are injected via `tools/backends.py` protocols so tools
are testable with fakes; real clients live in `tools/clients/`.

1. **Define input** — a strict Pydantic model. Extend `StrictToolModel` (from
   `validation/adversarial.py`) so unknown fields are rejected; constrain
   every field (`pattern`, `min/max`, bounds).

2. **Implement the tool** — subclass `tools/base.py::Tool`, declare `meta` and
   `input_schema`, implement `async def run(self, tenant, args)`:

```python
from typing import ClassVar
from atlas_mcp.tools.base import Tool, ToolLevel, ToolMetadata
from atlas_mcp.validation.adversarial import StrictToolModel

class MyToolInput(StrictToolModel):
    query: str = Field(..., min_length=1, max_length=500)

class MyTool(Tool):
    meta: ClassVar[ToolMetadata] = ToolMetadata(
        name="my.tool",
        description="One clear sentence an agent can act on.",
        level=ToolLevel.ATOMIC,
        scopes_required=("tool:mybackend:read",),
        destructive=False,          # set True for writes/deletes/sends
        cacheable=True,
        cache_ttl_seconds=60,
    )
    input_schema: ClassVar[type[StrictToolModel]] = MyToolInput

    def __init__(self, backend: MyBackend | None = None) -> None:
        self._backend = backend      # inject a fake in tests

    async def run(self, tenant: str, args: MyToolInput) -> dict:
        # Always scope to `tenant`. Map backend errors to UpstreamError
        # with retryable + hint so agents and the breaker can react.
        ...
```

3. **Register it** — add the class to `registry.discover()` in
   `tools/registry.py`.

4. **Authorize it** — add a policy rule (below). The policy action is the tool
   name (`_policy_action` returns `tool.meta.name`); without an allow rule the
   tool is denied by default.

5. **Destructive tools** — set `destructive=True`. They are excluded from
   retries and, when `ATLAS_DESTRUCTIVE_TOOL_REQUIRES_APPROVAL=true`, routed
   through `governance/approval.py`. If agents should call it, decide whether
   it belongs in the Retriever's `_ALLOWED_TOOLS` (read-only only).

6. **Test it** — tenant scoping, error mapping, and validation. See
   `tests/test_tool_*.py` for patterns using fake backends.

---

## Add a policy rule

Rules live in `config/policy.yaml`. The engine is **deny-by-default**,
evaluated top-to-bottom, and **deny beats allow**.

```yaml
- id: analysts-read-orders          # unique, descriptive
  subjects: ["role:analyst"]         # roles/subjects, or "*"
  actions: ["my.tool"]               # the tool name (see _policy_action)
  resources: ["tenant:*/orders/*"]   # glob, tenant-scoped
  # Optional ABAC conditions:
  conditions:
    sql_contains_any: ["DROP", "TRUNCATE"]
  effect: deny                       # omit for allow (default)
```

Guidance:
- Prefer the **narrowest** subjects/resources that work.
- Use a `deny` rule for hard prohibitions (it wins over any allow).
- Add a test in `tests/test_policy.py` proving both the allow and the deny
  path — default-deny is a safety property, so cover it.

---

## Add an agent role

Agents live in `src/atlas_mcp/agents/`. Each is a thin subclass of
`base.py::Agent` with a single-purpose system prompt.

1. **Prompt** — add a system prompt to `agents/prompts.py`. Keep the role
   narrow: an agent responsible for one small job is far more reliable than a
   "be helpful" agent. Define a strict JSON output contract.

2. **Agent** — subclass `Agent`, set `name` and `system_prompt`, implement
   `async def act(self, run, **inputs)`. Use `self._complete_json(...)` for
   structured output or `self._complete_text(...)` for prose; token usage is
   accounted automatically on the shared `AgentRun`.

3. **Never trust the model for security-critical values** — extract IDs and
   enforce allow-lists in code (see the Planner's regex customer-id extraction
   and the Retriever's `_ALLOWED_TOOLS`).

4. **Wire it** into `orchestrator.py` at the right point, respecting the
   budgets (iteration cap, single revise loop, token caps).

5. **Test it** with `tests/agent_fakes.py::FakeLLM` — no network. Cover the
   contract, the happy path, and at least one guard (e.g. the agent refuses or
   flags a disallowed action). See `tests/test_agents_*.py`.

---

## Documentation stays in sync

If a change alters behaviour, update the relevant doc in the same PR:
[`ARCHITECTURE.md`](./docs/ARCHITECTURE.md),
[`AGENT_SYSTEM.md`](./docs/AGENT_SYSTEM.md),
[`DEPLOYMENT.md`](./docs/DEPLOYMENT.md),
[`RUNBOOK.md`](./docs/RUNBOOK.md), or
[`SECURITY.md`](./docs/SECURITY.md). Docs review is part of the PR checklist.
