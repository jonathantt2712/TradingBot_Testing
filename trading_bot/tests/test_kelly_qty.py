"""_kelly_qty — half-Kelly position sizing behind dashboard recommendations.

Pure function in api_server (needs FastAPI; skip if absent). Pins the
fail-closed guards, the negative-edge → no-bet rule, the exposure cap, and a
normal sized trade.
"""
import pytest

pytest.importorskip("fastapi")

from api_server import _conviction, _kelly_qty  # noqa: E402


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


# ── direction awareness ──────────────────────────────────────────────────────
# composite_score is a LONG-ness scale, so a SHORT's win probability is its
# mirror image. Reading it raw sized every good short at zero.

def test_conviction_mirrors_for_shorts():
    assert _conviction(80, "LONG") == 80
    assert _conviction(20, "SHORT") == 80


def test_strong_short_is_sized():
    # score 20 = strong short. Read raw it means p=0.20 → negative edge → no bet.
    assert _kelly_qty(100_000, 100, 102, 94, 20, "SHORT") > 0


def test_weak_short_still_declines_to_bet():
    # score 55 = a weak short (conviction 45); with reward == risk that's a
    # negative edge, so Kelly says don't bet — same as the mirrored long.
    assert _kelly_qty(100_000, 100, 102, 98, 55, "SHORT") == 0


def test_short_and_mirrored_long_size_identically():
    long_qty  = _kelly_qty(100_000, 100, 90, 130, 80, "LONG")
    short_qty = _kelly_qty(100_000, 100, 110, 70, 20, "SHORT")
    assert short_qty == long_qty
