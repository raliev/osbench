"""cheapest_myopic policy — minimizes the current shipping + handling cost."""

from __future__ import annotations

from ...config.policy import SourcingPolicyConfig
from ...core.candidates import enumerate_assignments, plan_to_assignment
from ...core.network import Network
from ...registry import register_sourcing
from ...types import Assignment, Decision, Order, PolicyContext
from ..base import candidates_for


@register_sourcing("cheapest_myopic")
class CheapestMyopicPolicy:
    name = "cheapest_myopic"

    def __init__(self, cfg: SourcingPolicyConfig, net: Network):
        self.cfg = cfg
        self.net = net

    def decide(self, order: Order, ctx: PolicyContext) -> Decision:
        cs = candidates_for(order, ctx, self.net)
        if cs.n_candidates == 0:
            return Assignment(order_id=order.order_id, lines=(), unassigned_lines=tuple(
                ln.line_id for ln in order.lines
            ))
        assigned, unassigned, cost = enumerate_assignments(
            cs, topk=self.cfg.topk_warehouses, split_penalty_fixed=self.cfg.split_penalty_fixed
        )
        return plan_to_assignment(cs, assigned, unassigned, cost)

    def on_period_start(self, ctx: PolicyContext) -> None:
        pass

    def fit(self, history: object) -> None:
        pass
