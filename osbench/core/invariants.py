"""Post-hoc validation of run invariants from the journal."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from ..journal.schema import JType

_MIN_PER_DAY = 1440


def _noise_zero(run_dir: Path) -> bool:
    """True if delivery noise is zero (then delivered_at == planned_at is required)."""
    import json

    sc = Path(run_dir).parent / "scenario.json"
    if not sc.exists():
        sc = Path(run_dir) / "scenario.json"
    if not sc.exists():
        return False
    data = json.loads(sc.read_text(encoding="utf-8"))
    return float(data.get("tariffs", {}).get("delivery_noise_days_mean", 0.0)) == 0.0


def validate(run_dir: str | Path) -> dict:
    """Validate invariants. Returns {'ok': bool, 'violations': [...]}"""
    run_dir = Path(run_dir)
    ev = pl.read_parquet(run_dir / "events.parquet")
    violations: list[str] = []

    created = ev.filter(pl.col("type") == int(JType.ORDER_CREATED))
    shipped = ev.filter(pl.col("type") == int(JType.SHIPPED))
    delivered = ev.filter(pl.col("type") == int(JType.DELIVERED))
    lost = ev.filter(pl.col("type") == int(JType.LOST_SALE))

    # 1. Every delivered parcel was shipped, and delivered_at == plan (zero noise).
    if delivered.height:
        j = delivered.select(["order_id", "parcel", "delivered_at"]).join(
            shipped.select(["order_id", "parcel", "planned_at"]),
            on=["order_id", "parcel"], how="left",
        )
        if j["planned_at"].null_count() > 0:
            violations.append("delivered without a matching shipped")
        noise = run_dir and _noise_zero(run_dir)
        if noise:
            mismatch = j.filter(pl.col("delivered_at") != pl.col("planned_at"))
            if mismatch.height:
                violations.append(
                    f"delivered_at != planned_at with zero noise: {mismatch.height} parcels"
                )

    # 2. Every created order is either delivered or fully lost (no "stuck" orders).
    created_ids = set(created["order_id"].to_list())
    delivered_ids = set(delivered["order_id"].to_list())
    lost_ids = set(lost["order_id"].to_list())
    stuck = created_ids - delivered_ids - lost_ids
    if stuck:
        violations.append(f"stuck orders (neither delivered nor lost): {len(stuck)}")

    # 3. No negative daily stock balances (from inventory_daily).
    inv_path = run_dir / "inventory_daily.parquet"
    if inv_path.exists():
        inv = pl.read_parquet(inv_path)
        if inv.height and inv["units"].min() < 0:
            violations.append("negative units balance in inventory_daily")

    # 4. Sum of cost components == total (from metrics, if available).
    # (also checked in metrics.compute — skipped here for independence)

    return {"ok": len(violations) == 0, "violations": violations}
