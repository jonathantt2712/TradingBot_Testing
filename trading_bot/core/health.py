"""Operator health board — surface what the app NEEDS from a human.

Adapters and agents report actionable issues here (a rejected API key, a missing
config value, a broker that won't authenticate). Issues are deduplicated by key
and logged prominently ONCE, so a failure that recurs 500 times in a loop
becomes a single clear "here's what to fix" line instead of a flood. The live
set is exposed so the operator can be told via the log, Telegram, and the
end-of-day report.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class Issue:
    key:         str
    message:     str
    remediation: str = ""
    severity:    str = "error"          # "error" | "warning"
    count:       int = 1
    first_seen:  datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_seen:   datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def as_line(self) -> str:
        icon = "🔴" if self.severity == "error" else "🟡"
        line = f"{icon} {self.message}"
        if self.remediation:
            line += f" → {self.remediation}"
        return line


_lock = threading.Lock()
_issues: dict[str, Issue] = {}
_unsent: set[str] = set()           # keys not yet pushed to an alert channel


def report_issue(key: str, message: str, *, remediation: str = "", severity: str = "error") -> None:
    """Record an actionable issue. Logs prominently only on first sight (or when
    the message changes), then just counts repeats — no flood."""
    now = datetime.now(timezone.utc)
    announce = False
    with _lock:
        existing = _issues.get(key)
        if existing is None:
            _issues[key] = Issue(key, message, remediation, severity, last_seen=now)
            _unsent.add(key)
            announce = True
        else:
            existing.count += 1
            existing.last_seen = now
            if existing.message != message or existing.remediation != remediation:
                existing.message, existing.remediation, existing.severity = message, remediation, severity
                _unsent.add(key)
                announce = True
    if announce:
        log = logger.error if severity == "error" else logger.warning
        log("NEEDS ATTENTION: %s%s", message, f" — {remediation}" if remediation else "")


def resolve(key: str) -> None:
    """Clear an issue once it's no longer true (e.g. a call started succeeding)."""
    with _lock:
        if _issues.pop(key, None) is not None:
            _unsent.discard(key)
            logger.info("Resolved issue: %s", key)


def active_issues() -> list[Issue]:
    with _lock:
        return sorted(_issues.values(), key=lambda i: (i.severity != "error", i.first_seen))


def take_unsent() -> list[Issue]:
    """Return issues not yet pushed to an alert channel, marking them as sent."""
    with _lock:
        out = [_issues[k] for k in _unsent if k in _issues]
        _unsent.clear()
        return sorted(out, key=lambda i: i.first_seen)


def format_block(title: str = "NEEDS ATTENTION") -> str:
    issues = active_issues()
    if not issues:
        return ""
    return f"{title}:\n" + "\n".join(f"- {i.as_line()} (x{i.count})" for i in issues)


def reset() -> None:
    """Clear all state (process restart / tests)."""
    with _lock:
        _issues.clear()
        _unsent.clear()
