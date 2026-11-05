"""Paired comparison statistics: t-based CI and bootstrap CI."""

from __future__ import annotations

import numpy as np


def paired_ci(
    diffs: np.ndarray,
    ci_level: float = 0.95,
    bootstrap_iters: int = 10_000,
    bootstrap_seed: int = 12345,
) -> dict:
    """Paired statistics for per-seed metric differences (policy - baseline).

    Returns mean difference, t-based CI, bootstrap percentile CI, t-statistic, p-value,
    and a significance flag (CI excludes 0).
    """
    d = np.asarray(diffs, dtype=np.float64)
    d = d[~np.isnan(d)]
    n = d.shape[0]
    out = {
        "n_seeds": int(n),
        "mean_diff": float(d.mean()) if n else float("nan"),
        "t_ci_low": float("nan"),
        "t_ci_high": float("nan"),
        "boot_ci_low": float("nan"),
        "boot_ci_high": float("nan"),
        "t_stat": float("nan"),
        "p_value": float("nan"),
        "significant_95": False,
    }
    if n < 2:
        return out

    mean = d.mean()
    sd = d.std(ddof=1)
    se = sd / np.sqrt(n)
    alpha = 1.0 - ci_level

    if se == 0:
        # Identical across seeds: difference is exactly the mean, zero variance.
        out["t_ci_low"] = out["t_ci_high"] = mean
        out["boot_ci_low"] = out["boot_ci_high"] = mean
        out["t_stat"] = float("inf") if mean != 0 else 0.0
        out["p_value"] = 0.0 if mean != 0 else 1.0
        out["significant_95"] = bool(mean != 0)
        return out

    from scipy import stats

    tcrit = stats.t.ppf(1 - alpha / 2, df=n - 1)
    out["t_ci_low"] = float(mean - tcrit * se)
    out["t_ci_high"] = float(mean + tcrit * se)
    tstat = mean / se
    out["t_stat"] = float(tstat)
    out["p_value"] = float(2 * stats.t.sf(abs(tstat), df=n - 1))

    rng = np.random.Generator(np.random.Philox(np.random.SeedSequence(bootstrap_seed)))
    idx = rng.integers(0, n, size=(bootstrap_iters, n))
    boot_means = d[idx].mean(axis=1)
    out["boot_ci_low"] = float(np.quantile(boot_means, alpha / 2))
    out["boot_ci_high"] = float(np.quantile(boot_means, 1 - alpha / 2))

    out["significant_95"] = bool((out["t_ci_low"] > 0) or (out["t_ci_high"] < 0))
    return out


def pareto_front(points: np.ndarray) -> np.ndarray:
    """Indices of non-dominated points for minimization on both axes (cost, -on_time)."""
    n = points.shape[0]
    keep = np.ones(n, dtype=bool)
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            if (points[j] <= points[i]).all() and (points[j] < points[i]).any():
                keep[i] = False
                break
    return np.nonzero(keep)[0]
