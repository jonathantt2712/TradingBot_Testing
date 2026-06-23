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
    # ib_insync -> eventkit imports asyncio.get_event_loop() at import time;
    # earlier asyncio.run() calls in the suite leave no current loop on 3.13.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
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
    monkeypatch.setattr(bootstrap, "active_broker", lambda s: "alpaca")
    bootstrap.preflight_checks(load_settings())
    keys = {i.key for i in health.active_issues()}
    assert not (keys & {"config:ibkr_lib", "config:ibkr_conn"})


# ── order management parity (breakeven lock) ──────────────────────────────────

def test_cancel_order_cancels_matching_open_trade():
    cancelled = []
    trade = _trade(55, "Submitted", 0.0, 0)
    b = IBKRBroker()
    b._ib = SimpleNamespace(
        isConnected=lambda: True,
        openTrades=lambda: [trade],
        cancelOrder=lambda o: cancelled.append(o),
    )
    assert asyncio.run(b.cancel_order("55")) is True
    assert cancelled == [trade.order]


def test_cancel_order_missing_returns_false():
    b = IBKRBroker()
    b._ib = SimpleNamespace(
        isConnected=lambda: True,
        openTrades=lambda: [],
        cancelOrder=lambda o: None,
    )
    assert asyncio.run(b.cancel_order("999")) is False
    assert asyncio.run(b.cancel_order("nope")) is False


def test_get_positions_enriched_with_pnl():
    pos = SimpleNamespace(contract=SimpleNamespace(symbol="NVDA"), position=10.0)
    item = SimpleNamespace(contract=SimpleNamespace(symbol="NVDA"),
                           marketValue=1010.0, unrealizedPNL=10.0)

    async def _req():
        return [pos]

    b = IBKRBroker()
    b._ib = SimpleNamespace(
        isConnected=lambda: True,
        reqPositionsAsync=_req,
        portfolio=lambda: [item],
    )
    out = asyncio.run(b.get_positions())
    assert out == [{
        "symbol": "NVDA", "qty": 10.0, "side": "long",
        "market_value": 1010.0, "unrealized_pl": 10.0,
    }]


def test_get_positions_degrades_without_portfolio():
    pos = SimpleNamespace(contract=SimpleNamespace(symbol="AMD"), position=-5.0)

    async def _req():
        return [pos]

    b = IBKRBroker()
    b._ib = SimpleNamespace(
        isConnected=lambda: True,
        reqPositionsAsync=_req,
        portfolio=lambda: [],            # no live P&L available
    )
    out = asyncio.run(b.get_positions())
    assert out[0]["side"] == "short"
    assert out[0]["market_value"] == 0.0 and out[0]["unrealized_pl"] == 0.0


def test_get_open_orders_maps_order_type():
    def _ot(sym, oid, action, otype):
        return SimpleNamespace(
            contract=SimpleNamespace(symbol=sym),
            order=SimpleNamespace(orderId=oid, action=action, orderType=otype),
        )
    b = IBKRBroker()
    b._ib = SimpleNamespace(
        isConnected=lambda: True,
        openTrades=lambda: [_ot("NVDA", 1, "SELL", "STP"), _ot("AMD", 2, "BUY", "LMT")],
    )
    out = asyncio.run(b.get_open_orders())
    assert out[0] == {"symbol": "NVDA", "id": "1", "side": "sell", "type": "stop"}
    assert out[1]["type"] == "limit"


# ── broker toggle: active_broker precedence + build_broker routing ────────────

def test_active_broker_file_overrides_env(tmp_path, monkeypatch):
    f = tmp_path / "broker_mode.json"
    f.write_text('{"broker": "ibkr"}', encoding="utf-8")
    monkeypatch.setattr(bootstrap, "_BROKER_MODE_FILE", f)
    monkeypatch.setenv("BROKER", "alpaca")
    assert bootstrap.active_broker(load_settings()) == "ibkr"


def test_active_broker_falls_back_to_env(tmp_path, monkeypatch):
    f = tmp_path / "broker_mode.json"
    monkeypatch.setattr(bootstrap, "_BROKER_MODE_FILE", f)   # file does not exist
    monkeypatch.setenv("BROKER", "ibkr")
    assert bootstrap.active_broker(load_settings()) == "ibkr"


def test_active_broker_ignores_garbage_value(tmp_path, monkeypatch):
    f = tmp_path / "broker_mode.json"
    f.write_text('{"broker": "robinhood"}', encoding="utf-8")
    monkeypatch.setattr(bootstrap, "_BROKER_MODE_FILE", f)
    monkeypatch.setenv("BROKER", "alpaca")
    assert bootstrap.active_broker(load_settings()) == "alpaca"


def test_build_broker_routes_to_ibkr_when_toggled(tmp_path, monkeypatch):
    from execution.alpaca_broker import AlpacaBroker
    f = tmp_path / "broker_mode.json"
    f.write_text('{"broker": "ibkr"}', encoding="utf-8")
    monkeypatch.setattr(bootstrap, "_BROKER_MODE_FILE", f)
    broker = bootstrap.build_broker(load_settings(), force_live=True)
    assert isinstance(broker, IBKRBroker)

    f.write_text('{"broker": "alpaca"}', encoding="utf-8")
    broker = bootstrap.build_broker(load_settings(), force_live=True)
    assert isinstance(broker, AlpacaBroker)


# ── live switch watcher ───────────────────────────────────────────────────────

def test_broker_switch_watch_returns_on_change(monkeypatch):
    import live_runner
    monkeypatch.setattr(live_runner, "BROKER_SWITCH_POLL_S", 0.01)
    monkeypatch.setattr(live_runner, "active_broker", lambda s: "ibkr")
    # current_mode is alpaca; the watcher sees ibkr -> returns promptly.
    asyncio.run(asyncio.wait_for(
        live_runner._broker_switch_watch(load_settings(), "alpaca"), timeout=2.0))
