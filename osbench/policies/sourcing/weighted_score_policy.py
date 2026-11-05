"""weighted_score policy (industrial baseline, Adobe Commerce OMS / US 10,839,338).

Each candidate warehouse gets score = sum_i w_i * f_i, f_i in [0, 1], sum w_i = 1,
with factors normalized min-max across the order's candidates. A full-cover warehouse
with the highest score is chosen (single parcel); otherwise lines are filled greedily
by descending warehouse score.
"""

from __future__ import annotations

import numpy as np

from ...config.policy import SourcingPolicyConfig, WeightedScoreConfig
from ...core.candidates import plan_to_assignment
from ...core.network import Network
from ...registry import register_sourcing
from ...types import Assignment, Decision, Order, PolicyContext
from ..base import candidates_for
from .factors import score_candidates


@register_sourcing("weighted_score")
class WeightedScorePolicy:
    name = "weighted_score"

    def __init__(self, cfg: SourcingPolicyConfig, net: Network):
        self.cfg = cfg
        self.net = net
        self.ws: WeightedScoreConfig = cfg.weighted_score or WeightedScoreConfig()

    def decide(self, order: Order, ctx: PolicyContext) -> Decision:
        cs = candidates_for(order, ctx, self.net)
        if cs.n_candidates == 0:
            return Assignment(
                order_id=order.order_id,
                lines=(),
                unassigned_lines=tuple(ln.line_id for ln in order.lines),
            )

        # Coverage headroom above safety stock (min over lines), proxy for the above_safety factor.
        safety_headroom = cs.cover.min(axis=1).astype(np.float64)
        promise_day = order.promise_min // 1440
        scores = score_candidates(
            cs, ctx.now_day, promise_day, ctx.wh_load, ctx.wh_rank, safety_headroom, self.ws
        )

        full = np.nonzero(cs.full_cover_mask)[0]
        if full.size > 0:
            c = int(full[np.argmax(scores[full])])
            assigned = {l: c for l in range(cs.n_lines)}
            return plan_to_assignment(cs, assigned, set(), 0.0)

        # No single warehouse covers everything: fill each line by the highest-scoring
        # warehouse that can cover it.
        order_by_score = np.argsort(-scores, kind="stable")
        assigned: dict[int, int] = {}
        unassigned: set[int] = set()
        for l in range(cs.n_lines):
            placed = False
            for c in order_by_score:
                if cs.cover[c, l] >= cs.line_qty[l]:
                    assigned[l] = int(c)
                    placed = True
                    break
            if not placed:
                unassigned.add(l)
        return plan_to_assignment(cs, assigned, unassigned, 0.0)

    def on_period_start(self, ctx: PolicyContext) -> None:
        pass

    def fit(self, history: object) -> None:
        pass
