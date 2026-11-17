"""Scutch's own step library, on top of the shared engine.

The parser and dispatcher live in ``scutch.gherkin`` because other workspaces
(saleor-bi) execute their own feature files. Only the vocabulary is here.
"""

from __future__ import annotations

import json
import re
from typing import Any

from scutch.gherkin import (  # re-exported for tests/test_gherkin.py
    Scenario,
    UndefinedStep,
    coverage,
    discover,
    execute,
    named_values,
    numbers,
    parse_feature,
    quoted,
    step,
)


class Steps:
    """Step definitions. Subclasses provide the workspace harness."""

    def __init__(self, cli):
        self.cli = cli
        self.envelopes: list[dict] = []
        self.returncodes: list[int] = []
        self.customer: str | None = None
        self.permission: str | None = None
        self.channel: str = "retail"
        self.idem_base: str = "gherkin"
        self._resets: int = 0
        self.idem: str = "gherkin-0"
        self.env: dict = {}
        self.cancelled_digest: str | None = None
        # Every CLI invocation appends its request log here (see conftest shim),
        # so assertions can be made on what crossed the transport boundary.
        self.wire: list[dict] = []
        self.recording: str | None = None
        self.cassette: dict | None = None

    # -- helpers ---------------------------------------------------------

    @property
    def last(self) -> dict:
        assert self.envelopes, "no envelope yet"
        return self.envelopes[-1]

    def statuses(self) -> list[str]:
        return [e.get("status") for e in self.envelopes]

    def _exchanges(self) -> list[dict]:
        out: list[dict] = []
        for f in sorted((self.cli.workspace / "fixtures" / "saleor").glob("*.json")):
            out.extend(json.loads(f.read_text()).get("exchanges", []))
        return out

    def recorded_customer(self, customer: str) -> dict | None:
        """The customer object as the recordings say it is, before any mutation.

        Preconditions are read from the cassette so a scenario states what the
        API was believed to hold — not what a mutable seed happens to contain.
        """
        for ex in self._exchanges():
            if "mutation" in ex["request"]["document"]:
                continue
            user = (ex.get("response") or {}).get("user")
            if user and user.get("id") == customer:
                return user  # first read wins: the state before any mutation
        return None

    def recorded_permissions(self, customer: str, channel: str) -> list[str]:
        user = self.recorded_customer(customer)
        if not user:
            return []
        for entry in user.get("channelPermissions", []):
            if entry.get("channel", {}).get("slug") == channel:
                return list(entry.get("permissions", []))
        return []

    def run_repair(self, customer: str, permission: str, channel: str, approve: str | None = None):
        payload = {"customer_id": customer, "permission": permission, "channel": channel}
        argv = ["scutch", "run", "saleor.customer.repair", "--input", "@-", "--format", "json",
                "--idempotency-key", self.idem]
        if approve:
            argv += ["--approve-plan", approve]
        return self.invoke(argv, payload)

    def invoke(self, argv: list[str], payload: dict | None = None) -> dict:
        """Run the CLI and collect the request log it produced."""
        self.cli.recording = self.recording
        env, code, err = self.cli.run(argv, stdin=payload)
        assert env, f"no stdout from {argv}; stderr={err}"
        self.envelopes.append(env)
        self.returncodes.append(code)
        self.wire.extend(self.cli.drain_wire())
        return env

    # -- wire assertions -------------------------------------------------

    def mutations(self) -> list[dict]:
        return [r for r in self.wire if r.get("kind") == "mutation"]

    def queries(self) -> list[dict]:
        return [r for r in self.wire if r.get("kind") == "query"]

    # -- Given -----------------------------------------------------------

    def given_fresh_workspace(self) -> None:
        """Reset to the committed state.

        A feature is a workflow: scenarios share a workspace so a step can build
        on the last one. A scenario that must *not* inherit that state says so,
        here — declaratively, with no flag and no per-test magic.
        """
        from tests.conftest import _copy_workspace

        # Restore from the workspace's own snapshot, not from the repo: a test
        # that mutates the workspace must not be silently un-mutated by a reset.
        # The run store is included, so this scenario cannot inherit an applied
        # record from the last one and replay instead of apply.
        self.cli.reset()
        self.envelopes.clear()
        self.returncodes.clear()
        self.wire.clear()
        self.cancelled_digest = None
        # The idempotency key names an intent. A fresh workspace is a new
        # intent, so reusing one across a reset would be a false conflict.
        self._resets += 1
        self.idem = f"{self.idem_base}-{self._resets}"

    def given_recording(self, name: str) -> None:
        """Bind the scenario to one recorded HTTP session."""
        self.recording = name

    def given_customer_has(self, customer: str, permission: str, channel: str) -> None:
        self.customer, self.permission, self.channel = customer, permission, channel
        assert permission in self.recorded_permissions(customer, channel), (
            f"recording precondition failed: {customer} lacks {permission} on {channel}"
        )

    def given_customer_missing(self, customer: str, permission: str, channel: str) -> None:
        self.customer, self.permission, self.channel = customer, permission, channel
        assert permission not in self.recorded_permissions(customer, channel), (
            f"recording precondition failed: {customer} already has {permission} on {channel}"
        )

    # -- When ------------------------------------------------------------

    def when_preview(self) -> None:
        assert self.customer and self.permission
        self.run_repair(self.customer, self.permission, self.channel)

    def when_follow_next_argv(self) -> None:
        nxt = self.last.get("next") or {}
        argv = nxt.get("argv")
        assert argv, f"envelope has no next.argv: {json.dumps(self.last)[:400]}"
        env = self.invoke(argv, nxt.get("input") or {})

    def when_repeat_invocation(self) -> None:
        """Repeat the caller's own invocation, with the same idempotency key."""
        assert self.customer and self.permission
        self.run_repair(self.customer, self.permission, self.channel)

    def when_cancel_pending(self) -> None:
        job = self.last.get("job_id")
        assert job, "no job_id to cancel"
        env, code, err = self.cli.scutch("cancel", job, "--format", "json")
        assert env, f"no stdout; stderr={err}"
        assert env["status"] == "cancelled", env.get("error")
        self.cancelled_digest = self.last["plan"]["digest"]
        self.envelopes.append(env)

    def when_approve_cancelled_digest(self) -> None:
        assert self.customer and self.permission
        self.run_repair(
            self.customer, self.permission, self.channel, approve=self.cancelled_digest
        )

    def when_approve_digest(self, digest: str) -> None:
        assert self.customer and self.permission
        self.run_repair(self.customer, self.permission, self.channel, approve=digest)

    def when_run_no_customer(self) -> None:
        env, code, err = self.cli.run(
            ["scutch", "run", "saleor.customer.repair", "--input", "@-", "--format", "json"],
            stdin={},
        )
        assert env, f"no stdout; stderr={err}"
        self.envelopes.append(env)
        self.returncodes.append(code)

    def when_process_restarts(self) -> None:
        self.env = {}

    def when_list_customers(self) -> None:
        self.invoke(["scutch", "run", "saleor.customer.list", "--input", "@-", "--format", "json"])

    def when_inspect_customer(self, customer: str) -> None:
        self.customer = customer
        self.invoke(
            ["scutch", "run", "saleor.customer.inspect", "--input", "@-", "--format", "json"],
            {"customer_id": customer},
        )

    def when_context(self) -> None:
        env, code, err = self.cli.scutch("context", "--format", "json")
        assert env, f"no stdout; stderr={err}"
        self.envelopes.append(env)

    def when_describe(self, name: str) -> None:
        env, code, err = self.cli.scutch("describe", name, "--format", "json")
        assert env, f"no stdout; stderr={err}"
        self.envelopes.append(env)

    # -- Then ------------------------------------------------------------

    def then_envelope_status(self, status: str) -> None:
        assert self.last["status"] == status, json.dumps(self.last, indent=2)[:1200]

    def then_result_status(self, status: str) -> None:
        assert self.last["result"]["status"] == status, json.dumps(self.last.get("result"))

    def then_no_approval_required(self) -> None:
        assert self.last["status"] != "needs_approval"
        assert "approve-plan" not in " ".join(self.last["next"]["argv"])

    def then_no_mutation_requested(self) -> None:
        assert self.last["plan"]["changes"] == []
        assert self.last["status"] == "complete"

    def then_no_mutation_happened(self) -> None:
        """No mutation crossed the wire in *this* scenario.

        Asserted against the request log rather than the run store: a feature is
        a workflow, so earlier scenarios may legitimately have applied the same
        operation. The wire log is reset by `Given a fresh workspace`, which is
        exactly the boundary "this scenario" means.
        """
        assert self.mutations() == [], [m["document"][:60] for m in self.mutations()]

    def then_exactly_one_change(self) -> None:
        assert len(self.last["plan"]["changes"]) == 1, self.last["plan"]

    def then_change_kind(self, kind: str) -> None:
        assert self.last["plan"]["changes"][0]["kind"] == kind

    def then_next_approves_digest(self) -> None:
        digest = self.last["plan"]["digest"]
        argv = self.last["next"]["argv"]
        assert "--approve-plan" in argv and digest in argv, argv

    def then_replay_reported(self) -> None:
        notes = " ".join(self.last.get("notes") or [])
        assert "replayed_from=" in notes, json.dumps(self.last)[:800]

    def then_customer_holds_exactly(self, permission: str, channel: str) -> None:
        """After an apply the projector reports the state the API returned."""
        assert self.customer
        perms = [
            p
            for entry in self.last["result"]["channel_permissions"]
            if entry.get("channel", {}).get("slug") == channel
            for p in entry.get("permissions", [])
        ]
        assert perms.count(permission) == 1, f"{perms}"

    def then_error_code(self, code: str) -> None:
        assert self.last["error"]["code"] == code, json.dumps(self.last.get("error"))

    def then_error_lists_missing(self, field: str) -> None:
        details = self.last["error"]["details"]
        assert field in details["missing"], details

    def then_next_carries_canonical(self) -> None:
        nxt = self.last["next"]
        assert isinstance(nxt.get("input"), dict) and nxt["input"]
        assert nxt["argv"][0] == "scutch"

    # discovery assertions

    def then_bootstrap_forbids_credentials(self) -> None:
        assert "Never request credential values" in self.last["bootstrap"]

    def then_catalogue_lists(self, operation: str) -> None:
        names = [o["name"] for o in self.last["operations"]]
        assert operation in names, names

    def then_policy_requires_approval(self, effect: str) -> None:
        assert effect in self.last["policy"]["require_approval_for"]

    def then_declares_effect(self, effect: str) -> None:
        assert self.last["effect"] == effect

    def then_credentials_handle_only(self, provider: str) -> None:
        creds = self.last["credentials"]
        assert creds, "no credentials declared"
        for value in creds.values():
            assert value == {"provider": provider, "resolution": "runtime"}, value
            assert "secret" not in json.dumps(creds).lower()

    def then_input_schema_requires(self, *fields: str) -> None:
        required = set(self.last["input_schema"]["required"])
        assert set(fields) <= required, required

    def then_idempotency_covers(self, *fields: str) -> None:
        assert set(fields) == set(self.last["idempotency_key"])

    def then_source_is_module(self) -> None:
        source = self.last["source"]
        assert source["implementation"].endswith("repair_customer")
        assert "customer_repair" in source["module"]

    def then_intended_effects_name(self, effect: str) -> None:
        assert effect in self.last["intended_effects"]

    def then_external_reads_name(self, read: str) -> None:
        assert read in self.last["external_reads"]

    def then_result_lists(self, value: str) -> None:
        ids = [c["customer_id"] for c in self.last["result"]["customers"]]
        assert value in ids, ids

    def then_result_has_no_restricted_fields(self) -> None:
        assert "raw_response" not in json.dumps(self.last["result"])

    def then_withholds_nothing(self) -> None:
        assert not self.last.get("redacted"), self.last.get("redacted")

    # -- wire ------------------------------------------------------------

    def then_plan_intends_one_mutation(self, kind: str) -> None:
        changes = self.last["plan"]["changes"]
        assert len(changes) == 1, changes
        assert changes[0]["kind"] == kind, changes

    def then_request_log_holds_no_mutation(self) -> None:
        assert self.mutations() == [], [m["document"][:60] for m in self.mutations()]

    def then_request_log_holds_read_of(self, customer: str) -> None:
        reads = [q for q in self.queries() if q["variables"].get("id") == customer]
        assert reads, [q["variables"] for q in self.queries()]

    def then_request_log_holds_exactly(self, count: str) -> None:
        assert len(self.mutations()) == int(count), [
            {"document": m["document"][:60], "variables": m["variables"]} for m in self.mutations()
        ]

    def then_mutation_calls(self, field: str) -> None:
        doc = self.mutations()[0]["document"]
        assert f"{field}(" in doc, doc

    def then_mutation_sends(self, *pairs: str) -> None:
        variables = self.mutations()[0]["variables"]
        expected = dict(pairs)
        assert variables == expected, {"sent": variables, "expected": expected}

    def then_no_credential_in_exchanges(self) -> None:
        import re as _re

        text = json.dumps(self.wire)
        assert not self._cassette_contains_secret(text), "a credential reached the wire log"

    def _cassette_contains_secret(self, text: str) -> bool:
        import os as _os
        import re as _re

        token = _os.environ.get("SCUTCH_SALEOR_TOKEN", "")
        if token and token in text:
            return True
        return bool(_re.search(r"Bearer\s+[A-Za-z0-9._\-]{8,}", text))

    def then_hint_says_rerecord(self) -> None:
        hint = (self.last.get("error") or {}).get("hint", "")
        assert "re-record" in hint, hint

    def when_request_unrecorded(self) -> None:
        """Call a provider method whose query the recording does not contain.

        A changed query or a new call site must fail loudly: replay inventing a
        response would make a contract test pass while the API contract broke.
        """
        self.invoke(
            ["scutch", "run", "saleor.customer.list", "--input", "@-", "--format", "json"],
            {"limit": 5},
        )

    def then_reports_redacted_field(self) -> None:
        assert self.last["redacted"], self.last


