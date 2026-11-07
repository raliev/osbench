"""Figures for Studies C (reservation) and D (warehouse outage).

Reads only experiments/results/study_{c,d}_*.{parquet,csv}, so figures regenerate without
re-running the simulations.
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
    "nearest": ("#1f77b4", "o", "nearest"),
    "weighted_score": ("#2ca02c", "s", "weighted_score"),
    "cheapest_myopic": ("#d62728", "^", "cheapest_myopic"),
}


def _ci95(s: pd.Series) -> float:
    return 1.96 * s.sem() if len(s) > 1 else 0.0


def fig_c_reservation() -> None:
    df = pd.read_parquet(RESULTS / "study_c_raw.parquet")
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4))
    panels = [
        ("lost_sales_rate", "lost sales rate, % of lines", "Fill: reservation helps under contention"),
        ("promise_hit_rate", "on-time delivery rate, %", "On-time: but the saved orders arrive late"),
    ]
    for ax, (metric, ylab, title) in zip(axes, panels, strict=True):
        for res, color, label in [(False, "#d62728", "reservation OFF"),
                                  (True, "#2ca02c", "reservation ON")]:
            g = df[df.reservation == res].groupby("rho")[metric]
            ax.errorbar(g.mean().index, g.mean().values * 100, yerr=g.apply(_ci95).values * 100,
                        color=color, marker="o", capsize=3, lw=1.8, ms=5, label=label)
        ax.axvspan(0.15, 0.5, color="#eeeeee", zorder=0, label="no contention (control)")
        ax.set_title(title, fontsize=10)
        ax.set_xlabel("network load ρ = demand / capacity")
        ax.set_ylabel(ylab)
        ax.grid(alpha=0.3)
    axes[0].legend(fontsize=8)
    fig.suptitle("Study C — online reservation is a fill lever with an on-time cost "
                 "(30 seeds, 95 % CI)", fontsize=11)
    fig.tight_layout()
    fig.savefig(FIGS / "study_c_reservation.png", dpi=150)
    plt.close(fig)


def fig_d_cliff() -> None:
    cmp_df = pd.read_csv(RESULTS / "study_d_compare.csv")
    c = cmp_df[(cmp_df.metric == "promise_hit_rate")]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4))
    for days, ax in zip([3, 7], axes, strict=True):
        sub = c[c.outage_days == days]
        for pol, (color, marker, label) in POLICY_STYLE.items():
            s = sub[sub.policy == pol].sort_values("rho")
            ax.plot(s.rho, s.mean_diff * 100, color=color, marker=marker, label=label, lw=1.8, ms=6)
        ax.axhline(0, color="#888", lw=1)
        ax.set_title(f"{days}-day outage", fontsize=10)
        ax.set_xlabel("network load ρ before the outage")
        ax.grid(alpha=0.3)
    axes[0].set_ylabel("on-time rate change vs calm, pp\n(negative = worse)")
    axes[1].legend(fontsize=8, loc="lower left")
    fig.suptitle("Study D — the resilience cliff: losing 1 of 4 warehouses is absorbed "
                 "below ρ≈0.7 and collapses service above it", fontsize=11)
    fig.tight_layout()
    fig.savefig(FIGS / "study_d_cliff.png", dpi=150)
    plt.close(fig)


def fig_d_recovery() -> None:
    r = pd.read_csv(RESULTS / "study_d_recovery.csv")
    start = int(r["outage_start"].iloc[0])
    end = int(r["outage_end"].iloc[0])
    fig, ax = plt.subplots(figsize=(9, 4.4))
    for pol, (color, marker, label) in POLICY_STYLE.items():
        s = r[(r.policy == pol) & (r.arm == "outage")].sort_values("day")
        ax.plot(s.day, s.on_time * 100, color=color, label=label, lw=1.8)
    # Calm baseline (weighted_score) as a flat reference the outage run never returns to.
    cal = r[(r.policy == "weighted_score") & (r.arm == "calm")].sort_values("day")
    ax.plot(cal.day, cal.on_time * 100, color="#555", ls="--", lw=1.4, label="calm (no outage)")
    ax.axvspan(start, end, color="#ffd0d0", zorder=0, label=f"W0 down (days {start}–{end})")
    ax.set_xlabel("delivery day")
    ax.set_ylabel("on-time rate that day, %")
    ax.set_xlim(8, 44)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, loc="lower left")
    fig.suptitle("Study D — at ρ=0.8 a 7-day outage does NOT recover within the horizon "
                 "(5 seeds; calm stays at ~98 %)", fontsize=11)
    fig.tight_layout()
    fig.savefig(FIGS / "study_d_recovery.png", dpi=150)
    plt.close(fig)


def main() -> None:
    FIGS.mkdir(parents=True, exist_ok=True)
    fig_c_reservation()
    fig_d_cliff()
    fig_d_recovery()
    print("wrote:", *sorted(p.name for p in FIGS.glob("study_[cd]_*.png")))


if __name__ == "__main__":
    main()
