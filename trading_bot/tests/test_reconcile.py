"""Broker ↔ trades.json reconciliation: _classify_reconciliation decision table.

The reconciler repairs only the safe direction (JSON open, broker verifiably
flat) and must never guess when evidence is inconclusive.
"""
import pytest

pytest.importorskip("fastapi")

from api_server import _classify_reconciliation  # noqa: E402


def _trade(order_id="real-1", ticker="NVDA"):
    return {"status": "open", "order_id": order_id, "ticker": ticker,
            "direction": "LONG", "entry": 100.0, "qty": 5}


def test_still_held_at_broker_keeps():
    assert _classify_reconciliation(_trade(), {"NVDA"}, None) == "keep"


def test_simulated_trades_are_ignored():
    assert _classify_reconciliation(_trade(order_id="PAPER-X1"), set(), None) == "keep"
    assert _classify_reconciliation(_trade(order_id=""), set(), None) == "keep"


def test_flat_with_unverifiable_order_keeps():
    # Broker flat but the entry order can't be fetched — never guess.
    assert _classify_reconciliation(_trade(), set(), None) == "keep"


def test_flat_with_pending_order_keeps():
    status = {"status": "accepted", "filled_qty": "0"}
    assert _classify_reconciliation(_trade(), set(), status) == "keep"


def test_flat_with_dead_unfilled_entry_cancels():
    for st in ("canceled", "expired", "rejected"):
        status = {"status": st, "filled_qty": "0"}
        assert _classify_reconciliation(_trade(), set(), status) == "cancel"


def test_flat_with_filled_entry_closes():
    # Entry filled but broker is flat: manually closed / EOD close_all — the
    # exit monitor never sees a leg fill for these.
    status = {"status": "filled", "filled_qty": "5"}
    assert _classify_reconciliation(_trade(), set(), status) == "close"


def test_partially_filled_then_cancelled_closes():
    # Shares WERE bought before the cancel — the flat book means they were
    # later sold; treat as a close, not a phantom cancel.
    status = {"status": "canceled", "filled_qty": "3"}
    assert _classify_reconciliation(_trade(), set(), status) == "close"
