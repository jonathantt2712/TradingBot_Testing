"""Autonomous paper executor (Railway) — arming gates, candidate selection,
shared entry guards, and an end-to-end sweep with a mocked broker.

The executor must (a) never fire unless fully armed and on a paper account, and
(b) honour the EXACT same risk gates as /api/execute (it reuses _entry_guard_reason).
"""
import asyncio
import json

import pytest

pytest.importorskip("fastapi")

import api_server  # noqa: E402
from api_server import ExecuteBody  # noqa: E402


def _rec(ticker, direction, score, qty=10, expires="9999-01-01T00:00:00"):
    return {
        "id": f"{ticker}-1",
        "ticker": ticker,
        "direction": direction,
        "composite_score": score,
        "expires_at": expires,
        "risk": {"entry": 100.0, "stop_loss": 95.0, "take_profit": 110.0, "qty": qty},
        "beta": 1.0,
    }


# ── _auto_exec_candidates ────────────────────────────────────────────────────

def test_strong_long_and_short_selected():
    recs = [_rec("AAA", "LONG", 65), _rec("BBB", "SHORT", 35)]
    out = api_server._auto_exec_candidates(recs, "2026-06-23T00:00:00")
    assert {r["ticker"] for r in out} == {"AAA", "BBB"}


def test_weak_conviction_excluded():
    recs = [_rec("AAA", "LONG", 58), _rec("BBB", "SHORT", 45)]
    assert api_server._auto_exec_candidates(recs, "2026-06-23T00:00:00") == []


def test_expired_excluded():
    recs = [_rec("AAA", "LONG", 70, expires="2000-01-01T00:00:00")]
    assert api_server._auto_exec_candidates(recs, "2026-06-23T00:00:00") == []


def test_unsizable_qty_excluded():
    recs = [_rec("AAA", "LONG", 70, qty=0)]
    assert api_server._auto_exec_candidates(recs, "2026-06-23T00:00:00") == []


# ── _auto_exec_disarmed_reason ───────────────────────────────────────────────

def _arm_all(monkeypatch):
    monkeypatch.setattr(api_server, "AUTO_EXECUTE_ON_RAILWAY", True)
    monkeypatch.setattr(api_server, "_ALPACA_PAPER", True)
    monkeypatch.setattr(api_server, "_ALPACA_KEY", "k")
    monkeypatch.setattr(api_server, "_ALPACA_SECRET", "s")
    monkeypatch.setattr(api_server, "_load_trade_mode", lambda: {"auto_execute": True})


def test_armed_when_all_conditions_met(monkeypatch):
    _arm_all(monkeypatch)
    assert api_server._auto_exec_disarmed_reason() is None


def test_disarmed_when_env_off(monkeypatch):
    _arm_all(monkeypatch)
    monkeypatch.setattr(api_server, "AUTO_EXECUTE_ON_RAILWAY", False)
    assert api_server._auto_exec_disarmed_reason() == "AUTO_EXECUTE_ON_RAILWAY off"


def test_disarmed_refuses_non_paper(monkeypatch):
    _arm_all(monkeypatch)
    monkeypatch.setattr(api_server, "_ALPACA_PAPER", False)
    assert "non-paper" in api_server._auto_exec_disarmed_reason()


def test_disarmed_without_keys(monkeypatch):
    _arm_all(monkeypatch)
    monkeypatch.setattr(api_server, "_ALPACA_SECRET", "")
    assert api_server._auto_exec_disarmed_reason() == "Alpaca API keys not set"


def test_disarmed_when_toggle_off(monkeypatch):
    _arm_all(monkeypatch)
    monkeypatch.setattr(api_server, "_load_trade_mode", lambda: {"auto_execute": False})
    assert api_server._auto_exec_disarmed_reason() == "dashboard auto-execute toggle off"


# ── _entry_guard_reason (shared with /api/execute) ───────────────────────────

def _open(ticker, direction="LONG", beta=1.0):
    return {"status": "open", "ticker": ticker, "direction": direction, "beta": beta}


def test_guard_clear_on_empty_book(monkeypatch):
    monkeypatch.setattr(api_server, "_check_circuit_breaker", lambda: None)
    assert api_server._entry_guard_reason("AAA", "LONG", 70, 1.0, []) is None


def test_guard_circuit_breaker(monkeypatch):
    monkeypatch.setattr(api_server, "_check_circuit_breaker", lambda: "daily loss hit")
    assert api_server._entry_guard_reason("AAA", "LONG", 70, 1.0, []) == "daily loss hit"


