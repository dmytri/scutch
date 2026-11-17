"""The step vocabulary for Scutch workspaces.

It lives in the package, not the test suite, because a workspace's features are
executed by ``scutch run <feature>`` as well as by pytest. The *harness* is
injected: tests hand in one that shells out to the CLI; `scutch run` hands in one
that calls the runner in-process. The steps themselves are identical, which is
what makes a feature both the workflow and its proof.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .gherkin import (  # re-exported for tests/test_gherkin.py
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
        # The most recent invocation, so a step can repeat it exactly.
        self.last_argv: list[str] | None = None
        self.last_payload: dict | None = None
        self.cassette: dict | None = None
        #: Input supplied to the generic `When I run <operation>` step.
        self.inputs: dict = {}

    # -- helpers ---------------------------------------------------------

    @property
    def last(self) -> dict:
        assert self.envelopes, "no envelope yet"
        return self.envelopes[-1]

    def statuses(self) -> list[str]:
        return [e.get("status") for e in self.envelopes]


    def invoke(self, argv: list[str], payload: dict | None = None) -> dict:
        """Run the CLI and collect the request log it produced.

        The argv and payload are remembered so a later step can repeat *this*
        invocation byte for byte — including its idempotency key, which is what
        makes "repeating does not repeat the effect" a claim about the same
        intent rather than a similar one.
        """
        self.cli.recording = self.recording
        env, code, err = self.cli.run(argv, stdin=payload)
        assert env, f"no stdout from {argv}; stderr={err}"
        self.last_argv = list(argv)
        self.last_payload = dict(payload) if payload is not None else None
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

    def then_error_names(self, field: str) -> None:
        message = (self.last.get("error") or {}).get("message", "")
        assert field in message, message

    def given_fresh_workspace(self) -> None:
        """Reset to the committed state.

        A feature is a workflow: scenarios share a workspace so a step can build
        on the last one. A scenario that must *not* inherit that state says so,
        here — declaratively, with no flag and no per-test magic.
        """
        
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

    def given_order_status(self, order_id: str, status: str) -> None:
        """State a precondition about the seed data, and fail loudly if it is false.

        A scenario that assumes ``ord_a1`` is ``placed`` should not quietly pass
        when it is not: the precondition is asserted against the committed seed,
        not against whatever the working copy happens to hold.
        """
        from .providers import resolve

        cfg = self.cli.ws.providers["ledger"]
        ledger = resolve(cfg, self.cli.ws.root).load()
        ledger.reset()  # read the seed, not a working copy from a previous step
        actual = ledger.get(order_id).get("status")
        assert actual == status, (
            f"seed precondition failed: {order_id} is {actual!r}, scenario says {status!r}"
        )

    # -- When ------------------------------------------------------------

    def when_run_operation(self, operation: str) -> None:
        """`When I run <operation>` — the generic step every scaffold uses.

        Without this, a scaffolded workflow cannot execute at all, which would
        make `scutch new` emit a feature that can never pass.
        """
        self.invoke(
            ["scutch", "run", operation, "--input", "@-", "--format", "json"],
            self.inputs or {},
        )

    def when_run_with_input(self, operation: str, raw: str) -> None:
        """`When I run <op> with input {...}` — one step, so the input is set
        before the call rather than after it."""
        import json as _json

        try:
            payload = _json.loads(raw)
        except _json.JSONDecodeError as exc:
            raise AssertionError(f"with input: not valid JSON: {exc}") from exc
        self.invoke(
            ["scutch", "run", operation, "--input", "@-", "--format", "json"], payload
        )

    def when_with_input(self, raw: str) -> None:
        """`And with input {"id": "x"}` — the inputs for the generic run step."""
        import json as _json

        try:
            self.inputs = _json.loads(raw)
        except _json.JSONDecodeError as exc:
            raise AssertionError(f"with input: not valid JSON: {exc}") from exc

    def when_preview(self) -> None:
        """Preview the last operation the scenario named, with its last input."""
        assert self.last_argv is not None, "no operation named before the preview"
        argv = [a for a in self.last_argv if a not in ("--approve-plan",)]
        # Drop any previous approval so this is a genuine preview.
        if "--approve-plan" in argv:
            i = argv.index("--approve-plan")
            del argv[i : i + 2]
        self.invoke(argv, self.last_payload)

    def when_follow_next_argv(self) -> None:
        nxt = self.last.get("next") or {}
        argv = nxt.get("argv")
        assert argv, f"envelope has no next.argv: {json.dumps(self.last)[:400]}"
        env = self.invoke(argv, nxt.get("input") or {})

    def when_repeat_invocation(self) -> None:
        """Repeat the caller's own last invocation, unchanged.

        "Unchanged" includes the idempotency key, which is the point: the same
        intent must not produce a second effect. The argv and payload are those
        of the previous call, so this step works for any operation.
        """
        assert self.last_argv is not None, "nothing has been invoked yet"
        self.invoke(list(self.last_argv), self.last_payload)

    def when_cancel_pending(self) -> None:
        job = self.last.get("job_id")
        assert job, "no job_id to cancel"
        env, code, err = self.cli.scutch("cancel", job, "--format", "json")
        assert env, f"no stdout; stderr={err}"
        assert env["status"] == "cancelled", env.get("error")
        self.cancelled_digest = self.last["plan"]["digest"]
        self.envelopes.append(env)

    def _approve(self, digest: str) -> None:
        """Re-run the last invocation with an explicit approval digest."""
        assert self.last_argv is not None, "nothing to approve"
        argv = [a for a in self.last_argv if a != "--approve-plan"]
        if "--approve-plan" in argv:
            i = argv.index("--approve-plan")
            del argv[i : i + 2]
        self.invoke([*argv, "--approve-plan", digest], self.last_payload)

    def when_approve_cancelled_digest(self) -> None:
        assert self.cancelled_digest, "no cancelled digest to try"
        self._approve(self.cancelled_digest)

    def when_approve_digest(self, digest: str) -> None:
        self._approve(digest)

    def when_run_without_input(self, operation: str) -> None:
        """Call an operation with no input at all, to see what it asks for."""
        self.invoke(
            ["scutch", "run", operation, "--input", "@-", "--format", "json"], {}
        )

    def when_process_restarts(self) -> None:
        self.env = {}

    def when_list_orders(self) -> None:
        self.invoke(["scutch", "run", "orders.list", "--input", "@-", "--format", "json"])

    def when_inspect_order(self, order_id: str) -> None:
        self.invoke(
            ["scutch", "run", "orders.inspect", "--input", "@-", "--format", "json"],
            {"order_id": order_id},
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

    def then_scaffold_is_unfinished(self) -> None:
        """A scaffold asserts its own incompleteness, so it cannot pass falsely.

        It is replaced when the body is written. Until then, "this is not done"
        is the only true statement about the step.
        """
        assert self.last["status"] == "failed", self.last["status"]
        error = self.last.get("error") or {}
        assert "NotImplementedError" in str(error) or error.get("code") == "operation_failed", error

    def then_result_lists_n_bases(self, n: str) -> None:
        result = self.last.get("result") or {}
        bases = result.get("bases") or result.get("rows") or []
        assert len(bases) == int(n), f"expected {n} bases, got {len(bases)}"

    def then_error_names_permitted(self) -> None:
        error = self.last.get("error") or {}
        details = error.get("details") or {}
        text = json.dumps(details) + " " + str(error.get("message", ""))
        assert "net-confirmed" in text or "permitted" in text.lower() or "one of" in text.lower(), text[:300]

    def then_status_in(self, allowed: str) -> None:
        """`Then the envelope status is one of "a", "b"` — for a scaffold.

        A scaffolded step does not know whether the operation needs approval;
        it should assert that the outcome is *definite*, not that it is a
        particular one it cannot yet predict.
        """
        import re as _re

        options = _re.findall(r'"([^"]+)"', allowed)
        assert self.last["status"] in options, (self.last["status"], options)

    def then_envelope_status(self, status: str) -> None:
        assert self.last["status"] == status, json.dumps(self.last, indent=2)[:1200]

    def then_result_status(self, status: str) -> None:
        assert self.last["result"]["status"] == status, json.dumps(self.last.get("result"))

    def then_no_approval_required(self) -> None:
        assert self.last["status"] != "needs_approval"
        assert "approve-plan" not in " ".join(self.last["next"]["argv"])

    def then_no_mutation_requested(self) -> None:
        """No mutation is requested, whether or not a plan was even computed.

        A read has no `plan` key at all, which is stronger than an empty plan;
        indexing it would turn the strongest case into a crash.
        """
        assert (self.last.get("plan") or {}).get("changes", []) == []
        assert self.last["status"] == "complete", self.last.get("error")

    def then_no_mutation_happened(self) -> None:
        """No mutation crossed the wire in *this* scenario.

        Asserted against the request log rather than the run store: a feature is
        a workflow, so earlier scenarios may legitimately have applied the same
        operation. The wire log is reset by `Given a fresh workspace`, which is
        exactly the boundary "this scenario" means.
        """
        assert self.mutations() == [], [m["document"][:60] for m in self.mutations()]

    def then_change_count(self, n: str) -> None:
        """`Then there are N changes` — a count, not just "exactly one"."""
        changes = (self.last.get("plan") or {}).get("changes") or []
        assert len(changes) == int(n), f"expected {n} change(s), got {len(changes)}: {changes}"

    def then_result_field(self, field_name: str, value: str) -> None:
        """`Then the result field "x" is "y"` — one labelled result value."""
        result = self.last.get("result") or {}
        actual = result.get(field_name)
        if isinstance(actual, list):
            actual = len(actual)
        assert str(actual) == value, f"result[{field_name!r}] = {actual!r}, expected {value!r}"

    def then_no_further_change(self) -> None:
        """`Then repeating reports no further change` — the second envelope agrees."""
        assert len(self.envelopes) >= 2, "there is no repeat to compare"
        first, second = self.envelopes[-2], self.envelopes[-1]
        assert second["status"] == "complete", second.get("error")
        assert (second.get("result") or {}) == (first.get("result") or {}), (
            "repeating the same intent produced a different result: "
            f"{first.get('result')} then {second.get('result')}"
        )

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

    def then_error_has_hint(self) -> None:
        """`Then the error suggests what to do next` — an error a caller can act on."""
        hint = (self.last.get("error") or {}).get("hint")
        assert hint, f"error carries no hint: {json.dumps(self.last.get('error'))}"

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
        """The description points at real code, and at a spec.

        Generic: it asserts the implementation is a dotted name inside a module
        that exists on disk, and that a spec is named. It does not assert any
        particular filename, which is what made it demo-specific.
        """
        source = self.last["source"]
        implementation = source["implementation"]
        module = source["module"]
        assert "." in implementation, implementation
        assert implementation.rsplit(".", 1)[0] == module, (implementation, module)
        path = self.cli.workspace / "operations"
        stems = {p.stem for p in path.rglob("*.py")} if path.is_dir() else set()
        assert module.rsplit("_", 2)[-1] in stems or any(module.endswith(s) for s in stems), (
            f"module {module!r} does not correspond to a file under operations/"
        )
        assert source.get("spec"), "the description names no spec"

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

    def when_request_missing_record(self) -> None:
        """Ask an operation for something the provider cannot supply.

        A provider must fail loudly rather than invent a response: a lookup that
        quietly returned a default would make a scenario pass while the outside
        world had changed underneath it.
        """
        self.invoke(
            ["scutch", "run", "orders.inspect", "--input", "@-", "--format", "json"],
            {"order_id": "ord_zz"},
        )

    def then_reports_redacted_field(self) -> None:
        assert self.last["redacted"], self.last


# --------------------------------------------------------------------------
# step vocabulary

LIBRARY = [
    step(r"a fresh workspace", "given_fresh_workspace"),
    step(r'order "([^"]+)" is "([^"]+)"', "given_order_status"),
    step(r"I run ([a-z][a-z0-9_.]*) with no input", "when_run_without_input"),
    step(r"I list orders", "when_list_orders"),
    step(r'I inspect order "([^"]+)"', "when_inspect_order"),
    step(r'the error names "([^"]+)"', "then_error_names"),
    step(r"I follow the published next argv", "when_follow_next_argv"),
    step(r"I repeat the original invocation unchanged", "when_repeat_invocation"),
    step(r"I cancel the pending plan", "when_cancel_pending"),
    step(r"I approve the cancelled digest", "when_approve_cancelled_digest"),
    step(r'I approve digest "([^"]+)"', "when_approve_digest"),
    step(r"there are (\d+) changes", "then_change_count"),
    step(r'the result field "([^"]+)" is "([^"]+)"', "then_result_field"),
    step(r"repeating reports no further change", "then_no_further_change"),
    step(r"I run ([a-z][a-z0-9_.]*)", "when_run_operation"),
    step(r"I run ([a-z][a-z0-9_.]*) with input (.+)$", "when_run_with_input"),
    step(r"the scaffold is unfinished", "then_scaffold_is_unfinished"),
    step(r"the error suggests what to do next", "then_error_has_hint"),
    step(r"the envelope status is one of (.+)$", "then_status_in"),
    step(r"with input (.+)$", "when_with_input"),
    step(r"the process restarts", "when_process_restarts"),
    step(r"I ask for the workspace context", "when_context"),
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
    step(r'the result lists "([^"]+)"', "then_result_lists"),
    step(r"the result has no restricted fields", "then_result_has_no_restricted_fields"),
    step(r"the envelope reports a redacted field", "then_reports_redacted_field"),
    step(r"the envelope withholds nothing", "then_withholds_nothing"),
    step(r'the plan intends one mutation of kind "([^"]+)"', "then_plan_intends_one_mutation"),
    step(r"the request log holds no mutation", "then_request_log_holds_no_mutation"),
    step(r"the request log holds exactly (\d+) mutation", "then_request_log_holds_exactly"),
    step(r'that mutation calls "([^"]+)"', "then_mutation_calls"),
    step(r"that mutation sends (.+)", "then_mutation_sends", extract=named_values),
    step(r"no credential appears in the recorded exchanges", "then_no_credential_in_exchanges"),
    step(r"the hint says to re-record the fixture", "then_hint_says_rerecord"),
]


def library() -> list:
    return LIBRARY
