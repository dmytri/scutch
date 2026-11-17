"""Lifecycle, idempotency, redaction and crash-recovery tests.

These deliberately use the subprocess CLI: the acceptance test is about what a
fresh agent sees on stdout, not what an internal function returns.
"""

from __future__ import annotations

import json

REPAIR = ["run", "saleor.customer.repair", "--format", "json"]


def test_envelopes_never_contain_credentials(cli):
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


def test_missing_input_is_recoverable_from_envelope_alone(repair_world):
    cli = repair_world
    env, code, _ = cli.scutch(*REPAIR, "--input", "@-", stdin={"permission": "read_orders"})
    assert env["status"] == "needs_input"
    assert env["error"]["code"] == "missing_input"
    assert env["error"]["details"]["missing"] == ["customer_id"]
    assert env["next"]["argv"][:3] == ["scutch", "run", "saleor.customer.repair"]
    # Filling the template with the returned canonical inputs + the missing
    # field produces a valid transition: this customer needs the grant, so the
    # next state is needs_approval with a plan and a digest.
    follow = dict(env["next"]["input"], customer_id="cust_456")
    env2, _, _ = cli.run(env["next"]["argv"], stdin=follow)
    assert env2["status"] == "needs_approval"
    assert env2["plan"]["digest"]
    assert env2["next"]["input"]["customer_id"] == "cust_456"


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


def test_apply_then_repeat_never_mutates_twice(repair_world):
    cli = repair_world
    payload = {"customer_id": "cust_456", "permission": "read_orders"}
    plan, _, _ = cli.scutch(*REPAIR, "--input", "@-", "--idempotency-key", "k-fixed", stdin=payload)
    digest = plan["plan"]["digest"]
    first, _, _ = cli.scutch(*REPAIR, "--input", "@-", "--idempotency-key", "k-fixed", "--approve-plan", digest, stdin=payload)
    assert first["status"] == "complete"
    assert first["result"]["status"] == "applied"

    # Exactly one mutation reached the wire, and the projector reported the
    # state the API returned after it.
    wire = cli.drain_wire()
    assert len([w for w in wire if w["kind"] == "mutation"]) == 1
    assert first["result"]["channel_permissions"] == [
        {"channel": {"slug": "retail"}, "permissions": ["read_orders"]}
    ]

    second, _, _ = cli.scutch(*REPAIR, "--input", "@-", "--idempotency-key", "k-fixed", "--approve-plan", digest, stdin=payload)
    assert second["status"] == "complete"
    assert any(n.startswith("replayed_from=") for n in second["notes"])
    assert [w for w in cli.drain_wire() if w["kind"] == "mutation"] == []


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

    # `status` carries the recovery hint; there is no separate recover command.
    hinted = status["next"]["recommended"]
    assert ["scutch", "status", rec.job_id] in hinted or hinted[0][:2] == ["scutch", "status"]


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


def test_doctor_agrees_with_validate(cli):
    """One soundness command: doctor reports contracts and specs together."""
    report, code, _ = cli.scutch("doctor", "--format", "json")
    assert report["ok"]
    assert code == 0
    names = {c["check"] for c in report["checks"]}
    assert {"contracts", "specs_present"} <= names


def test_status_of_a_pending_plan_returns_the_approval_command(cli):
    """A preview's job id must not be a dead end."""
    preview, _, _ = cli.scutch(*REPAIR, "--input", "@-", stdin={"customer_id": "cust_456", "permission": "read_orders"})
    status, _, _ = cli.scutch("status", preview["job_id"], "--format", "json")
    assert status["status"] == "needs_approval"
    assert status["phase"] == "planned"
    assert "--approve-plan" in status["next"]["argv"]
    assert preview["plan"]["digest"] in status["next"]["argv"]
    assert status["next"]["input"] == preview["next"]["input"]


def test_applying_a_plan_supersedes_its_preview(cli):
    payload = {"customer_id": "cust_456", "permission": "read_orders"}
    preview, _, _ = cli.scutch(*REPAIR, "--input", "@-", stdin=payload)
    applied, _, _ = cli.scutch(
        *REPAIR, "--input", "@-", "--idempotency-key", "sup-1",
        "--approve-plan", preview["plan"]["digest"], stdin=payload,
    )
    assert applied["result"]["status"] == "applied"
    assert any(n.startswith("superseded_previews=") for n in applied["notes"])

    # The job names the invocation, so `status <job>` now reports the applied
    # run — the consequential state, which is what a caller means. The preview
    # is addressed by its own run id.
    job, _, _ = cli.scutch("status", preview["job_id"], "--format", "json")
    assert job["phase"] == "applied"
    assert job["run_id"] == applied["run_id"]

    old, _, _ = cli.scutch("status", preview["run_id"], "--format", "json")
    assert old["phase"] == "superseded"
    assert old["superseded_by"] == applied["run_id"]
    assert old["next"]["argv"][:2] == ["scutch", "status"]
    assert old["next"]["argv"][2] == applied["run_id"]


