"""Faithful map preview: sample customer locations the same way the generator does.

Customers = region centroid + a uniform random offset within region_radius_km, with the
region chosen proportionally to population_weight. This mirrors osbench.generate.demand so
the preview matches an actual run.
"""

from __future__ import annotations

import numpy as np

from ..config.schema import ScenarioConfig


def preview(config_json: dict, n_customers: int = 500, seed: int = 0) -> dict:
    cfg = ScenarioConfig.model_validate(config_json)
    rng = np.random.Generator(np.random.Philox(np.random.SeedSequence(seed)))

    regions = cfg.regions
    rx = np.array([r.x for r in regions], dtype=np.float64)
    ry = np.array([r.y for r in regions], dtype=np.float64)
    w = np.array([r.population_weight for r in regions], dtype=np.float64)
    w = w / w.sum()

    idx = np.searchsorted(np.cumsum(w), rng.random(n_customers), side="left")
    idx = np.clip(idx, 0, len(regions) - 1)
    ang = rng.uniform(0, 2 * np.pi, n_customers)
    rad = cfg.region_radius_km * np.sqrt(rng.random(n_customers))
    cx = rx[idx] + rad * np.cos(ang)
    cy = ry[idx] + rad * np.sin(ang)

    # Nearest-warehouse label for each sampled customer (illustrative, geometry only).
    wx = np.array([wh.x for wh in cfg.warehouses], dtype=np.float64)
    wy = np.array([wh.y for wh in cfg.warehouses], dtype=np.float64)
    if cfg.geo.mode == "plane":
        d2 = (cx[:, None] - wx[None, :]) ** 2 + (cy[:, None] - wy[None, :]) ** 2
        nearest = d2.argmin(axis=1)
    else:
        nearest = np.zeros(n_customers, dtype=int)

    return {
        "mode": cfg.geo.mode,
        "plane_size": cfg.geo.plane_size,
        "warehouses": [
            {"name": wh.name, "x": wh.x, "y": wh.y, "capacity": wh.capacity_lines_per_day}
            for wh in cfg.warehouses
        ],
        "regions": [
            {"name": r.name, "x": r.x, "y": r.y, "weight": r.population_weight}
            for r in regions
        ],
        "radius": cfg.region_radius_km,
        "customers": {
            "x": cx.round(1).tolist(), "y": cy.round(1).tolist(),
            "nearest": nearest.astype(int).tolist(),
        },
    }
