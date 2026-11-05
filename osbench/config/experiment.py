"""Experiment config and guardrails."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .policy import ReplenishmentPolicyConfig, SourcingPolicyConfig

_Cfg = ConfigDict(extra="forbid", frozen=True)


class GuardrailConfig(BaseModel):
    model_config = _Cfg
    enabled: bool = True
    mode: str = Field(default="abort", pattern="^(abort|warn)$")
    warmup_days: int = Field(default=7, ge=0)
    cost_regression_pct: float = Field(default=25.0, ge=0)
    cost_regression_days: int = Field(default=7, ge=1)
    policy_p99_budget_ms: float | None = Field(default=50.0, gt=0)
    max_lost_sales_rate: float | None = Field(default=None, ge=0, le=1)
    invariant_hard_stop: bool = True
    sweep_seed_strike_out: int = Field(default=3, ge=1)


class PairedCompareConfig(BaseModel):
    model_config = _Cfg
    baseline_policy: str = "nearest"
    ci_level: float = Field(default=0.95, gt=0, lt=1)
    bootstrap_iters: int = Field(default=10_000, ge=1000)
    bootstrap_seed: int = 12345


class ExperimentConfig(BaseModel):
    model_config = _Cfg
    name: str
    scenarios: tuple[str, ...]  # paths to YAML files or preset names
    sourcing_policies: tuple[SourcingPolicyConfig, ...]
    replenishment_policy: ReplenishmentPolicyConfig = ReplenishmentPolicyConfig()
    seeds: tuple[int, ...] = tuple(range(30))
    grid_seeds: tuple[int, ...] = tuple(range(10))
    parallelism: int = Field(default=1, ge=1)
    guardrails: GuardrailConfig = GuardrailConfig()
    compare: PairedCompareConfig = PairedCompareConfig()
    output_dir: str = "runs"
