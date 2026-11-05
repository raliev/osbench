"""Core-to-server bridge: thread-safe ring of snapshots + static meta information."""

from __future__ import annotations

import threading

from .control import ControlFlags


class LiveHub:
    """Stores run snapshots and serves them to the server. The core writes, the server reads."""

    def __init__(self, meta: dict, control: ControlFlags, maxlen: int = 2000):
        self.meta = meta  # static: warehouse/region coordinates, names
        self.control = control
        self._ring: list[dict] = []
        self._lock = threading.Lock()
        self._maxlen = maxlen
        self.finished = False

    def emit(self, snapshot: dict) -> None:
        with self._lock:
            self._ring.append(snapshot)
            if len(self._ring) > self._maxlen:
                self._ring.pop(0)

    def latest_seq(self) -> int:
        with self._lock:
            return self._ring[-1]["seq"] if self._ring else -1

    def snapshots_since(self, seq: int) -> list[dict]:
        with self._lock:
            return [s for s in self._ring if s["seq"] > seq]

    def all_snapshots(self) -> list[dict]:
        with self._lock:
            return list(self._ring)

    def warehouse_detail(self, wh: int) -> dict:
        """Assemble per-warehouse time series from the snapshot ring."""
        with self._lock:
            ring = list(self._ring)
        days, stock, util, queue, inbound, outbound = [], [], [], [], [], []
        for s in ring:
            w = s.get("wh")
            if not w or wh >= len(w["stock"]):
                continue
            days.append(s["sim_day"])
            stock.append(w["stock"][wh])
            util.append(w["util"][wh])
            queue.append(w["queue"][wh])
            inbound.append(w["inbound"][wh])
            outbound.append(w["outbound"][wh])
        name = self.meta["wh_names"][wh] if wh < len(self.meta["wh_names"]) else f"W{wh}"
        return {
            "type": "wh_detail",
            "wh": wh,
            "name": name,
            "day": days,
            "stock": stock,
            "util": util,
            "queue": queue,
            "inbound": inbound,
            "outbound": outbound,
        }
