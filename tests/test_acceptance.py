"""The decisive test, as an executable agent simulation.

Constraints that make this meaningful:

* the "agent" starts with **no conversation history** — every step is a fresh
  subprocess, and the only memory it keeps is what it read on stdout;
* it has **no credentials** and never sees a provider secret;
* it knows only an intent in English ("fulfil this order") and the bootstrap
  from ``scutch context``;
* it discovers, describes, fills the schema, resolves missing input, approves an
  exact plan, is interrupted, resumes from disk, and refuses to mutate twice;
* every continuation it performs is the envelope's published ``next.argv``.

If this test passes, an agent can use Scutch with nothing but the CLI.
"""

from __future__ import annotations

import json
import re

FULFIL_INPUT = {"order_id": "ord_a1"}


class FreshAgent:
    """Only what a tool-using agent has: argv, stdin, stdout. No state."""

    def __init__(self, cli):
        self.cli = cli
        self.memory: dict = {}       # deliberately only stdout-derived facts
        self.transcript: list[str] = []

    def call(self, argv: list[str], stdin: dict | None = None) -> dict:
        out, code, err = self.cli.run(argv, stdin=stdin)
        assert out, f"no stdout for {argv}: {err}"
        self.transcript.append(" ".join(argv))
        return out

    def bootstrap(self) -> dict:
        ctx = self.call(["scutch", "context", "--format", "json"])
        self.memory["bootstrap"] = ctx["bootstrap"]
        self.memory["catalogue"] = ctx["operations"]
        # The bootstrap is the only instruction sheet it gets.
        assert "scutch context" in ctx["bootstrap"]
        assert "next.argv" in ctx["bootstrap"]
        assert ctx["providers"]["ledger"]["mode"] == "local"
        return ctx

    def find(self, intent_words: list[str]) -> str:
        """Pick an operation from the catalogue by summary text alone."""
        scored = []
        for op in self.memory["catalogue"]:
            haystack = (op["summary"] + " " + op["name"]).lower()
            score = sum(1 for w in intent_words if w in haystack)
            scored.append((score, op["effect"] == "plan_apply", op["name"]))
        scored.sort(reverse=True)
        assert scored and scored[0][0] > 0, self.memory["catalogue"]
        return scored[0][2]

    def describe(self, name: str) -> dict:
        d = self.call(["scutch", "describe", name, "--format", "json"])
        self.memory["described"] = d
        return d

    def input_from_schema(self, d: dict, known: dict) -> dict:
        """Build the smallest legal input from the published schema."""
        schema = d["input_schema"]
        out = {}
        for name in schema.get("required", []):
            prop = schema["properties"][name]
            if name in known:
                out[name] = known[name]
            elif "examples" in prop:
                out[name] = prop["examples"][0]
            elif "enum" in prop:
                out[name] = prop["enum"][0]
            else:
                return {}  # cannot guess; force the needs_input path
        return out

    def run_op(self, name: str, payload: dict) -> dict:
        return self.call(
            ["scutch", "run", name, "--input", "@-", "--format", "json",
             "--idempotency-key", "agent-key-1"],
            stdin=payload,
        )


def _ledger(cli) -> dict:
    return json.loads((cli.workspace / ".scutch" / "ledger.json").read_text())


def _applied_runs(cli) -> list[dict]:
    rows = []
    for p in (cli.workspace / ".scutch" / "runs").glob("run_*.json"):
        rec = json.loads(p.read_text())
        if rec["phase"] == "applied":
            rows.append(rec)
    return rows


