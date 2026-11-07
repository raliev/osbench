"""Study C — does online inventory reservation earn its keep?

Design
------
One base scenario (``exp_a1_reservation``): a concentrated catalogue (40 SKUs, heavy Zipf
head) with East-skewed demand on a capacity-tight warehouse. That is what creates
contention — several orders competing for the same scarce units in the window between the
sourcing decision (ATP is read) and the pick (book stock is decremented).

Reservation on/off is the treatment. The contention knob is network load, swept via
demand.base_orders_per_day:

  rho = orders/day * lines_per_order / capacity ∈ {0.2, 0.4, 0.6, 0.75, 0.9}

Sourcing policy is fixed at ``weighted_score``; 30 seeds.

Primary metric is ``lost_sales_rate``. Total cost per order is deliberately NOT the
primary metric here: it is ~85 % purchase cost of goods sold, so reservation — which
fulfils more demand — mechanically *raises* it while doing its job. Lost sales, on-time
rate and the pick-stage shortage are the honest signals.

The low-load cells are the control arm: with no contention, reservation should do nothing.
"""

from __future__ import annotations

import sys
import time

import pandas as pd

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

from common import REPO, cell_summary, check_errors, compare_cells, save  # noqa: E402

from osbench.config.loader import load_scenario  # noqa: E402
from osbench.experiments.sweep import run_sweep  # noqa: E402

BASE = REPO / "scenarios" / "exp_a1_reservation.yaml"
RHOS = [0.2, 0.4, 0.6, 0.75, 0.9]
POLICY = "weighted_score"
SEEDS = list(range(30))
WORKERS = 20

METRICS = ["lost_sales_rate", "promise_hit_rate", "shortage_rate_pick",
           "shortage_rate_post_order", "penalty_cost", "total_cost_per_order",
           "shipping_cost_per_order", "n_orders"]


def build_variants() -> list[tuple]:
    base = load_scenario(str(BASE))
    cap = sum(w.capacity_lines_per_day for w in base.warehouses)
    lpo = base.demand.lines_per_order_mean
    variants = []
    for rho in RHOS:
        opd = round(rho * cap / lpo)
        for res in (False, True):
            cfg = base.model_copy(update={
                "demand": base.demand.model_copy(update={"base_orders_per_day": float(opd)}),
                "reservation": base.reservation.model_copy(update={"enabled": res}),
            })
            variants.append((f"rho{rho}_res{int(res)}", cfg,
                             {"rho": rho, "orders_per_day": opd, "reservation": res}))
    return variants


def main() -> None:
    variants = build_variants()
    out_dir = REPO / "runs" / "study_c"
    print(f"Study C: {len(variants)} cells x {len(SEEDS)} seeds = {len(variants) * len(SEEDS)} runs")

    t0 = time.time()
    df = run_sweep(variants, [POLICY], SEEDS, out_dir, replenishment="base_stock",
                   workers=WORKERS,
                   progress=lambda i, n: print(f"\r  {i}/{n} worlds", end="", flush=True))
    print(f"\n  done in {time.time() - t0:.0f}s")
    check_errors(df)

    save(df, "study_c_raw.parquet")
    save(cell_summary(df, ["rho", "reservation"], METRICS), "study_c_cells.csv")
    # reservation = True against reservation = False, paired by seed within each load.
    cmp_df = compare_cells(df, ["rho"], "reservation", False, METRICS)
    save(cmp_df, "study_c_compare.csv")

    pd.set_option("display.width", 220, "display.max_columns", 60)
    for m in ("lost_sales_rate", "promise_hit_rate", "shortage_rate_pick", "penalty_cost"):
        print(f"\nMean {m}:")
        print(df.pivot_table(index="rho", columns="reservation", values=m).round(4))
    print("\nReservation on − off, lost_sales_rate (Holm-adjusted):")
    c = cmp_df[cmp_df.metric == "lost_sales_rate"]
    print(c[["rho", "mean_baseline", "mean_treatment", "mean_diff", "rel_diff_pct",
             "t_ci_low", "t_ci_high", "p_adj_holm", "significant_holm"]].round(5).to_string(index=False))


if __name__ == "__main__":
    main()
