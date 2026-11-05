"""Generation of the static world: regions, warehouses, SKUs, suppliers, assortment.

All randomness is drawn from the 'geo' and 'catalog' RNG streams, so the world is
fully reproducible from the master seed.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config.schema import ScenarioConfig
from ..rng.streams import RngHub


@dataclass(slots=True)
class World:
    """The run's static world as flat tables (pandas)."""

    regions: pd.DataFrame
    warehouses: pd.DataFrame
    skus: pd.DataFrame
    suppliers: pd.DataFrame
    assortment: np.ndarray  # [n_sku, n_wh] uint8 — whether a warehouse can stock the SKU
    config: ScenarioConfig

    @property
    def n_sku(self) -> int:
        return len(self.skus)

    @property
    def n_wh(self) -> int:
        return len(self.warehouses)

    @property
    def n_regions(self) -> int:
        return len(self.regions)


def build_world(cfg: ScenarioConfig, hub: RngHub) -> World:
    catalog = hub.generator("catalog")
    geo = hub.generator("geo")

    regions = pd.DataFrame(
        {
            "region_id": [r.region_id for r in cfg.regions],
            "name": [r.name for r in cfg.regions],
            "x": [r.x for r in cfg.regions],
            "y": [r.y for r in cfg.regions],
            "population_weight": [r.population_weight for r in cfg.regions],
        }
    )

    warehouses = pd.DataFrame(
        {
            "warehouse_id": [w.warehouse_id for w in cfg.warehouses],
            "name": [w.name for w in cfg.warehouses],
            "x": [w.x for w in cfg.warehouses],
            "y": [w.y for w in cfg.warehouses],
            "rank": [w.rank for w in cfg.warehouses],
            "capacity_lines_per_day": [w.capacity_lines_per_day for w in cfg.warehouses],
            "carrier_parcels_per_day": [w.carrier_parcels_per_day for w in cfg.warehouses],
            "storage_capacity_units": [w.storage_capacity_units for w in cfg.warehouses],
            "handling_cost_per_line": [w.handling_cost_per_line for w in cfg.warehouses],
            "storage_cost_per_unit_day": [w.storage_cost_per_unit_day for w in cfg.warehouses],
            "cutoff_min": [w.cutoff_min for w in cfg.warehouses],
            "assortment_fraction": [w.assortment_fraction for w in cfg.warehouses],
        }
    )

    skus = _build_skus(cfg, catalog)
    suppliers = _build_suppliers(cfg)
    assortment = _build_assortment(cfg, geo)

    return World(
        regions=regions,
        warehouses=warehouses,
        skus=skus,
        suppliers=suppliers,
        assortment=assortment,
        config=cfg,
    )


def _build_skus(cfg: ScenarioConfig, rng: np.random.Generator) -> pd.DataFrame:
    n = cfg.sku.n_sku
    sku_ids = np.arange(n, dtype=np.int64)
    category = rng.integers(0, cfg.sku.n_categories, size=n).astype(np.int32)
    w_lo, w_hi = cfg.sku.weight_kg_range
    weight = rng.uniform(w_lo, w_hi, size=n)
    volume = weight * rng.uniform(0.8, 1.2, size=n)
    c_lo, c_hi = cfg.sku.unit_cost_range
    unit_cost = rng.uniform(c_lo, c_hi, size=n)
    m_lo, m_hi = cfg.sku.price_markup_range
    price = unit_cost * rng.uniform(m_lo, m_hi, size=n)
    # Popularity rank (0 = best seller); shuffle so id does not correlate with rank.
    popularity_rank = rng.permutation(n).astype(np.int64)
    return pd.DataFrame(
        {
            "sku_id": sku_ids,
            "category": category,
            "weight_kg": weight,
            "volume": volume,
            "unit_cost": unit_cost,
            "price": price,
            "popularity_rank": popularity_rank,
        }
    )


def _build_suppliers(cfg: ScenarioConfig) -> pd.DataFrame:
    rows = []
    for s in cfg.suppliers:
        rows.append(
            {
                "supplier_id": s.supplier_id,
                "name": s.name,
                "unit_price": s.unit_price,
                "moq": s.moq,
                "lead_time_days": s.lead_time_days,
                "lead_time_std_days": s.lead_time_std_days,
                "fill_rate": s.fill_rate,
                "capacity_per_period": s.capacity_per_period,
                "period_days": s.period_days,
            }
        )
    return pd.DataFrame(rows)


def _build_assortment(cfg: ScenarioConfig, rng: np.random.Generator) -> np.ndarray:
    n_sku = cfg.sku.n_sku
    n_wh = cfg.n_warehouses
    assortment = np.zeros((n_sku, n_wh), dtype=np.uint8)
    for j, w in enumerate(cfg.warehouses):
        frac = w.assortment_fraction
        if frac >= 1.0:
            assortment[:, j] = 1
        else:
            keep = rng.random(n_sku) < frac
            assortment[keep, j] = 1
            # Ensure every warehouse stocks at least one SKU.
            if not keep.any():
                assortment[0, j] = 1
    return assortment
