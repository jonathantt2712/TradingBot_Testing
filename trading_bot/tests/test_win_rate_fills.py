"""FIFO win-rate reconstruction from Alpaca fill activities.

`_win_rate_from_fills` turns a raw fill stream into completed round-trips and a
win rate — the number shown on the dashboard stats card. It handles longs,
shorts, scale-ins and position flips, so the maths is pinned here.

The function lives in api_server, which needs FastAPI; skip cleanly if absent.
"""
import pytest

pytest.importorskip("fastapi")

from api_server import _win_rate_from_fills  # noqa: E402


def _fill(sym, side, qty, price):
    return {"symbol": sym, "side": side, "qty": qty, "price": price}


# ── degenerate inputs ────────────────────────────────────────────────────────

def test_no_fills_returns_none():
    assert _win_rate_from_fills([]) is None


def test_open_position_not_counted():
    # a buy with no matching sell never completes a trade
    assert _win_rate_from_fills([_fill("AAA", "buy", 10, 100)]) is None


def test_malformed_fills_ignored():
    bad = [_fill("", "buy", 10, 100), _fill("AAA", "", 10, 100),
           _fill("AAA", "buy", 0, 100), _fill("AAA", "buy", 10, 0)]
    assert _win_rate_from_fills(bad) is None


# ── single round-trips ───────────────────────────────────────────────────────

def test_long_win():
    fills = [_fill("AAA", "buy", 10, 100), _fill("AAA", "sell", 10, 110)]
    assert _win_rate_from_fills(fills) == (100.0, 1)


def test_long_loss():
    fills = [_fill("AAA", "buy", 10, 100), _fill("AAA", "sell", 10, 90)]
    assert _win_rate_from_fills(fills) == (0.0, 1)


def test_short_win():
    # sell high, buy back lower
    fills = [_fill("AAA", "sell", 10, 100), _fill("AAA", "buy", 10, 90)]
    assert _win_rate_from_fills(fills) == (100.0, 1)


def test_short_loss():
    fills = [_fill("AAA", "sell", 10, 100), _fill("AAA", "buy", 10, 110)]
    assert _win_rate_from_fills(fills) == (0.0, 1)


# ── scaling and flips ────────────────────────────────────────────────────────

def test_scale_in_uses_weighted_average_cost():
    # buy 10@100 then 10@120 → avg 110; sell 20@115 → win (115 > 110)
    fills = [_fill("AAA", "buy", 10, 100), _fill("AAA", "buy", 10, 120),
             _fill("AAA", "sell", 20, 115)]
    assert _win_rate_from_fills(fills) == (100.0, 1)


def test_position_flip_closes_one_trade_and_opens_opposite():
    # long 10@100, sell 15@110 → closes long (+100 win) and opens 5-share short@110
    # then buy 5@105 → closes short (+25 win) → 2 wins / 2 trades
    fills = [_fill("AAA", "buy", 10, 100), _fill("AAA", "sell", 15, 110),
             _fill("AAA", "buy", 5, 105)]
    assert _win_rate_from_fills(fills) == (100.0, 2)


def test_mixed_win_rate_across_symbols():
    fills = [
        _fill("AAA", "buy", 10, 100), _fill("AAA", "sell", 10, 110),   # win
        _fill("BBB", "buy", 10, 100), _fill("BBB", "sell", 10, 90),    # loss
        _fill("CCC", "sell", 10, 50), _fill("CCC", "buy", 10, 40),     # win (short)
    ]
    assert _win_rate_from_fills(fills) == (66.7, 3)
