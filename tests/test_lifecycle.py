"""Lifecycle, idempotency, redaction and crash-recovery tests.

These deliberately use the subprocess CLI: the acceptance test is about what a
fresh agent sees on stdout, not what an internal function returns.
"""

from __future__ import annotations

import json

REPAIR = ["run", "saleor.customer.repair", "--format", "json"]


def test_envelopes_never_contain_credentials(shim, cli):
    """Redaction: no provider secret, and nothing shaped like one, in any output."""
    env, code, err = cli.scutch(*REPAIR, "--input", "@-", stdin={"customer_id": "cust_456", "permission": "read_orders"})
    text = json.dumps(env)
    assert "SCUTCH_SALEOR_TOKEN" not in text
    assert "Bearer" not in text
    assert "secret_present\": true" not in text
    # provider handles only
    assert env["input"] == {"channel": "retail", "customer_id": "cust_456", "permission": "read_orders"}


def test_private_result_fields_are_withheld(cli):
    env, _, _ = cli.scutch(
        *REPAIR, "--input", "@-", stdin={"customer_id": "cust_123", "permission": "read_orders"}
    )
    assert env["status"] == "complete"
    assert env["result"]["status"] == "no_change"
    assert "raw_response" not in env["result"]
    assert "raw_response" in env["redacted"]


def test_missing_input_is_recoverable_from_envelope_alone(cli):
    env, code, _ = cli.scutch(*REPAIR, "--input", "@-", stdin={"permission": "read_orders"})
    assert env["status"] == "needs_input"
    assert env["error"]["code"] == "missing_input"
    assert env["error"]["details"]["missing"] == ["customer_id"]
    assert env["next"]["argv"][:3] == ["scutch", "run", "saleor.customer.repair"]
    # Filling the template with the returned canonical inputs + the missing field works.
    follow = dict(env["next"]["input"], customer_id="cust_789")
    env2, _, _ = cli.run(env["next"]["argv"], stdin=follow)
    assert env2["status"] == "complete"


def test_invalid_input_reports_violations(cli):
    env, code, _ = cli.scutch(
        *REPAIR, "--input", "@-", stdin={"customer_id": "nope", "permission": "read_orders"}
    )
    assert env["status"] == "failed"
    assert env["error"]["code"] == "invalid_input"
    assert code == 4
    assert env["error"]["details"]["violations"]


def test_plan_digest_is_stable_and_input_sensitive(cli):
    a, _, _ = cli.scutch(*REPAIR, "--input", "@-", stdin={"customer_id": "cust_456", "permission": "read_orders"})
    b, _, _ = cli.scutch(*REPAIR, "--input", "@-", stdin={"customer_id": "cust_456", "permission": "read_orders"})
    c, _, _ = cli.scutch(*REPAIR, "--input", "@-", stdin={"customer_id": "cust_456", "permission": "manage_orders"})
    assert a["plan"]["digest"] == b["plan"]["digest"]
    assert a["plan"]["digest"] != c["plan"]["digest"]


def test_approval_is_bound_to_the_digest(cli):
    plan, _, _ = cli.scutch(*REPAIR, "--input", "@-", stdin={"customer_id": "cust_456", "permission": "read_orders"})
    bad, code, _ = cli.scutch(
        *REPAIR,
        "--input",
        "@-",
        "--approve-plan",
        "sha256:" + "0" * 64,
        stdin={"customer_id": "cust_456", "permission": "read_orders"},
    )
    assert bad["status"] == "needs_approval"
    assert bad["error"]["code"] == "approval_mismatch"
    assert code == 5
    # nothing applied yet
    runs = [json.loads(p.read_text()) for p in (cli.workspace / ".scutch" / "runs").glob("run_*.json")]
    assert not [r for r in runs if r["phase"] == "applied"]