# --------------------------------------------------------------------------
# step vocabulary

LIBRARY = [
    step(r"a fresh workspace", "given_fresh_workspace"),
    step(r'the recording "([^"]+)"', "given_recording"),
    step(r'customer "([^"]+)" already has permission "([^"]+)" on channel "([^"]+)"', "given_customer_has"),
    step(r'customer "([^"]+)" is missing permission "([^"]+)" on channel "([^"]+)"', "given_customer_missing"),
    step(r"I preview the permission repair", "when_preview"),
    step(r"I follow the published next argv", "when_follow_next_argv"),
    step(r"I repeat the original invocation unchanged", "when_repeat_invocation"),
    step(r"I cancel the pending plan", "when_cancel_pending"),
    step(r"I approve the cancelled digest", "when_approve_cancelled_digest"),
    step(r'I approve digest "([^"]+)"', "when_approve_digest"),
    step(r"I run the permission repair with no customer", "when_run_no_customer"),
    step(r"the process restarts", "when_process_restarts"),
    step(r"I ask for the workspace context", "when_context"),
    step(r"I list customers", "when_list_customers"),
    step(r'I inspect customer "([^"]+)"', "when_inspect_customer"),
    step(r"I request an operation whose query is not recorded", "when_request_unrecorded"),
    step(r'I describe "([^"]+)"', "when_describe"),
    step(r'the envelope status is "([^"]+)"', "then_envelope_status"),
    step(r'the result status is "([^"]+)"', "then_result_status"),
    step(r"no approval is required", "then_no_approval_required"),
    step(r"no mutation is requested", "then_no_mutation_requested"),
    step(r"no mutation has happened", "then_no_mutation_happened"),
    step(r"there is exactly one change", "then_exactly_one_change"),
    step(r'the change kind is "([^"]+)"', "then_change_kind"),
    step(r"the next argv approves the returned digest", "then_next_approves_digest"),
    step(r"the second response reports a replay", "then_replay_reported"),
    step(r'the customer holds exactly one "([^"]+)" permission on "([^"]+)"', "then_customer_holds_exactly"),
    step(r'the error code is "([^"]+)"', "then_error_code"),
    step(r'the error lists "([^"]+)" as missing', "then_error_lists_missing"),
    step(r"the next block carries canonical inputs and an argv", "then_next_carries_canonical"),
    step(r"the bootstrap forbids requesting credential values", "then_bootstrap_forbids_credentials"),
    step(r'the catalogue lists "([^"]+)"', "then_catalogue_lists"),
    step(r'the policy requires approval for "([^"]+)"', "then_policy_requires_approval"),
    step(r'it declares effect "([^"]+)"', "then_declares_effect"),
    step(r'its credentials name only the provider handle "([^"]+)"', "then_credentials_handle_only"),
    step(r"its input schema requires (.+)", "then_input_schema_requires", extract=quoted),
    step(r"its idempotency key covers (.+)", "then_idempotency_covers", extract=quoted),
    step(r"its source is the implementing module", "then_source_is_module"),
    step(r'the intended effects name "([^"]+)"', "then_intended_effects_name"),
    step(r'the external reads name "([^"]+)"', "then_external_reads_name"),
    step(r'the result lists "([^"]+)"', "then_result_lists"),
    step(r"the result has no restricted fields", "then_result_has_no_restricted_fields"),
    step(r"the envelope reports a redacted field", "then_reports_redacted_field"),
    step(r"the envelope withholds nothing", "then_withholds_nothing"),
    step(r'the plan intends one mutation of kind "([^"]+)"', "then_plan_intends_one_mutation"),
    step(r"the request log holds no mutation", "then_request_log_holds_no_mutation"),
    step(r'the request log holds a read of "([^"]+)"', "then_request_log_holds_read_of"),
    step(r"the request log holds exactly (\d+) mutation", "then_request_log_holds_exactly"),
    step(r'that mutation calls "([^"]+)"', "then_mutation_calls"),
    step(r"that mutation sends (.+)", "then_mutation_sends", extract=named_values),
    step(r"no credential appears in the recorded exchanges", "then_no_credential_in_exchanges"),
    step(r"the hint says to re-record the fixture", "then_hint_says_rerecord"),
]


def library() -> list:
    return LIBRARY
