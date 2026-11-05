"""Run control flags from the UI (stop/pause)."""

from __future__ import annotations

import threading


class ControlFlags:
    """Thread-safe flags: the UI thread sets them, the core thread checks them."""

    def __init__(self):
        self._stop = threading.Event()
        self.state = "running"  # running | stopping | stopped | done | failed

    def request_stop(self) -> None:
        self._stop.set()
        self.state = "stopping"

    @property
    def stop_requested(self) -> bool:
        return self._stop.is_set()
