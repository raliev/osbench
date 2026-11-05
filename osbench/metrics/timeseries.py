"""Per-day time series computed from a run's journal — for run analysis and comparison.

These answer "are orders delivered on time, as promised, and what does cost/service look
like over the run?" — the kind of signal that single aggregate numbers hide.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from ..journal.schema import JType

_MIN_PER_DAY = 1440


def _spine(max_day: int) -> pl.DataFrame:
    return pl.DataFrame({"day": list(range(max_day + 1))})


def daily_series(run_dir: str | Path) -> dict:
    run_dir = Path(run_dir)
    ev = pl.read_parquet(run_dir / "events.parquet").with_columns(
        (pl.col("t") // _MIN_PER_DAY).alias("day")
    )
    if ev.height == 0:
        return {"days": [], "series": {}}
    max_day = int(ev["day"].max())
    spine = _spine(max_day)

    def join(df: pl.DataFrame, cols: list[str]) -> None:
        nonlocal spine
        spine = spine.join(df, on="day", how="left")
        for c in cols:
            spine = spine.with_columns(pl.col(c).fill_null(0))

    created = ev.filter(pl.col("type") == int(JType.ORDER_CREATED)).group_by("day").len().rename(
        {"len": "created"}
    )
    join(created, ["created"])

    deliv = ev.filter(pl.col("type") == int(JType.DELIVERED)).with_columns(
        ((pl.col("delivered_at") - pl.col("promised_at")).cast(pl.Float64) / _MIN_PER_DAY).alias("gap")
    )
    if deliv.height:
        d_agg = deliv.group_by("day").agg(
            pl.len().alias("delivered"),
            (pl.col("gap") <= 0).mean().alias("on_time_rate"),
            pl.col("gap").mean().alias("planfact_days"),
            pl.col("gap").filter(pl.col("gap") > 0).quantile(0.9).alias("lateness_p90"),
        )
        spine = spine.join(d_agg, on="day", how="left").with_columns(
            pl.col("delivered").fill_null(0)
        )
    else:
        spine = spine.with_columns(
            pl.lit(0).alias("delivered"), pl.lit(None).alias("on_time_rate"),
            pl.lit(None).alias("planfact_days"), pl.lit(None).alias("lateness_p90"),
        )

    shipped = ev.filter(pl.col("type") == int(JType.SHIPPED))
    if shipped.height:
        s_agg = shipped.group_by("day").agg(
            pl.col("cost").sum().alias("shipping"),
            pl.len().alias("parcels"),
            pl.col("order_id").n_unique().alias("orders_shipped"),
        ).with_columns((pl.col("parcels") / pl.col("orders_shipped")).alias("parcels_per_order"))
        join(s_agg.select(["day", "shipping"]), ["shipping"])
        spine = spine.join(s_agg.select(["day", "parcels_per_order"]), on="day", how="left")
    else:
        spine = spine.with_columns(pl.lit(0.0).alias("shipping"), pl.lit(None).alias("parcels_per_order"))

    for jt, name in ((JType.HOLDING, "holding"), (JType.RECEIVED, "purchase"), (JType.LOST_SALE, "penalty")):
        agg = ev.filter(pl.col("type") == int(jt)).group_by("day").agg(pl.col("cost").sum().alias(name))
        join(agg, [name])
    lost = ev.filter(pl.col("type") == int(JType.LOST_SALE)).group_by("day").len().rename({"len": "lost"})
    join(lost, ["lost"])

    # Inventory (if present): total units and stockout share per day.
    inv_path = run_dir / "inventory_daily.parquet"
    if inv_path.exists():
        inv = pl.read_parquet(inv_path).group_by("day").agg(
            pl.col("units").sum().alias("inv_units"),
            pl.col("stockout_skus").sum().alias("stockout_skus"),
        )
        spine = spine.join(inv, on="day", how="left")

    spine = spine.sort("day")
    days = spine["day"].to_list()
    series = {c: spine[c].to_list() for c in spine.columns if c != "day"}
    return {"days": days, "series": series, "lateness": _lateness_distribution(ev)}


# Lateness buckets in days: on-time (<=0), then widening late bands.
_LATE_EDGES = [0.0, 1.0, 2.0, 3.0, 5.0, 7.0, 14.0]
_LATE_LABELS = ["on time", "0-1d", "1-2d", "2-3d", "3-5d", "5-7d", "7-14d", "14d+"]


def _lateness_distribution(ev: pl.DataFrame) -> dict:
    """How late delivered orders were: per-order gap (last delivery - promise) in days,
    bucketed into a histogram plus summary stats. Answers "not just late/not, but how late"."""
    deliv = ev.filter(pl.col("type") == int(JType.DELIVERED))
    if deliv.height == 0:
        return {"labels": _LATE_LABELS, "counts": [0] * len(_LATE_LABELS),
                "mean": None, "p50": None, "p90": None, "max": None, "late_orders": 0, "n": 0}
    per_order = deliv.group_by("order_id").agg(
        ((pl.col("delivered_at").max() - pl.col("promised_at").max()).cast(pl.Float64) / _MIN_PER_DAY)
        .alias("gap")
    )
    gaps = per_order["gap"]
    counts = [0] * len(_LATE_LABELS)
    for g in gaps.to_list():
        if g <= 0:
            counts[0] += 1
            continue
        b = len(_LATE_EDGES)  # default = last (14d+)
        for i in range(1, len(_LATE_EDGES)):
            if g <= _LATE_EDGES[i]:
                b = i
                break
        counts[b] += 1
    late = gaps.filter(gaps > 0)
    return {
        "labels": _LATE_LABELS, "counts": counts,
        "mean": round(float(late.mean()), 2) if late.len() else 0.0,
        "p50": round(float(late.median()), 2) if late.len() else 0.0,
        "p90": round(float(late.quantile(0.9)), 2) if late.len() else 0.0,
        "max": round(float(late.max()), 2) if late.len() else 0.0,
        "late_orders": int(late.len()), "n": int(gaps.len()),
    }
