"""The seam: one door, four kinds, and a double that leans on nothing."""
import json

import pytest

from scutch import World, Unanswered, UnknownEffect, key


def test_a_write_happens_only_when_executing(tmp_path):
    World(root=tmp_path, execute=False)("write", path="a.txt", text="x")
    assert not (tmp_path / "a.txt").exists()
    World(root=tmp_path)("write", path="a.txt", text="x")
    assert (tmp_path / "a.txt").read_text() == "x"


def test_an_unknown_kind_is_refused(tmp_path):
    with pytest.raises(UnknownEffect):
        World(root=tmp_path)("delete", path="a.txt")


def test_the_double_answers_from_the_canned_list(tmp_path):
    world = World(root=tmp_path, answers=["first", "second"])
    assert world("read", path="x") == "first"
    assert world("read", path="y") == "second"


def test_a_write_consumes_no_answer(tmp_path):
    """A write returns nothing, so it must not eat a canned reply."""
    world = World(root=tmp_path, answers=["only"])
    world("write", path="a.txt", text="x")
    assert world("read", path="a.txt") == "only"


def test_running_out_of_answers_names_the_unanswered_ask(tmp_path):
    world = World(root=tmp_path, answers=[])
    with pytest.raises(Unanswered) as exc:
        world("exec", argv=["true"])
    assert '"kind":"exec"' in str(exc.value)


def test_asks_are_recorded_in_order_and_count_repeats(tmp_path):
    world = World(root=tmp_path, answers=["", ""])
    world("exec", argv=["a"])
    world("exec", argv=["a"])
    assert world.asks == [key("exec", {"argv": ["a"]})] * 2


def test_effects_exclude_observations(tmp_path):
    """Only what changes the world is an effect.

    `exec` used to be an effect, which meant a session token it returned was
    printed in the plan and hashed into the digest — so the same plan hashed
    differently every run. A command's answer is data, not a change.
    """
    world = World(root=tmp_path, answers=["", "", ""])
    world("read", path="a")
    world("exec", argv=["x"])
    world("http", url="https://x")
    assert [json.loads(e)["kind"] for e in world.effects] == ["http"]
    assert [json.loads(o)["kind"] for o in world.observations] == ["read", "exec"]


def test_a_credential_in_headers_is_never_hashed_or_shown(tmp_path):
    world = World(root=tmp_path, answers=["ok"])
    world("http", url="https://x", method="POST", body="",
       headers={"Authorization": "Bearer super-secret"})
    shown = world.effects[0]
    assert "super-secret" not in shown
    assert "<redacted>" in shown


def test_a_missing_file_is_a_sentence_not_a_traceback(tmp_path):
    from scutch import Missing

    with pytest.raises(Missing) as exc:
        World(root=tmp_path)("read", path="nope.json")
    assert "no file nope.json" in str(exc.value)


def test_a_failing_command_reports_its_exit_code(tmp_path):
    from scutch import Failed

    with pytest.raises(Failed) as exc:
        World(root=tmp_path)("exec", argv=["sh", "-c", "echo boom >&2; exit 3"])
    assert "exited 3" in str(exc.value)
    assert "boom" in str(exc.value)


def test_an_unreachable_url_is_a_sentence(tmp_path):
    from scutch import Failed

    with pytest.raises(Failed) as exc:
        World(root=tmp_path)("http", url="https://nothing.invalid/x", method="POST", body="")
    assert "cannot reach" in str(exc.value)


def test_a_read_with_the_double_never_touches_the_disk(tmp_path):
    world = World(root=tmp_path, answers=["canned"])
    assert world("read", path="absent.json") == "canned"
