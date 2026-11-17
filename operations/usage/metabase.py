"""Monthly order counts, from a Metabase saved-question export.

A saved question can be downloaded as CSV or JSON. This reads the JSON export,
which is what an analyst actually hands over — no token, no session, no API key,
and therefore nothing credential-shaped anywhere near the plan.

The row shape is asserted rather than guessed: a row without `month` or `orders`
raises, because a missing column means the question changed and a zero would be
an invented observation.
"""
import json


def orders_per_month(io, export: str, customer: str = ""):
    """Monthly order counts, oldest first, from one export file."""
    raw = io("read", path=export)
    payload = json.loads(raw)
    if isinstance(payload, dict):
        payload = payload.get("rows") or payload.get("data", {}).get("rows") or []

    rows = [
        {"month": str(r["month"]), "orders": int(r["orders"])}
        for r in sorted(payload, key=lambda r: str(r["month"]))
    ]
    return {"customer": customer, "rows": rows, "count": len(rows)}
