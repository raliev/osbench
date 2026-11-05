"""weighted_score factors and their min-max normalization across candidates.

Each factor is mapped to [0, 1] where higher is better, normalized min-max over the
current order's candidates so that weights are comparable across scenarios.
"""

from __future__ import annotations

import numpy as np

from ...config.policy import WeightedScoreConfig
from ...types import CandidateSet


def _minmax(x: np.ndarray, degenerate: float) -> np.ndarray:
    """Min-max to [0, 1]; constant input maps to the degenerate fill value."""
    lo = float(x.min())
    hi = float(x.max())
    if hi - lo < 1e-12:
        return np.full(x.shape, degenerate, dtype=np.float64)
    return (x.astype(np.float64) - lo) / (hi - lo)


def score_candidates(
    cs: CandidateSet,
    now_day: int,
    promise_day: int,
    wh_load: np.ndarray,
    wh_rank: np.ndarray,
    safety_headroom: np.ndarray,
    cfg: WeightedScoreConfig,
) -> np.ndarray:
    """Return a weighted score per candidate warehouse (higher is better).

    wh_load / wh_rank are indexed by global warehouse id; safety_headroom is per candidate
    (min over lines of coverage headroom above safety stock).
    """
    C = cs.n_candidates
    deg = cfg.degenerate_fill
    w = cfg.weights

    # Lower-is-better factors -> invert after min-max.
    f_distance = 1.0 - _minmax(cs.distance_km.astype(np.float64), deg)
    f_ship = 1.0 - _minmax(cs.ship_cost.astype(np.float64), deg)
    f_load = 1.0 - _minmax(wh_load[cs.wh_ids].astype(np.float64), deg)

    # Higher-is-better factors.
    f_rank = _minmax(wh_rank[cs.wh_ids].astype(np.float64), deg)
    f_safety = _minmax(safety_headroom.astype(np.float64), deg)

    # Fewer parcels: full-cover warehouses (single parcel) are preferred.
    f_splits = cs.full_cover_mask.astype(np.float64)
    if not cs.full_cover_mask.any():
        f_splits = np.full(C, deg)

    # Promise: binary on-time plus a linear bonus for extra slack days.
    cap = cfg.promise_slack_cap_days
    slack = promise_day - (now_day + cs.ship_days.astype(np.float64))
    on_time = (slack >= 0).astype(np.float64)
    bonus = np.clip(slack, 0, cap) / cap
    f_promise = cfg.promise_on_time_weight * on_time + (1 - cfg.promise_on_time_weight) * bonus
    f_promise = np.where(on_time > 0, f_promise, cfg.penalty_late)

    factors = {
        "distance": f_distance,
        "ship_price": f_ship,
        "promise": f_promise,
        "splits": f_splits,
        "above_safety": f_safety,
        "wh_load": f_load,
        "wh_rank": f_rank,
    }
    score = np.zeros(C, dtype=np.float64)
    for name, weight in w.items():
        score += weight * factors[name]
    return score
