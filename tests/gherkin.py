"""A deliberately small Gherkin runner.

Scenarios in ``specs/*.feature`` are executable: each step maps to a function
here, and continuations are performed by running the published ``next.argv``
through the real CLI. Unknown steps fail loudly — the suite cannot silently skip
a documented behaviour.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Scenario:
    feature: str
    name: str
    steps: list[tuple[str, str]] = field(default_factory=list)


def parse_feature(path: Path) -> list[Scenario]:
    scenarios: list[Scenario] = []
    feature_name = path.stem
    current: Scenario | None = None
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if line.startswith("Feature:"):
            feature_name = line.split(":", 1)[1].strip()
        elif line.startswith("Scenario:"):
            current = Scenario(feature_name, line.split(":", 1)[1].strip())
            scenarios.append(current)
        elif re.match(r"^(Given|When|Then|And|But)\b", line):
            if current is None:
                raise ValueError(f"{path}: step outside a scenario: {line}")
            keyword, text = line.split(" ", 1)
            current.steps.append((keyword, text.strip()))
    return scenarios


class Steps:
    """Step definitions. Subclasses provide the workspace harness."""

    def __init__(self, cli):
        self.cli = cli
        self.envelopes: list[dict] = []
        self.returncodes: list[int] = []
        self.customer: str | None = None
        self.permission: str | None = None
        self.channel: str = "retail"
        self.idem: str = "gherkin-fixed-key"
        self.env: dict = {}
        self.cancelled_digest: str | None = None

    # -- helpers ---------------------------------------------------------

    @property
    def last(self) -> dict:
        assert self.envelopes, "no envelope yet"
        return self.envelopes[-1]

    def statuses(self) -> list[str]:
        return [e.get("status") for e in self.envelopes]

    def _state(self) -> dict:
        """Applied state, falling back to the pristine fixture seeds."""
        p = self.cli.workspace / ".scutch" / "state" / "saleor.json"
        if p.is_file():
            return json.loads(p.read_text())
        seed = self.cli.workspace / "fixtures" / "saleor"
        return {
            "customers": json.loads((seed / "customers.json").read_text()),
            "orders": json.loads((seed / "orders.json").read_text()),
        }

    def _permissions(self, customer: str, channel: str) -> list[str]:
        state = self._state()
        c = state["customers"].get(customer, {})
        for entry in c.get("channelPermissions", []):
            if entry.get("channel", {}).get("slug") == channel:
                return list(entry.get("permissions", []))
        return []

    def run_repair(self, customer: str, permission: str, channel: str, approve: str | None = None):
        payload = {"customer_id": customer, "permission": permission, "channel": channel}
        argv = ["scutch", "run", "saleor.customer.repair", "--input", "@-", "--format", "json",
                "--idempotency-key", self.idem]
        if approve:
            argv += ["--approve-plan", approve]
        env, code, err = self.cli.run(argv, stdin=payload)
        assert env, f"no stdout from run; stderr={err}"
        self.envelopes.append(env)
        self.returncodes.append(code)
        return env

    # -- Given -----------------------------------------------------------

    def given_customer_has(self, customer: str, permission: str, channel: str) -> None:
        self.customer, self.permission, self.channel = customer, permission, channel
        assert permission in self._permissions(customer, channel), (
            f"fixture precondition failed: {customer} lacks {permission} on {channel}"
        )

    def given_customer_missing(self, customer: str, permission: str, channel: str) -> None:
        self.customer, self.permission, self.channel = customer, permission, channel
        assert permission not in self._permissions(customer, channel), (
            f"fixture precondition failed: {customer} already has {permission} on {channel}"
        )

    # -- When ------------------------------------------------------------

    def when_preview(self) -> None:
        assert self.customer and self.permission
        self.run_repair(self.customer, self.permission, self.channel)

    def when_follow_next_argv(self) -> None:
        nxt = self.last.get("next") or {}
        argv = nxt.get("argv")
        assert argv, f"envelope has no next.argv: {json.dumps(self.last)[:400]}"
        env, code, err = self.cli.run(argv, stdin=nxt.get("input") or {})
        assert env, f"no stdout following next.argv; stderr={err}"
        self.envelopes.append(env)
        self.returncodes.append(code)

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
        env, code, err = self.cli.scutch("run", "saleor.customer.list", "--input", "@-", "--format", "json")
        assert env, f"no stdout; stderr={err}"
        self.envelopes.append(env)

    def when_inspect_customer(self, customer: str) -> None:
        self.customer = customer
        env, code, err = self.cli.scutch(
            "run", "saleor.customer.inspect", "--input", "@-", "--format", "json",
            stdin={"customer_id": customer},
        )
        assert env, f"no stdout; stderr={err}"
        self.envelopes.append(env)

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
        """No applied run record for this invocation may exist."""
        runs = list((self.cli.workspace / ".scutch" / "runs").glob("run_*.json"))
        applied = [
            json.loads(p.read_text())
            for p in runs
        ]
        assert not [r for r in applied if r["phase"] == "applied" and r["operation"] == "saleor.customer.repair"], (
            "an apply happened when none was approved"
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
        assert self.customer
        perms = self._permissions(self.customer, channel)
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

    def then_reports_redacted_field(self) -> None:
        assert self.last["redacted"], self.last


# --------------------------------------------------------------------------
# step dispatch

PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r'^customer "([^"]+)" already has permission "([^"]+)" on channel "([^"]+)"$'), "given_customer_has"),
    (re.compile(r'^customer "([^"]+)" is missing permission "([^"]+)" on channel "([^"]+)"$'), "given_customer_missing"),
    (re.compile(r"^I preview the permission repair$"), "when_preview"),
    (re.compile(r"^I cancel the pending plan$"), "when_cancel_pending"),
    (re.compile(r"^I approve the cancelled digest$"), "when_approve_cancelled_digest"),
    (re.compile(r"^I follow the published next argv$"), "when_follow_next_argv"),
    (re.compile(r"^I repeat the original invocation unchanged$"), "when_repeat_invocation"),
    (re.compile(r'^I approve digest "([^"]+)"$'), "when_approve_digest"),
    (re.compile(r"^I run the permission repair with no customer$"), "when_run_no_customer"),
    (re.compile(r"^the process restarts$"), "when_process_restarts"),
    (re.compile(r"^I list customers$"), "when_list_customers"),
    (re.compile(r'^I inspect customer "([^"]+)"$'), "when_inspect_customer"),
    (re.compile(r'^the result lists "([^"]+)"$'), "then_result_lists"),
    (re.compile(r"^the result has no restricted fields$"), "then_result_has_no_restricted_fields"),
    (re.compile(r"^the envelope reports a redacted field$"), "then_reports_redacted_field"),
    (re.compile(r"^the envelope withholds nothing$"), "then_withholds_nothing"),
    (re.compile(r"^I ask for the workspace context$"), "when_context"),
    (re.compile(r'^I describe "([^"]+)"$'), "when_describe"),
    (re.compile(r'^the envelope status is "([^"]+)"$'), "then_envelope_status"),
    (re.compile(r'^the result status is "([^"]+)"$'), "then_result_status"),
    (re.compile(r"^no approval is required$"), "then_no_approval_required"),
    (re.compile(r"^no mutation is requested$"), "then_no_mutation_requested"),
    (re.compile(r"^no mutation has happened$"), "then_no_mutation_happened"),
    (re.compile(r"^there is exactly one change$"), "then_exactly_one_change"),
    (re.compile(r'^the change kind is "([^"]+)"$'), "then_change_kind"),
    (re.compile(r"^the next argv approves the returned digest$"), "then_next_approves_digest"),
    (re.compile(r"^the second response reports a replay$"), "then_replay_reported"),
    (re.compile(r'^the customer holds exactly one "([^"]+)" permission on "([^"]+)"$'), "then_customer_holds_exactly"),
    (re.compile(r'^the error code is "([^"]+)"$'), "then_error_code"),
    (re.compile(r'^the error lists "([^"]+)" as missing$'), "then_error_lists_missing"),
    (re.compile(r"^the next block carries canonical inputs and an argv$"), "then_next_carries_canonical"),
    (re.compile(r"^the bootstrap forbids requesting credential values$"), "then_bootstrap_forbids_credentials"),
    (re.compile(r'^the catalogue lists "([^"]+)"$'), "then_catalogue_lists"),
    (re.compile(r'^the policy requires approval for "([^"]+)"$'), "then_policy_requires_approval"),
    (re.compile(r'^it declares effect "([^"]+)"$'), "then_declares_effect"),
    (re.compile(r'^its credentials name only the provider handle "([^"]+)"$'), "then_credentials_handle_only"),
    (re.compile(r"^its input schema requires (.+)$"), "then_input_schema_requires"),
    (re.compile(r"^its idempotency key covers (.+)$"), "then_idempotency_covers"),
    (re.compile(r"^its source is the implementing module$"), "then_source_is_module"),
    (re.compile(r'^the intended effects name "([^"]+)"$'), "then_intended_effects_name"),
    (re.compile(r'^the external reads name "([^"]+)"$'), "then_external_reads_name"),
]


def split_list(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r'"([^"]+)"', text))


def execute(scenario: Scenario, steps: Steps) -> None:
    for keyword, text in scenario.steps:
        for pattern, method in PATTERNS:
            m = pattern.match(text)
            if not m:
                continue
            fn = getattr(steps, method)
            if method in ("then_input_schema_requires", "then_idempotency_covers"):
                fn(*split_list(text))
            else:
                fn(*m.groups())
            break
        else:
            raise AssertionError(f"undefined step: {keyword} {text}")
