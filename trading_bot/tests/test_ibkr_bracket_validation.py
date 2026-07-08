"""IBKRBroker: submit_bracket pre-flight validation.

Mirrors test_alpaca_broker.py's guard tests: IBKRBroker.submit_bracket must
reject an inverted bracket (SL/TP on the wrong side of entry) *before*
touching TWS at all — the stub `_ib` has no qualifyContracts/getReqId/
placeOrder, so the test fails loudly if the guard doesn't short-circuit.
"""
import asyncio
from types import SimpleNamespace

import pytest

from core.enums import Decision
from core.models import RiskParameters, TradeDecision
from execution.ibkr_broker import IBKRBroker


def _broker() -> IBKRBroker:
    b = IBKRBroker()
    b._ib = SimpleNamespace(isConnected=lambda: True)  # no other methods — must not be touched
    return b


def _long_decision(entry: float, sl: float, tp: float, qty: float = 10) -> TradeDecision:
    return TradeDecision(
        ticker="TEST",
        decision=Decision.LONG,
        composite_score=75.0,
        risk=RiskParameters(
            qty=qty, entry=entry, stop_loss=sl, take_profit=tp,
            risk_reward=abs(tp - entry) / max(abs(entry - sl), 0.01),
        ),
    )


def _short_decision(entry: float, sl: float, tp: float, qty: float = 10) -> TradeDecision:
    return TradeDecision(
        ticker="TEST",
        decision=Decision.SHORT,
        composite_score=25.0,
        risk=RiskParameters(
            qty=qty, entry=entry, stop_loss=sl, take_profit=tp,
            risk_reward=abs(entry - tp) / max(abs(sl - entry), 0.01),
        ),
    )


def test_long_inverted_sl_above_entry_rejected():
    dec = _long_decision(entry=100.0, sl=102.0, tp=105.0)
    result = asyncio.run(_broker().submit_bracket(dec))
    assert result is None


def test_long_tp_below_entry_rejected():
    dec = _long_decision(entry=100.0, sl=98.0, tp=95.0)
    result = asyncio.run(_broker().submit_bracket(dec))
    assert result is None


def test_short_inverted_sl_below_entry_rejected():
    dec = _short_decision(entry=100.0, sl=98.0, tp=95.0)
    result = asyncio.run(_broker().submit_bracket(dec))
    assert result is None


def test_short_tp_above_entry_rejected():
    dec = _short_decision(entry=100.0, sl=103.0, tp=105.0)
    result = asyncio.run(_broker().submit_bracket(dec))
    assert result is None
