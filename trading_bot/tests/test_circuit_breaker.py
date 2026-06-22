"""Circuit breaker — the safety brake that halts trading.

_consecutive_losses and _check_circuit_breaker read TRADES_FILE; we point that
at a temp file so the real _load runs. In api_server (needs FastAPI).
"""
import json
from datetime import date, timedelta

import pytest

pytest.importorskip("fastapi")

import api_server  # noqa: E402


@pytest.fixture
def trades_file(tmp_path, monkeypatch):
    f = tmp_path / "trades.json"
    monkeypatch.setattr(api_server, "TRADES_FILE", f)
    return f


def _write(f, trades):
    f.write_text(json.dumps(trades))


def _closed(pnl, closed_at):
    return {"status": "closed", "pnl": pnl, "closed_at": closed_at}


def _day(n):
    return (date.today() - timedelta(days=n)).isoformat() + "T12:00:00"


# ── consecutive losses ───────────────────────────────────────────────────────

def test_no_trades_is_zero(trades_file):
    _write(trades_file, [])
    assert api_server._consecutive_losses() == 0


def test_counts_trailing_losses_only(trades_file):
    # most recent first by closed_at: 2 losses then a win breaks the streak
    _write(trades_file, [
        _closed(50, _day(5)),    # oldest, win
        _closed(-10, _day(3)),
        _closed(-20, _day(1)),   # newest, loss
    ])
    assert api_server._consecutive_losses() == 2


def test_recent_win_resets_streak(trades_file):
    _write(trades_file, [
        _closed(-10, _day(3)),
        _closed(-20, _day(2)),
        _closed(5, _day(1)),     # newest is a win
    ])
    assert api_server._consecutive_losses() == 0


# ── circuit breaker decision ─────────────────────────────────────────────────

def test_clear_when_no_losses(trades_file):
    _write(trades_file, [_closed(10, _day(1))])
    assert api_server._check_circuit_breaker() is None
    assert api_server._circuit_breaker["halted"] is False


def test_halts_on_consecutive_losses(trades_file):
    _write(trades_file, [_closed(-5, _day(n)) for n in (3, 2, 1)])  # 3 in a row
    reason = api_server._check_circuit_breaker()
    assert reason is not None
    assert "consecutive losses" in reason
    assert api_server._circuit_breaker["halted"] is True


def test_halts_on_daily_loss_limit(trades_file):
    # one win long ago to set a non-trivial equity estimate, then a big loss today
    today = date.today().isoformat() + "T15:00:00"
    _write(trades_file, [
        _closed(100, _day(10)),
        _closed(-500, today),   # today's loss well beyond 2% of estimated equity
    ])
    reason = api_server._check_circuit_breaker()
    assert reason is not None
    assert "Daily loss limit" in reason
