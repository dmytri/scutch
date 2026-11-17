"""Order counts per week, from a Metabase saved question.

The seam is used for both steps: `exec` to get a Metabase session token, then
`http` to run the saved question. Both are doubled by the same rule as any other
effect, which is the point — a dashboard query and a curl are the same kind of
thing to this runner.
"""
import json


def orders_per_week(io, customer: str, question: int = 42):
    """Weekly order counts for one customer, oldest first."""
    token = io(
        "exec",
        argv=[
            "curl", "-s", "-X", "POST", "http://metabase.internal/api/session",
            "-H", "Content-Type: application/json",
            "-d", json.dumps({"username": "analyst"}),
        ],
    )
    session = json.loads(token)["id"]

    body = io(
        "http",
        url=f"http://metabase.internal/api/card/{question}/query/json",
        method="POST",
        body=json.dumps({"parameters": [{"type": "category", "value": customer}]}),
        headers={"X-Metabase-Session": session},
    )
    rows = json.loads(body)["rows"]
    out = [
        {"week_start": r["week_start"], "orders": int(r["orders"])}
        for r in sorted(rows, key=lambda r: r["week_start"])
    ]
    return {"customer": customer, "rows": out, "count": len(out)}
