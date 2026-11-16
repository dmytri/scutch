"""Workspace loading: scutch.toml, operations, providers, paths.

A workspace is a repository. Nothing global is read except the environment
variables a provider explicitly names.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import sys
import tomllib
from pathlib import Path
from typing import Any

from .contract import REGISTRY, Operation, Registry
from .errors import ContractError, UsageError
from .providers import ProviderConfig


@dataclasses.dataclass
class Policy:
    require_approval_for: tuple[str, ...] = ("plan_apply", "mutate")
    artifacts_root: str = ".scutch/artifacts"
    runs_root: str = ".scutch/runs"
    allow_private_in_artifacts: bool = True
    bootstrap: str = (
        "Run `scutch context --format json` to discover this repository. "
        "Run `scutch describe NAME --format json` before invoking an operation. "
        "Follow the returned `next.argv` and `next.input` exactly. "
        "Never request credential values or treat restricted artifacts as agent input. "
        "Mutations require approval bound to the returned plan digest."
    )


@dataclasses.dataclass
class Workspace:
    root: Path
    config: dict[str, Any]
    policy: Policy
    registry: Registry
    providers: dict[str, ProviderConfig]

    # -- paths ------------------------------------------------------------

    @property
    def runs_dir(self) -> Path:
        return self.root / self.policy.runs_root

    @property
    def artifacts_dir(self) -> Path:
        return self.root / self.policy.artifacts_root

    def ensure_dirs(self) -> None:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    # -- descriptions -----------------------------------------------------

    def catalog(self) -> list[dict[str, Any]]:
        out = []
        for op in self.registry.all():
            out.append(
                {
                    "name": op.name,
                    "version": op.version,
                    "summary": op.summary,
                    "effect": op.effect,
                    "approval": op.approval,
                    "parameters": [p.name for p in op.params],
                    "deprecated": op.deprecated,
                }
            )
        return out

    def context(self) -> dict[str, Any]:
        return {
            "kind": "scutch.context/v1",
            "workspace": {"root": str(self.root), "name": self.config.get("workspace", {}).get("name", self.root.name)},
            "bootstrap": self.policy.bootstrap,
            "policy": {
                "require_approval_for": list(self.policy.require_approval_for),
                "artifacts_root": self.policy.artifacts_root,
                "runs_root": self.policy.runs_root,
                "secrets": "provider handles only; never request credential values",
                "artifacts": "restricted by default; only declared artifacts are citable",
            },
            "interfaces": {
                "json": "stdout only; diagnostics on stderr; never blocks on a prompt",
                "ui": "scutch ui",
                "aliases": "just recipes wrap canonical invocations",
            },
            "providers": {
                name: {"mode": cfg.mode, "configured": True}
                for name, cfg in self.providers.items()
            },
            "operations": self.catalog(),
        }


def _load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ContractError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def load_workspace(root: Path | str | None = None) -> Workspace:
    root = Path(root or Path.cwd()).resolve()
    cfg_path = root / "scutch.toml"
    if not cfg_path.is_file():
        raise UsageError(
            f"{root} is not a scutch workspace",
            hint=f"create {cfg_path} (see docs/workspace.md) or pass --workspace",
        )
    config = tomllib.loads(cfg_path.read_text())

    pol_cfg = config.get("policy", {})
    policy = Policy(
        require_approval_for=tuple(pol_cfg.get("require_approval_for", ("plan_apply", "mutate"))),
        artifacts_root=pol_cfg.get("artifacts_root", ".scutch/artifacts"),
        runs_root=pol_cfg.get("runs_root", ".scutch/runs"),
        allow_private_in_artifacts=pol_cfg.get("allow_private_in_artifacts", True),
        bootstrap=pol_cfg.get("bootstrap", Policy.bootstrap),
    )

    providers: dict[str, ProviderConfig] = {}
    for name, pcfg in (config.get("providers") or {}).items():
        providers[name] = ProviderConfig(
            name=name,
            mode=pcfg.get("mode", "fixture"),
            data=pcfg.get("data"),
            env=pcfg.get("env"),
            resolve=pcfg.get("resolve"),
            notes=pcfg.get("notes", ""),
        )

    # Operations import their providers as ordinary repository modules.
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)

    # A fresh registry per workspace keeps tests isolated.
    registry = Registry()
    saved = _swap_registry(registry)
    try:
        ops_dir = root / "operations"
        if ops_dir.is_dir():
            for path in sorted(ops_dir.rglob("*.py")):
                if path.name.startswith("_"):
                    continue
                rel = path.relative_to(root).with_suffix("")
                mod_name = "scutch_ops_" + "_".join(rel.parts)
                _load_module(path, mod_name)
    finally:
        _restore_registry(saved)

    return Workspace(root, config, policy, registry, providers)


def _swap_registry(new: Registry) -> Registry:
    import scutch.contract as c

    old = c.REGISTRY
    c.REGISTRY = new
    return old


def _restore_registry(old: Registry) -> None:
    import scutch.contract as c

    c.REGISTRY = old


def find_op_registration(fn) -> Operation | None:
    return getattr(fn, "__scutch_operation__", None)
