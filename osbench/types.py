"""Core data types shared between the engine and policies."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum

import numpy as np


class LineTerminal(IntEnum):
    """Terminal state of an order line."""

    NONE = 0
    DELIVERED = 1
    CANCELLED = 2


class CancelPoint(IntEnum):
    """Line cancellation point (for shortage metrics)."""

    NONE = 0
    CHECKOUT = 1
    POST_ORDER = 2
    PICK = 3
    HORIZON = 4  # still in the pipeline when the run ended (overloaded network)
    CUSTOMER = 5  # customer cancelled the order before it was fulfilled
    TRANSFER = 6  # marker on RECEIVED events for inbound stock that came via a transfer (2.13)


class DeferReason(StrEnum):
    WAIT_FOR_RECEIPT = "wait_for_receipt"
    WAIT_TO_CONSOLIDATE = "wait_to_consolidate"


@dataclass(frozen=True, slots=True)
class OrderLine:
    """Order line as seen by a policy."""

    line_id: int
    sku_id: int
    qty: int


@dataclass(frozen=True, slots=True)
class Order:
    """Order as seen by a sourcing policy."""

    order_id: int
    region_id: int
    zone_hint: int  # zone to the nearest warehouse, a hint (exact zone is computed per warehouse)
    dest_x: float
    dest_y: float
    created_min: int
    promise_min: int
    lines: tuple[OrderLine, ...]
    deferral_deadline_min: int | None = None


@dataclass(frozen=True, slots=True)
class LineAlloc:
    """Assignment of a single line to a warehouse."""

    line_id: int
    sku_id: int
    warehouse_id: int
    qty: int


@dataclass(frozen=True, slots=True)
class Assignment:
    """Policy decision: assignment of all (or some) order lines to warehouses."""

    order_id: int
    lines: tuple[LineAlloc, ...]
    unassigned_lines: tuple[int, ...] = ()  # lines without a warehouse → post-order branch
    est_ship_cost: float = 0.0
    est_handling_cost: float = 0.0
    reason: str = ""

    @property
    def n_parcels(self) -> int:
        return len({a.warehouse_id for a in self.lines})


@dataclass(frozen=True, slots=True)
class Transfer:
    """A transfer policy's decision to move stock along a lane (2.13)."""

    sku_id: int
    from_wh: int
    to_wh: int
    qty: int


@dataclass
class TransferContext:
    """State a transfer policy sees each review (2.13). Arrays are [n_sku, n_wh] views.

    ``lanes`` maps (from_wh, to_wh) → remaining units the lane can still carry this period.
    """

    now_day: int
    snapshot: np.ndarray       # OMS stock view (= book when sync off)
    reserved: np.ndarray
    in_transit: np.ndarray     # inbound (supplier + transfer) not yet received
    safety_stock: np.ndarray
    demand_rate: np.ndarray    # [n_sku, n_wh] realized units/day so far
    lanes: dict[tuple[int, int], int]
    params: dict


@dataclass(frozen=True, slots=True)
class Defer:
    """Deferred decision (v2 extension point; in v1 the engine supports the type, policies don't return it)."""

    order_id: int
    until_min: int | None
    reason: str


Decision = Assignment | Defer


@dataclass(frozen=True, slots=True)
class CandidateSet:
    """Result of the candidate engine for a single order.

    All arrays are indexed consistently along the [n_cand] and [n_line] axes.
    """

    order_id: int
    wh_ids: np.ndarray  # [C] int32 — candidate warehouses (cover >=1 lines)
    cover: np.ndarray  # [C, L] int32 — units of line l covered by warehouse c (min(atp, qty))
    full_cover_mask: np.ndarray  # [C] bool — warehouse fully covers all lines
    ship_cost: np.ndarray  # [C] float32 — tariff warehouse→order zone
    ship_days: np.ndarray  # [C] int32
    handling: np.ndarray  # [C] float32
    distance_km: np.ndarray  # [C] float32
    line_ids: np.ndarray  # [L] int32 — mapping of cover columns to order lines
    line_sku: np.ndarray  # [L] int32
    line_qty: np.ndarray  # [L] int32

    @property
    def n_candidates(self) -> int:
        return int(self.wh_ids.shape[0])

    @property
    def n_lines(self) -> int:
        return int(self.line_ids.shape[0])


@dataclass(frozen=True, slots=True)
class Receipt:
    """Expected inbound receipt (for policies and cumulative ATP)."""

    sku_id: int
    warehouse_id: int
    qty: int
    eta_min: int


@dataclass
class PolicyContext:
    """State snapshot visible to a policy. Physical on-hand is NOT included.

    Inventory arrays are indexed [n_sku, n_wh]. Passed as views, not copies.
    """

    now_min: int
    now_day: int
    book_onhand: np.ndarray  # [n_sku, n_wh] int32 — book on-hand
    reserved: np.ndarray  # [n_sku, n_wh] int32
    atp: np.ndarray  # [n_sku, n_wh] int32
    in_transit: np.ndarray  # [n_sku, n_wh] int32
    safety_stock: np.ndarray  # [n_sku, n_wh] int32
    wh_load: np.ndarray  # [n_wh] float32 — current load 0..1+
    wh_rank: np.ndarray  # [n_wh] float32
    rng: np.random.Generator  # 'policy' stream — the only source of randomness for policies
    receipts: tuple[Receipt, ...] = field(default_factory=tuple)
    forecast: np.ndarray | None = None  # [n_sku, n_wh, H], if use_forecast
