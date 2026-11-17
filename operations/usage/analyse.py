"""Turn two weekly series into the numbers a commercial conversation needs.

WoW, MoM, TTM, then a SARIMAX projection for next week and next month, and the
next twelve months. The whole thing is a pure function of the two inputs — it
reads and writes nothing — so it is the one operation here that can be re-run
freely and whose answer can be checked by hand.
"""
import datetime as dt

import pandas as pd
from statsmodels.tsa.statespace.sarimax import SARIMAX


def _week_index(rows, field):
    series = pd.Series(
        [float(r[field]) for r in rows],
        index=pd.to_datetime([r["week_start"] for r in rows]),
        name=field,
    )
    return series.sort_index()


def _pct(now, before):
    if before == 0:
        return None
    return round((now - before) / before * 100, 2)


def _forecast(series, steps):
    """SARIMAX on a weekly series, with the annual cycle as a seasonal term.

    (1,1,1)(1,1,0,52) is the smallest model that captures both a random walk
    with drift and a year of seasonality. Any failure is reported as `error`
    rather than an exception: a projection that cannot be made is information.
    """
    model = SARIMAX(
        series,
        order=(1, 1, 1),
        seasonal_order=(1, 1, 0, 52),
        enforce_stationarity=False,
        enforce_invertibility=False,
    )
    fit = model.fit(disp=False)
    forecast = fit.get_forecast(steps=steps)
    mean = forecast.predicted_mean
    band = forecast.conf_int(alpha=0.20)
    return [
        {
            "week_start": index.date().isoformat(),
            "point": round(float(value), 0),
            "low": round(float(band.iloc[i, 0]), 0),
            "high": round(float(band.iloc[i, 1]), 0),
        }
        for i, (index, value) in enumerate(mean.items())
    ]


def usage_report(io, customer: str, project: str):
    """Cross-reference Metabase order counts with Vercel API usage.

    Weeks are aligned by `week_start`; a week present in one series and not the
    other is reported, never silently zero-filled, because a zero would look like
    real usage that did not happen.
    """
    orders = _week_index(
        io("run", operation="usage.metabase", customer=customer)["rows"], "orders"
    )
    requests = _week_index(
        io("run", operation="usage.vercel", project=project)["rows"], "requests"
    )

    joined = pd.concat([orders, requests], axis=1, join="outer")
    unmatched = int(joined["orders"].isna().sum() + joined["requests"].isna().sum())
    joined = joined.dropna()

    latest = joined.iloc[-1]
    month_ago = joined.iloc[-5] if len(joined) >= 5 else None
    year_ago = joined.iloc[-53] if len(joined) >= 53 else None

    def window(weeks):
        return joined.tail(weeks)

    ttm = window(52)
    report = {
        "customer": customer,
        "project": project,
        "weeks": int(len(joined)),
        "unmatched_weeks": unmatched,
        "latest": {
            "week_start": latest.name.date().isoformat(),
            "orders": int(latest["orders"]),
            "requests": int(latest["requests"]),
            "requests_per_order": round(float(latest["requests"] / latest["orders"]), 1),
        },
        "wow": {
            "orders": _pct(latest["orders"], joined.iloc[-2]["orders"]),
            "requests": _pct(latest["requests"], joined.iloc[-2]["requests"]),
        },
        "mom": (
            {
                "orders": _pct(latest["orders"], month_ago["orders"]),
                "requests": _pct(latest["requests"], month_ago["requests"]),
            }
            if month_ago is not None
            else None
        ),
        "yoy": (
            {
                "orders": _pct(latest["orders"], year_ago["orders"]),
                "requests": _pct(latest["requests"], year_ago["requests"]),
            }
            if year_ago is not None
            else None
        ),
        "ttm": {
            "orders": int(ttm["orders"].sum()),
            "requests": int(ttm["requests"].sum()),
            "avg_requests_per_order": round(
                float(ttm["requests"].sum() / ttm["orders"].sum()), 1
            ),
        },
    }

    horizon = 53
    orders_fc = _forecast(joined["orders"], horizon)
    requests_fc = _forecast(joined["requests"], horizon)
    report["forecast"] = {
        "next_week": {
            "week_start": orders_fc[0]["week_start"],
            "orders": orders_fc[0]["point"],
            "requests": requests_fc[0]["point"],
        },
        "next_month": {
            "orders": int(sum(f["point"] for f in orders_fc[:4])),
            "requests": int(sum(f["point"] for f in requests_fc[:4])),
        },
        "next_twelve_months": {
            "orders": int(sum(f["point"] for f in orders_fc[:52])),
            "requests": int(sum(f["point"] for f in requests_fc[:52])),
        },
        "confidence_80_next_week": {
            "orders": [orders_fc[0]["low"], orders_fc[0]["high"]],
            "requests": [requests_fc[0]["low"], requests_fc[0]["high"]],
        },
    }
    return report
