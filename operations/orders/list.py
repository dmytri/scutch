"""List orders, optionally filtered by status."""
import json


def list_orders(world, status: str = ""):
    """Read the ledger and return every order, or those in one status."""
    orders = json.loads(world("read", path="ledger.json"))["orders"]
    if status:
        orders = [o for o in orders if o["status"] == status]
    return {"orders": orders, "count": len(orders)}
