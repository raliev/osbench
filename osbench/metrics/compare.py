"""Paired comparison of policies across seeds -> compare.csv."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..reports.stats import paired_ci

# Numeric metrics compared pairwise (lower is better unless noted).
_COMPARE_METRICS = [
    "total_cost_per_order",
    "shipping_cost_per_order",
    "holding_cost",
    "penalty_cost",
    "promise_hit_rate",  # higher is better
    "split_rate",
    "lost_sales_rate",
    "decision_time_ms_p99",
]


def _load_run(run_dir: Path) -> dict | None:
    mpath = run_dir / "metrics.json"
    if not mpath.exists():
        return None
    metrics = json.loads(mpath.read_text(encoding="utf-8"))
    manifest_path = run_dir.parent / "manifest.json"
    seed, scenario = 0, run_dir.parent.name
    if manifest_path.exists():
        man = json.loads(manifest_path.read_text(encoding="utf-8"))
        seed = man.get("seed", 0)
        scenario = man.get("scenario", scenario)
    return {"policy": run_dir.name, "seed": int(seed), "scenario": scenario, "metrics": metrics}


def compare_runs(
    run_dirs: list[Path],
    baseline_policy: str,
    ci_level: float = 0.95,
    bootstrap_iters: int = 10_000,
    bootstrap_seed: int = 12345,
) -> pd.DataFrame:
    """Build a paired-comparison table of each policy against the baseline."""
    runs = [r for r in (_load_run(Path(d)) for d in run_dirs) if r is not None]
    if not runs:
        raise ValueError("no run directories with metrics.json were found")

    scenario = runs[0]["scenario"]
    # metric -> policy -> {seed: value}
    table: dict[str, dict[str, dict[int, float]]] = {}
    for run in runs:
        for m in _COMPARE_METRICS:
            v = run["metrics"].get(m)
            if v is None:
                continue
            table.setdefault(m, {}).setdefault(run["policy"], {})[run["seed"]] = float(v)

    policies = sorted({r["policy"] for r in runs})
    if baseline_policy not in policies:
        raise ValueError(f"baseline policy {baseline_policy!r} not among runs: {policies}")

    rows = []
    for policy in policies:
        if policy == baseline_policy:
            continue
        for m in _COMPARE_METRICS:
            if m not in table:
                continue
            base = table[m].get(baseline_policy, {})
            cur = table[m].get(policy, {})
            common = sorted(set(base) & set(cur))
            if not common:
                continue
            diffs = np.array([cur[s] - base[s] for s in common])
            stats = paired_ci(diffs, ci_level, bootstrap_iters, bootstrap_seed)
            rows.append(
                {
                    "scenario": scenario,
                    "policy": policy,
                    "baseline": baseline_policy,
                    "metric": m,
                    **stats,
                }
            )
    return pd.DataFrame(rows)


def compare_sweep(
    df: pd.DataFrame,
    baseline_policy: str,
    metrics: list[str] | None = None,
    ci_level: float = 0.95,
    bootstrap_iters: int = 10_000,
    bootstrap_seed: int = 12345,
) -> pd.DataFrame:
    """Paired comparison from a sweep table (rows: scenario/policy/seed + metric columns).

    Pairs each policy against the baseline by seed within each scenario.
    """
    import numpy as np

    df = df[df["policy"] != "__error__"]
    if metrics is None:
        metrics = [m for m in _COMPARE_METRICS if m in df.columns]
    rows = []
    for scenario in sorted(df["scenario"].unique()):
        sub = df[df["scenario"] == scenario]
        policies = sorted(sub["policy"].unique())
        if baseline_policy not in policies:
            continue
        base = sub[sub["policy"] == baseline_policy].set_index("seed")
        for policy in policies:
            if policy == baseline_policy:
                continue
            cur = sub[sub["policy"] == policy].set_index("seed")
            common = sorted(set(base.index) & set(cur.index))
            if not common:
                continue
            for m in metrics:
                if m not in sub.columns:
                    continue
                bvals = base.loc[common, m].astype(float)
                cvals = cur.loc[common, m].astype(float)
                diffs = (cvals.values - bvals.values)
                stats = paired_ci(diffs, ci_level, bootstrap_iters, bootstrap_seed)
                rows.append({
                    "scenario": scenario, "policy": policy, "baseline": baseline_policy,
                    "metric": m, "mean_baseline": float(np.nanmean(bvals.values)),
                    "mean_policy": float(np.nanmean(cvals.values)), **stats,
                })
    return pd.DataFrame(rows)


def write_compare(
    run_dirs: list[Path], baseline_policy: str, out_dir: Path, **kw
) -> pd.DataFrame:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    df = compare_runs(run_dirs, baseline_policy, **kw)
    df.to_csv(out_dir / "compare.csv", index=False)
    return df
