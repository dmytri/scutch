"""The seam: one door, four kinds, and a double that leans on nothing."""
import json

import pytest

from scutch import IO, Unanswered, UnknownEffect, key


def test_a_write_happens_only_when_executing(tmp_path):
    IO(root=tmp_path, execute=False)("write", path="a.txt", text="x")
    assert not (tmp_path / "a.txt").exists()
    IO(root=tmp_path)("write", path="a.txt", text="x")
    assert (tmp_path / "a.txt").read_text() == "x"


def test_an_unknown_kind_is_refused(tmp_path):
    with pytest.raises(UnknownEffect):
        IO(root=tmp_path)("delete", path="a.txt")


def test_the_double_answers_from_the_canned_list(tmp_path):
    io = IO(root=tmp_path, answers=["first", "second"])
    assert io("read", path="x") == "first"
    assert io("read", path="y") == "second"


def test_a_write_consumes_no_answer(tmp_path):
    """A write returns nothing, so it must not eat a canned reply."""
    io = IO(root=tmp_path, answers=["only"])
    io("write", path="a.txt", text="x")
    assert io("read", path="a.txt") == "only"


def test_running_out_of_answers_names_the_unanswered_ask(tmp_path):
    io = IO(root=tmp_path, answers=[])
    with pytest.raises(Unanswered) as exc:
        io("exec", argv=["true"])
    assert '"kind":"exec"' in str(exc.value)


def test_asks_are_recorded_in_order_and_count_repeats(tmp_path):
    io = IO(root=tmp_path, answers=["", ""])
    io("exec", argv=["a"])
    io("exec", argv=["a"])
    assert io.asks == [key("exec", {"argv": ["a"]})] * 2


def test_effects_exclude_reads(tmp_path):
    io = IO(root=tmp_path, answers=["", "", ""])
    io("read", path="a")
    io("exec", argv=["x"])
    io("http", url="https://x")
    assert [json.loads(e)["kind"] for e in io.effects] == ["exec", "http"]


def test_a_missing_file_is_a_sentence_not_a_traceback(tmp_path):
    from scutch import Missing

    with pytest.raises(Missing) as exc:
        IO(root=tmp_path)("read", path="nope.json")
    assert "no file nope.json" in str(exc.value)


def test_a_failing_command_reports_its_exit_code(tmp_path):
    from scutch import Failed

    with pytest.raises(Failed) as exc:
        IO(root=tmp_path)("exec", argv=["sh", "-c", "echo boom >&2; exit 3"])
    assert "exited 3" in str(exc.value)
    assert "boom" in str(exc.value)


def test_an_unreachable_url_is_a_sentence(tmp_path):
    from scutch import Failed

    with pytest.raises(Failed) as exc:
        IO(root=tmp_path)("http", url="https://nothing.invalid/x", method="POST", body="")
    assert "cannot reach" in str(exc.value)


def test_a_read_with_the_double_never_touches_the_disk(tmp_path):
    io = IO(root=tmp_path, answers=["canned"])
    assert io("read", path="absent.json") == "canned"
