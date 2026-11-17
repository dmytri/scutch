"""Vercel hosting counters, and the equivalent-requests weights.

The weights are the contract: one equivalent request is priced to cost what one
Saleor API request costs, so hosting and API usage draw down a single
entitlement. They live here, next to the arithmetic, rather than in a workflow
description — see `~/saleor-bi/extensions/reports/vercel_equivalent_requests.ts`,
which this mirrors.

The source is Vercel's FOCUS billing export: one JSON record per charge period,
per service. Those exports are what the account actually bills from, so this
reads them rather than calling an API that needs a token we do not have.
"""
import json

#: Equivalent-request weights. Any change here changes every customer's bill.
EDGE_REQUESTS = 6
ISR_WRITES = 6
TRANSFER_GB = 6000
IMAGE_TRANSFORMS = 3

GB = 1000**3

FORMULA = (
    "CDN Requests / 6 + ISR Writes / 6 "
    "+ (Fast Data Transfer GB + Fast Origin Transfer GB) x 6,000 "
    "+ Image Transformations x 3"
)

#: Vercel's service names, mapped to the components the formula uses. A name not
#: listed here is ignored rather than guessed at.
CDN_NAMES = ("CDN Requests", "Edge Requests")
ISR_NAME = "ISR Writes"
TRANSFER_NAMES = ("Fast Data Transfer", "Fast Origin Transfer")
IMAGE_NAME = "Image Optimization Transformation"


def _billing_counters(io, export: str):
    """Quantities per billing SKU, summed over an export.

    A FOCUS export is JSON Lines: one record per period per service. Blank and
    unparseable lines are counted and reported, never silently skipped — a
    truncated download should look truncated.
    """
    raw = io("read", path=export)
    totals: dict[str, float] = {}
    records = 0
    bad = 0
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            bad += 1
            continue
        records += 1
        name = record.get("ServiceName") or ""
        totals[name] = totals.get(name, 0.0) + float(record.get("ConsumedQuantity") or 0)
    return {"totals": totals, "records": records, "unparseable": bad}


def equivalent_requests(io, export: str):
    """Hosting converted into equivalent requests, with every component shown.

    Each component is reported raw *and* converted, so a customer conversation
    can explain the number rather than assert it.
    """
    counted = _billing_counters(io, export=export)
    totals = counted["totals"]

    edge = sum(totals.get(name, 0.0) for name in CDN_NAMES)
    isr = totals.get(ISR_NAME, 0.0)
    transfer_gb = sum(totals.get(name, 0.0) for name in TRANSFER_NAMES) / GB
    images = totals.get(IMAGE_NAME, 0.0)

    components = [
        {
            "name": "CDN requests",
            "raw": edge,
            "weight": f"/ {EDGE_REQUESTS}",
            "equivalent": edge / EDGE_REQUESTS,
        },
        {
            "name": "ISR writes",
            "raw": isr,
            "weight": f"/ {ISR_WRITES}",
            "equivalent": isr / ISR_WRITES,
        },
        {
            "name": "Transfer GB",
            "raw": round(transfer_gb, 3),
            "weight": f"x {TRANSFER_GB:,}",
            "equivalent": transfer_gb * TRANSFER_GB,
        },
        {
            "name": "Image transforms",
            "raw": images,
            "weight": f"x {IMAGE_TRANSFORMS}",
            "equivalent": images * IMAGE_TRANSFORMS,
        },
    ]
    total = sum(c["equivalent"] for c in components)
    return {
        "export": export,
        "records": counted["records"],
        "unparseable": counted["unparseable"],
        "formula": FORMULA,
        "components": components,
        # Floored: usage is billed in whole requests and only whole requests are
        # charged. The components keep their fractions so a reader can see where
        # the remainder went rather than wonder about it.
        # Rounded, not truncated: truncating makes the total disagree with the
        # sum of the components, which reads as an arithmetic error.
        "equivalent_requests": round(total),
        "equivalent_requests_exact": total,
    }
