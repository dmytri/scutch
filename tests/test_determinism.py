"""The reproducibility guarantee.

A workflow step pipes an operation's envelope into whatever stores it — an
artifact, a hash, a cache, a diff. That is only useful if the envelope is a
function of the invocation. These tests state and enforce that property, so a new
field that leaks a clock or a random id fails here rather than silently making
every run unique.
"""

from __future__ import annotations

import json

import pytest

from scutch.envelope import canonical, digest

REPAIR = ["run", "saleor.customer.repair", "--format", "json"]
LIST = ["run", "saleor.customer.list", "--format", "json"]


def test_envelope_is_identical_across_runs(cli):
    """Same operation + same input ⇒ identical envelope, modulo attempt id."""
    first, _, _ = cli.scutch(*LIST, "--input", "@-", stdin={})
    second, _, _ = cli.scutch(*LIST, "--input", "@-", stdin={})
    assert canonical(first) == canonical(second)
    assert digest(first) == digest(second)


def test_job_id_is_derived_not_random(cli):
    first, _, _ = cli.scutch(*LIST, "--input", "@-", stdin={})
    second, _, _ = cli.scutch(*LIST, "--input", "@-", stdin={})
    assert first["job_id"] == second["job_id"]
    assert first["job_id"].startswith("job_")
    # the attempt label still differs, so run history stays meaningful
    assert first["run_id"] != second["run_id"]


def test_different_input_is_a_different_envelope(cli):
    a, _, _ = cli.scutch(*REPAIR, "--input", "@-", stdin={"customer_id": "cust_456", "permission": "read_orders"})
    b, _, _ = cli.scutch(*REPAIR, "--input", "@-", stdin={"customer_id": "cust_456", "permission": "manage_orders"})
    assert digest(a) != digest(b)


def test_preview_is_reproducible(cli):
    """A dry run must be reproducible even though it allocates a run record."""
    payload = {"customer_id": "cust_456", "permission": "read_orders"}
    a, _, _ = cli.scutch(*REPAIR, "--input", "@-", stdin=payload)
    b, _, _ = cli.scutch(*REPAIR, "--input", "@-", stdin=payload)
    assert a["status"] == b["status"] == "needs_approval"
    assert a["plan"]["digest"] == b["plan"]["digest"]
    assert canonical(a) == canonical(b)


def test_canonical_form_drops_only_attempt_identity():
    base = {"kind": "scutch.envelope/v1", "status": "complete", "result": {"n": 1}, "job_id": "job_a"}
    a = dict(base, run_id="run_1", timing={"elapsed_ms": 1})
    b = dict(base, run_id="run_2", timing={"elapsed_ms": 99})
    assert canonical(a) == canonical(b)
    # a change in the answer is not volatile
    assert canonical(a) != canonical(dict(b, result={"n": 2}))
    # job id is part of the answer: it names the invocation
    assert canonical(a) != canonical(dict(b, job_id="job_b"))


def test_no_clock_or_randomness_in_the_reproducible_part(cli):
    """Guard against future volatile fields: nothing timestamp-shaped."""
    env, _, _ = cli.scutch(*LIST, "--input", "@-", stdin={})
    text = json.dumps(canonical(env), sort_keys=True)
    for banned in ("timestamp", "elapsed_ms", "duration", "started_at", "generated_at"):
        assert banned not in text, f"volatile field {banned!r} leaked into the envelope"


def test_console_script_help_mentions_verify():
    """`scutch verify` is the user-facing form of this guarantee."""
    from scutch.cli import app

    commands = {c.name or c.callback.__name__ for c in app.registered_commands}
    assert "verify" in commands
