"""api_server: _entry_guard_reason risk-gate unit tests.

Tests the shared circuit-breaker / position / sector / beta caps that protect
BOTH the manual /api/execute endpoint and the autonomous executor.
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# Import only the pure functions — avoids triggering the full FastAPI startup
import importlib
import types

# We need to load just the guard functions without running the lifespan hooks.
# Import at module level — the api_server module is large but import is safe
# because lifespan only runs inside uvicorn.
import api_server as _srv


# ── helpers ─────────────────────────────────────────────────────────────────

def _open_trade(ticker: str, *, direction: str = "LONG", beta: float = 1.0) -> dict:
    return {"status": "open", "ticker": ticker, "direction": direction, "beta": beta}


def _closed_trade(ticker: str, *, pnl: float = -50.0) -> dict:
    return {
        "status":    "closed",
        "ticker":    ticker,
        "direction": "LONG",
        "beta":      1.0,
        "pnl":       pnl,
        "closed_at": "2026-06-24T15:00:00",
        "executed_at": "2026-06-24T13:30:00",
    }


# ── circuit breaker ──────────────────────────────────────────────────────────

def test_circuit_breaker_blocks_on_consecutive_losses(tmp_path, monkeypatch):
    trades = [_closed_trade("AAA", pnl=-10) for _ in range(3)]
    monkeypatch.setattr(_srv, "MAX_CONSECUTIVE_LOSSES", 3)
    monkeypatch.setattr(_srv, "TRADES_FILE", tmp_path / "nonexistent.json")

    # Patch _consecutive_losses to return 3 without reading disk
    with patch.object(_srv, "_consecutive_losses", return_value=3), \
         patch.object(_srv, "_daily_pnl_pct", return_value=0.0):
        reason = _srv._entry_guard_reason("NVDA", "LONG", 70.0, 1.0, trades)
    assert reason is not None
    assert "consecutive" in reason.lower() or "loss" in reason.lower()


def test_circuit_breaker_clears_on_no_losses(tmp_path, monkeypatch):
    monkeypatch.setattr(_srv, "TRADES_FILE", tmp_path / "nonexistent.json")
    # After a profitable trade the breaker should clear
    with patch.object(_srv, "_consecutive_losses", return_value=0), \
         patch.object(_srv, "_daily_pnl_pct", return_value=0.0):
        _srv._circuit_breaker.update({"halted": False, "reason": None})
        reason = _srv._entry_guard_reason("NVDA", "LONG", 70.0, 1.0, [])
    assert reason is None


# ── max open positions ───────────────────────────────────────────────────────

def test_max_positions_blocks_when_full(monkeypatch):
    monkeypatch.setattr(_srv, "MAX_OPEN_POSITIONS", 3)
    with patch.object(_srv, "_check_circuit_breaker", return_value=None):
        history = [_open_trade(s) for s in ("AAA", "BBB", "CCC")]
        reason = _srv._entry_guard_reason("DDD", "LONG", 70.0, 1.0, history)
    assert reason is not None
    assert "max" in reason.lower() or "position" in reason.lower()


def test_max_positions_allows_below_cap(monkeypatch):
    monkeypatch.setattr(_srv, "MAX_OPEN_POSITIONS", 5)
    with patch.object(_srv, "_check_circuit_breaker", return_value=None):
        # Use cross-sector real tickers so the sector cap doesn't fire
        history = [_open_trade("AAPL"), _open_trade("JPM")]
        reason = _srv._entry_guard_reason("TSLA", "LONG", 70.0, 1.0, history)
    assert reason is None


# ── sector concentration ─────────────────────────────────────────────────────

def test_sector_cap_blocks_third_tech_position(monkeypatch):
    # AAPL, MSFT, NVDA all map to "Technology" in _SECTOR_MAP
    monkeypatch.setattr(_srv, "MAX_OPEN_POSITIONS", 10)
    with patch.object(_srv, "_check_circuit_breaker", return_value=None):
        history = [_open_trade("AAPL"), _open_trade("MSFT")]
        reason = _srv._entry_guard_reason("NVDA", "LONG", 70.0, 1.0, history)
    assert reason is not None
    assert "sector" in reason.lower() or "Technology" in reason


def test_sector_cap_allows_first_two(monkeypatch):
    monkeypatch.setattr(_srv, "MAX_OPEN_POSITIONS", 10)
    with patch.object(_srv, "_check_circuit_breaker", return_value=None):
        history = [_open_trade("AAPL")]
        reason = _srv._entry_guard_reason("MSFT", "LONG", 70.0, 1.0, history)
    assert reason is None


def test_sector_cap_cross_sector_allowed(monkeypatch):
    # JPM is Financials, AAPL+MSFT are Technology — should be allowed
    monkeypatch.setattr(_srv, "MAX_OPEN_POSITIONS", 10)
    with patch.object(_srv, "_check_circuit_breaker", return_value=None):
        history = [_open_trade("AAPL"), _open_trade("MSFT")]
        reason = _srv._entry_guard_reason("JPM", "LONG", 70.0, 1.0, history)
    assert reason is None


# ── beta cap ─────────────────────────────────────────────────────────────────

def test_beta_cap_blocks_when_exceeded(monkeypatch):
    monkeypatch.setattr(_srv, "MAX_OPEN_POSITIONS", 10)
    monkeypatch.setattr(_srv, "PORTFOLIO_BETA_CAP", 2.0)
    with patch.object(_srv, "_check_circuit_breaker", return_value=None):
        # Cross-sector tickers so sector cap doesn't fire; beta=1.2 each → net 2.4
        history = [_open_trade("AAPL", beta=1.2), _open_trade("JPM", beta=1.2)]
        # New LONG with beta=1.0 → 2.4 + 1.0 = 3.4 > 2.0 cap
        reason = _srv._entry_guard_reason("TSLA", "LONG", 70.0, 1.0, history)
    assert reason is not None
    assert "beta" in reason.lower()


def test_beta_cap_short_offsets_long(monkeypatch):
    monkeypatch.setattr(_srv, "MAX_OPEN_POSITIONS", 10)
    monkeypatch.setattr(_srv, "PORTFOLIO_BETA_CAP", 5.0)
    with patch.object(_srv, "_check_circuit_breaker", return_value=None):
        # Cross-sector tickers; LONG beta=2.0, SHORT beta=2.0 → net = 0
        history = [_open_trade("AAPL", beta=2.0, direction="LONG"),
                   _open_trade("JPM",  beta=2.0, direction="SHORT")]
        reason = _srv._entry_guard_reason("TSLA", "LONG", 70.0, 1.0, history)
    assert reason is None


def test_all_clear_returns_none(monkeypatch):
    monkeypatch.setattr(_srv, "MAX_OPEN_POSITIONS", 5)
    monkeypatch.setattr(_srv, "PORTFOLIO_BETA_CAP", 5.0)
    with patch.object(_srv, "_check_circuit_breaker", return_value=None):
        reason = _srv._entry_guard_reason("AAPL", "LONG", 70.0, 1.0, [])
    assert reason is None