def test_apply_then_repeat_never_mutates_twice(cli):
    payload = {"customer_id": "cust_456", "permission": "read_orders"}
    plan, _, _ = cli.scutch(*REPAIR, "--input", "@-", "--idempotency-key", "k-fixed", stdin=payload)
    digest = plan["plan"]["digest"]
    first, _, _ = cli.scutch(*REPAIR, "--input", "@-", "--idempotency-key", "k-fixed", "--approve-plan", digest, stdin=payload)
    assert first["status"] == "complete"
    assert first["result"]["status"] == "applied"

    state = json.loads((cli.workspace / ".scutch/state/saleor.json").read_text())
    perms = state["customers"]["cust_456"]["channelPermissions"]
    assert perms == [{"channel": {"slug": "retail"}, "permissions": ["read_orders"]}]

    second, _, _ = cli.scutch(*REPAIR, "--input", "@-", "--idempotency-key", "k-fixed", "--approve-plan", digest, stdin=payload)
    assert second["status"] == "complete"
    assert any(n.startswith("replayed_from=") for n in second["notes"])
    state2 = json.loads((cli.workspace / ".scutch/state/saleor.json").read_text())
    assert state2["customers"]["cust_456"]["channelPermissions"] == perms


def test_idempotency_key_reuse_with_different_inputs_is_refused(cli):
    a = {"customer_id": "cust_456", "permission": "read_orders"}
    plan, _, _ = cli.scutch(*REPAIR, "--input", "@-", "--idempotency-key", "k1", stdin=a)
    cli.scutch(*REPAIR, "--input", "@-", "--idempotency-key", "k1", "--approve-plan", plan["plan"]["digest"], stdin=a)
    b = {"customer_id": "cust_456", "permission": "manage_orders"}
    conflict, code, _ = cli.scutch(*REPAIR, "--input", "@-", "--idempotency-key", "k1", stdin=b)
    assert conflict["status"] == "failed"
    assert conflict["error"]["code"] == "idempotency_conflict"
    assert code == 9


def test_crash_between_plan_and_apply_leaves_a_recoverable_record(cli):
    """Simulate a process killed mid-apply, then ask status for the next action."""
    from scutch.runs import RunRecord, RunStore, new_job_id, new_run_id
    import time

    store = RunStore(cli.workspace / ".scutch" / "runs")
    rec = RunRecord(
        run_id=new_run_id(),
        job_id=new_job_id(),
        operation="saleor.customer.repair",
        status="running",
        input={"customer_id": "cust_456", "permission": "read_orders", "channel": "retail"},
        fingerprint="deadbeef",
        idempotency="k-fixed",
        plan_digest="sha256:x",
        phase="applying",
        created_at=time.time(),
        updated_at=time.time(),
    )
    store.save(rec)

    status, _, _ = cli.scutch("status", rec.job_id, "--format", "json")
    assert status["status"] == "running"
    assert status["next"]["recommended"][0][:2] == ["scutch", "status"]
    assert status["next"]["recommended"][1][0] == "scutch"
    assert status["next"]["recommended"][1][1] == "recover"

    recover, _, _ = cli.scutch("recover", rec.run_id, "--format", "json")
    assert recover["phase"] == "applying"
    assert recover["input"]["customer_id"] == "cust_456"


def test_a_new_process_sees_identical_descriptions(cli):
    """Self-description is registry-derived, so it cannot drift between runs."""
    a, _, _ = cli.scutch("describe", "saleor.customer.repair", "--format", "json")
    b, _, _ = cli.scutch("describe", "saleor.customer.repair", "--format", "json")
    assert a == b
    assert a["input_schema"] == b["input_schema"]
    assert a["result_schema"] == b["result_schema"]


def test_json_stdout_stays_clean(cli):
    env, code, err = cli.scutch("list", "--format", "json")
    assert isinstance(env, dict) and env["kind"] == "scutch.list/v1"


def test_doctor_passes_on_a_complete_workspace(cli):
    report, _, _ = cli.scutch("doctor", "--format", "json")
    failing = [c for c in report["checks"] if not c["ok"]]
    assert report["ok"], failing


def test_validate_agrees(cli):
    report, code, _ = cli.scutch("validate", "--format", "json")
    assert report["ok"]
    assert code == 0
