"""Secrets are names. The value never enters a plan, a digest or a repr."""
from pathlib import Path

import pytest

from scutch import IO
from scutch.secrets import MissingSecret, Vault, resolve


def test_env_reference_resolves(monkeypatch):
    monkeypatch.setenv("SOME_TOKEN", "abc")
    assert resolve("env:SOME_TOKEN") == "abc"
    assert resolve("SOME_TOKEN") == "abc"     # bare name means env


def test_a_missing_secret_names_itself_and_shows_no_value(monkeypatch):
    monkeypatch.delenv("NOPE_TOKEN", raising=False)
    with pytest.raises(MissingSecret) as exc:
        resolve("env:NOPE_TOKEN")
    assert "NOPE_TOKEN" in str(exc.value)
    assert exc.value.hint


def test_a_failing_command_does_not_leak_its_stderr():
    with pytest.raises(MissingSecret) as exc:
        resolve("cmd:echo hunter2 >&2; exit 1")
    assert "hunter2" not in str(exc.value)


def test_a_file_reference_reads_the_file(tmp_path):
    path = tmp_path / "tok"
    path.write_text("file-secret\n")
    assert resolve(f"file:{path}") == "file-secret"


def test_a_secret_is_never_an_effect(tmp_path):
    io = IO(root=tmp_path, vault=Vault({"env:T": "s3cret"}))
    assert io("secret", name="env:T") == "s3cret"
    assert io.effects == []
    assert io.observations


def test_a_dry_run_does_not_resolve_a_secret(tmp_path):
    io = IO(root=tmp_path, execute=False)
    assert io("secret", name="env:NOT_SET") == "<unresolved>"


def test_the_vault_repr_holds_no_values():
    assert repr(Vault({"env:A": "hunter2"})) == "Vault(1 secret(s))"


def test_a_secret_resolves_once_per_run(monkeypatch):
    calls = []

    def fake(reference):
        calls.append(reference)
        return "v"

    monkeypatch.setattr("scutch.secrets.resolve", fake)
    vault = Vault()
    vault("env:X")
    vault("env:X")
    assert len(calls) == 1
