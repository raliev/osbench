"""random policy — a random warehouse that has stock (lower bound).

Randomness is drawn ONLY from ctx.rng (the 'policy' stream).
"""

from __future__ import annotations

from ...config.policy import SourcingPolicyConfig
from ...core.candidates import plan_to_assignment
from ...core.network import Network
from ...registry import register_sourcing
from ...types import Assignment, Decision, Order, PolicyContext
from ..base import candidates_for


@register_sourcing("random")
class RandomPolicy:
    name = "random"

    def __init__(self, cfg: SourcingPolicyConfig, net: Network):
        self.cfg = cfg
        self.net = net

    def decide(self, order: Order, ctx: PolicyContext) -> Decision:
        cs = candidates_for(order, ctx, self.net)
        if cs.n_candidates == 0:
            return Assignment(order_id=order.order_id, lines=(), unassigned_lines=tuple(
                ln.line_id for ln in order.lines
            ))
        assigned: dict[int, int] = {}
        unassigned: set[int] = set()
        for l in range(cs.n_lines):
            eligible = [c for c in range(cs.n_candidates) if cs.cover[c, l] >= cs.line_qty[l]]
            if not eligible:
                unassigned.add(l)
            else:
                assigned[l] = int(ctx.rng.choice(eligible))
        return plan_to_assignment(cs, assigned, unassigned, 0.0)

    def on_period_start(self, ctx: PolicyContext) -> None:  # noqa: D102
        pass

    def fit(self, history: object) -> None:  # noqa: D102
        pass
