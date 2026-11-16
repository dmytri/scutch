"""The decisive test, as an executable agent simulation.

Constraints that make this meaningful:

* the "agent" starts with **no conversation history** — every step is a fresh
  subprocess, and the only memory it keeps is what it read on stdout;
* it has **no credentials** and never sees a provider secret;
* it knows only an intent in English ("grant a customer read access to
  orders") and the bootstrap from ``scutch context``;
* it discovers, describes, fills the schema, resolves missing input, approves an
  exact plan, is interrupted, resumes from disk, and refuses to mutate twice;
* every continuation it performs is the envelope's published ``next.argv``.

If this test passes, an agent can use Scutch with nothing but the CLI.
"""

from __future__ import annotations

import json
import re

REPAIR_INPUT = {"customer_id": "cust_456", "permission": "read_orders", "channel": "retail"}


class FreshAgent:
    """Only what a tool-using agent has: argv, stdin, stdout. No state."""

    def __init__(self, cli, recording: str | None = None):
        self.cli = cli
        if recording:
            cli.recording = recording
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
        assert ctx["providers"]["saleor"]["mode"] == "replay"
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


def test_fresh_agent_end_to_end(repair_world):
    cli = repair_world
    agent = FreshAgent(cli, "scenario-repair")

    # 1 — discover the workspace with no prior knowledge.
    ctx = agent.bootstrap()

    # 2 — choose an operation by intent.
    name = agent.find(["customer", "permission", "repair", "ensure"])
    assert name == "saleor.customer.repair"

    # 3 — learn the contract; assert it can answer every question from the brief.
    d = agent.describe(name)
    assert "what does this do" and d["summary"]
    assert d["effect"] == "plan_apply"                       # what can it change
    assert d["input_schema"]["required"]                     # what args
    assert d["invocation"]["argv"][:3] == ["scutch", "run", name]  # how invoked
    assert d["idempotency_key"]                              # repetition behaviour
    assert d["result_schema"]["properties"]                  # what comes back
    assert d["credentials"]["api"] == {"provider": "saleor", "resolution": "runtime"}
    # no secret material or credential-shaped text anywhere in the description
    text = json.dumps(d).lower()
    assert "bearer" not in text
    assert "scutch_saleor_token" not in text
    assert d["credentials"] == {"api": {"provider": "saleor", "resolution": "runtime"}}

    # 4 — a first attempt with only the customer: the agent must learn what else
    #     is required from the response, not from documentation.
    partial = agent.run_op(name, {"customer_id": "cust_456"})
    assert partial["status"] == "needs_input"
    missing = partial["error"]["details"]["missing"]
    assert missing == ["permission"], missing
    assert partial["next"]["argv"][0] == "scutch"
    # the missing-field schema is narrow: only what is actually missing
    assert list(partial["error"]["details"]["schema"]["properties"]) == ["permission"]

    # 5 — complete the template with the canonical inputs it was handed.
    template = dict(partial["next"]["input"], permission="read_orders")
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
    assert applied["result"]["status"] == "applied"
    assert "raw_response" not in applied["result"]
    assert applied["redacted"] == ["raw_response"]
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
    # 8b — exactly one mutation crossed the wire across all of the above.
    assert len([w for w in cli.drain_wire() if w["kind"] == "mutation"]) == 1

    # 9 — the agent never had to ask for a credential.
    assert all("TOKEN" not in t and "token" not in t for t in fresh.transcript)


def test_agent_cannot_talk_itself_into_an_unapproved_mutation(repair_world):
    """Approval cannot be forged, guessed, or reused across plans."""
    cli = repair_world
    agent = FreshAgent(cli, "scenario-repair")
    agent.bootstrap()
    name = "saleor.customer.repair"

    preview = agent.run_op(name, REPAIR_INPUT)
    digest = preview["plan"]["digest"]

    forged = agent.call(
        ["scutch", "run", name, "--input", "@-", "--format", "json",
         "--approve-plan", "sha256:" + "a" * 64],
        stdin=REPAIR_INPUT,
    )
    assert forged["status"] == "needs_approval"
    assert forged["error"]["code"] == "approval_mismatch"

    # Approving the digest of a *different* input set is equally refused.
    other = agent.call(
        ["scutch", "run", name, "--input", "@-", "--format", "json",
         "--approve-plan", digest],
        stdin={**REPAIR_INPUT, "channel": "wholesale"},
    )
    assert other["status"] == "needs_approval"
    assert other["error"]["code"] == "approval_mismatch"

    runs = [json.loads(p.read_text()) for p in (cli.workspace / ".scutch/runs").glob("run_*.json")]
    assert not [r for r in runs if r["phase"] == "applied"]

    # The real digest still works, and only once.
    ok = agent.call(
        ["scutch", "run", name, "--input", "@-", "--format", "json", "--approve-plan", digest],
        stdin=REPAIR_INPUT,
    )
    assert ok["result"]["status"] == "applied"


def test_human_ui_and_agent_perform_equivalent_transitions(repair_world, workspace):
    """One lifecycle: the same inputs produce the same envelope via both surfaces."""
    import os

    from scutch.runner import execute
    from scutch.workspace import load_workspace

    cli = repair_world
    os.environ["SCUTCH_REPLAY"] = "scenario-repair"
    ws = load_workspace(workspace)
    payload = {"customer_id": "cust_456", "permission": "read_orders", "channel": "retail"}

    cli_preview, _, _ = cli.scutch("run", "saleor.customer.repair", "--input", "@-", "--format", "json", stdin=payload)
    ui_preview, _ = execute(ws, "saleor.customer.repair", payload)

    assert cli_preview["status"] == ui_preview["status"] == "needs_approval"
    assert cli_preview["plan"]["digest"] == ui_preview["plan"]["digest"]
    assert cli_preview["next"]["argv"] == ui_preview["next"]["argv"]

    cli_apply, _, _ = cli.scutch(
        "run", "saleor.customer.repair", "--input", "@-", "--format", "json",
        "--approve-plan", cli_preview["plan"]["digest"], "--idempotency-key", "eq-1", stdin=payload,
    )
    assert cli_apply["status"] == "complete"
    assert cli_apply["result"]["status"] == "applied"
    # Both surfaces agree on the decision and the continuation. (A second
    # preview would legitimately re-plan, because each provider instance starts
    # replay from the beginning of the recording — that is not a UI difference.)
    assert ui_preview["plan"] == cli_preview["plan"]
    assert ui_preview["next"] == cli_preview["next"]
    os.environ.pop("SCUTCH_REPLAY", None)
