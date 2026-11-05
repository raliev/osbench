"""Event types, priorities, and the event queue.

The queue is a min-heap keyed by the tuple (time, type_priority, seq). seq is a monotonic
tie-break that ensures determinism when (time, priority) are equal. Lower priority = earlier.
"""

from __future__ import annotations

import heapq
from enum import IntEnum


class Ev(IntEnum):
    DAY_ROLL = 0
    RECEIPT = 1  # PO receipt before new orders in the same minute
    SOURCING_DECISION = 3
    RESERVE = 4
    WH_QUEUE_ADMIT = 5
    PICK_DONE = 6
    PACK_DONE = 7
    SHIP = 8
    DELIVER = 9
    ORDER_CREATED = 10
    ENTER_SOURCING = 11
    DEFER_WAKE = 12  # a deferred order's wait expired (receipt arrival or deadline) -> re-source
    REPLENISH_REVIEW = 13
    CHECKPOINT = 14
    LIVE_TICK = 15
    SIM_END = 99


class EventQueue:
    """Wrapper around heapq with a monotonic seq counter."""

    __slots__ = ("_heap", "_seq")

    def __init__(self):
        self._heap: list[tuple[int, int, int, int, int]] = []
        self._seq = 0

    def push(self, time_min: int, kind: Ev, a: int = -1, b: int = -1) -> None:
        heapq.heappush(self._heap, (int(time_min), int(kind), self._seq, int(a), int(b)))
        self._seq += 1

    def pop(self) -> tuple[int, int, int, int]:
        """Return (time_min, kind, a, b)."""
        t, kind, _seq, a, b = heapq.heappop(self._heap)
        return t, kind, a, b

    def __len__(self) -> int:
        return len(self._heap)
