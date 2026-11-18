"""Fulfil every placed order in one plan."""
import json


def fulfil_all(world):
    """One approval covers every outstanding order, as a single change."""
    data = json.loads(world("read", path="ledger.json"))
    placed = [o for o in data["orders"] if o["status"] == "placed"]
    if not placed:
        return {"fulfilled": [], "remaining": 0}
    for order in placed:
        order["status"] = "fulfilled"
    world("write", path="ledger.json", text=json.dumps(data, indent=2, sort_keys=True) + "\n")
    remaining = len([o for o in data["orders"] if o["status"] == "placed"])
    return {"fulfilled": [o["id"] for o in placed], "remaining": remaining}
