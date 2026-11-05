"""Configs for sourcing and replenishment policies."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_Cfg = ConfigDict(extra="forbid", frozen=True)

# weighted_score factors and their default weights (sum = 1.0).
DEFAULT_WS_WEIGHTS: dict[str, float] = {
    "distance": 0.20,
    "ship_price": 0.20,
    "promise": 0.25,
    "splits": 0.10,
    "above_safety": 0.10,
    "wh_load": 0.10,
    "wh_rank": 0.05,
}


class WeightedScoreConfig(BaseModel):
    model_config = _Cfg
    weights: dict[str, float] = Field(default_factory=lambda: dict(DEFAULT_WS_WEIGHTS))
    promise_slack_cap_days: int = Field(default=3, ge=1)
    promise_on_time_weight: float = Field(default=0.7, ge=0, le=1)
    penalty_late: float = Field(default=0.0, ge=0, le=1)  # f_promise when late
    degenerate_fill: float = Field(default=0.5, ge=0, le=1)  # minmax when max==min

    @model_validator(mode="after")
    def _weights_valid(self):
        unknown = set(self.weights) - set(DEFAULT_WS_WEIGHTS)
        if unknown:
            raise ValueError(f"unknown weighted_score factors: {sorted(unknown)}")
        s = sum(self.weights.values())
        if abs(s - 1.0) > 1e-9:
            raise ValueError(f"weighted_score weights sum = {s}, must be 1.0")
        return self


class SourcingPolicyConfig(BaseModel):
    model_config = _Cfg
    name: str
    topk_warehouses: int = Field(default=4, ge=1, le=8)
    split_penalty_fixed: float = Field(default=1.0, ge=0)
    weighted_score: WeightedScoreConfig | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class ReplenishmentPolicyConfig(BaseModel):
    model_config = _Cfg
    name: str = "base_stock"
    review_days: int = Field(default=7, ge=1)  # R
    safety_days: float = Field(default=0.5, ge=0)  # safety stock in days of demand
    S_overshoot: float = Field(default=1.5, gt=0)  # order-up-to = (lead+review) days * this
    target_mode: Literal["explicit", "service_level"] = "service_level"
    service_level: float = Field(default=0.95, ge=0.5, le=0.9999)
    allow_supplier_split: bool = True
    backlog_mode: Literal["cancel", "backorder"] = "backorder"
    params: dict[str, Any] = Field(default_factory=dict)
