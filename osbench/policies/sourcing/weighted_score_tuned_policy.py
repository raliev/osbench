"""weighted_score_tuned: the same scoring as weighted_score, but with factor weights tuned
offline on training seeds (RQ1's "ML-light" line). Tuned weights are passed in
``cfg.params['weights']`` (produced by ``osbench tune`` / experiments.tune); without them it
falls back to the default weights, so the policy always runs.
"""

from __future__ import annotations

from ...config.policy import SourcingPolicyConfig, WeightedScoreConfig
from ...core.network import Network
from ...registry import register_sourcing
from .weighted_score_policy import WeightedScorePolicy


@register_sourcing("weighted_score_tuned")
class WeightedScoreTunedPolicy(WeightedScorePolicy):
    name = "weighted_score_tuned"

    def __init__(self, cfg: SourcingPolicyConfig, net: Network):
        super().__init__(cfg, net)
        weights = cfg.params.get("weights")
        if weights:
            # Rebuild the scoring config from the tuned weights (keeps other ws settings).
            base = (cfg.weighted_score or WeightedScoreConfig()).model_dump()
            base["weights"] = {k: float(v) for k, v in weights.items()}
            self.ws = WeightedScoreConfig(**base)
