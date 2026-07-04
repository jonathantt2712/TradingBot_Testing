"""Nightly self-improvement: _apply_optimizer_params guard rails.

The autonomous path must only ever apply params with positive, walk-forward-
validated (held-out) profit; the operator path keeps its looser guard. Both
read optimization_results.json volume-aware and respect manual field locks.
"""
import json

import pytest

pytest.importorskip("fastapi")

import api_server  # noqa: E402


@pytest.fixture
def opt_env(tmp_path, monkeypatch):
    """Point the results file and weights file at a temp dir."""
    monkeypatch.setattr(api_server, "_VOLUME", tmp_path)
    monkeypatch.setattr(api_server, "WEIGHTS_FILE", tmp_path / "strategy_weights.json")
    return tmp_path


def _write_results(tmp_path, best):
    (tmp_path / "optimization_results.json").write_text(json.dumps({"best": best}))


_GOOD_BEST = {
    "params": {"LONG_THRESHOLD": 62.0, "SHORT_THRESHOLD": 38.0,
               "ATR_STOP_MULTIPLE": 1.8, "ATR_TARGET_MULTIPLE": 3.2},
    "in_sample": {"total_pnl": 900.0},
    "oos": {"total_pnl": 250.0, "total_trades": 12},
    "validated": True,
}


def test_auto_apply_requires_walk_forward_validation(opt_env):
    best = dict(_GOOD_BEST)
    del best["oos"]           # full-window only — no held-out evidence
    _write_results(opt_env, best)

    res = api_server._apply_optimizer_params(require_validated=True, source="auto")
    assert res["status"] == "rejected"
    assert "validated" in res["reason"]


def test_auto_apply_rejects_negative_oos(opt_env):
    best = dict(_GOOD_BEST)
    best["oos"] = {"total_pnl": -50.0, "total_trades": 12}
    _write_results(opt_env, best)

    res = api_server._apply_optimizer_params(require_validated=True, source="auto")
    assert res["status"] == "rejected"
    assert "not positive" in res["reason"]


def test_auto_apply_rejects_thin_holdout(opt_env):
    best = dict(_GOOD_BEST)
    best["validated"] = False   # optimizer: held-out split had too few trades
    _write_results(opt_env, best)

    res = api_server._apply_optimizer_params(require_validated=True, source="auto")
    assert res["status"] == "rejected"


def test_auto_apply_activates_live_tuning(opt_env):
    _write_results(opt_env, _GOOD_BEST)

    res = api_server._apply_optimizer_params(require_validated=True, source="auto")
    assert res["status"] == "applied"
    assert res["applied"]["long_threshold"] == 62.0

    saved = json.loads(api_server.WEIGHTS_FILE.read_text())
    assert saved["live_tuning_active"] is True
    assert saved["atr_stop_multiple"] == 1.8
    assert saved["applied_by"] == "auto"


def test_apply_respects_manual_overrides(opt_env):
    # A field the operator locked via the dashboard must not be overwritten.
    api_server._save(api_server.WEIGHTS_FILE, {
        **api_server.DEFAULT_WEIGHTS,
        "atr_stop_multiple": 2.5,
        "manual_overrides": {"atr_stop_multiple": True},
    })
    _write_results(opt_env, _GOOD_BEST)

    res = api_server._apply_optimizer_params(require_validated=True, source="auto")
    assert res["status"] == "applied"
    assert "atr_stop_multiple" not in res["applied"]

    saved = json.loads(api_server.WEIGHTS_FILE.read_text())
    assert saved["atr_stop_multiple"] == 2.5          # lock held
    assert saved["long_threshold"] == 62.0            # unlocked fields applied


def test_operator_apply_accepts_unvalidated_positive(opt_env):
    best = {
        "params": {"LONG_THRESHOLD": 58.0},
        "total_pnl": 120.0,   # full-window metrics, no "oos" key
    }
    _write_results(opt_env, best)

    res = api_server._apply_optimizer_params(require_validated=False, source="operator")
    assert res["status"] == "applied"
    assert res["validated"] is False


def test_self_tuner_skips_when_no_new_outcomes(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "TRADES_FILE", tmp_path / "trades.json")
    monkeypatch.setattr(api_server, "WEIGHTS_FILE", tmp_path / "strategy_weights.json")
    trades = [
        {"status": "closed", "pnl": 10.0 if i % 2 else -5.0,
         "direction": "LONG", "closed_at": f"2026-07-0{1 + i % 3}T14:00:00"}
        for i in range(16)
    ]
    (tmp_path / "trades.json").write_text(json.dumps(trades))

    api_server._update_strategy_weights()
    first = json.loads(api_server.WEIGHTS_FILE.read_text())
    assert first["update_count"] == 1

    # Same trade set again → update_count must NOT inflate (it gates Kelly).
    api_server._update_strategy_weights()
    again = json.loads(api_server.WEIGHTS_FILE.read_text())
    assert again["update_count"] == 1
