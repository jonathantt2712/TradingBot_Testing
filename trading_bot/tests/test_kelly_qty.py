"""_kelly_qty — half-Kelly position sizing behind dashboard recommendations.

Pure function in api_server (needs FastAPI; skip if absent). Pins the
fail-closed guards, the negative-edge → no-bet rule, the exposure cap, and a
normal sized trade.
"""
import pytest

pytest.importorskip("fastapi")

from api_server import _kelly_qty  # noqa: E402


def test_no_equity_is_zero():
    assert _kelly_qty(0, 100, 98, 106, 80) == 0


def test_degenerate_stop_is_zero():
    # entry == stop_loss → risk_per_share ~ 0 → fail closed
    assert _kelly_qty(100_000, 100, 100, 106, 80) == 0


def test_negative_edge_declines_to_bet():
    # composite 50 with reward==risk → Kelly fraction 0 → no bet
    assert _kelly_qty(100_000, 100, 98, 102, 50) == 0


def test_sized_trade_within_exposure_cap():
    # b=3, p=0.8 → half-Kelly sizes ~733 sh but 15% equity cap on a $100 stock = 150
    assert _kelly_qty(100_000, 100, 98, 106, 80) == 150


def test_sized_trade_below_cap_uses_kelly():
    # wider stop (risk $10) keeps Kelly qty (146) under the 150-share cap
    qty = _kelly_qty(100_000, 100, 90, 130, 80)
    assert 0 < qty < 150


def test_higher_conviction_sizes_at_least_as_large():
    low  = _kelly_qty(100_000, 100, 90, 130, 60)
    high = _kelly_qty(100_000, 100, 90, 130, 90)
    assert high >= low