def test_cancel_kills_a_pending_plan_digest(cli):
    """Cancellation must be a real transition, not a discarded envelope."""
    payload = {"customer_id": "cust_456", "permission": "read_orders"}
    preview, _, _ = cli.scutch(*REPAIR, "--input", "@-", stdin=payload)
    assert preview["status"] == "needs_approval"
    digest = preview["plan"]["digest"]

    # status offers the cancel command for a pending plan
    pending, _, _ = cli.scutch("status", preview["job_id"], "--format", "json")
    assert pending["next"]["cancel"][:3] == ["scutch", "cancel", preview["job_id"]]

    cancelled, code, _ = cli.scutch("cancel", preview["job_id"], "--format", "json")
    assert cancelled["status"] == "cancelled"
    assert code == 0

    # the digest is now dead — and nothing was applied
    stale, code2, _ = cli.scutch(*REPAIR, "--input", "@-", "--approve-plan", digest, stdin=payload)
    assert stale["status"] == "cancelled"
    assert stale["error"]["code"] == "plan_cancelled"
    assert code2 == 10

    runs = [json.loads(p.read_text()) for p in (cli.workspace / ".scutch" / "runs").glob("run_*.json")]
    assert not [r for r in runs if r["phase"] == "applied"]

    after, _, _ = cli.scutch("status", preview["job_id"], "--format", "json")
    assert after["phase"] == "cancelled"


def test_cancel_refuses_an_applied_plan(cli):
    payload = {"customer_id": "cust_456", "permission": "read_orders"}
    preview, _, _ = cli.scutch(*REPAIR, "--input", "@-", stdin=payload)
    applied, _, _ = cli.scutch(
        *REPAIR, "--input", "@-", "--idempotency-key", "c1",
        "--approve-plan", preview["plan"]["digest"], stdin=payload,
    )
    assert applied["result"]["status"] == "applied"

    refused, code, _ = cli.scutch("cancel", applied["run_id"], "--format", "json")
    assert refused["status"] == "complete"
    assert refused["error"]["code"] == "already_applied"
    assert code == 11


def test_cancel_requires_a_pending_plan(cli):
    read, _, _ = cli.scutch("run", "saleor.customer.list", "--input", "@-", "--format", "json", stdin={})
    refused, code, _ = cli.scutch("cancel", read["job_id"], "--format", "json")
    assert refused["error"]["code"] == "not_cancellable"
    assert code == 12


def test_unused_provider_does_not_break_other_operations(cli):
    """A provider with no recording must not fail operations that don't use it.

    Found in saleor-bi: declaring a replay provider before recording anything
    made every operation fail, because resolution was eager.
    """
    cfg = cli.workspace / "scutch.toml"
    cfg.write_text(cfg.read_text().replace(
        '[policy]', '[providers.unrecorded]\nmode = "replay"\ndata = "fixtures/does-not-exist"\n\n[policy]'
    ))
    cli.recording = "scenario-list"
    env, _, _ = cli.scutch(
        "run", "saleor.customer.list", "--input", "@-", "--format", "json", stdin={"limit": 20}
    )
    assert env["status"] == "complete", env.get("error")


def test_operation_that_needs_a_broken_provider_still_fails(cli):
    """Lazy resolution must not become no resolution: the provider the
    operation declares is still validated, and its failure is the operation's."""
    cfg = cli.workspace / "scutch.toml"
    cfg.write_text(
        cfg.read_text().replace(
            'data = "fixtures/saleor"', 'data = "fixtures/does-not-exist"'
        )
    )
    env, code, _ = cli.scutch(
        "run", "saleor.customer.list", "--input", "@-", "--format", "json", stdin={"limit": 1}
    )
    assert env["status"] == "failed", env
    assert env["error"]["code"] == "provider_error"
    assert "does not exist" in env["error"]["message"]
    assert code == 6
