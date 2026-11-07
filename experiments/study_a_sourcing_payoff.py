"""Study A — when does a sourcing algorithm pay off, and when does it not?

Design
------
Two base scenarios with *identical* total daily picking capacity (600 lines/day) and
identical tariffs, catalogue, supplier and horizon. They differ only in geometry:

  exp_a_symmetric    three equidistant warehouses — distance carries no information,
                     so only load balancing can separate the policies;
  exp_a_geographic   four corner warehouses with local regions — distance is highly
                     informative, so balancing load costs freight.

Each is run at five network loads rho = orders/day * lines_per_order / capacity, by
setting demand.base_orders_per_day. Five sourcing policies run on every (scenario, rho,
seed) world; 30 seeds. The baseline is ``nearest`` — the rule most networks actually use.

Reading the result
------------------
The question is not "which policy wins" but "in which regimes is the gap to the
incumbent large enough to be worth a project". A flat ladder at some rho is a finding,
not a failure.
"""

from __future__ import annotations

import sys
import time

import pandas as pd

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

from common import REPO, cell_summary, check_errors, compare_policies, save  # noqa: E402

from osbench.config.loader import load_scenario  # noqa: E402
from osbench.experiments.sweep import run_sweep  # noqa: E402

SCENARIOS = {
    "symmetric": REPO / "scenarios" / "exp_a_symmetric.yaml",
    "geographic": REPO / "scenarios" / "exp_a_geographic.yaml",
}
RHOS = [0.4, 0.6, 0.8, 1.0, 1.2]
POLICIES = ["random", "nearest", "cheapest_myopic", "weighted_score", "dtlp_bidprice"]
BASELINE = "nearest"
SEEDS = list(range(30))
WORKERS = 20


def build_variants() -> list[tuple]:
    variants = []
    for geom, path in SCENARIOS.items():
        base = load_scenario(str(path))
        cap = sum(w.capacity_lines_per_day for w in base.warehouses)
        lpo = base.demand.lines_per_order_mean
        for rho in RHOS:
            opd = round(rho * cap / lpo)
            cfg = base.model_copy(update={
                "demand": base.demand.model_copy(update={"base_orders_per_day": float(opd)})
            })
            variants.append((f"{geom}_rho{rho}", cfg, {"geometry": geom, "rho": rho,
                                                       "orders_per_day": opd}))
    return variants


def main() -> None:
    variants = build_variants()
    out_dir = REPO / "runs" / "study_a"
    n_tasks = len(variants) * len(SEEDS)
    print(f"Study A: {len(variants)} cells x {len(SEEDS)} seeds x {len(POLICIES)} policies "
          f"= {n_tasks * len(POLICIES)} runs")

    t0 = time.time()
    df = run_sweep(
        variants, POLICIES, SEEDS, out_dir, replenishment="base_stock", workers=WORKERS,
        progress=lambda i, n: print(f"\r  {i}/{n} worlds", end="", flush=True),
    )
    print(f"\n  done in {time.time() - t0:.0f}s")
    check_errors(df)

    save(df, "study_a_raw.parquet")
    save(cell_summary(df, ["geometry", "rho", "policy"]), "study_a_cells.csv")
    cmp_df = compare_policies(df, ["geometry", "rho"], BASELINE)
    save(cmp_df, "study_a_compare.csv")

    pd.set_option("display.width", 220, "display.max_columns", 60)
    print("\nMean total_cost_per_order by cell:")
    print(df.pivot_table(index=["geometry", "rho"], columns="policy",
                         values="total_cost_per_order").round(2))
    print("\nPaired difference vs nearest, total_cost_per_order (Holm-adjusted):")
    c = cmp_df[cmp_df.metric == "total_cost_per_order"]
    print(c[["geometry", "rho", "policy", "mean_diff", "rel_diff_pct",
             "t_ci_low", "t_ci_high", "p_adj_holm", "significant_holm"]].round(4).to_string(index=False))


if __name__ == "__main__":
    main()
