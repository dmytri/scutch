"""Weekly API usage for a Vercel project."""
import json


def requests_per_week(io, project: str, team: str = "acme"):
    """Weekly request counts for one project, oldest first."""
    raw = io(
        "http",
        url=f"https://api.vercel.com/v1/usage?projectId={project}&granularity=week",
        method="GET",
        headers={"Authorization": f"Bearer {team}-token"},
    )
    rows = json.loads(raw)["rows"]
    out = [
        {"week_start": r["week_start"], "requests": int(r["requests"])}
        for r in sorted(rows, key=lambda r: r["week_start"])
    ]
    return {"project": project, "rows": out, "count": len(out)}
