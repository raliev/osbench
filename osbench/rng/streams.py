"""Named independent RNG streams over a single master seed (the basis for CRN).

A single master_seed → SeedSequence.spawn() in the FIXED STREAM_NAMES order yields
independent reproducible streams. Two policies on the same seed see the same world,
because the world and demand are generated from these streams in advance.
"""

from __future__ import annotations

import numpy as np

# Order is fixed: spawn follows this list. Don't change the order — it breaks reproducibility.
STREAM_NAMES: tuple[str, ...] = (
    "geo",
    "catalog",
    "demand_arrivals",
    "demand_basket",
    "supplier_leadtime",
    "supplier_fill",
    "shocks",
    "delivery_noise",
    "process_times",
    "inventory_error",
    "customer_choice",
    "policy",
    # Appended at the end so existing streams' child SeedSequences are unchanged
    # (SeedSequence.spawn is per-index, independent of the total count).
    "demand_cancel",
    "sync_jitter",
    "transfer_leadtime",
)

_INDEX = {name: i for i, name in enumerate(STREAM_NAMES)}


class RngHub:
    """Factory of named generators from a single master seed."""

    def __init__(self, master_seed: int):
        self.master_seed = int(master_seed)
        self._root = np.random.SeedSequence(self.master_seed)
        self._children = self._root.spawn(len(STREAM_NAMES))
        self._cache: dict[str, np.random.Generator] = {}

    def generator(self, name: str) -> np.random.Generator:
        """Return the (cached) Philox-based Generator for a named stream."""
        if name not in _INDEX:
            raise KeyError(f"unknown RNG stream {name!r}; available: {STREAM_NAMES}")
        if name not in self._cache:
            self._cache[name] = np.random.Generator(np.random.Philox(self._children[_INDEX[name]]))
        return self._cache[name]

    def spawn_seed(self, name: str) -> np.random.SeedSequence:
        """The stream's SeedSequence — for passing to child processes/workers."""
        return self._children[_INDEX[name]]
