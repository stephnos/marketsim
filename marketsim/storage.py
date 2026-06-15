"""Tiny JSON persistence for account / order / watchlist data.

The simulated market itself is regenerated deterministically on startup, so we
only persist user state. Writes are throttled and atomic.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path

DEFAULT_PATH = Path(
    os.environ.get("MARKETSIM_DATA", Path.home() / ".marketsim" / "state.json")
)


class Storage:
    def __init__(self, path: Path | str = DEFAULT_PATH, min_interval: float = 0.0):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._min_interval = min_interval
        self._last_write = 0.0

    def load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            return {}

    def save(self, data: dict) -> None:
        with self._lock:
            now = time.time()
            if self._min_interval and now - self._last_write < self._min_interval:
                return
            self._last_write = now
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, indent=2)
                os.replace(tmp, self.path)
            except Exception:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise
