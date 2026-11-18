"""Secrets are names. The value never enters a plan, a digest or a repr."""
from pathlib import Path

import pytest

from scutch import World
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


def test_a_secret_is_never_an_effect(tmp_path):
    world = World(root=tmp_path, vault=Vault({"env:T": "s3cret"}))
    assert world("secret", name="env:T") == "s3cret"
    assert world.effects == []
    assert world.observations


def test_a_dry_run_does_not_resolve_a_secret(tmp_path):
    world = World(root=tmp_path, execute=False)
    assert world("secret", name="env:NOT_SET") == "<unresolved>"


def test_the_vault_repr_holds_no_values():
    assert repr(Vault({"env:A": "hunter2"})) == "Vault(1 secret(s))"


def test_a_vault_can_be_given_a_value_directly():
    """The only way to run a secret-bearing operation without the environment."""
    assert Vault({"env:X": "v"})("env:X") == "v"
