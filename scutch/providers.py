"""Provider resolution.

A provider is the seam between an operation and an external service. Operators
declare *references* (``credentials={"api": ProviderRef("saleor")}``); the
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
    data_dir: Path | None = None      # fixture seeds (read-only)

    def public(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "mode": self.mode,
            "configured": True,
            "secret_present": self.secret is not None,
        }

    def load(self) -> Any:
        if self.factory is None:
            raise ProviderError(
                f"provider {self.name!r} has no implementation",
                hint=f"define @provider(name={self.name!r}) in providers/{self.name}.py",
            )
        return self.factory(self)


def resolve(cfg: ProviderConfig, root: Path) -> Resolution:
    if cfg.mode == "fixture":
        data_dir = root / (cfg.data or f"fixtures/{cfg.name}")
        if not data_dir.is_dir():
            raise ProviderError(
                f"provider {cfg.name!r} is in fixture mode but {data_dir} does not exist"
            )
        factory = load_implementation(cfg.name, root)
        return Resolution(cfg.name, "fixture", factory, cfg, root=root, data_dir=data_dir)

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
    spec = importlib.util.spec_from_file_location(f"scutch_provider_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    fn = getattr(module, "make", None)
    if fn is None:
        raise ProviderError(
            f"providers/{name}.py defines no make(resolution) factory",
        )
    return fn


def fixture_files(res: Resolution) -> dict[str, Any]:
    """Load every JSON fixture under the provider's read-only seed directory."""
    assert res.data_dir is not None
    out: dict[str, Any] = {}
    for f in sorted(res.data_dir.glob("*.json")):
        out[f.stem] = json.loads(f.read_text())
    return out


def state_path(res: Resolution) -> Path:
    """Where fixture-mode mutations persist.

    Committed fixtures stay pristine seeds; applied effects land in
    ``.scutch/state/<provider>.json`` so a workspace can show a real before and
    after. Deleting ``.scutch/`` resets the demo without touching fixtures.
    """
    assert res.root is not None
    return res.root / ".scutch" / "state" / f"{res.name}.json"


def require(res: Resolution, ref: ProviderRef) -> None:
    if not isinstance(ref, ProviderRef):  # pragma: no cover - defensive
        raise ProviderError(f"credentials must map to ProviderRef, got {type(ref).__name__}")
