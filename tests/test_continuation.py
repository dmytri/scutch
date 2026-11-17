"""The double proves one thing: the continuation reaches the next ask.

No test here asserts what an answer contained. Answers are permission slips: they
exist so the code can walk to its next call, and the sequence of calls is the
claim.
"""
import json

import pytest

from scutch import IO, Unanswered, key


def kinds(io):
    return [json.loads(ask)["kind"] for ask in io.asks]


def test_a_three_effect_operation_asks_read_write_http(ws, ops):
    io = IO(root=ws, answers=[(ws / "ledger.json").read_text(), "ok"])
    ops["orders.notify"].fn(io, order_id="ord_a1", url="https://hooks.example/x")
    assert kinds(io) == ["read", "write", "http"]


def test_an_answer_steers_the_next_ask(ws, ops):
    """The stub is not proving a value; it decides which call comes next."""
    io = IO(root=ws, answers=[
        json.dumps({"orders": [{"id": "ord_a1", "status": "placed"}]}),
        "ok",
    ])
    ops["orders.fulfil"].fn(io, order_id="ord_a1")
    assert kinds(io) == ["read", "write"]
    written = json.loads(io.asks[1])
    assert "fulfilled" in written["text"]


def test_one_answer_short_names_the_ask_that_went_unanswered(ws, ops):
    io = IO(root=ws, answers=[])
    with pytest.raises(Unanswered) as exc:
        ops["orders.fulfil"].fn(io, order_id="ord_a1")
    assert '"kind":"read"' in str(exc.value)


def test_extra_answers_are_visible_so_a_lost_ask_is_noticed(ws, ops):
    io = IO(root=ws, answers=[(ws / "ledger.json").read_text(), "unused"])
    ops["orders.list"].fn(io)
    assert io.answers == ["unused"]


def test_the_plan_covers_http_as_well_as_write(ws, ops):
    from scutch import plan

    env = plan(ops["orders.notify"].fn, root=ws,
               inputs={"order_id": "ord_a1", "url": "https://hooks.example/x"},
               answers=[(ws / "ledger.json").read_text(), "ok"])
    assert [json.loads(e)["kind"] for e in env["effects"]] == ["write", "http"]
