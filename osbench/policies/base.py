"""Shared helpers for sourcing policies."""

from __future__ import annotations

import numpy as np

from ..core.candidates import build_candidate_set
from ..core.network import Network
from ..types import CandidateSet, Order, PolicyContext


def candidates_for(order: Order, ctx: PolicyContext, net: Network) -> CandidateSet:
    """Build the candidate set for an order from the policy context."""
    line_ids = np.array([ln.line_id for ln in order.lines], dtype=np.int32)
    line_sku = np.array([ln.sku_id for ln in order.lines], dtype=np.int64)
    line_qty = np.array([ln.qty for ln in order.lines], dtype=np.int32)
    return build_candidate_set(
        order.order_id,
        line_ids,
        line_sku,
        line_qty,
        order.dest_x,
        order.dest_y,
        ctx.now_day,
        net,
        ctx.atp,
        promise_day=order.promise_min // 1440,
    )
