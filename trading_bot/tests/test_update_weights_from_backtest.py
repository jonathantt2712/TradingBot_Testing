"""_update_weights_from_backtest — the nightly-backtest weight/ATR writer.

Pins the minimum-sample gate: it must be judged on DISTINCT trades (backtest +
live), not the 3x-duplicated (recency-weighted) list used for the win-rate/PF
stats — otherwise a handful of live trades tripled up could pass a "10 trades
minimum" bar meant to guard against tuning real risk params off noise.
"""
import json

import pytest

pytest.importorskip("fastapi")  # module imports api-server-adjacent deps

from backtest_intraday import TradeResult, _update_weights_from_backtest  # noqa: E402


def _bt_trade(pnl: float, direction: str = "LONG") -> TradeResult:
    return TradeResult(
        ticker="TEST", direction=direction, entry_time="2026-01-01T10:00:00",
        exit_time="2026-01-01T10:30:00", entry_price=100.0, exit_price=101.0,
        qty=10.0, stop_loss=98.0, take_profit=104.0, risk_reward=2.0,
        outcome="TP_HIT", pnl_usd=pnl, pnl_pct=1.0, score=70.0,
    )


def _live_trades_file(tmp_path, trades: list) -> "Path":
    path = tmp_path / "trades.json"
    path.write_text(json.dumps(trades))
    return path


def test_few_live_trades_tripled_do_not_clear_the_gate(tmp_path, monkeypatch):
    import backtest_intraday as bt

    weights_file = tmp_path / "strategy_weights.json"
    monkeypatch.setattr(bt, "_WEIGHTS_FILE", weights_file)

    # Only 4 real closed live trades, no backtest trades. Tripled for recency
    # weighting that's 12 (>=10) — the OLD buggy gate would have proceeded and
    # nudged live ATR multiples off just 4 real data points.
    live = [{"pnl": 10.0, "direction": "LONG", "status": "closed"} for _ in range(4)]
    trades_file = _live_trades_file(tmp_path, live)

    _update_weights_from_backtest([], live_trades_file=trades_file)

    assert not weights_file.exists(), "must not write tuned params from only 4 distinct trades"


def test_enough_distinct_trades_clears_the_gate(tmp_path, monkeypatch):
    import backtest_intraday as bt

    weights_file = tmp_path / "strategy_weights.json"
    monkeypatch.setattr(bt, "_WEIGHTS_FILE", weights_file)

    bt_trades = [_bt_trade(10.0) for _ in range(10)]
    trades_file = _live_trades_file(tmp_path, [])

    _update_weights_from_backtest(bt_trades, live_trades_file=trades_file)

    assert weights_file.exists(), "10 distinct backtest trades should clear the gate"
    written = json.loads(weights_file.read_text())
    assert written["bt_trades"] == 10
