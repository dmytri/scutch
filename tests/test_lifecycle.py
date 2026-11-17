"""Lifecycle, idempotency, redaction and crash-recovery tests.

These deliberately use the subprocess CLI: the acceptance test is about what a
fresh agent sees on stdout, not what an internal function returns.
"""

from __future__ import annotations

import json

FULFIL = ["run", "orders.fulfil", "--format", "json"]


def _ledger(cli) -> dict:
    """The working ledger the provider keeps after any mutation."""
    return json.loads((cli.workspace / ".scutch" / "ledger.json").read_text())


def _status_of(cli, order_id: str) -> str:
    return next(o["status"] for o in _ledger(cli)["orders"] if o["id"] == order_id)


def _runs(cli, phase: str) -> list[dict]:
    rows = []
    for path in (cli.workspace / ".scutch" / "runs").glob("run_*.json"):
        record = json.loads(path.read_text())
        if record["phase"] == phase:
            rows.append(record)
    return rows


def test_envelopes_never_contain_credentials(cli):
    """Redaction: no provider secret, and nothing shaped like one, in any output."""
    env, code, err = cli.scutch(*FULFIL, "--input", "@-", stdin={"order_id": "ord_a1"})
    text = json.dumps(env)
    assert "SCUTCH_LEDGER_TOKEN" not in text
    assert "Bearer" not in text
    # the echoed input is the canonical input, nothing more
    assert env["input"] == {"order_id": "ord_a1"}


def test_private_result_fields_are_withheld(cli):
    """The ledger row carries keys the public result type does not declare.

    `placed_at` and `customer` are stored per order; a result that leaked the
    whole row would put them on stdout. The projection keeps only the declared
    fields, so those internals never leave the process.
    """
    env, _, _ = cli.scutch(*FULFIL, "--input", "@-", stdin={"order_id": "ord_a1"})
    assert env["status"] == "needs_approval"
    env2, _, _ = cli.scutch(
        *FULFIL,
        "--input",
        "@-",
        "--idempotency-key",
        "redact-1",
        "--approve-plan",
        env["plan"]["digest"],
        stdin={"order_id": "ord_a1"},
    )
    assert env2["status"] == "complete"
    assert set(env2["result"]) == {"order_id", "status", "previous_status"}
    assert "placed_at" not in json.dumps(env2["result"])
    assert "customer" not in json.dumps(env2["result"])


def test_missing_input_is_recoverable_from_envelope_alone(cli):
    env, code, _ = cli.scutch(*FULFIL, "--input", "@-", stdin={})
    assert env["status"] == "needs_input"
    assert env["error"]["code"] == "missing_input"
    assert env["error"]["details"]["missing"] == ["order_id"]
    assert env["next"]["argv"][:3] == ["scutch", "run", "orders.fulfil"]
    # Filling the template with the returned canonical inputs + the missing
    # field produces a valid transition: a placed order needs the change, so
    # the next state is needs_approval with a plan and a digest.
    follow = dict(env["next"]["input"], order_id="ord_a1")
    env2, _, _ = cli.run(env["next"]["argv"], stdin=follow)
    assert env2["status"] == "needs_approval"
    assert env2["plan"]["digest"]
    assert env2["next"]["input"]["order_id"] == "ord_a1"


def test_invalid_input_reports_violations(cli):
    env, code, _ = cli.scutch(*FULFIL, "--input", "@-", stdin={"order_id": "nope"})
    assert env["status"] == "failed"
    assert env["error"]["code"] == "invalid_input"
    assert code == 4
    assert env["error"]["details"]["violations"]


def test_plan_digest_is_stable_and_input_sensitive(cli):
    a, _, _ = cli.scutch(*FULFIL, "--input", "@-", stdin={"order_id": "ord_a1"})
    b, _, _ = cli.scutch(*FULFIL, "--input", "@-", stdin={"order_id": "ord_a1"})
    c, _, _ = cli.scutch(*FULFIL, "--input", "@-", stdin={"order_id": "ord_b1"})
    assert a["plan"]["digest"] == b["plan"]["digest"]
    assert a["plan"]["digest"] != c["plan"]["digest"]


def test_approval_is_bound_to_the_digest(cli):
    plan, _, _ = cli.scutch(*FULFIL, "--input", "@-", stdin={"order_id": "ord_a1"})
    bad, code, _ = cli.scutch(
        *FULFIL,
        "--input",
        "@-",
        "--approve-plan",
        "sha256:" + "0" * 64,
        stdin={"order_id": "ord_a1"},
    )
    assert bad["status"] == "needs_approval"
    assert bad["error"]["code"] == "approval_mismatch"
    assert code == 5
    # nothing applied yet
    assert _status_of(cli, "ord_a1") == "placed"
    assert not _runs(cli, "applied")


def test_apply_then_repeat_never_mutates_twice(cli):
    payload = {"order_id": "ord_a1"}
    plan, _, _ = cli.scutch(*FULFIL, "--input", "@-", "--idempotency-key", "k-fixed", stdin=payload)
    digest = plan["plan"]["digest"]
    first, _, _ = cli.scutch(
        *FULFIL, "--input", "@-", "--idempotency-key", "k-fixed", "--approve-plan", digest, stdin=payload
    )
    assert first["status"] == "complete"
    assert first["result"]["status"] == "fulfilled"
    assert first["result"]["previous_status"] == "placed"

    # Exactly one mutation reached the ledger, and the result reports the state
    # it moved to.
    assert _status_of(cli, "ord_a1") == "fulfilled"

    second, _, _ = cli.scutch(
        *FULFIL, "--input", "@-", "--idempotency-key", "k-fixed", "--approve-plan", digest, stdin=payload
    )
    assert second["status"] == "complete"
    assert any(n.startswith("replayed_from=") for n in second["notes"])
    assert second["replay"]["mutation_requested"] is False
    # still exactly one applied run: the repeat did not take effect again
    assert len(_runs(cli, "applied")) == 1


