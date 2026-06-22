"""IBKR adaptations: order-state mapping, TCP reachability, and preflight.

These cover the IBKR-specific glue without a live TWS: the broker's get_order
shape (so fill/slippage tracking works), the socket probe, and the startup
preflight that tells the operator when TWS/Gateway isn't reachable.
"""
import asyncio
import socket
from types import SimpleNamespace

import pytest

import bootstrap
from config.settings import load_settings
from core import health
from execution.ibkr_broker import IBKRBroker


@pytest.fixture(autouse=True)
def _clean_health():
    health.reset()
    yield
    health.reset()


def _broker_with_trades(trades):
    b = IBKRBroker()
    b._ib = SimpleNamespace(
        isConnected=lambda: True,
        trades=lambda: trades,
    )
    return b


def _trade(order_id, status, avg_fill, filled):
    return SimpleNamespace(
        order=SimpleNamespace(orderId=order_id),
        orderStatus=SimpleNamespace(status=status, avgFillPrice=avg_fill, filled=filled),
    )


# ── get_order: the shape PortfolioManager._track_fill consumes ────────────────

def test_get_order_maps_filled_status():
    b = _broker_with_trades([_trade(42, "Filled", 101.25, 10)])
    order = asyncio.run(b.get_order("42"))
    assert order == {"status": "filled", "filled_avg_price": 101.25, "filled_qty": 10}


def test_get_order_unfilled_has_no_price():
    b = _broker_with_trades([_trade(7, "Submitted", 0.0, 0)])
    order = asyncio.run(b.get_order("7"))
    assert order["status"] == "submitted"
    assert order["filled_avg_price"] is None      # 0.0 -> None, so callers don't treat it as a fill
    assert order["filled_qty"] is None


def test_get_order_missing_returns_none():
    b = _broker_with_trades([_trade(1, "Filled", 50.0, 5)])
    assert asyncio.run(b.get_order("999")) is None


def test_get_order_non_numeric_id_returns_none():
    b = _broker_with_trades([_trade(1, "Filled", 50.0, 5)])
    assert asyncio.run(b.get_order("abc")) is None


# ── _tcp_reachable ────────────────────────────────────────────────────────────

def test_tcp_reachable_true_for_listening_socket():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        assert bootstrap._tcp_reachable("127.0.0.1", port, timeout=1.0) is True
    finally:
        srv.close()


def test_tcp_reachable_false_for_closed_port():
    # Bind then close to obtain a port nothing is listening on.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert bootstrap._tcp_reachable("127.0.0.1", port, timeout=0.5) is False


# ── preflight ─────────────────────────────────────────────────────────────────

def test_preflight_reports_ibkr_when_not_ready(monkeypatch):
    # Force the unreachable path so the test is deterministic regardless of
    # whether ib_insync is installed or a TWS happens to be running locally.
    monkeypatch.setattr(bootstrap, "_tcp_reachable", lambda *a, **k: False)
    monkeypatch.setenv("BROKER", "ibkr")
    monkeypatch.setenv("USE_LIQUID_BROKER", "false")
    bootstrap.preflight_checks(load_settings())
    keys = {i.key for i in health.active_issues()}
    # ib_insync missing -> config:ibkr_lib; present but no TWS -> config:ibkr_conn.
    assert keys & {"config:ibkr_lib", "config:ibkr_conn"}


def test_preflight_silent_when_ibkr_ready(monkeypatch):
    pytest.importorskip("ib_insync")
    monkeypatch.setattr(bootstrap, "_tcp_reachable", lambda *a, **k: True)
    monkeypatch.setenv("BROKER", "ibkr")
    monkeypatch.setenv("USE_LIQUID_BROKER", "false")
    bootstrap.preflight_checks(load_settings())
    keys = {i.key for i in health.active_issues()}
    assert not (keys & {"config:ibkr_lib", "config:ibkr_conn"})


def test_preflight_skips_ibkr_when_broker_is_alpaca(monkeypatch):
    monkeypatch.setattr(bootstrap, "_tcp_reachable", lambda *a, **k: False)
    monkeypatch.setenv("BROKER", "alpaca")
    bootstrap.preflight_checks(load_settings())
    keys = {i.key for i in health.active_issues()}
    assert not (keys & {"config:ibkr_lib", "config:ibkr_conn"})
