"""pull_base_stock (2.13): each receiver pulls from a source up to an order-up-to level.

The receiver's target cover is ``S`` days of its own demand; when on-hand + inbound falls below it,
it pulls the gap from the cheapest lane whose source can spare the stock. With topology=hub this is
the classic RDC→FDC replenishment network.
"""

from __future__ import annotations

import numpy as np

from ...config.schema import TransfersConfig
from ...core.network import Network
from ...registry import register_transfer
from ...types import Transfer, TransferContext


@register_transfer("pull_base_stock")
class PullBaseStockPolicy:
    name = "pull_base_stock"

    def __init__(self, cfg: TransfersConfig, net: Network):
        self.cfg = cfg
        self.net = net
        p = cfg.params
        self.S_days = float(p.get("S", 10.0))  # order-up-to cover at the receiver, in days

    def decide(self, ctx: TransferContext) -> list[Transfer]:
        avail = np.clip(ctx.snapshot - ctx.reserved, 0, None)
        rate = ctx.demand_rate
        position = ctx.snapshot + ctx.in_transit - ctx.reserved
        target = np.ceil(rate * self.S_days).astype(np.int64)
        moves: list[Transfer] = []
        remaining = dict(ctx.lanes)
        lanes = sorted(ctx.lanes.items(), key=lambda kv: self._lane_cost(kv[0]))
        for sku in range(rate.shape[0]):
            for (fr, to), _cap in lanes:
                if remaining[(fr, to)] <= 0:
                    continue
                gap = int(target[sku, to] - position[sku, to])
                if gap <= 0:
                    continue
                give = int(min(gap, remaining[(fr, to)], avail[sku, fr]))
                if give <= 0:
                    continue
                moves.append(Transfer(sku_id=sku, from_wh=fr, to_wh=to, qty=give))
                remaining[(fr, to)] -= give
                avail[sku, fr] -= give
                position[sku, to] += give
        return moves

    def _lane_cost(self, lane: tuple[int, int]) -> float:
        fr, to = lane
        return float(np.hypot(self.net.wh_x[fr] - self.net.wh_x[to],
                              self.net.wh_y[fr] - self.net.wh_y[to]))
