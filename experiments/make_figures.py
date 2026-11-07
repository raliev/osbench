"""Figures for Studies A and B, built from the saved sweep tables.

Run the two study scripts first; this reads only experiments/results/*.parquet, so the
figures can be regenerated without re-running the simulations.
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import RESULTS  # noqa: E402

FIGS = RESULTS / "figures"
POLICY_STYLE = {
    "nearest": ("#1f77b4", "o", "nearest (incumbent)"),
    "weighted_score": ("#2ca02c", "s", "weighted_score"),
    "cheapest_myopic": ("#d62728", "^", "cheapest_myopic"),
    "dtlp_bidprice": ("#9467bd", "D", "dtlp_bidprice"),
    "random": ("#7f7f7f", "x", "random (lower bound)"),
}
GEOM_TITLE = {
    "symmetric": "Symmetric network\n(distance carries no signal)",
    "geographic": "Geographic network\n(distance is informative)",
}


def _ci95(s: pd.Series) -> float:
    """Half-width of the 95 % CI of the mean across seeds."""
    return 1.96 * s.sem() if len(s) > 1 else 0.0


def fig_a_cost() -> None:
    df = pd.read_parquet(RESULTS / "study_a_raw.parquet")
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4), sharey=True)
    for ax, geom in zip(axes, ["symmetric", "geographic"], strict=True):
        sub = df[df.geometry == geom]
        for pol, (color, marker, label) in POLICY_STYLE.items():
            g = sub[sub.policy == pol].groupby("rho")["total_cost_per_order"]
            ax.errorbar(g.mean().index, g.mean().values, yerr=g.apply(_ci95).values,
                        color=color, marker=marker, label=label, capsize=3, lw=1.8, ms=5)
        ax.axvspan(0.55, 0.85, color="#ffe9b0", zorder=0,
                   label="band where the choice pays" if geom == "symmetric" else None)
        ax.set_title(GEOM_TITLE[geom], fontsize=10)
        ax.set_xlabel("network load ρ = demand / picking capacity")
        ax.grid(alpha=0.3)
    axes[0].set_ylabel("total cost per order")
    axes[0].legend(fontsize=8, loc="upper left")
    fig.suptitle("Study A — cost per order by policy and network load (30 seeds, 95 % CI)",
                 fontsize=11)
    fig.tight_layout()
    fig.savefig(FIGS / "study_a_cost.png", dpi=150)
    plt.close(fig)


def fig_a_gap() -> None:
    """Relative paired gap to the incumbent — the number a manager actually budgets against."""
    cmp_df = pd.read_csv(RESULTS / "study_a_compare.csv")
    c = cmp_df[cmp_df.metric == "total_cost_per_order"]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4), sharey=True)
    for ax, geom in zip(axes, ["symmetric", "geographic"], strict=True):
        sub = c[c.geometry == geom]
        for pol, (color, marker, label) in POLICY_STYLE.items():
            if pol == "nearest":
                continue
            s = sub[sub.policy == pol].sort_values("rho")
            ax.plot(s.rho, s.rel_diff_pct, color=color, marker=marker, label=label, lw=1.8, ms=5)
        ax.axhline(0, color="#1f77b4", lw=2, ls="--", label="nearest (incumbent)")
        ax.axhspan(-2, 2, color="#eeeeee", zorder=0)
        ax.set_title(GEOM_TITLE[geom], fontsize=10)
        ax.set_xlabel("network load ρ")
        ax.grid(alpha=0.3)
    axes[0].set_ylabel("cost per order vs nearest, %\n(negative = cheaper)")
    axes[0].legend(fontsize=8, loc="upper left")
    fig.suptitle("Study A — paired gap to the incumbent rule (grey band = ±2 %, "
                 "below any project's noise floor)", fontsize=11)
    fig.tight_layout()
    fig.savefig(FIGS / "study_a_gap.png", dpi=150)
    plt.close(fig)


def fig_b_lost_sales() -> None:
    df = pd.read_parquet(RESULTS / "study_b_raw.parquet")
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4), sharey=True)
    cmap = plt.get_cmap("viridis")
    accepts = sorted(df.accept.unique(), reverse=True)
    for ax, mode, title in zip(
        axes, ["none", "wait_for_receipt"],
        ["deferral = none\n(the buffer is inert)", "deferral = wait_for_receipt\n(the buffer is usable)"],
        strict=True,
    ):
        sub = df[df.deferral == mode]
        for i, acc in enumerate(accepts):
            g = sub[sub.accept == acc].groupby("buffer_days")["lost_sales_rate"]
            ax.errorbar(g.mean().index, g.mean().values * 100, yerr=g.apply(_ci95).values * 100,
                        color=cmap(i / max(1, len(accepts) - 1)), marker="o", capsize=3,
                        lw=1.8, ms=5, label=f"acceptance at cap = {acc}")
        ax.set_title(title, fontsize=10)
        ax.set_xlabel("promise buffer, days")
        ax.set_xticks(sorted(df.buffer_days.unique()))
        ax.grid(alpha=0.3)
    axes[0].set_ylabel("lost sales rate, % of lines")
    axes[1].legend(fontsize=8)
    fig.suptitle("Study B — lost sales against the promise buffer (30 seeds, 95 % CI)",
                 fontsize=11)
    fig.tight_layout()
    fig.savefig(FIGS / "study_b_lost_sales.png", dpi=150)
    plt.close(fig)


def fig_b_decomposition() -> None:
    """Why the sign flips: two opposing components of the same metric."""
    df = pd.read_parquet(RESULTS / "study_b_raw.parquet")
    sub = df[df.deferral == "wait_for_receipt"]
    accepts = sorted(sub.accept.unique(), reverse=True)
    fig, axes = plt.subplots(1, len(accepts), figsize=(13, 3.8), sharey=True)
    for ax, acc in zip(axes, accepts, strict=True):
        g = sub[sub.accept == acc].groupby("buffer_days")
        rej = g["promise_rejection_rate"].mean() * 100
        sho = g["shortage_rate_post_order"].mean() * 100
        ax.bar(rej.index, sho.values, color="#4c78a8", label="stock-outs after the order")
        ax.bar(rej.index, rej.values, bottom=sho.values, color="#e45756",
               label="refusals at checkout (promise too long)")
        ax.plot(rej.index, (sho + rej).values, color="black", marker="o", lw=1.4, ms=4,
                label="total")
        ax.set_title(f"acceptance at cap = {acc}", fontsize=10)
        ax.set_xlabel("promise buffer, days")
        ax.set_xticks(sorted(sub.buffer_days.unique()))
        ax.grid(alpha=0.3, axis="y")
    axes[0].set_ylabel("% of lines")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, fontsize=9, ncol=3, loc="lower center", frameon=False)
    fig.suptitle("Study B — the buffer trades stock-outs for checkout refusals "
                 "(deferral = wait_for_receipt)", fontsize=11)
    fig.tight_layout(rect=(0, 0.09, 1, 1))
    fig.savefig(FIGS / "study_b_decomposition.png", dpi=150)
    plt.close(fig)


def main() -> None:
    FIGS.mkdir(parents=True, exist_ok=True)
    fig_a_cost()
    fig_a_gap()
    fig_b_lost_sales()
    fig_b_decomposition()
    print("wrote:", *sorted(p.name for p in FIGS.glob("*.png")))


if __name__ == "__main__":
    main()
