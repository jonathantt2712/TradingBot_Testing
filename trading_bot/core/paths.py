"""Persistent storage path resolution.

Railway (and most container hosts) give each deploy a fresh, ephemeral
filesystem. Runtime data written to the repo — backtest results, trade
history, recommendations — is therefore wiped on every redeploy. When a
persistent volume is attached, Railway injects RAILWAY_VOLUME_MOUNT_PATH;
writing data there makes it survive deploys.

volume_dir() returns that mount (creating it if needed) or None when no
volume is attached, so callers can fall back to their existing local paths
and keep behaviour identical for local/no-volume runs.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def volume_dir() -> Optional[Path]:
    """Persistent volume mount path, or None when not attached.

    PERSIST_DIR overrides (useful for local testing); otherwise Railway's
    auto-injected RAILWAY_VOLUME_MOUNT_PATH is used.
    """
    raw = os.getenv("PERSIST_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    if not raw:
        return None
    d = Path(raw)
    d.mkdir(parents=True, exist_ok=True)
    return d
