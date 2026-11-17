"""GMV bases: `docs/gmv-basis.md` §3, as data rather than prose.

The doc is explicit that GMV is not one number and that the basis choice decides
whether an allowance is breached. Each formula is a DuckDB file in this
directory — runnable by hand, and verified against a stored report — and this
operation is the guardrail around them: it validates the inputs, picks the file
the declared basis names, and returns a result a caller can act on without
guessing which basis produced a figure.

Read-only. It computes; it never writes a report.
"""

from __future__ import annotations

import dataclasses
import subprocess
import tempfile
from pathlib import Path
from typing import Annotated, Literal

from scutch import field, operation

Basis = Literal["net-confirmed", "charged", "charged-plus-offplatform", "api-derived"]

#: The query files, one per basis, beside this module.
QUERY_DIR = Path(__file__).parent
QUERIES = {
    "net-confirmed": "net_confirmed.sql",
    "charged": "charged.sql",
    "charged-plus-offplatform": "charged_plus_offplatform.sql",
    "api-derived": "api_derived.sql",
}

Fraction = Annotated[float, field(min=0.0, max=1.0)]
NonNegative = Annotated[float, field(min=0.0)]
Positive = Annotated[float, field(min=0.0)]


@dataclasses.dataclass
class BasisInfo:
    """One GMV basis: the formula, the inputs it needs, and when it applies."""

    basis: str
    formula: str
    requires: list[str]
    use_when: str


@dataclasses.dataclass
class BasesList:
    """The basis table from the doc, as data."""

    bases: list[BasisInfo]
    default: str


@dataclasses.dataclass
class GmvResult:
    """One GMV figure, on a named basis, with the inputs that produced it."""

    basis: str
    our_gmv: float
    formula: str
    query: str
    inputs_used: dict[str, float]


def _run_sql(basis: str, variables: dict[str, float]) -> float:
    """Execute the basis's query in DuckDB and return `our_gmv`.

    DuckDB is the implementation, not the interface: callers see a number and
    the inputs behind it. The `.sql` file stays a standalone artifact, which is
    what lets a human check the arithmetic without running Scutch.
    """
    sql_file = QUERY_DIR / QUERIES[basis]
    if not sql_file.is_file():
        raise FileNotFoundError(f"missing query file: {sql_file}")
    script = "\n".join(
        f"SET VARIABLE {name} = {value};" for name, value in variables.items()
    ) + "\n" + sql_file.read_text()
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as fh:
        fh.write(script)
        path = fh.name
    try:
        out = subprocess.run(
            ["duckdb", "-no-agent", "-csv", "-c", f".read {path}"],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        Path(path).unlink(missing_ok=True)
    if out.returncode != 0:
        raise RuntimeError(f"duckdb failed: {out.stderr.strip()[:200]}")
    rows = [line for line in out.stdout.strip().splitlines() if line]
    if not rows:
        raise RuntimeError("query returned no rows")
    return float(rows[-1].split(",")[-1])


@operation(
    name="saleor.gmv.compute",
    summary="Compute GMV on one of the four contract bases.",
    effect="read",
    external_reads=["docs/gmv-basis.md"],
    transforms_data=True,
    query="operations/bi/net_confirmed.sql",
    examples=[
        {
            "name": "net-confirmed",
            "why": "the default basis, verified against a stored report",
            "input": {
                "basis": "net-confirmed",
                "net": 2967349.6045842674,
                "confirmation": 0.8850600770800272,
                "return_rate": 0.15,
            },
        },
        {
            "name": "api-derived",
            "why": "for customers whose orders never reach Saleor",
            "input": {"basis": "api-derived", "requests": 4283648.67, "requests_per_dollar": 50.0},
        },
    ],
)
def compute_gmv(
    basis: Basis,
    net: NonNegative | None = None,
    charged: NonNegative | None = None,
    confirmation: Fraction | None = None,
    return_rate: Fraction = 0.15,
    off_platform_share: Fraction = 0.0,
    external_payment_uplift: Positive = 1.0,
    requests: NonNegative | None = None,
    requests_per_dollar: Positive = 50.0,
) -> GmvResult:
    """Compute GMV on a declared basis, or refuse with the reason it cannot.

    Each basis needs different inputs; a missing one is an error naming the
    field, never a silent default that would quietly change a money figure.
    """
    required: dict[str, dict[str, float | None]] = {
        "net-confirmed": {"net": net, "confirmation": confirmation, "return_rate": return_rate},
        "charged": {"charged": charged, "external_payment_uplift": external_payment_uplift},
        "charged-plus-offplatform": {
            "charged": charged,
            "net": net,
            "confirmation": confirmation,
            "off_platform_share": off_platform_share,
        },
        "api-derived": {"requests": requests, "requests_per_dollar": requests_per_dollar},
    }
    used = required[basis]
    missing = [name for name, value in used.items() if value is None]
    if missing:
        raise ValueError(
            f"basis {basis!r} needs {', '.join(missing)}; "
            f"the permitted bases are {sorted(QUERIES)}"
        )

    formula = {
        "net-confirmed": "net × confirmation × (1 − returnRate)",
        "charged": "charged × externalPaymentUplift",
        "charged-plus-offplatform": "charged + max(0, net × confirmation − charged) × p",
        "api-derived": "requests ÷ apiRequestsPerGmvDollar",
    }[basis]

    value = _run_sql(basis, {k: float(v) for k, v in used.items()})  # type: ignore[arg-type]
    return GmvResult(
        basis=basis,
        our_gmv=value,
        formula=formula,
        query=f"operations/bi/{QUERIES[basis]}",
        inputs_used={k: float(v) for k, v in used.items()},  # type: ignore[arg-type]
    )


@operation(
    name="saleor.gmv.bases",
    summary="List the GMV bases and the inputs each requires.",
    effect="read",
    examples=[{"name": "all", "why": "which basis for which customer", "input": {}}],
)
def list_bases() -> BasesList:
    """The basis table from the doc, as data: a caller picks, it does not recall."""
    return BasesList(
        bases=[
            BasisInfo(
                basis="net-confirmed",
                formula="net × confirmation × (1 − returnRate)",
                requires=["net", "confirmation", "return_rate"],
                use_when="capture is high; charged understates commerce only modestly",
            ),
            BasisInfo(
                basis="charged",
                formula="charged × externalPaymentUplift",
                requires=["charged", "external_payment_uplift"],
                use_when="unconfirmed orders dominate; charged is a floor",
            ),
            BasisInfo(
                basis="charged-plus-offplatform",
                formula="charged + max(0, net × confirmation − charged) × p",
                requires=["charged", "net", "confirmation", "off_platform_share"],
                use_when="phantom orders AND off-platform settlement at once",
            ),
            BasisInfo(
                basis="api-derived",
                formula="requests ÷ apiRequestsPerGmvDollar",
                requires=["requests", "requests_per_dollar"],
                use_when="orders never reach Saleor; GMV inferred from API usage",
            ),
        ],
        default="net-confirmed",
    )
