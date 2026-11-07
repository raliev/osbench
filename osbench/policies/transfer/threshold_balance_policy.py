"""threshold_balance (2.13): move stock from warehouses with plenty to warehouses running low.

For each SKU, a receiver whose days-of-cover falls below ``low_days`` pulls up to ``target_days``
from the cheapest lane out of a source whose cover stays above ``high_days``. The low/high gap is
hysteresis — with low == high the policy ping-pongs stock, which is what the guards exist to stop.
"""

from __future__ import annotations

import numpy as np

from ...config.schema import TransfersConfig
from ...core.network import Network
from ...registry import register_transfer
from ...types import Transfer, TransferContext


@register_transfer("threshold_balance")
class ThresholdBalancePolicy:
    name = "threshold_balance"

    def __init__(self, cfg: TransfersConfig, net: Network):
        self.cfg = cfg
        self.net = net
        p = cfg.params
        self.low_days = float(p.get("low_days", 5.0))
        self.high_days = float(p.get("high_days", 15.0))
        self.target_days = float(p.get("target_days", 10.0))

    def decide(self, ctx: TransferContext) -> list[Transfer]:
        avail = np.clip(ctx.snapshot - ctx.reserved, 0, None)  # units a source could give up
        rate = ctx.demand_rate  # [n_sku, n_wh] units/day
        # Cover in days (receiver uses on-hand + inbound so a transfer already on the way counts).
        incoming = ctx.snapshot + ctx.in_transit - ctx.reserved
        moves: list[Transfer] = []
        # Cheapest lane first keeps the total transfer bill down.
        lanes = sorted(ctx.lanes.items(), key=lambda kv: self._lane_cost(kv[0]))
        remaining = dict(ctx.lanes)
        for sku in range(rate.shape[0]):
            for (fr, to), _cap in lanes:
                if remaining[(fr, to)] <= 0:
                    continue
                r = rate[sku, to]
                if r <= 0:
                    continue
                cover_to = incoming[sku, to] / r
                if cover_to >= self.low_days:
                    continue
                rs = rate[sku, fr]
                cover_from = (avail[sku, fr] / rs) if rs > 0 else np.inf
                if cover_from <= self.high_days:
                    continue
                want = int(np.ceil((self.target_days - cover_to) * r))
                give = int(min(want, remaining[(fr, to)], avail[sku, fr]))
                if give <= 0:
                    continue
                moves.append(Transfer(sku_id=sku, from_wh=fr, to_wh=to, qty=give))
                remaining[(fr, to)] -= give
                avail[sku, fr] -= give
                incoming[sku, to] += give
        return moves

    def _lane_cost(self, lane: tuple[int, int]) -> float:
        fr, to = lane
        d = float(np.hypot(self.net.wh_x[fr] - self.net.wh_x[to], self.net.wh_y[fr] - self.net.wh_y[to]))
        return d