def test_idempotency_key_reuse_with_different_inputs_is_refused(cli):
    a = {"order_id": "ord_a1"}
    plan, _, _ = cli.scutch(*FULFIL, "--input", "@-", "--idempotency-key", "k1", stdin=a)
    cli.scutch(
        *FULFIL, "--input", "@-", "--idempotency-key", "k1", "--approve-plan", plan["plan"]["digest"], stdin=a
    )
    b = {"order_id": "ord_b1"}
    conflict, code, _ = cli.scutch(*FULFIL, "--input", "@-", "--idempotency-key", "k1", stdin=b)
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
        operation="orders.fulfil",
        status="running",
        input={"order_id": "ord_a1"},
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
    a, _, _ = cli.scutch("describe", "orders.fulfil", "--format", "json")
    b, _, _ = cli.scutch("describe", "orders.fulfil", "--format", "json")
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
    preview, _, _ = cli.scutch(*FULFIL, "--input", "@-", stdin={"order_id": "ord_a1"})
    status, _, _ = cli.scutch("status", preview["job_id"], "--format", "json")
    assert status["status"] == "needs_approval"
    assert status["phase"] == "planned"
    assert "--approve-plan" in status["next"]["argv"]
    assert preview["plan"]["digest"] in status["next"]["argv"]
    assert status["next"]["input"] == preview["next"]["input"]


def test_applying_a_plan_supersedes_its_preview(cli):
    payload = {"order_id": "ord_a1"}
    preview, _, _ = cli.scutch(*FULFIL, "--input", "@-", stdin=payload)
    applied, _, _ = cli.scutch(
        *FULFIL, "--input", "@-", "--idempotency-key", "sup-1",
        "--approve-plan", preview["plan"]["digest"], stdin=payload,
    )
    assert applied["result"]["status"] == "fulfilled"
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
    payload = {"order_id": "ord_a1"}
    preview, _, _ = cli.scutch(*FULFIL, "--input", "@-", stdin=payload)
    assert preview["status"] == "needs_approval"
    digest = preview["plan"]["digest"]

    # status offers the cancel command for a pending plan
    pending, _, _ = cli.scutch("status", preview["job_id"], "--format", "json")
    assert pending["next"]["cancel"][:3] == ["scutch", "cancel", preview["job_id"]]

    cancelled, code, _ = cli.scutch("cancel", preview["job_id"], "--format", "json")
    assert cancelled["status"] == "cancelled"
    assert code == 0

    # the digest is now dead — and nothing was applied
    stale, code2, _ = cli.scutch(*FULFIL, "--input", "@-", "--approve-plan", digest, stdin=payload)
    assert stale["status"] == "cancelled"
    assert stale["error"]["code"] == "plan_cancelled"
    assert code2 == 10

    assert _status_of(cli, "ord_a1") == "placed"
    assert not _runs(cli, "applied")

    after, _, _ = cli.scutch("status", preview["job_id"], "--format", "json")
    assert after["phase"] == "cancelled"


def test_cancel_refuses_an_applied_plan(cli):
    payload = {"order_id": "ord_a1"}
    preview, _, _ = cli.scutch(*FULFIL, "--input", "@-", stdin=payload)
    applied, _, _ = cli.scutch(
        *FULFIL, "--input", "@-", "--idempotency-key", "c1",
        "--approve-plan", preview["plan"]["digest"], stdin=payload,
    )
    assert applied["result"]["status"] == "fulfilled"

    refused, code, _ = cli.scutch("cancel", applied["run_id"], "--format", "json")
    assert refused["status"] == "complete"
    assert refused["error"]["code"] == "already_applied"
    assert code == 11


def test_cancel_requires_a_pending_plan(cli):
    read, _, _ = cli.scutch("run", "orders.list", "--input", "@-", "--format", "json", stdin={})
    refused, code, _ = cli.scutch("cancel", read["job_id"], "--format", "json")
    assert refused["error"]["code"] == "not_cancellable"
    assert code == 12


def test_unused_provider_does_not_break_other_operations(cli):
    """A provider that cannot be resolved must not fail operations that don't use it.

    Found in saleor-bi: declaring a replay provider before recording anything
    made every operation fail, because resolution was eager. `unrecorded` names
    a cassette directory that does not exist, so resolving it would fail; the
    point is that nothing resolves it.
    """
    cfg = cli.workspace / "scutch.toml"
    cfg.write_text(cfg.read_text().replace(
        '[policy]', '[providers.unrecorded]\nmode = "replay"\ndata = "fixtures/does-not-exist"\n\n[policy]'
    ))
    env, _, _ = cli.scutch(
        "run", "orders.list", "--input", "@-", "--format", "json", stdin={"status": "placed"}
    )
    assert env["status"] == "complete", env.get("error")


def test_operation_that_needs_a_broken_provider_still_fails(cli):
    """Lazy resolution must not become no resolution: the provider the
    operation declares is still validated, and its failure is the operation's."""
    cfg = cli.workspace / "scutch.toml"
    cfg.write_text(
        cfg.read_text().replace(
            'data = "fixtures/orders.json"', 'data = "fixtures/does-not-exist"'
        )
    )
    env, code, _ = cli.scutch(
        "run", "orders.list", "--input", "@-", "--format", "json", stdin={}
    )
    assert env["status"] == "failed", env
    assert env["error"]["code"] == "provider_error"
    assert "does-not-exist" in env["error"]["message"]
    # The provider resolves (the mode is local); the failure happens when the
    # operation asks it for data, so it is the operation's failure (7), not a
    # resolution failure (6).
    assert code == 7