def test_guard_max_positions(monkeypatch):
    monkeypatch.setattr(api_server, "_check_circuit_breaker", lambda: None)
    monkeypatch.setattr(api_server, "MAX_OPEN_POSITIONS", 1)
    reason = api_server._entry_guard_reason("AAA", "LONG", 70, 1.0, [_open("ZZZ")])
    assert "Max open positions" in reason


def test_guard_sector_cap(monkeypatch):
    monkeypatch.setattr(api_server, "_check_circuit_breaker", lambda: None)
    monkeypatch.setattr(api_server, "MAX_OPEN_POSITIONS", 99)
    monkeypatch.setattr(api_server, "_SECTOR_MAP", {"AAA": "Tech", "BBB": "Tech", "CCC": "Tech"})
    hist = [_open("BBB"), _open("CCC")]
    assert "Sector limit" in api_server._entry_guard_reason("AAA", "LONG", 70, 1.0, hist)


def test_guard_beta_cap(monkeypatch):
    monkeypatch.setattr(api_server, "_check_circuit_breaker", lambda: None)
    monkeypatch.setattr(api_server, "MAX_OPEN_POSITIONS", 99)
    monkeypatch.setattr(api_server, "_SECTOR_MAP", {})
    monkeypatch.setattr(api_server, "PORTFOLIO_BETA_CAP", 2.0)
    hist = [_open("BBB", "LONG", beta=2.0)]
    assert "beta cap" in api_server._entry_guard_reason("AAA", "LONG", 1.5, 1.0, hist)


# ── _run_auto_executor (end to end, mocked broker) ───────────────────────────

def _wire_files(tmp_path, monkeypatch, recs):
    trades_f = tmp_path / "trades.json"
    recs_f = tmp_path / "recommendations.json"
    ctx_f = tmp_path / "context.json"
    regime_f = tmp_path / "regime.json"
    trades_f.write_text("[]")
    recs_f.write_text(json.dumps(recs))
    monkeypatch.setattr(api_server, "TRADES_FILE", trades_f)
    monkeypatch.setattr(api_server, "HISTORY_FILE", trades_f)   # same file in prod
    monkeypatch.setattr(api_server, "RECS_FILE", recs_f)
    monkeypatch.setattr(api_server, "CONTEXT_FILE", ctx_f)
    monkeypatch.setattr(api_server, "REGIME_FILE", regime_f)
    monkeypatch.setattr(api_server, "_check_circuit_breaker", lambda: None)
    return trades_f


def test_executor_places_and_records(tmp_path, monkeypatch):
    trades_f = _wire_files(tmp_path, monkeypatch, [_rec("AAA", "LONG", 70)])

    async def fake_submit(session, **kw):
        return "ORDER-AAA"
    monkeypatch.setattr(api_server, "_submit_paper_bracket", fake_submit)

    async def run():
        api_server._trades_lock = asyncio.Lock()
        return await api_server._run_auto_executor()

    placed = asyncio.run(run())
    assert placed == 1
    book = json.loads(trades_f.read_text())
    assert len(book) == 1
    assert book[0]["order_id"] == "ORDER-AAA" and book[0]["ticker"] == "AAA"
    assert book[0]["status"] == "open"


def test_executor_dedups_existing_position(tmp_path, monkeypatch):
    trades_f = _wire_files(tmp_path, monkeypatch, [_rec("AAA", "LONG", 70)])
    trades_f.write_text(json.dumps([{"status": "open", "ticker": "AAA", "direction": "LONG"}]))

    async def fake_submit(session, **kw):
        raise AssertionError("should not place an order for an already-open name")
    monkeypatch.setattr(api_server, "_submit_paper_bracket", fake_submit)

    async def run():
        api_server._trades_lock = asyncio.Lock()
        return await api_server._run_auto_executor()

    assert asyncio.run(run()) == 0


def test_executor_respects_guards_without_placing(tmp_path, monkeypatch):
    _wire_files(tmp_path, monkeypatch, [_rec("AAA", "LONG", 70)])
    monkeypatch.setattr(api_server, "_check_circuit_breaker", lambda: "halted")

    async def fake_submit(session, **kw):
        raise AssertionError("guards must block BEFORE any order is placed")
    monkeypatch.setattr(api_server, "_submit_paper_bracket", fake_submit)

    async def run():
        api_server._trades_lock = asyncio.Lock()
        return await api_server._run_auto_executor()

    assert asyncio.run(run()) == 0
