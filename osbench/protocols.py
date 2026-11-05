"""Policy plugin protocols."""

from __future__ import annotations

from typing import ClassVar, Protocol, runtime_checkable

from .types import Decision, Order, PolicyContext


@runtime_checkable
class SourcingPolicy(Protocol):
    """Warehouse selection policy. Implementation = one file + registration in the registry."""

    name: ClassVar[str]

    def decide(self, order: Order, ctx: PolicyContext) -> Decision:
        """Return the assignment of order lines to warehouses (or Defer)."""
        ...

    def on_period_start(self, ctx: PolicyContext) -> None:
        """Optional period-start hook (recomputing dual prices, etc.)."""
        ...

    def fit(self, history: object) -> None:
        """Optional training on history (for trainable v2 policies)."""
        ...


@runtime_checkable
class ReplenishmentPolicy(Protocol):
    """Warehouse replenishment policy."""

    name: ClassVar[str]

    def decide(self, ctx: PolicyContext) -> list:
        """Return a list of ReplenishOrder purchases; called on the R schedule."""
        ...