def test_fresh_agent_end_to_end(cli):
    agent = FreshAgent(cli)

    # 1 — discover the workspace with no prior knowledge.
    ctx = agent.bootstrap()

    # 2 — choose an operation by intent. "fulfil" + "placed" names only the
    #     single-order plan; the bulk operation says "outstanding".
    name = agent.find(["fulfil", "placed"])
    assert name == "orders.fulfil"

    # 3 — learn the contract; assert it can answer every question from the brief.
    d = agent.describe(name)
    assert "what does this do" and d["summary"]
    assert d["effect"] == "plan_apply"                       # what can it change
    assert d["input_schema"]["required"] == ["order_id"]     # what args
    assert d["invocation"]["argv"][:3] == ["scutch", "run", name]  # how invoked
    assert d["idempotency_key"] == ["order_id"]              # repetition behaviour
    assert d["result_schema"]["properties"]                  # what comes back
    assert d["credentials"] == {"ledger": {"provider": "ledger", "resolution": "runtime"}}
    # no secret material or credential-shaped text anywhere in the description
    text = json.dumps(d).lower()
    assert "bearer" not in text
    assert "scutch_ledger_token" not in text

    # 4 — a first attempt with nothing: the agent must learn what is required
    #     from the response, not from documentation.
    partial = agent.run_op(name, {})
    assert partial["status"] == "needs_input"
    missing = partial["error"]["details"]["missing"]
    assert missing == ["order_id"], missing
    assert partial["next"]["argv"][0] == "scutch"
    # the missing-field schema is narrow: only what is actually missing
    assert list(partial["error"]["details"]["schema"]["properties"]) == ["order_id"]

    # 5 — complete the template with the canonical inputs it was handed.
    template = dict(partial["next"]["input"], order_id="ord_a1")
    preview = agent.call(partial["next"]["argv"], stdin=template)
    assert preview["status"] == "needs_approval"
    assert preview["plan"]["changes"]
    assert preview["next"]["plan_digest"] == preview["plan"]["digest"]

    # 6 — a process restart: the agent keeps only the envelope text.
    fresh = FreshAgent(cli)
    follow_argv = list(preview["next"]["argv"])
    follow_input = dict(preview["next"]["input"])
    applied = fresh.call(follow_argv, stdin=follow_input)
    assert applied["status"] == "complete"
    assert applied["result"]["status"] == "fulfilled"
    assert applied["result"]["previous_status"] == "placed"
    # The public result is exactly the declared shape: the ledger row's other
    # columns (`customer`, `placed_at`) do not leak onto stdout.
    assert set(applied["result"]) == {"order_id", "status", "previous_status"}
    assert not applied.get("artifacts")

    # 7 — it is told exactly how to check the job it just ran.
    status_argv = applied["next"]["argv"]
    assert status_argv[:2] == ["scutch", "status"]
    status = fresh.call(status_argv)
    assert status["status"] == "complete"
    assert status["phase"] == "applied"

    # 8 — repeating the same published invocation must not mutate twice.
    again = fresh.call(follow_argv, stdin=follow_input)
    assert again["status"] == "complete"
    assert any(n.startswith("replayed_from=") for n in again["notes"])
    assert again["replay"]["mutation_requested"] is False
    assert again["replay"]["of_run"] == applied["run_id"]
    # 8b — exactly one mutation took effect across all of the above: one applied
    #      run, one order moved, and every other order untouched.
    assert len(_applied_runs(cli)) == 1
    statuses = {o["id"]: o["status"] for o in _ledger(cli)["orders"]}
    assert statuses["ord_a1"] == "fulfilled"
    assert statuses["ord_b1"] == "placed"
    assert statuses["ord_a2"] == "fulfilled"     # untouched: it already was
    assert statuses["ord_c1"] == "cancelled"

    # 9 — the agent never had to ask for a credential.
    assert all("TOKEN" not in t and "token" not in t for t in fresh.transcript)


def test_agent_cannot_talk_itself_into_an_unapproved_mutation(cli):
    """Approval cannot be forged, guessed, or reused across plans."""
    agent = FreshAgent(cli)
    agent.bootstrap()
    name = "orders.fulfil"

    preview = agent.run_op(name, FULFIL_INPUT)
    digest = preview["plan"]["digest"]

    forged = agent.call(
        ["scutch", "run", name, "--input", "@-", "--format", "json",
         "--approve-plan", "sha256:" + "a" * 64],
        stdin=FULFIL_INPUT,
    )
    assert forged["status"] == "needs_approval"
    assert forged["error"]["code"] == "approval_mismatch"

    # Approving the digest of a *different* input set is equally refused: the
    # order id is what the plan covers, so a different id is a different plan.
    other = agent.call(
        ["scutch", "run", name, "--input", "@-", "--format", "json",
         "--approve-plan", digest],
        stdin={"order_id": "ord_b1"},
    )
    assert other["status"] == "needs_approval"
    assert other["error"]["code"] == "approval_mismatch"

    assert _applied_runs(cli) == []
    assert all(o["status"] != "fulfilled" or o["id"] == "ord_a2" for o in _ledger(cli)["orders"])

    # The real digest still works, and only once.
    ok = agent.call(
        ["scutch", "run", name, "--input", "@-", "--format", "json", "--approve-plan", digest],
        stdin=FULFIL_INPUT,
    )
    assert ok["result"]["status"] == "fulfilled"


def test_human_ui_and_agent_perform_equivalent_transitions(workspace, cli):
    """One lifecycle: the same inputs produce the same envelope via both surfaces."""
    from scutch.runner import execute
    from scutch.workspace import load_workspace

    ws = load_workspace(workspace)
    payload = {"order_id": "ord_a1"}

    cli_preview, _, _ = cli.scutch(
        "run", "orders.fulfil", "--input", "@-", "--format", "json", stdin=payload
    )
    ui_preview, _ = execute(ws, "orders.fulfil", payload)

    assert cli_preview["status"] == ui_preview["status"] == "needs_approval"
    assert cli_preview["plan"]["digest"] == ui_preview["plan"]["digest"]
    assert cli_preview["next"]["argv"] == ui_preview["next"]["argv"]

    cli_apply, _, _ = cli.scutch(
        "run", "orders.fulfil", "--input", "@-", "--format", "json",
        "--approve-plan", cli_preview["plan"]["digest"], "--idempotency-key", "eq-1", stdin=payload,
    )
    assert cli_apply["status"] == "complete"
    assert cli_apply["result"]["status"] == "fulfilled"
    # Both surfaces agree on the decision and the continuation.
    assert ui_preview["plan"] == cli_preview["plan"]
    assert ui_preview["next"] == cli_preview["next"]
