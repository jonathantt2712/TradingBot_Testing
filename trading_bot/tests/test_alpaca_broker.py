"""AlpacaBroker: submit_bracket pre-flight validation.

Tests the structural sanity guard added to submit_bracket() — the guard must
reject inverted brackets (SL >= entry for LONG, or SL <= entry for SHORT)
*before* hitting the network, so a sizing bug never sends a malformed order.
"""
import asyncio
from dataclasses import dataclass
from typing import Optional

import pytest

from core.enums import Decision
from core.models import RiskParameters, TradeDecision
from execution.alpaca_broker import AlpacaBroker


def _broker() -> AlpacaBroker:
    return AlpacaBroker(key_id="test", secret="test", paper=True)


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


# ── Pre-flight validation: LONG brackets ─────────────────────────────────────

def test_long_inverted_sl_above_entry_rejected():
    # SL=102 > entry=100 > TP=105 is nonsensical for a LONG — would stop out
    # immediately. Must be caught before any network call.
    dec = _long_decision(entry=100.0, sl=102.0, tp=105.0)
    result = asyncio.run(_broker().submit_bracket(dec))
    assert result is None


def test_long_tp_below_entry_rejected():
    # TP=95 < entry=100 means the profit leg is below the entry — structurally
    # inverted. Must be caught before sending.
    dec = _long_decision(entry=100.0, sl=98.0, tp=95.0)
    result = asyncio.run(_broker().submit_bracket(dec))
    assert result is None


def test_long_sl_equals_entry_rejected():
    # SL == entry means zero risk distance — also invalid (division by zero risk).
    dec = _long_decision(entry=100.0, sl=100.0, tp=103.0)
    result = asyncio.run(_broker().submit_bracket(dec))
    assert result is None


# ── Pre-flight validation: SHORT brackets ────────────────────────────────────

def test_short_inverted_sl_below_entry_rejected():
    # For a SHORT, SL must be ABOVE entry; SL=98 < entry=100 is inverted.
    dec = _short_decision(entry=100.0, sl=98.0, tp=95.0)
    result = asyncio.run(_broker().submit_bracket(dec))
    assert result is None


def test_short_tp_above_entry_rejected():
    # TP=105 > entry=100 for a SHORT — profit leg is above entry, wrong side.
    dec = _short_decision(entry=100.0, sl=103.0, tp=105.0)
    result = asyncio.run(_broker().submit_bracket(dec))
    assert result is None


# ── Zero / non-positive quantity guard ───────────────────────────────────────

def test_zero_qty_rejected_before_network():
    # qty=0 must be caught before any network call (already guarded, but verify).
    dec = _long_decision(entry=100.0, sl=98.0, tp=103.0, qty=0)
    result = asyncio.run(_broker().submit_bracket(dec))
    assert result is None


# ── Non-actionable decisions ─────────────────────────────────────────────────

def test_pass_decision_skipped():
    dec = TradeDecision(ticker="TEST", decision=Decision.PASS, composite_score=50.0)
    result = asyncio.run(_broker().submit_bracket(dec))
    assert result is None


def test_no_risk_plan_skipped():
    dec = TradeDecision(ticker="TEST", decision=Decision.LONG, composite_score=70.0, risk=None)
    result = asyncio.run(_broker().submit_bracket(dec))
    assert result is None
