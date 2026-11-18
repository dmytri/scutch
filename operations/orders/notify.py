"""Fulfil an order and tell an external service, through the same seam."""
import json


def fulfil_and_notify(world, order_id: str, url: str):
    """One step with three effects: a read, a write and an http call."""
    data = json.loads(world("read", path="ledger.json"))
    order = next((o for o in data["orders"] if o["id"] == order_id), None)
    if order is None:
        raise ValueError(f"no order {order_id!r}")
    order["status"] = "fulfilled"
    world("write", path="ledger.json", text=json.dumps(data, indent=2, sort_keys=True) + "\n")
    world("http", url=url, method="POST", body=json.dumps({"order": order_id, "event": "fulfilled"}))
    return {"order_id": order_id, "notified": url}
