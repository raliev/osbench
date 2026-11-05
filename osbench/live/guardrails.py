"""Guardrails: automatic early stopping of a run based on snapshots."""

from __future__ import annotations

from ..config.experiment import GuardrailConfig

_MIN_PER_DAY = 1440


class GuardrailEngine:
    """Checks snapshots and decides whether to stop the run.

    Two rules are implemented in v1:
    - lost sales rate above the threshold after warmup → aborted (or warn);
    - share of SKUs in stockout above the threshold after warmup → aborted (or warn).
    """

    def __init__(self, cfg: GuardrailConfig, lost_sales_max: float = 0.30,
                 stockout_max: float = 0.80):
        self.cfg = cfg
        self.lost_sales_max = lost_sales_max
        self.stockout_max = stockout_max
        self.triggered: list[dict] = []

    def check(self, snap: dict) -> tuple[str | None, str]:
        """Return (action, reason). action ∈ {None, 'aborted', 'warn'}."""
        if not self.cfg.enabled:
            return None, ""
        if snap["sim_day"] < self.cfg.warmup_days:
            return None, ""
        lsr = snap.get("lost_sales_rate", 0.0)
        if lsr > self.lost_sales_max:
            return self._fire(snap, f"lost_sales_rate={lsr:.3f} > {self.lost_sales_max}")
        so = snap.get("stockout_share", 0.0)
        if so > self.stockout_max:
            return self._fire(snap, f"stockout_share={so:.3f} > {self.stockout_max}")
        return None, ""

    def _fire(self, snap: dict, reason: str) -> tuple[str, str]:
        action = "warn" if self.cfg.mode == "warn" else "aborted"
        self.triggered.append({"sim_day": snap["sim_day"], "reason": reason, "action": action})
        return action, reason
