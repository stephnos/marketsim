"""Minimal .env loader (no external dependency).

Loads KEY=VALUE lines from a .env file in the project root into the environment
so the server and CLI pick up credentials (e.g. Alpaca keys) without the user
having to export them every shell. Existing environment variables always win.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: str | os.PathLike | None = None) -> None:
    if path is None:
        # project root is two levels up from this file (marketsim/config.py)
        path = Path(__file__).resolve().parent.parent / ".env"
    path = Path(path)
    if not path.exists():
        return
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        pass
