"""Per-regime learned strategies: the WeightTuner tunes each regime on its own
trades and writes regime_params; the PortfolioManager applies the current
regime's block, falling back to global/heuristic behaviour when absent.
"""
import json
import types

import pytest

pytest.importorskip("pandas")

import core.weight_tuner as wt
from core.weight_tuner import WeightTuner
import execution.portfolio_manager as pmmod
from execution.portfolio_manager import PortfolioManager


# ── WeightTuner: per-regime tuning ───────────────────────────────────────────

def _trade(regime, direction, pnl, tech_score):
    # fundamental deliberately leans the OPPOSITE way (score 30 = SHORT) so the
    # two agents diverge within a regime: on a LONG win tech is right & funda
    # wrong; on a LONG loss it flips. That makes per-regime weights differ.
    return {
        "status": "closed", "direction": direction, "pnl": pnl, "regime": regime,
        "evaluations": [{"role": "technical", "score": tech_score},
                        {"role": "fundamental", "score": 30}],
    }


@pytest.fixture
def tuner_files(tmp_path, monkeypatch):
    monkeypatch.setattr(wt, "_WEIGHTS_FILE", tmp_path / "strategy_weights.json")
    monkeypatch.setattr(wt, "_HISTORY_FILE", tmp_path / "learning_history.jsonl")
    return tmp_path / "strategy_weights.json"


def test_tuner_writes_per_regime_blocks(tuner_files):
    # risk_on: tech leaned LONG (70) and WON every time → tech accurate.
    # risk_off: tech leaned LONG (70) and LOST every time → tech inaccurate.
    trades = ([_trade("risk_on", "LONG", 100, 70) for _ in range(12)]
              + [_trade("risk_off", "LONG", -100, 70) for _ in range(12)])
    WeightTuner({"technical": 0.5, "fundamental": 0.5}).update_from_trades(trades)

    out = json.loads(tuner_files.read_text())
    rp = out["regime_params"]
    assert set(rp) == {"risk_on", "risk_off"}
    # tech earns more weight where it was right (risk_on) than where it was wrong
    assert rp["risk_on"]["agent_weights"]["technical"] > rp["risk_off"]["agent_weights"]["technical"]
    # win-rate driven thresholds diverge: risk_on (100%) loosens, risk_off (0%) tightens
    assert rp["risk_on"]["long_threshold"] < rp["risk_off"]["long_threshold"]


def test_tuner_skips_under_sampled_regime(tuner_files):
    # 12 risk_on (enough) + 3 risk_off (below _MIN_TRADES) → only risk_on tuned
    trades = ([_trade("risk_on", "LONG", 100, 70) for _ in range(12)]
              + [_trade("risk_off", "LONG", -50, 70) for _ in range(3)])
    WeightTuner({"technical": 0.5, "fundamental": 0.5}).update_from_trades(trades)
    rp = json.loads(tuner_files.read_text())["regime_params"]
    assert "risk_on" in rp and "risk_off" not in rp


# ── PortfolioManager: applying / falling back ────────────────────────────────

def _pm(tmp_path, monkeypatch, regime, file_dict):
    f = tmp_path / "strategy_weights.json"
    f.write_text(json.dumps(file_dict))
    monkeypatch.setattr(pmmod, "_WEIGHTS_FILE", f)
    pm = object.__new__(PortfolioManager)
    pm._weights = {"technical": 0.30, "fundamental": 0.30, "vision": 0.20, "liquid": 0.20}
    pm._thresholds = types.SimpleNamespace(long_above=60.0, short_below=40.0)
    pm._regime = types.SimpleNamespace(regime=types.SimpleNamespace(value=regime))
    pm._tuned_file = {}
    pm._tuned_weights = {}
    pm._tuned_weights_ts = 0.0
    return pm


_FILE = {
    "live_tuning_active": True,
    "agent_weights": {"technical": 0.40, "fundamental": 0.30, "vision": 0.15, "liquid": 0.15},
    "long_threshold": 60.0, "short_threshold": 40.0,
    "regime_params": {
        "risk_off": {
            "agent_weights": {"technical": 0.10, "fundamental": 0.60, "vision": 0.15, "liquid": 0.15},
            "long_threshold": 70.0, "short_threshold": 30.0,
        }
    },
}


def test_pm_applies_current_regime_block(tmp_path, monkeypatch):
    pm = _pm(tmp_path, monkeypatch, "risk_off", _FILE)
    assert pm._regime_block()  # non-empty
    assert pm._live_weight("technical") == 0.10            # regime value, not global 0.40
    assert pm._effective_thresholds(False) == (70.0, 30.0)  # regime thresholds


def test_pm_falls_back_to_global_when_regime_unlearned(tmp_path, monkeypatch):
    pm = _pm(tmp_path, monkeypatch, "risk_on", _FILE)      # no risk_on block
    assert pm._regime_block() == {}
    assert pm._live_weight("technical") == 0.40            # global tuned
    assert pm._effective_thresholds(False) == (60.0, 40.0)  # global thresholds


def test_pm_falls_back_to_settings_when_tuning_inactive(tmp_path, monkeypatch):
    pm = _pm(tmp_path, monkeypatch, "risk_off", {"agent_weights": {"technical": 0.9}})
    assert pm._regime_block() == {}                        # not live_tuning_active
    assert pm._live_weight("technical") == 0.30            # settings default
    assert pm._effective_thresholds(False) == (60.0, 40.0)


def test_pm_backtest_mode_ignores_learned_thresholds(tmp_path, monkeypatch):
    pm = _pm(tmp_path, monkeypatch, "risk_off", _FILE)
    assert pm._effective_thresholds(True) == (60.0, 40.0)  # settings, never the file
