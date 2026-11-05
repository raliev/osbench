"""Counter-based (stateless) RNG for event streams.

The value depends only on (master_seed, stream name, event key), not on the order
or number of previous calls. This provides CRN (identical draws for the same
event under any policy) and bit-for-bit resume without saving PRNG state.

v1 implementation: the event key is hashed into a seed for numpy Philox, from which
a single value is drawn. Deterministic and platform-independent (integer hash).
"""

from __future__ import annotations

import hashlib

import numpy as np

_MASK64 = (1 << 64) - 1


def _hash_key(master_seed: int, stream: str, key: tuple[int, ...]) -> int:
    """Reduce (master_seed, stream, key) into a stable 128-bit seed."""
    h = hashlib.blake2b(digest_size=16)
    h.update(int(master_seed & _MASK64).to_bytes(8, "little"))
    h.update(stream.encode("utf-8"))
    for k in key:
        h.update(int(k & _MASK64).to_bytes(8, "little", signed=False))
    return int.from_bytes(h.digest(), "little")


def make_generator(master_seed: int, stream: str, key: tuple[int, ...]) -> np.random.Generator:
    """Stateless Generator for (master_seed, stream, event key)."""
    seed = _hash_key(master_seed, stream, key)
    return np.random.Generator(np.random.Philox(np.random.SeedSequence(seed)))


class CounterRng:
    """Factory of stateless draws for a single event."""

    def __init__(self, master_seed: int, stream: str):
        self.master_seed = int(master_seed)
        self.stream = stream

    def _gen(self, key: tuple[int, ...]) -> np.random.Generator:
        seed = _hash_key(self.master_seed, self.stream, key)
        return np.random.Generator(np.random.Philox(np.random.SeedSequence(seed)))

    def uniform(self, key: tuple[int, ...]) -> float:
        return float(self._gen(key).random())

    def lognormal(self, key: tuple[int, ...], median: float, sigma: float) -> float:
        return float(self._gen(key).lognormal(mean=float(np.log(median)), sigma=sigma))

    def geometric(self, key: tuple[int, ...], p: float) -> int:
        return int(self._gen(key).geometric(p))
