"""simulate_day_trade — the fill engine that judges the nightly self-improvement.

Pins: TP/SL detection both directions, worst-case same-bar double trigger,
gap-through fills (stops fill WORSE at the open, limits fill BETTER),
forced 15:55 ET close, and slippage accounting.
"""
from datetime import timezone

import pandas as pd
import pytest

pytest.importorskip("fastapi")  # module imports api-server-adjacent deps

from backtest_intraday import simulate_day_trade  # noqa: E402
from core.enums import Decision  # noqa: E402


def _bars(rows, start="2026-06-10 14:30:00"):
    """rows: list of (open, high, low, close) starting at 10:30 ET."""
    idx = pd.date_range(start=start, periods=len(rows), freq="5min", tz=timezone.utc)
    return pd.DataFrame(
        [{"open": o, "high": h, "low": l, "close": c, "volume": 10_000}
         for o, h, l, c in rows],
        index=idx,
    )


def _run(bars, direction=Decision.LONG, entry=100.0, sl=98.0, tp=104.0, qty=10):
    return simulate_day_trade(bars, direction=direction, entry=entry,
                              stop_loss=sl, take_profit=tp, qty=qty)


def test_long_take_profit_hit():
    outcome, exit_px, _, pnl, _ = _run(_bars([(100, 101, 99.5, 100.5),
                                              (100.5, 104.5, 100, 104)]))
    assert outcome == "TP_HIT" and exit_px == 104.0
    assert pnl == pytest.approx((104 - 100) * 10)


def test_long_stop_hit():
    outcome, exit_px, _, pnl, _ = _run(_bars([(100, 100.5, 97.5, 98)]))
    assert outcome == "SL_HIT" and exit_px == 98.0
    assert pnl == pytest.approx((98 - 100) * 10)


def test_short_directions_mirror():
    outcome, exit_px, _, pnl, _ = _run(
        _bars([(100, 100.5, 95.5, 96)]),
        direction=Decision.SHORT, entry=100.0, sl=102.0, tp=96.0,
    )
    assert outcome == "TP_HIT" and exit_px == 96.0
    assert pnl == pytest.approx((100 - 96) * 10)


def test_same_bar_double_trigger_is_worst_case_stop():
    # One wide bar spans both levels — must assume the stop filled.
    outcome, exit_px, *_ = _run(_bars([(100, 105, 97, 101)]))
    assert outcome == "SL_HIT" and exit_px == 98.0


def test_gap_through_stop_fills_at_the_open():
    # Bar OPENS at 96, below the 98 stop: a stop order fills ~at the open,
    # not at the stop price. Pretending otherwise flatters losses.
    outcome, exit_px, _, pnl, _ = _run(_bars([(96, 96.5, 95, 95.5)]))
    assert outcome == "SL_HIT"
    assert exit_px == 96.0
    assert pnl == pytest.approx((96 - 100) * 10)


def test_gap_through_target_fills_better_at_the_open():
    # Bar opens at 105, above the 104 limit: the limit fills at the better open.
    outcome, exit_px, *_ = _run(_bars([(105, 106, 104.5, 105.5)]))
    assert outcome == "TP_HIT"
    assert exit_px == 105.0


def test_forced_eod_close_at_1555():
    rows = [(100, 100.5, 99.5, 100)] * 2
    bars = _bars(rows, start="2026-06-10 19:55:00")   # 15:55 ET in UTC (EDT)
    outcome, exit_px, _, pnl, _ = _run(bars)
    assert outcome == "EOD_CLOSE"
    assert exit_px == 100.0                            # first 15:55 bar's open


def test_slippage_charged_on_both_sides():
    bars = _bars([(100.5, 104.5, 100, 104)])
    _, _, _, pnl, _ = simulate_day_trade(
        bars, direction=Decision.LONG, entry=100.0,
        stop_loss=98.0, take_profit=104.0, qty=10, slippage_pct=0.001,
    )
    # Raw $40 minus 2 × 0.1% × $100 × 10 shares = $2
    assert pnl == pytest.approx(40 - 2)
