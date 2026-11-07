"""The no-op transfer policy: never moves anything (the default)."""

from __future__ import annotations

from ...config.schema import TransfersConfig
from ...core.network import Network
from ...registry import register_transfer
from ...types import Transfer, TransferContext


@register_transfer("none")
class NoneTransferPolicy:
    name = "none"

    def __init__(self, cfg: TransfersConfig, net: Network):
        self.cfg = cfg
        self.net = net

    def decide(self, ctx: TransferContext) -> list[Transfer]:
        return []
