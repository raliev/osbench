"""Demo "broken" policies for testing guardrails.

Not meant for comparisons — only to demonstrate automatic stopping.
"""

from __future__ import annotations

from ...config.policy import SourcingPolicyConfig
from ...core.candidates import plan_to_assignment
from ...core.network import Network
from ...registry import register_sourcing
from ...types import Assignment, Decision, Order, PolicyContext
from ..base import candidates_for


@register_sourcing("broken_wh0")
class BrokenWh0Policy:
    """Sends everything only to warehouse 0; if it can't cover a line, the line is lost.

    Quickly drains a single warehouse → spike in lost sales → the guardrail must stop the run.
    """

    name = "broken_wh0"

    def __init__(self, cfg: SourcingPolicyConfig, net: Network):
        self.cfg = cfg
        self.net = net

    def decide(self, order: Order, ctx: PolicyContext) -> Decision:
        cs = candidates_for(order, ctx, self.net)
        assigned: dict[int, int] = {}
        unassigned: set[int] = set()
        for l in range(cs.n_lines):
            c0 = [c for c in range(cs.n_candidates)
                  if int(cs.wh_ids[c]) == 0 and cs.cover[c, l] >= cs.line_qty[l]]
            if c0:
                assigned[l] = c0[0]
            else:
                unassigned.add(l)
        if cs.n_candidates == 0:
            return Assignment(order_id=order.order_id, lines=(),
                              unassigned_lines=tuple(ln.line_id for ln in order.lines))
        return plan_to_assignment(cs, assigned, unassigned, 0.0)
