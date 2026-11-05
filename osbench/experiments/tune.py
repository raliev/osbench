"""Offline weight tuning for weighted_score (RQ1).

Random (Dirichlet) search over the factor weights, scoring each candidate by the mean
total cost per order across training seeds. Simple and dependency-free (no optuna/CMA); the
search RNG is seeded so a tune run is reproducible. Returns the best weights and the full trial
log, which ``osbench tune`` writes to JSON for use as ``weighted_score_tuned`` params.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np

from ..config.loader import load_scenario
from ..config.policy import DEFAULT_WS_WEIGHTS, SourcingPolicyConfig, WeightedScoreConfig
from ..core.engine import SimEngine
from ..generate import build_orders, build_world
from ..metrics.compute import compute_metrics
from ..rng.streams import RngHub

_FACTORS = list(DEFAULT_WS_WEIGHTS)


def evaluate_weights(
    scenario_ref: str, weights: dict[str, float], seeds: list[int],
    replenishment: str = "base_stock",
) -> float:
    """Mean total cost per order over the seeds for a weighted_score run with these weights."""
    cfg = load_scenario(scenario_ref)
    scfg = SourcingPolicyConfig(
        name="weighted_score", weighted_score=WeightedScoreConfig(weights=weights)
    )
    costs = []
    for seed in seeds:
        hub = RngHub(seed)
        world = build_world(cfg, hub)
        orders = build_orders(world, cfg, hub)
        with tempfile.TemporaryDirectory() as d:
            SimEngine(world, orders, seed, "weighted_score", replenishment,
                      sourcing_cfg=scfg).run(Path(d))
            m = compute_metrics(d, warmup_days=cfg.warmup_days)
        costs.append(m["total_cost_per_order"])
    return float(np.mean(costs))


def tune_weighted_score(
    scenario_ref: str, seeds: list[int], n_trials: int = 40,
    replenishment: str = "base_stock", tune_seed: int = 0,
) -> dict:
    """Dirichlet random search over factor weights. Returns {best_weights, best_cost, trials}.

    The default weights are always evaluated first so the tuned result never loses to the baseline.
    """
    rng = np.random.default_rng(tune_seed)
    n = len(_FACTORS)
    trials = []
    best_w, best_cost = dict(DEFAULT_WS_WEIGHTS), None

    def consider(weights: dict[str, float]) -> None:
        nonlocal best_w, best_cost
        cost = evaluate_weights(scenario_ref, weights, seeds, replenishment)
        trials.append({"weights": weights, "cost": cost})
        if best_cost is None or cost < best_cost:
            best_w, best_cost = dict(weights), cost

    consider(dict(DEFAULT_WS_WEIGHTS))  # baseline first
    for _ in range(max(0, n_trials - 1)):
        # Concentration 2.0 keeps samples spread but not too spiky around the simplex.
        w = rng.dirichlet(np.full(n, 2.0))
        w = w / w.sum()  # exact simplex (weights must sum to 1.0)
        consider({f: float(wi) for f, wi in zip(_FACTORS, w)})

    return {"best_weights": best_w, "best_cost": best_cost, "n_trials": len(trials),
            "trials": trials}
