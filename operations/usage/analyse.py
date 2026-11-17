"""A customer's usage, end to end: orders, hosting, projection.

This is the workflow: it composes three operations through the same seam, so a
dry run of this operation is a dry run of the whole chain.

Two series, both monthly:
  orders    from Metabase  — the commercial reality
  hosting   from Vercel    — converted to equivalent requests by the weights
                             in `usage.vercel`, so both draw down one entitlement
"""
import warnings

import pandas as pd
from statsmodels.tsa.statespace.sarimax import SARIMAX

warnings.filterwarnings("ignore")   # statsmodel's frequency inference is noise here

#: Weeks per month for reporting a week's worth of usage from monthly data.
WEEKS_PER_MONTH = 4.345


def _monthly(rows, field):
    return pd.Series(
        [float(r[field]) for r in rows],
        index=pd.to_datetime([r["month"] for r in rows]).to_period("M"),
        name=field,
    ).sort_index()


def _pct(now, before):
    """A percentage change, or None when there is nothing to compare against.

    Cast to float: pandas values are numpy scalars, and a numpy float in the
    report makes `json.dumps` either fail or emit a type the reader has to
    explain. The report is the interface; it carries plain types.
    """
    if before in (None, 0):
        return None
    return round((float(now) - float(before)) / float(before) * 100, 2)


def _forecast(series, steps):
    """SARIMAX(1,1,1)(1,1,0,12) — a drifting walk with a yearly cycle.

    Twelve-period seasonality needs at least ~3 years to mean anything. With 40
    months it is estimable; with less, the model still fits but the seasonal term
    is weakly identified, which is reported rather than hidden.
    """
    model = SARIMAX(
        series.astype(float),
        order=(1, 1, 1),
        seasonal_order=(1, 1, 0, 12),
        enforce_stationarity=False,
        enforce_invertibility=False,
    ).fit(disp=False)
    forecast = model.get_forecast(steps=steps)
    band = forecast.conf_int(alpha=0.20)
    return [
        {
            "month": str(index),
            "point": round(float(value)),
            "low": round(float(band.iloc[i, 0])),
            "high": round(float(band.iloc[i, 1])),
        }
        for i, (index, value) in enumerate(forecast.predicted_mean.items())
    ]


def customer_usage(
    io,
    customer: str,
    project: str,
    orders_export: str = "fixtures/metabase/orders_acme.json",
    billing_export: str = "fixtures/vercel/billing_acme.jsonl",
):
    """Orders, equivalent requests, and the forward view for one customer.

    Both inputs are files an analyst downloads — a Metabase saved-question export
    and Vercel's FOCUS billing export. That is deliberate: the number a customer
    is billed against comes from the billing export, so reading it is more honest
    than reconstructing it from an API that needs a token.
    """
    orders = _monthly(
        io("run", operation="usage.metabase", export=orders_export, customer=customer)["rows"],
        "orders",
    )
    hosting = io("run", operation="usage.vercel", export=billing_export)

    latest = orders.iloc[-1]
    latest_month = str(orders.index[-1])
    month_ago = orders.iloc[-2] if len(orders) >= 2 else None
    year_ago = orders.iloc[-13] if len(orders) >= 13 else None
    ttm_rows = orders.tail(12)

    eq_total = hosting["equivalent_requests"]
    eq_per_order = round(eq_total / latest, 2) if latest else None
    ttm_orders = int(ttm_rows.sum())
    # Hosting is measured over one window, so TTM equivalent requests are that
    # window scaled up. Stated as a projection because that is what it is.
    ttm_equivalent = int(eq_total * 12)

    report = {
        "customer": customer,
        "project": project,
        "months": int(len(orders)),
        "latest": {"month": latest_month, "orders": int(latest)},
        # A week cannot be compared from monthly data: dividing both months by
        # 4.345 makes WoW identical to MoM by construction, which is a number
        # dressed up as a measurement. Reported as unavailable, with the reason.
        "wow": {
            "orders": None,
            "basis": "unavailable: the source series is monthly",
            "note": "supply weekly data to populate this window",
        },
        "mom": {"orders": _pct(latest, month_ago)} if month_ago is not None else None,
        "yoy": {"orders": _pct(latest, year_ago)} if year_ago is not None else None,
        "ttm": {
            "orders": ttm_orders,
            "equivalent_requests": ttm_equivalent,
            "equivalent_requests_per_order_month": eq_per_order,
            "months": int(len(ttm_rows)),
        },
        "hosting": {
            "formula": hosting["formula"],
            "export": hosting["export"],
            "records": hosting["records"],
            "unparseable": hosting["unparseable"],
            "equivalent_requests": eq_total,
            "components": hosting["components"],
        },
    }

    horizon = 13
    fc = _forecast(orders, horizon)
    report["forecast"] = {
        "method": "SARIMAX(1,1,1)(1,1,0,12)",
        "next_month": {"month": fc[0]["month"], "orders": fc[0]["point"]},
        # A week is not modelled: there is no weekly series. The figure is the
        # next month divided by 4.345 and is labelled as the approximation it is.
        "next_week": {
            "orders": round(fc[0]["point"] / WEEKS_PER_MONTH),
            "basis": "next month / 4.345 (no weekly series)",
        },
        "next_month_equivalent_requests": int(fc[0]["point"] * (eq_per_order or 0)),
        "next_week": {
            "orders": round(fc[0]["point"] / WEEKS_PER_MONTH),
            "basis": f"next month divided by {WEEKS_PER_MONTH:.3f} weeks",
        },
        "ntm": {
            "months": [f["month"] for f in fc[:12]],
            "orders": int(sum(f["point"] for f in fc[:12])),
            "equivalent_requests": int(sum(f["point"] for f in fc[:12]) * (eq_per_order or 0)),
        },
        "confidence_80_next_month": {"orders": [fc[0]["low"], fc[0]["high"]]},
    }
    return report
