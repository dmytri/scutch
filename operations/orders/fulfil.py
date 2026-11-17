"""Fulfil one placed order."""
import json


def fulfil_order(io, order_id: str):
    """Mark a placed order fulfilled. Does nothing if it cannot be fulfilled."""
    data = json.loads(io("read", path="ledger.json"))
    order = next((o for o in data["orders"] if o["id"] == order_id), None)
    if order is None:
        raise ValueError(f"no order {order_id!r}")
    if order["status"] != "placed":
        return {"order_id": order_id, "status": order["status"], "changed": False}
    order["status"] = "fulfilled"
    io("write", path="ledger.json", text=json.dumps(data, indent=2, sort_keys=True) + "\n")
    return {"order_id": order_id, "status": "fulfilled", "changed": True}
