"""TelegramPublisher — what gets pushed, and when.

Two rules the publisher owns for every caller: alerts are trade entries and
exits only, and nothing goes out while the US market is closed.
"""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from data import telegram_publisher as tp

_ET = ZoneInfo("America/New_York")


def _at(monkeypatch, when: datetime) -> None:
    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return when
    monkeypatch.setattr(tp, "datetime", _FrozenDatetime)


# ── market_is_open ───────────────────────────────────────────────────────────

def test_open_during_regular_session(monkeypatch):
    _at(monkeypatch, datetime(2026, 6, 23, 11, 0, tzinfo=_ET))   # Tuesday 11:00
    assert tp.market_is_open() is True


def test_closed_before_the_open(monkeypatch):
    _at(monkeypatch, datetime(2026, 6, 23, 9, 0, tzinfo=_ET))
    assert tp.market_is_open() is False


def test_closed_after_the_close(monkeypatch):
    _at(monkeypatch, datetime(2026, 6, 23, 16, 30, tzinfo=_ET))
    assert tp.market_is_open() is False


def test_closed_on_the_weekend(monkeypatch):
    _at(monkeypatch, datetime(2026, 6, 27, 11, 0, tzinfo=_ET))   # Saturday
    assert tp.market_is_open() is False


# ── _notify gating ───────────────────────────────────────────────────────────

def _record_posts(monkeypatch) -> list:
    posted: list = []

    class _Resp:
        status = 200

        async def text(self):
            return ""

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def post(self, url, json=None, headers=None, timeout=None):
            posted.append(json)
            return _Resp()

    monkeypatch.setattr(tp.aiohttp, "ClientSession", lambda *a, **kw: _Session())
    return posted


def test_trade_entry_sent_while_open(monkeypatch):
    monkeypatch.setenv("DASHBOARD_URL", "https://dash.example")
    posted = _record_posts(monkeypatch)
    _at(monkeypatch, datetime(2026, 6, 23, 11, 0, tzinfo=_ET))
    pub = tp.TelegramPublisher(bot_token="token")

    asyncio.run(pub.send_trade_entry({"ticker": "NVDA", "direction": "LONG"}))
    assert [p["type"] for p in posted] == ["trade_entry"]


def test_nothing_sent_while_market_closed(monkeypatch):
    monkeypatch.setenv("DASHBOARD_URL", "https://dash.example")
    posted = _record_posts(monkeypatch)
    _at(monkeypatch, datetime(2026, 6, 23, 18, 0, tzinfo=_ET))   # after the close
    pub = tp.TelegramPublisher(bot_token="token")

    asyncio.run(pub.send_trade_entry({"ticker": "NVDA", "direction": "LONG"}))
    asyncio.run(pub.send_trade_exit({"ticker": "NVDA"}, 101.0, "take_profit", 42.0))
    assert posted == []


def test_only_buy_and_sell_alerts_exist():
    """No scanner/gapper/weekly/health push survives on the publisher."""
    senders = {n for n in dir(tp.TelegramPublisher) if n.startswith("send_")}
    assert senders == {"send_trade_entry", "send_trade_exit"}
