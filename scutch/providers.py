"""Provider resolution.

A provider is the seam between an operation and an external service. Operators
declare *references* (``credentials={"ledger": ProviderRef("ledger")}``); the
runtime resolves them. Credential values never enter an envelope, a run record
or a description — only the provider handle does.

Resolution order, per provider declared in ``scutch.toml``:

1. ``mode = "fixture"`` — the provider is backed by files under
   ``fixtures/<provider>/``. Deterministic, credential-free, used by the
   example operation and the whole test suite.
2. ``mode = "live"`` — the provider resolves a secret from the environment
   (``SCUTCH_<PROVIDER>_TOKEN``) or a command (``resolve = "cmd:..."``). The
   value is handed to the worker only, and is never summarised back.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import sys
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable

from .contract import ProviderRef
from .errors import ProviderError


@dataclasses.dataclass
class ProviderConfig:
    name: str
    mode: str = "fixture"
    data: str | None = None
    env: str | None = None
    resolve: str | None = None
    notes: str = ""


@dataclasses.dataclass
class Resolution:
    """What a worker gets. ``secret`` is a real value: never serialise this."""

    name: str
    mode: str
    factory: Callable[[Resolution], Any] | None
    config: ProviderConfig
    secret: str | None = None
    root: Path | None = None          # workspace root
    data_dir: Path | None = None      # recorded exchanges (read-only)

    def public(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "mode": self.mode,
            "configured": True,
            "secret_present": self.secret is not None,
        }

    _instance: Any = None

    def load(self) -> Any:
        """Build the provider object once per resolution.

        Rebuilding per call would reset any per-connection state (a replay
        cursor, a transaction, a session) between two calls of the same
        operation — which silently changes what the operation observes.
        """
        if self._instance is None:
            if self.factory is None:
                raise ProviderError(
                    f"provider {self.name!r} has no implementation",
                    hint=f"define @provider(name={self.name!r}) in providers/{self.name}.py",
                )
            self._instance = self.factory(self)
        return self._instance


def resolve(cfg: ProviderConfig, root: Path) -> Resolution:
    if cfg.mode == "replay":
        data_dir = root / (cfg.data or f"fixtures/{cfg.name}")
        if not data_dir.is_dir():
            raise ProviderError(
                f"provider {cfg.name!r} is in replay mode but {data_dir} does not exist"
            )
        factory = load_implementation(cfg.name, root)
        return Resolution(cfg.name, "replay", factory, cfg, root=root, data_dir=data_dir)

    # `local` has nothing to authenticate: a file, an in-process service. It
    # exists so a demo need not pretend to be a remote API, and so "this run
    # touches no network" has an honest answer that is not "replay".
    if cfg.mode == "local":
        factory = load_implementation(cfg.name, root)
        return Resolution(cfg.name, "local", factory, cfg, root=root)

    if cfg.mode not in ("live",):
        raise ProviderError(
            f"provider {cfg.name!r} has unknown mode {cfg.mode!r}",
            hint="mode must be 'replay', 'live' or 'local'",
        )

    secret = None
    if cfg.env:
        secret = os.environ.get(cfg.env) or None
    elif cfg.resolve and cfg.resolve.startswith("cmd:"):
        try:
            secret = subprocess.run(
                cfg.resolve[4:], shell=True, capture_output=True, text=True, check=True
            ).stdout.strip() or None
        except subprocess.CalledProcessError as exc:
            raise ProviderError(
                f"provider {cfg.name!r} secret command failed",
                hint=exc.stderr.strip()[:200] if exc.stderr else None,
            ) from exc
    if secret is None:
        raise ProviderError(
            f"provider {cfg.name!r} is not resolvable",
            hint=f"set {cfg.env} (or switch to fixture mode in scutch.toml)",
        )
    factory = load_implementation(cfg.name, root)
    return Resolution(cfg.name, "live", factory, cfg, secret=secret, root=root)


def load_implementation(name: str, root: Path) -> Callable[[Resolution], Any] | None:
    path = root / "providers" / f"{name}.py"
    if not path.is_file():
        return None
    module_name = f"scutch_provider_{name}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Register before executing: `@dataclass` resolves annotations by looking the
    # module up in `sys.modules`, so a module that is exec'd without registering
    # fails on any dataclass with annotated fields.
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    fn = getattr(module, "make", None)
    if fn is None:
        raise ProviderError(
            f"providers/{name}.py defines no make(resolution) factory",
        )
    return fn


def wire_log(root: Path) -> list[dict[str, Any]]:
    """Requests the providers have sent in this workspace, in order.

    A workspace-level sink rather than per-instance state, so a caller does not
    have to hold the exact Resolution the runner used.
    """
    return list(_WIRE.get(str(root), []))


def record_wire(root: Path, entry: dict[str, Any]) -> None:
    _WIRE.setdefault(str(root), []).append(entry)


def clear_wire(root: Path) -> None:
    _WIRE.pop(str(root), None)


_WIRE: dict[str, list[dict[str, Any]]] = {}


def replay_data(res: Resolution) -> dict[str, Any]:
    """Recorded HTTP exchanges, merged from the provider's fixture directory.

    Each file is::

        {"exchanges": [{"name": "...", "request": {"document": "...",
                        "variables": {...}}, "response": {...}}, ...]}
    """
    assert res.data_dir is not None
    exchanges: list[dict[str, Any]] = []
    # SCUTCH_REPLAY selects one recording. Merging every cassette in a directory
    # would let unrelated scenarios' requests shadow each other.
    selected = os.environ.get("SCUTCH_REPLAY")
    files = [res.data_dir / f"{selected}.json"] if selected else sorted(res.data_dir.glob("*.json"))
    for f in files:
        if not f.is_file():
            raise ProviderError(
                f"no recording {selected!r} in {res.data_dir}",
                hint="check the scenario's `Given the recording ...` name",
            )
        payload = json.loads(f.read_text())
        exchanges.extend(payload.get("exchanges", []))
    if not exchanges:
        raise ProviderError(
            f"provider {res.name!r} has no recorded exchanges in {res.data_dir}",
            hint="record one with `scutch record`, or point data at another directory",
        )
    return {"exchanges": exchanges}


def require(res: Resolution, ref: ProviderRef) -> None:
    if not isinstance(ref, ProviderRef):  # pragma: no cover - defensive
        raise ProviderError(f"credentials must map to ProviderRef, got {type(ref).__name__}")
