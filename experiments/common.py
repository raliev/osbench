"""Shared plumbing for the osbench studies in this folder.

Each study builds a list of scenario variants, hands them to ``run_sweep`` (which runs
every policy on an identical world per seed — common random numbers), and then analyses
the tidy result table with paired statistics.

The two analysis helpers differ in what is being compared:

``compare_policies``  pairs policies against a baseline policy *within* a cell. This is
                      the question "which algorithm is better, holding the world fixed".
``compare_cells``     pairs scenario variants against a baseline variant, holding the
                      policy fixed. This is the question "what does turning this knob do".

Both pair by seed, report a t-based and a bootstrap CI, and apply a Holm step-down
correction across the whole family of tests in the study.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from osbench.reports.stats import holm_adjust, paired_ci

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "experiments" / "results"

# Reported for every cell. The first entry is the study's primary metric unless the
# study says otherwise; the rest are explanatory.
REPORT_METRICS = [
    "total_cost_per_order",
    "shipping_cost_per_order",
    "penalty_cost",
    "promise_hit_rate",
    "lost_sales_rate",
    "split_rate",
    "n_orders",
]


def cell_summary(df: pd.DataFrame, by: list[str], metrics: list[str] | None = None) -> pd.DataFrame:
    """Mean and standard error of each metric per cell, plus the seed count."""
    metrics = metrics or [m for m in REPORT_METRICS if m in df.columns]
    g = df.groupby(by, dropna=False)
    out = g[metrics].agg(["mean", "sem"])
    out.columns = [f"{m}_{s}" for m, s in out.columns]
    out["n_seeds"] = g.size()
    return out.reset_index()


def _paired_rows(
    df: pd.DataFrame,
    group_cols: list[str],
    contrast_col: str,
    baseline: str,
    metrics: list[str],
) -> list[dict]:
    rows: list[dict] = []
    for keys, sub in df.groupby(group_cols, dropna=False):
        keys = keys if isinstance(keys, tuple) else (keys,)
        levels = list(sub[contrast_col].unique())
        if baseline not in levels:
            continue
        base = sub[sub[contrast_col] == baseline].set_index("seed")
        for lvl in levels:
            if lvl == baseline:
                continue
            cur = sub[sub[contrast_col] == lvl].set_index("seed")
            common = sorted(set(base.index) & set(cur.index))
            if len(common) < 2:
                continue
            for m in metrics:
                if m not in sub.columns:
                    continue
                b = base.loc[common, m].astype(float).to_numpy()
                c = cur.loc[common, m].astype(float).to_numpy()
                stats = paired_ci(c - b)
                rows.append({
                    **dict(zip(group_cols, keys, strict=True)),
                    contrast_col: lvl,
                    "baseline": baseline,
                    "metric": m,
                    "mean_baseline": float(np.nanmean(b)),
                    "mean_treatment": float(np.nanmean(c)),
                    "rel_diff_pct": (float(np.nanmean(c - b)) / float(np.nanmean(b)) * 100.0
                                     if np.nanmean(b) else float("nan")),
                    **stats,
                })
    return rows


def _with_holm(rows: list[dict]) -> pd.DataFrame:
    """Apply Holm across the family, separately per metric.

    The family is "all contrasts reported for this metric in this study". Correcting
    across metrics as well would be over-conservative: the metrics are different
    questions, not repeated attempts at the same one.
    """
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["p_adj_holm"] = np.nan
    df["significant_holm"] = False
    for m, idx in df.groupby("metric").groups.items():
        res = holm_adjust(df.loc[idx, "p_value"].to_numpy())
        df.loc[idx, "p_adj_holm"] = res["p_adj"]
        df.loc[idx, "significant_holm"] = res["reject"]
    return df


def compare_policies(
    df: pd.DataFrame, cell_cols: list[str], baseline: str, metrics: list[str] | None = None
) -> pd.DataFrame:
    """Each policy against ``baseline`` policy, paired by seed within each cell."""
    metrics = metrics or [m for m in REPORT_METRICS if m in df.columns]
    return _with_holm(_paired_rows(df, cell_cols, "policy", baseline, metrics))


def compare_cells(
    df: pd.DataFrame, cell_cols: list[str], contrast_col: str, baseline, metrics: list[str] | None = None
) -> pd.DataFrame:
    """Each level of ``contrast_col`` against ``baseline``, paired by seed.

    ``cell_cols`` are the columns held fixed (everything except the contrast).
    """
    metrics = metrics or [m for m in REPORT_METRICS if m in df.columns]
    return _with_holm(_paired_rows(df, cell_cols, contrast_col, baseline, metrics))


def save(df: pd.DataFrame, name: str) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / name
    if path.suffix == ".parquet":
        df.to_parquet(path, index=False)
    else:
        df.to_csv(path, index=False)
    return path


def check_errors(df: pd.DataFrame) -> None:
    """Fail loudly if any sweep task raised — a silent error row would bias every mean."""
    bad = df[df["policy"] == "__error__"]
    if not bad.empty:
        raise RuntimeError(f"{len(bad)} sweep tasks failed, e.g.: {bad.iloc[0].to_dict()}")
