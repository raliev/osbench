"""Study B — what does an extra day on the promised date actually buy?

Design
------
One base scenario (``exp_b_scarcity``): slow jittery supply, thin cover, heavy Zipf head.
Shortages are frequent, so there is something for a longer promise to convert into sales.

Three crossed axes, 24 cells, 30 seeds, sourcing policy fixed at ``weighted_score``:

  promise.buffer_days        0, 1, 2, 3   — how much slack we add to the promised date
  deferral.mode              none, wait_for_receipt
                                          — may an order *use* that slack to wait for
                                            an inbound receipt, or is the slack inert?
  promise.p_accept_promise   1.0, 0.9,    — customer sensitivity to a long promise: the
                             0.8, 0.6       acceptance probability at promise.cap_days,
                                            interpolated linearly from 1.0 at the zone
                                            baseline. 1.0 means the date never costs a
                                            sale; 0.6 means a promise at cap_days is
                                            accepted only 60 % of the time. The middle
                                            levels bracket the predicted break-even.

Primary metric is ``lost_sales_rate``: the buffer's whole purpose is to stop cancelling
lines. Cost, on-time rate and promise rejections are explanatory.

The deferral = none arm is the control that makes the study honest. Adding buffer days
without deferred sourcing changes the promised date but changes nothing about how the
order is fulfilled, so any movement in the economics there would be a bug.
"""

from __future__ import annotations

import sys
import time

import pandas as pd

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

from common import REPO, cell_summary, check_errors, compare_cells, save  # noqa: E402

from osbench.config.loader import load_scenario  # noqa: E402
from osbench.experiments.sweep import run_sweep  # noqa: E402

BASE = REPO / "scenarios" / "exp_b_scarcity.yaml"
BUFFERS = [0, 1, 2, 3]
MODES = ["none", "wait_for_receipt"]
ACCEPT = [1.0, 0.9, 0.8, 0.6]
POLICY = "weighted_score"
SEEDS = list(range(30))
WORKERS = 20

METRICS = ["lost_sales_rate", "total_cost_per_order", "shipping_cost_per_order",
           "penalty_cost", "promise_hit_rate", "promise_rejection_rate", "deferred_share",
           "shortage_rate_post_order", "split_rate", "n_orders"]


def build_variants() -> list[tuple]:
    base = load_scenario(str(BASE))
    variants = []
    for acc in ACCEPT:
        for mode in MODES:
            for buf in BUFFERS:
                cfg = base.model_copy(update={
                    "promise": base.promise.model_copy(update={
                        "buffer_days": buf, "p_accept_promise": acc}),
                    "deferral": base.deferral.model_copy(update={"mode": mode}),
                })
                variants.append((f"acc{acc}_{mode}_buf{buf}", cfg,
                                 {"accept": acc, "deferral": mode, "buffer_days": buf}))
    return variants


def main() -> None:
    variants = build_variants()
    out_dir = REPO / "runs" / "study_b"
    print(f"Study B: {len(variants)} cells x {len(SEEDS)} seeds = "
          f"{len(variants) * len(SEEDS)} runs")

    t0 = time.time()
    df = run_sweep(
        variants, [POLICY], SEEDS, out_dir, replenishment="base_stock", workers=WORKERS,
        progress=lambda i, n: print(f"\r  {i}/{n} worlds", end="", flush=True),
    )
    print(f"\n  done in {time.time() - t0:.0f}s")
    check_errors(df)

    save(df, "study_b_raw.parquet")
    save(cell_summary(df, ["accept", "deferral", "buffer_days"], METRICS), "study_b_cells.csv")
    # Each buffer level against buffer_days = 0, holding acceptance and deferral fixed.
    cmp_df = compare_cells(df, ["accept", "deferral"], "buffer_days", 0, METRICS)
    save(cmp_df, "study_b_compare.csv")

    pd.set_option("display.width", 220, "display.max_columns", 60)
    for m in ("lost_sales_rate", "promise_rejection_rate", "shortage_rate_post_order",
              "deferred_share", "total_cost_per_order", "promise_hit_rate", "n_orders"):
        print(f"\nMean {m}:")
        print(df.pivot_table(index=["accept", "deferral"], columns="buffer_days",
                             values=m).round(4))
    print("\nPaired difference vs buffer_days=0, lost_sales_rate (Holm-adjusted):")
    c = cmp_df[cmp_df.metric == "lost_sales_rate"]
    print(c[["accept", "deferral", "buffer_days", "mean_diff", "rel_diff_pct",
             "t_ci_low", "t_ci_high", "p_adj_holm", "significant_holm"]].round(5).to_string(index=False))


if __name__ == "__main__":
    main()
