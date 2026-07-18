"""Structured audit log — JSONL, hashed args, tenant/tool queries."""

from __future__ import annotations

from atlas_mcp.observability.audit import AuditLogger, _hash_args


def test_record_writes_jsonl_without_raw_args(tmp_path) -> None:
    audit = AuditLogger(str(tmp_path / "audit.jsonl"))
    event = audit.record(
        trace_id="abc",
        tenant="acme",
        caller="agent:1",
        delegator="human:jane",
        tool="postgres.query",
        arguments={"sql": "SELECT ssn FROM customers"},
        duration_ms=12.5,
        status="ok",
    )
    # The sensitive SQL never appears; only its hash does.
    contents = (tmp_path / "audit.jsonl").read_text()
    assert "SELECT ssn" not in contents
    assert event["args_hash"].startswith("sha256:")
    assert event["status"] == "ok"


def test_hash_is_stable_and_order_independent() -> None:
    a = _hash_args({"x": 1, "y": 2})
    b = _hash_args({"y": 2, "x": 1})
    assert a == b


def test_query_answers_who_called_what_for_tenant(tmp_path) -> None:
    audit = AuditLogger(str(tmp_path / "audit.jsonl"))
    audit.record(
        trace_id=None, tenant="acme", caller="agent:1", delegator=None,
        tool="postgres.query", arguments={}, duration_ms=1, status="ok",
    )
    audit.record(
        trace_id=None, tenant="globex", caller="agent:2", delegator=None,
        tool="postgres.query", arguments={}, duration_ms=1, status="ok",
    )
    audit.record(
        trace_id=None, tenant="acme", caller="agent:3", delegator=None,
        tool="s3.get_object", arguments={}, duration_ms=1, status="error",
        error_code="not_found",
    )

    acme_pg = audit.query(tenant="acme", tool="postgres.query")
    assert len(acme_pg) == 1
    assert acme_pg[0]["caller"] == "agent:1"

    all_acme = audit.query(tenant="acme")
    assert {e["tool"] for e in all_acme} == {"postgres.query", "s3.get_object"}
