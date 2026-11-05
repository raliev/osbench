"""Event journal schema (versioned)."""

from __future__ import annotations

from enum import IntEnum

import pyarrow as pa

SCHEMA_VERSION = 1


class JType(IntEnum):
    """Semantic types of journal records."""

    ORDER_CREATED = 0
    ASSIGNED = 1  # sourcing decision (carries policy_time_ms, est costs)
    RESERVED = 2
    SHIPPED = 3  # parcel shipped (ship_cost, handling)
    DELIVERED = 4
    CANCELLED = 5  # line cancelled
    LOST_SALE = 6  # line could not be sourced (penalty)
    REPLENISH_ORDERED = 7
    RECEIVED = 8  # PO receipt (purchase cost)
    HOLDING = 9  # daily holding charge (aggregated per warehouse)
    STOCKOUT = 10
    ASSIGNED_LINE = 11  # a line assigned to a warehouse (for the inspector: line -> warehouse)
    LATE_PENALTY = 12  # cost charged when a parcel is delivered after the promise
    PROMISE_REJECTED = 13  # order refused at checkout because the promise was too long
    DEFERRED = 14  # order parked under deferred sourcing, waiting for inbound stock / deadline
    SUBSTITUTED = 15  # a checkout line was swapped for an in-stock SKU of the same category


JTYPE_NAMES = {int(j): j.name.lower() for j in JType}

# Journal columns and their Arrow types.
COLUMNS = [
    ("t", pa.int64()),
    ("type", pa.int8()),
    ("order_id", pa.int64()),
    ("line_id", pa.int32()),
    ("sku", pa.int64()),
    ("warehouse", pa.int16()),
    ("parcel", pa.int16()),
    ("qty", pa.int32()),
    ("cost", pa.float32()),
    ("promised_at", pa.int64()),
    ("planned_at", pa.int64()),
    ("delivered_at", pa.int64()),
    ("policy_time_ms", pa.float32()),
]

ARROW_SCHEMA = pa.schema(
    COLUMNS, metadata={b"osbench_schema_version": str(SCHEMA_VERSION).encode()}
)
COLUMN_NAMES = [c[0] for c in COLUMNS]
