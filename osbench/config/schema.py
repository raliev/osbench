"""Pydantic schemas for the simulation scenario.

All models forbid extra fields (``extra="forbid"``) and are immutable (``frozen=True``),
so the config is the deterministic source of truth for a run and hashes correctly.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_Cfg = ConfigDict(extra="forbid", frozen=True)


class GeoConfig(BaseModel):
    model_config = _Cfg
    mode: Literal["plane", "haversine"] = "plane"
    plane_size: float = Field(default=1000.0, gt=0)  # km, for mode=plane


class RegionConfig(BaseModel):
    model_config = _Cfg
    region_id: int = Field(ge=0)
    name: str
    x: float  # coordinate (km on the plane, or lon for haversine)
    y: float  # coordinate (km on the plane, or lat for haversine)
    population_weight: float = Field(gt=0)


class WarehouseConfig(BaseModel):
    model_config = _Cfg
    warehouse_id: int = Field(ge=0)
    name: str
    x: float
    y: float
    rank: float = Field(default=0.5, ge=0, le=1)
    capacity_lines_per_day: int = Field(gt=0)  # daily pick capacity, in lines
    carrier_parcels_per_day: int = Field(default=1_000_000, gt=0)
    storage_capacity_units: int = Field(default=1_000_000_000, gt=0)
    handling_cost_per_line: float = Field(default=0.5, ge=0)
    storage_cost_per_unit_day: float = Field(default=0.01, ge=0)
    cutoff_min: int = Field(default=16 * 60, ge=0, le=1440)  # cut-off time of day (min)
    assortment_fraction: float = Field(default=1.0, gt=0, le=1)
    tier: Literal["fdc", "rdc"] = "fdc"  # forward vs regional DC; hub transfer topology is rdc→fdc


class TariffConfig(BaseModel):
    model_config = _Cfg
    # Zone bounds by distance (km). Zone i = the first threshold that is >= the distance.
    zone_bounds_km: tuple[float, ...] = (50.0, 300.0, 1000.0, 1e9)
    cost_by_zone: tuple[float, ...] = (3.0, 5.0, 7.0, 10.0)  # fixed per-parcel rate
    per_kg_by_zone: tuple[float, ...] = (0.0, 0.0, 0.0, 0.0)
    transit_days_by_zone: tuple[int, ...] = (1, 2, 4, 7)
    steepness: float = Field(default=1.0, gt=0)  # steepness multiplier (for sensitivity grids)
    delivery_noise_days_mean: float = Field(default=0.0, ge=0)  # geometric mean of noise beyond tariff

    @model_validator(mode="after")
    def _lengths_match(self):
        n = len(self.zone_bounds_km)
        for fld in ("cost_by_zone", "per_kg_by_zone", "transit_days_by_zone"):
            if len(getattr(self, fld)) != n:
                raise ValueError(f"{fld}: length must match zone_bounds_km ({n})")
        return self

    @property
    def n_zones(self) -> int:
        return len(self.zone_bounds_km)


class SkuConfig(BaseModel):
    model_config = _Cfg
    n_sku: int = Field(gt=0)
    zipf_a: float = Field(default=1.1, gt=1.0)  # popularity parameter (long tail)
    n_categories: int = Field(default=5, gt=0)
    weight_kg_range: tuple[float, float] = (0.1, 5.0)
    unit_cost_range: tuple[float, float] = (5.0, 200.0)
    price_markup_range: tuple[float, float] = (1.2, 2.5)
    # Weight matrix "region × category"; if None — uniform. Size n_regions×n_categories.
    region_category_matrix: tuple[tuple[float, ...], ...] | None = None


class SupplierConfig(BaseModel):
    model_config = _Cfg
    supplier_id: int = Field(ge=0)
    name: str
    unit_price: float = Field(ge=0)
    moq: int = Field(default=1, ge=1)
    lead_time_days: int = Field(default=7, ge=0)
    lead_time_std_days: float = Field(default=0.0, ge=0)
    fill_rate: float = Field(default=1.0, ge=0, le=1)
    capacity_per_period: int = Field(default=10_000_000, gt=0)
    period_days: int = Field(default=7, gt=0)
    sku_ids: tuple[int, ...] | Literal["all"] = "all"
    serves_warehouses: tuple[int, ...] | Literal["all"] = "all"


class DemandConfig(BaseModel):
    model_config = _Cfg
    base_orders_per_day: float = Field(gt=0)
    seasonality_amplitude: float = Field(default=0.2, ge=0, le=1)
    weekly_profile: tuple[float, ...] = (1.1, 1.0, 1.0, 1.0, 1.2, 1.4, 0.9)
    intraday_profile: tuple[float, ...] | None = None  # 24 hours; None → uniform
    lines_per_order_mean: float = Field(default=1.6, gt=1.0)  # geometric
    qty_max: int = Field(default=3, ge=1)
    # Customer-initiated cancellation: a fraction of orders are abandoned by the customer
    # after creation but before fulfillment, at a time drawn uniformly in [0, cancel_within_hours]
    # after creation. Pre-generated into the order stream (CRN-stable). This is the real
    # argument against early binding: a reserved unit held for a to-be-cancelled order is a
    # unit nobody else could buy in the meantime.
    cancel_rate: float = Field(default=0.0, ge=0, le=1)
    cancel_within_hours: float = Field(default=24.0, gt=0)

    @model_validator(mode="after")
    def _profiles(self):
        if len(self.weekly_profile) != 7:
            raise ValueError("weekly_profile must have 7 values")
        if self.intraday_profile is not None and len(self.intraday_profile) != 24:
            raise ValueError("intraday_profile must have 24 values")
        return self


class ShockConfig(BaseModel):
    model_config = _Cfg
    enabled: bool = False
    peak_multiplier: float = Field(default=4.0, gt=1)
    peak_duration_days: int = Field(default=3, ge=1)
    peak_start_day: int | None = None
    warehouse_outage_id: int | None = None
    warehouse_outage_days: int = Field(default=0, ge=0)
    warehouse_outage_start_day: int | None = None


class ReservationConfig(BaseModel):
    model_config = _Cfg
    enabled: bool = True


class AtpConfig(BaseModel):
    model_config = _Cfg
    method: Literal["on_hand_only", "basic", "cumulative"] = "basic"
    # When enabled, a line can only be sourced from warehouses within tolerance of the
    # cheapest/fastest candidate; warehouses outside it are dropped (distant stock becomes
    # unusable -> stranding, so the sourcing allocation affects lost sales). Off by default.
    tolerance_enabled: bool = False
    tolerance_price_frac: float = Field(default=0.5, ge=0)  # keep wh with ship_cost <= cheapest*(1+frac)
    tolerance_days: int = Field(default=2, ge=0)  # keep wh delivering within promise + this many days
    horizon_days: int = Field(default=7, ge=0)  # cumulative ATP: inbound within this horizon counts


class InventoryAccuracyConfig(BaseModel):
    model_config = _Cfg
    accuracy: float = Field(default=1.0, ge=0.5, le=1.0)
    error_mean: float = Field(default=2.0, gt=0)  # geometric mean of the book-keeping error


class InventorySyncConfig(BaseModel):
    """Stock-sync latency (2.12): OMS sees the WMS book stock through a delayed snapshot.

    Distinct from inventory_accuracy (the WMS itself being wrong): here the WMS is exact but
    the OMS snapshot read by sourcing/ATP lags behind WMS debits (picks) and credits (receipts).
    mode=none keeps the OMS reading the live book, so the journal is byte-identical to before.
    """

    model_config = _Cfg
    mode: Literal["none", "fixed_lag", "periodic"] = "none"
    lag_minutes: int = Field(default=0, ge=0)  # fixed_lag: delay before a WMS event reaches OMS
    period_minutes: int = Field(default=15, gt=0)  # periodic: batch snapshot refresh interval
    jitter_minutes: int = Field(default=0, ge=0)  # random extra delay (stream sync_jitter)
    lagged_events: Literal["picks", "receipts", "all"] = "all"
    # Per-warehouse override of the effective lag/period (e.g. RDCs 0, stores 60).
    per_warehouse: dict[int, int] | None = None


class PromiseConfig(BaseModel):
    model_config = _Cfg
    method: Literal["zone_fixed", "transit_plus_buffer", "atp_based", "percentile"] = (
        "transit_plus_buffer"
    )
    buffer_days: int = Field(default=1, ge=0)
    min_processing_days: int = Field(default=1, ge=0)
    cap_days: int = Field(default=10, ge=1)
    percentile: float = Field(default=0.9, ge=0, le=1)  # method=percentile: delivery-time quantile
    # Customer acceptance of a long promise: 1.0 at the zone baseline, linearly down to this
    # floor at cap_days. Default 1.0 => customers always accept (no promise rejections).
    p_accept_promise: float = Field(default=1.0, ge=0, le=1)


class DeferralConfig(BaseModel):
    model_config = _Cfg
    mode: Literal["none", "wait_for_receipt", "wait_to_consolidate"] = "none"
    arbitration: Literal["edf", "fifo", "value"] = "edf"
    max_waiting_per_sku: int = Field(default=1000, ge=0)
    safety_margin_days: float = Field(default=0.5, ge=0)
    penalty_deferral_miss: float = Field(default=2.0, ge=0)


class LaneConfig(BaseModel):
    """A directed warehouse→warehouse transfer lane (2.13). Any field left None falls back to a
    default (lead time by the inter-warehouse zone; costs/limits from the parent TransfersConfig)."""

    model_config = _Cfg
    from_wh: int = Field(ge=0)
    to_wh: int = Field(ge=0)
    lead_time_days: int | None = None  # None → derived from the inter-warehouse distance zone
    lead_time_noise_days: float = Field(default=0.0, ge=0)
    cost_per_unit: float = Field(default=0.0, ge=0)
    cost_per_shipment: float = Field(default=0.0, ge=0)  # fixed per lane per day ("a truck")
    cost_per_kg: float = Field(default=0.0, ge=0)
    capacity_units_per_day: int | None = None  # None → unlimited
    moq_units: int = Field(default=0, ge=0)
    fill_rate: float = Field(default=1.0, ge=0, le=1)


class TransferGuardsConfig(BaseModel):
    """Anti-oscillation guards shared by every transfer policy (2.13)."""

    model_config = _Cfg
    min_qty: int = Field(default=1, ge=0)
    max_source_share: float = Field(default=0.5, ge=0, le=1)  # of source available stock per period
    cooldown_days: int = Field(default=7, ge=0)  # no reverse move of the same SKU on the same lane
    max_transfer_share: float = Field(default=1.0, ge=0)  # guardrail on transferred/sold


class TransfersConfig(BaseModel):
    """Inter-warehouse rebalancing (2.13). Disabled by default, so no lane exists and the
    journal is byte-identical to a run without transfers."""

    model_config = _Cfg
    enabled: bool = False
    topology: Literal["all_pairs", "hub", "explicit"] = "all_pairs"
    policy: str = "none"
    review_days: int = Field(default=1, ge=1)
    protect_safety: bool = True  # a source never ships below its safety stock
    visible_to_atp: bool = True  # in-transit transfers count in the receiver's cumulative ATP
    on_outage: Literal["hold", "redirect"] = "hold"  # a downed warehouse does not send; inbound waits
    lanes: tuple[LaneConfig, ...] = ()  # explicit lanes, or per-pair overrides for all_pairs/hub
    guards: TransferGuardsConfig = TransferGuardsConfig()
    params: dict[str, Any] = Field(default_factory=dict)  # policy knobs (low_days/high_days/...)


class ProcessTimesConfig(BaseModel):
    """Lognormal service times inside the warehouse (minutes). Defaults match the legacy run."""

    model_config = _Cfg
    pick_median_min: float = Field(default=10.0, gt=0)
    pick_per_line_min: float = Field(default=3.0, ge=0)  # added per line in the parcel
    pack_median_min: float = Field(default=5.0, gt=0)
    sigma: float = Field(default=0.3, ge=0)  # lognormal shape (spread) of pick/pack times


class ShortageConfig(BaseModel):
    """Three shortage points (2.9): checkout, post-order, and pick.

    Probabilities drive the customer/operator choice at each point. Defaults keep the legacy
    behaviour: checkout check off, post-order & pick always partial-ship what's available.
    """

    model_config = _Cfg
    # Point 1 — checkout (ATP does not cover a line at order creation). Off by default.
    checkout_atp_check: bool = False
    p_partial: float = Field(default=1.0, ge=0, le=1)      # customer keeps a partial order
    p_substitute: float = Field(default=0.0, ge=0, le=1)   # accepts an SKU substitute (not modeled yet)
    p_accept_slow: float = Field(default=0.0, ge=0, le=1)  # accepts out-of-tolerance (not modeled yet)
    # Points 2 & 3.
    p_partial_ship: float = Field(default=1.0, ge=0, le=1)  # ship what's available, cancel the rest
    p_resource: float = Field(default=0.0, ge=0, le=1)      # pick: re-source a line elsewhere (not modeled yet)
    penalty_lost_sale: float = Field(default=5.0, ge=0)    # checkout refusal / uncovered line
    penalty_post_order: float = Field(default=8.0, ge=0)   # cancelled after the order was taken
    penalty_pick: float = Field(default=10.0, ge=0)        # cancelled at pick (phantom/conflict)
    penalty_late_per_day: float = Field(default=0.0, ge=0)  # cost per day delivered after the promise
    backorder: bool = False


class ScenarioConfig(BaseModel):
    model_config = _Cfg
    name: str
    description: str = Field(default="")  # free text: what makes this scenario special (+ edit log)
    sim_days: int = Field(default=90, gt=0)
    warmup_days: int = Field(default=14, ge=0)
    geo: GeoConfig = GeoConfig()
    regions: tuple[RegionConfig, ...]
    warehouses: tuple[WarehouseConfig, ...]
    tariffs: TariffConfig = TariffConfig()
    sku: SkuConfig
    suppliers: tuple[SupplierConfig, ...]
    demand: DemandConfig
    shocks: ShockConfig = ShockConfig()
    reservation: ReservationConfig = ReservationConfig()
    atp: AtpConfig = AtpConfig()
    inventory_accuracy: InventoryAccuracyConfig = InventoryAccuracyConfig()
    inventory_sync: InventorySyncConfig = InventorySyncConfig()
    promise: PromiseConfig = PromiseConfig()
    deferral: DeferralConfig = DeferralConfig()
    shortage: ShortageConfig = ShortageConfig()
    transfers: TransfersConfig = TransfersConfig()
    process_times: ProcessTimesConfig = ProcessTimesConfig()
    ship_when_ready: bool = True
    initial_cover_days: int = Field(default=14, ge=0)
    region_radius_km: float = Field(default=20.0, ge=0)  # customer scatter radius within a region
    # "Awaiting sourcing" wait (batching / anti-fraud / payment) before the policy is called,
    # drawn uniformly in [min, max] minutes (2.7). Default 5-30 min.
    sourcing_wait_min: int = Field(default=5, ge=0)
    sourcing_wait_max: int = Field(default=30, ge=0)

    @model_validator(mode="after")
    def _ids_unique(self):
        for coll, label in ((self.regions, "regions"), (self.warehouses, "warehouses")):
            ids = [getattr(x, f"{label[:-1]}_id") for x in coll]
            if len(ids) != len(set(ids)):
                raise ValueError(f"{label}: ids are not unique")
        rcm = self.sku.region_category_matrix
        if rcm is not None:
            if len(rcm) != len(self.regions):
                raise ValueError("region_category_matrix: number of rows != number of regions")
            if any(len(row) != self.sku.n_categories for row in rcm):
                raise ValueError("region_category_matrix: width != n_categories")
        if self.geo.mode == "plane" and len(self.warehouses) > 8:
            raise ValueError("geo.mode=plane is allowed only for small networks (<=8 warehouses)")
        if self.sourcing_wait_max < self.sourcing_wait_min:
            raise ValueError("sourcing_wait_max must be >= sourcing_wait_min")
        return self

    @property
    def n_regions(self) -> int:
        return len(self.regions)

    @property
    def n_warehouses(self) -> int:
        return len(self.warehouses)
