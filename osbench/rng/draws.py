"""Vectorized distribution draws over numpy.Generator.

Thin wrappers so that all generation code goes through a single API (convenient
for a future switch to counter-based Philox without changing calling code).
"""

from __future__ import annotations

import numpy as np


def zipf_cdf(n: int, a: float) -> np.ndarray:
    """CDF of a truncated Zipf over ranks 1..n (for selecting SKUs by popularity)."""
    ranks = np.arange(1, n + 1, dtype=np.float64)
    pmf = ranks ** (-a)
    pmf /= pmf.sum()
    return np.cumsum(pmf)


def sample_from_cdf(rng: np.random.Generator, cdf: np.ndarray, size: int) -> np.ndarray:
    """Select indices 0..len(cdf)-1 from the given CDF (binary search)."""
    u = rng.random(size)
    return np.searchsorted(cdf, u, side="left").astype(np.int64)


def geometric_counts(rng: np.random.Generator, mean: float, size: int) -> np.ndarray:
    """Number of lines in an order ~ geometric with mean ``mean`` (>=1)."""
    p = 1.0 / mean
    return rng.geometric(p, size=size).astype(np.int32)


def lognormal_minutes(
    rng: np.random.Generator, median_min: float, sigma: float, size: int | None = None
) -> np.ndarray | float:
    """Log-normal time (minutes) with the given median."""
    mu = np.log(median_min)
    return rng.lognormal(mean=mu, sigma=sigma, size=size)
