"""Computation of run metrics from the event journal (polars)."""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from ..journal.schema import JType

_MIN_PER_DAY = 1440


def compute_metrics(run_dir: str | Path, warmup_days: int = 0) -> dict:
    """Compute aggregated run metrics after warmup."""
    run_dir = Path(run_dir)
    ev = pl.read_parquet(run_dir / "events.parquet")
    warmup_min = warmup_days * _MIN_PER_DAY
    ev = ev.filter(pl.col("t") >= warmup_min)

    def rows(jt: JType) -> pl.DataFrame:
        return ev.filter(pl.col("type") == int(jt))

    delivered = rows(JType.DELIVERED)
    shipped = rows(JType.SHIPPED)
    assigned = rows(JType.ASSIGNED)
    lost = rows(JType.LOST_SALE)
    holding = rows(JType.HOLDING)
    received = rows(JType.RECEIVED)
    created = rows(JType.ORDER_CREATED)

    late_pen = rows(JType.LATE_PENALTY)
    transfers = rows(JType.TRANSFER_SHIPPED)
    n_orders = created.height
    shipping_cost = float(shipped["cost"].sum() or 0.0)
    holding_cost = float(holding["cost"].sum() or 0.0)
    purchase_cost = float(received["cost"].sum() or 0.0)
    penalty_cost = float(lost["cost"].sum() or 0.0)
    lateness_penalty_cost = float(late_pen["cost"].sum() or 0.0)
    transfer_cost = float(transfers["cost"].sum() or 0.0)  # inter-warehouse rebalancing (2.13)
    transfer_units = int(transfers["qty"].sum() or 0)
    total_cost = (shipping_cost + holding_cost + purchase_cost + penalty_cost
                  + lateness_penalty_cost + transfer_cost)

    # On-time rate: parcels with delivered_at <= promised_at.
    on_time_rate = None
    lateness_p50 = lateness_p90 = lateness_p99 = None
    if delivered.height:
        late_days = (
            (delivered["delivered_at"] - delivered["promised_at"]).cast(pl.Float64) / _MIN_PER_DAY
        )
        on_time_rate = float((late_days <= 0).mean())
        late_only = late_days.filter(late_days > 0)
        if late_only.len():
            lateness_p50 = float(late_only.quantile(0.5))
            lateness_p90 = float(late_only.quantile(0.9))
            lateness_p99 = float(late_only.quantile(0.99))

    # Split rate: orders with >1 shipped parcel.
    split_rate = None
    avg_parcels = None
    if shipped.height:
        per_order = shipped.group_by("order_id").len()
        split_rate = float((per_order["len"] > 1).mean())
        avg_parcels = float(per_order["len"].mean())

    lost_lines = lost.height
    delivered_lines = int(delivered["qty"].sum() or 0)
    lost_sales_rate = (
        lost_lines / (lost_lines + delivered_lines) if (lost_lines + delivered_lines) else None
    )

    policy_ms = assigned["policy_time_ms"].drop_nulls()
    decision_p50 = float(policy_ms.quantile(0.5)) if policy_ms.len() else None
    decision_p99 = float(policy_ms.quantile(0.99)) if policy_ms.len() else None

    # RQ5: promise acceptance and deferred sourcing.
    rejected = rows(JType.PROMISE_REJECTED).height
    deferred_orders = rows(JType.DEFERRED)["order_id"].n_unique() if n_orders else 0
    promise_rejection_rate = rejected / n_orders if n_orders else None
    deferred_share = deferred_orders / n_orders if n_orders else None

    # Shortage points (2.9): cancelled lines by stage, from the parcel field of LOST_SALE rows
    # (1 checkout, 2 post-order, 3 pick; -1 for legacy rows with no stage).
    denom = lost_lines + delivered_lines
    by_stage = {1: 0, 2: 0, 3: 0, 4: 0}
    if lost.height and "parcel" in lost.columns:
        grp = lost.group_by("parcel").len()
        for r in grp.iter_rows(named=True):
            by_stage[int(r["parcel"])] = by_stage.get(int(r["parcel"]), 0) + int(r["len"])
    cancelled_by_stage = {
        "checkout": by_stage.get(1, 0), "post_order": by_stage.get(2, 0),
        "pick": by_stage.get(3, 0), "horizon": by_stage.get(4, 0),
    }
    shortage_rate_checkout = by_stage.get(1, 0) / denom if denom else None
    shortage_rate_post_order = by_stage.get(2, 0) / denom if denom else None
    shortage_rate_pick = by_stage.get(3, 0) / denom if denom else None
    substituted_lines = rows(JType.SUBSTITUTED).height
    # Customer-initiated cancellations: one event per abandoned order. Not a lost sale (the
    # demand withdrew itself), so it is reported separately and excluded from lost_sales_rate.
    customer_cancelled = rows(JType.CUSTOMER_CANCELLED).height
    customer_cancel_rate = customer_cancelled / n_orders if n_orders else None

    result = {
        "n_orders": n_orders,
        "total_cost_per_order": total_cost / n_orders if n_orders else None,
        "total_cost": total_cost,
        "shipping_cost": shipping_cost,
        "holding_cost": holding_cost,
        "purchase_cost": purchase_cost,
        "penalty_cost": penalty_cost,
        "lateness_penalty_cost": lateness_penalty_cost,
        "shipping_cost_per_order": shipping_cost / n_orders if n_orders else None,
        "promise_hit_rate": on_time_rate,
        "lateness_days_p50": lateness_p50,
        "lateness_days_p90": lateness_p90,
        "lateness_days_p99": lateness_p99,
        "split_rate": split_rate,
        "avg_parcels_per_order": avg_parcels,
        "lost_sales_rate": lost_sales_rate,
        "customer_cancel_rate": customer_cancel_rate,
        "transfer_cost": transfer_cost,
        "transfer_units": transfer_units,
        "transfer_share": (transfer_units / delivered_lines if delivered_lines else 0.0),
        "promise_rejection_rate": promise_rejection_rate,
        "deferred_share": deferred_share,
        "shortage_rate_checkout": shortage_rate_checkout,
        "shortage_rate_post_order": shortage_rate_post_order,
        "shortage_rate_pick": shortage_rate_pick,
        "lines_cancelled_by_stage": cancelled_by_stage,
        "substituted_lines": substituted_lines,
        "decision_time_ms_p50": decision_p50,
        "decision_time_ms_p99": decision_p99,
    }
    # Engine sidecar (2.12): pick-shortage cause split and the OMS/WMS snapshot divergence.
    extra_path = run_dir / "engine_extra.json"
    if extra_path.exists():
        extra = json.loads(extra_path.read_text(encoding="utf-8"))
        causes = extra.get("shortage_pick_causes", {})
        result["shortage_pick_causes"] = causes
        pick_total = sum(causes.values()) or 1
        result["shortage_pick_stale_snapshot_share"] = (
            causes.get("stale_snapshot", 0) / pick_total
        )
        result["oms_wms_divergence_units"] = extra.get("oms_wms_divergence_units_mean", 0.0)
        result["ping_pong_units"] = extra.get("ping_pong_units", 0)
    return result


def write_metrics(
    run_dir: str | Path, warmup_days: int = 0, scenario_dir: str | Path | None = None
) -> dict:
    run_dir = Path(run_dir)
    m = compute_metrics(run_dir, warmup_days)
    # gap_to_hindsight: how far this run's shipping cost is above the offline optimum.
    scen = Path(scenario_dir) if scenario_dir is not None else run_dir.parent
    if (scen / "orders.parquet").exists() and m.get("shipping_cost_per_order") is not None:
        try:
            from .hindsight import gap_to_hindsight, hindsight_cost

            h = hindsight_cost(scen, warmup_days)
            m["hindsight_per_order"] = h["hindsight_per_order"]
            m["gap_to_hindsight"] = gap_to_hindsight(
                m["shipping_cost_per_order"], h["hindsight_per_order"]
            )
        except Exception:
            m["hindsight_per_order"] = None
            m["gap_to_hindsight"] = None
    (run_dir / "metrics.json").write_text(
        json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return m
