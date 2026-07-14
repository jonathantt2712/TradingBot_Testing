"""Bounded growth for append-only JSONL logs.

decisions.jsonl, risk_rejections.jsonl, learning_history.jsonl, and the daily
snapshots grow forever by design (append-per-event). Left alone they become
multi-MB files that slow every dashboard read that tails them. trim_jsonl
keeps the newest records once a file crosses its cap — called once per day
from the api_server background loop.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def trim_jsonl(path: Path, *, max_lines: int = 20_000, keep_lines: int = 10_000) -> bool:
    """Truncate ``path`` to its newest ``keep_lines`` once it exceeds ``max_lines``.

    Hysteresis (trim to half the cap) means the rewrite cost is paid rarely,
    not on every call. Atomic replace; never raises. Returns True if trimmed.
    """
    try:
        if not path.exists():
            return False
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) <= max_lines:
            return False
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("".join(lines[-keep_lines:]), encoding="utf-8")
        tmp.replace(path)
        logger.info("Trimmed %s: %d → %d lines", path.name, len(lines), keep_lines)
        return True
    except Exception:
        logger.debug("trim_jsonl(%s) failed", path, exc_info=True)
        return False
