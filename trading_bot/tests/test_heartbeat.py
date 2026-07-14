"""Live-runner heartbeat watchdog — a crashed bot must not fail silent."""
import json
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")

import api_server  # noqa: E402
from core import health  # noqa: E402


@pytest.fixture
def hb_env(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "DATA_DIR", tmp_path)
    health.resolve("live_runner:heartbeat")   # clean board
    yield tmp_path
    health.resolve("live_runner:heartbeat")


def _write_hb(tmp_path, age_seconds):
    ts = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=age_seconds)
    (tmp_path / "live_heartbeat.json").write_text(
        json.dumps({"ts": ts.isoformat(), "execute": True, "broker": "alpaca", "tickers": 8}))


def _issue_keys():
    return {i.key for i in health.active_issues()}


def test_no_heartbeat_file_is_silent(hb_env, monkeypatch):
    monkeypatch.setattr(api_server, "_is_market_open", lambda: True)
    api_server._check_live_heartbeat()
    assert "live_runner:heartbeat" not in _issue_keys()


def test_fresh_heartbeat_is_healthy(hb_env, monkeypatch):
    monkeypatch.setattr(api_server, "_is_market_open", lambda: True)
    _write_hb(hb_env, age_seconds=30)
    api_server._check_live_heartbeat()
    assert "live_runner:heartbeat" not in _issue_keys()


def test_stale_heartbeat_during_market_hours_alerts(hb_env, monkeypatch):
    monkeypatch.setattr(api_server, "_is_market_open", lambda: True)
    _write_hb(hb_env, age_seconds=1200)   # 20 min silent
    api_server._check_live_heartbeat()
    assert "live_runner:heartbeat" in _issue_keys()


def test_stale_heartbeat_after_hours_is_expected(hb_env, monkeypatch):
    monkeypatch.setattr(api_server, "_is_market_open", lambda: False)
    _write_hb(hb_env, age_seconds=100_000)   # bot rightly off overnight
    api_server._check_live_heartbeat()
    assert "live_runner:heartbeat" not in _issue_keys()


def test_recovery_resolves_the_issue(hb_env, monkeypatch):
    monkeypatch.setattr(api_server, "_is_market_open", lambda: True)
    _write_hb(hb_env, age_seconds=1200)
    api_server._check_live_heartbeat()
    assert "live_runner:heartbeat" in _issue_keys()
    _write_hb(hb_env, age_seconds=10)      # bot restarted
    api_server._check_live_heartbeat()
    assert "live_runner:heartbeat" not in _issue_keys()
