"""Study D — how badly does losing a warehouse hurt, and does the policy change the answer?

Design
------
One base scenario (``exp_d3_outage``): four corner warehouses with local regions and a
contested centre, ample stock. Two crossed axes plus the policy:

  network load rho   0.6, 0.7, 0.8, 0.9   (via base_orders_per_day) — how much spare
                                            capacity the network carries into the shock;
  outage_days        0, 3, 7              — depth of the shock on W0, starting day 14;
  policy             nearest, weighted_score, cheapest_myopic.

30 seeds. The question is robustness: the degradation of each policy under the outage
relative to its own calm run, and whether a smarter sourcing policy absorbs the loss
better than the nearest-warehouse rule.

Primary metric is ``promise_hit_rate`` (the service dip). Cost per order is explanatory.

A second output, ``study_d_recovery.csv``, is the daily on-time rate around the outage
window for the rho = 0.8 / 7-day cell, so the recovery *shape* can be drawn, not just the
horizon aggregate.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import REPO, cell_summary, check_errors, compare_cells, save  # noqa: E402

from osbench.config.loader import load_scenario  # noqa: E402
from osbench.core.engine import SimEngine  # noqa: E402
from osbench.experiments.sweep import run_sweep  # noqa: E402
from osbench.generate import generate_run, load_world_from_dir  # noqa: E402

BASE = REPO / "scenarios" / "exp_d3_outage.yaml"
RHOS = [0.6, 0.7, 0.8, 0.9]
OUTAGE_DAYS = [0, 3, 7]
POLICIES = ["nearest", "weighted_score", "cheapest_myopic"]
SEEDS = list(range(30))
WORKERS = 20
_MIN_PER_DAY = 1440

METRICS = ["promise_hit_rate", "total_cost_per_order", "shipping_cost_per_order",
           "lost_sales_rate", "penalty_cost", "n_orders"]


def build_variants() -> list[tuple]:
    base = load_scenario(str(BASE))
    cap = sum(w.capacity_lines_per_day for w in base.warehouses)
    lpo = base.demand.lines_per_order_mean
    variants = []
    for rho in RHOS:
        opd = round(rho * cap / lpo)
        for days in OUTAGE_DAYS:
            sh = base.shocks.model_copy(update={
                "enabled": days > 0, "warehouse_outage_id": 0,
                "warehouse_outage_days": days, "warehouse_outage_start_day": 14})
            cfg = base.model_copy(update={
                "demand": base.demand.model_copy(update={"base_orders_per_day": float(opd)}),
                "shocks": sh})
            variants.append((f"rho{rho}_out{days}", cfg,
                             {"rho": rho, "orders_per_day": opd, "outage_days": days}))
    return variants


def recovery_curves(seeds=(0, 1, 2, 3, 4), rho=0.8, outage_days=7) -> pd.DataFrame:
    """Daily on-time rate by delivery day, averaged over seeds, per policy.

    Computed from the event log (DELIVERED = type 4): on time iff delivered_at <= promised_at.
    """
    base = load_scenario(str(BASE))
    cap = sum(w.capacity_lines_per_day for w in base.warehouses)
    opd = round(rho * cap / base.demand.lines_per_order_mean)
    demand = base.demand.model_copy(update={"base_orders_per_day": float(opd)})
    # The outage run plus a calm baseline, so the figure can show that calm stays flat
    # while the outage run never climbs back within the horizon.
    arms = {
        "outage": base.shocks.model_copy(update={
            "enabled": True, "warehouse_outage_id": 0,
            "warehouse_outage_days": outage_days, "warehouse_outage_start_day": 14}),
        "calm": base.shocks.model_copy(update={"enabled": False}),
    }
    tmp = REPO / "runs" / "study_d_recovery"
    rows = []
    for seed in seeds:
        for arm, sh in arms.items():
            cfg = base.model_copy(update={"demand": demand, "shocks": sh})
            sd = tmp / f"{arm}_s{seed}"
            generate_run(cfg, seed, sd)
            world = load_world_from_dir(sd)
            orders = pd.read_parquet(sd / "orders.parquet")
            for pol in POLICIES:
                ro = sd / pol
                SimEngine(world, orders, seed, pol, "base_stock").run(ro)
                e = pd.read_parquet(ro / "events.parquet")
                d = e[e["type"] == 4].copy()  # DELIVERED
                d["day"] = (d["delivered_at"] // _MIN_PER_DAY).astype(int)
                d["on_time"] = d["delivered_at"] <= d["promised_at"]
                g = d.groupby("day")["on_time"].mean().reset_index()
                g["policy"] = pol
                g["arm"] = arm
                g["seed"] = seed
                rows.append(g)
    allg = pd.concat(rows, ignore_index=True)
    out = allg.groupby(["arm", "policy", "day"])["on_time"].mean().reset_index()
    out["outage_start"] = 14
    out["outage_end"] = 14 + outage_days
    return out


def main() -> None:
    variants = build_variants()
    out_dir = REPO / "runs" / "study_d"
    n = len(variants) * len(SEEDS)
    print(f"Study D: {len(variants)} cells x {len(SEEDS)} seeds x {len(POLICIES)} policies "
          f"= {n * len(POLICIES)} runs")

    t0 = time.time()
    df = run_sweep(variants, POLICIES, SEEDS, out_dir, replenishment="base_stock",
                   workers=WORKERS,
                   progress=lambda i, n: print(f"\r  {i}/{n} worlds", end="", flush=True))
    print(f"\n  done in {time.time() - t0:.0f}s")
    check_errors(df)

    save(df, "study_d_raw.parquet")
    save(cell_summary(df, ["rho", "outage_days", "policy"], METRICS), "study_d_cells.csv")
    # Each outage depth against outage_days = 0, holding (rho, policy) fixed.
    cmp_df = compare_cells(df, ["rho", "policy"], "outage_days", 0, METRICS)
    save(cmp_df, "study_d_compare.csv")

    print("  computing recovery curves ...")
    save(recovery_curves(), "study_d_recovery.csv")

    pd.set_option("display.width", 220, "display.max_columns", 60)
    print("\nMean promise_hit_rate by (rho, outage_days), policy = weighted_score:")
    ws = df[df.policy == "weighted_score"]
    print(ws.pivot_table(index="rho", columns="outage_days", values="promise_hit_rate").round(3))
    print("\nDegradation vs calm, promise_hit_rate (outage 7, Holm-adjusted):")
    c = cmp_df[(cmp_df.metric == "promise_hit_rate") & (cmp_df.outage_days == 7)]
    print(c[["rho", "policy", "mean_baseline", "mean_treatment", "mean_diff",
             "t_ci_low", "t_ci_high", "p_adj_holm", "significant_holm"]].round(4).to_string(index=False))


if __name__ == "__main__":
    main()
