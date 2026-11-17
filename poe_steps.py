"""Workflow steps, as Poe script tasks run in-process.

Every step here is a thin, explicit invocation: it names the operation, hands it
typed inputs, and prints the envelope. Running in Poe's own Python process is
what makes the seams doubleable — ``http`` is a module attribute, ``exec`` is
``subprocess.run``, and a provider is an object. No shims, no string parsing.

The point of the convention (steps are Poe script tasks, never shell strings) is
that a feature can assert *exactly* what a step would send:

    assert calls == [["gcloud", "auth", "application-default", "print-access-token"]]

A shell string cannot be asserted that precisely, because ``sh`` re-joins argv.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


# --------------------------------------------------------------------------
# Plumbing


def _run(operation: str, inputs: dict[str, Any], **kwargs: Any) -> int:
    """Execute one operation and print its envelope."""
    from scutch.runner import execute
    from scutch.workspace import load_workspace

    root = Path.cwd()
    ws = load_workspace(root)
    env, code = execute(ws, operation, inputs, **kwargs)
    print(json.dumps(env, indent=2, default=str))
    return code


# --------------------------------------------------------------------------
# Steps


def list_customers() -> int:
    """A read-only step: no approval, no mutation."""
    return _run("saleor.customer.list", {})


def repair_customer(customer_id: str, permission: str, channel: str = "retail") -> int:
    """A consequential step: it plans, so a caller sees the effect before it lands."""
    return _run(
        "saleor.customer.repair",
        {"customer_id": customer_id, "permission": permission, "channel": channel},
    )


def gmv_net_confirmed(net: str, confirmation: str, return_rate: str = "0.15") -> int:
    """The GMV basis as a step, so a report can quote a figure with its inputs."""
    return _run(
        "saleor.gmv.compute",
        {
            "basis": "net-confirmed",
            "net": float(net),
            "confirmation": float(confirmation),
            "return_rate": float(return_rate),
        },
    )


def ga_token() -> int:
    """The exec seam, made explicit.

    This is the shape 13 scripts in saleor-bi use to reach Google. As a script
    task the call is a list — which is why a feature can assert it exactly, and
    why a test can replace it without a PATH shim.
    """
    proc = subprocess.run(
        ["gcloud", "auth", "application-default", "print-access-token"],
        capture_output=True,
        text=True,
        check=False,
    )
    envelope = {
        "kind": "scutch.envelope/v1",
        "operation": "google.adc.token",
        "status": "complete" if proc.returncode == 0 else "failed",
        "input": {},
        "result": {"token_present": bool(proc.stdout.strip())},
        "next": {"argv": ["scutch", "run", "google.adc.token", "--format", "json"]},
    }
    print(json.dumps(envelope, indent=2))
    return proc.returncode


def doctor() -> int:
    """Convention checks, as a step: a workflow can refuse to run in a bad repo."""
    from scutch.discovery import doctor as run_doctor
    from scutch.workspace import load_workspace

    report = run_doctor(load_workspace(Path.cwd()))
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1
