"""Online WeightTuner — accuracy-driven multipliers and threshold nudges.

The tuner is the core "learn on the run" loop: it reads resolved trade
outcomes, scores each agent's directional accuracy, and rewrites
strategy_weights.json. These tests pin the maths so a refactor can't
silently break the feedback loop.
"""
import json

import pytest

from core import weight_tuner
from core.weight_tuner import WeightTuner, _MIN_TRADES


@pytest.fixture(autouse=True)
def _isolate_files(tmp_path, monkeypatch):
    """Redirect the tuner's output files into a temp dir (never touch data/)."""
    monkeypatch.setattr(weight_tuner, "_WEIGHTS_FILE", tmp_path / "strategy_weights.json")
    monkeypatch.setattr(weight_tuner, "_HISTORY_FILE", tmp_path / "learning_history.jsonl")
    return tmp_path


def _entry(decision, pnl, scores):
    return {"decision": decision, "outcome_pnl": pnl, "agent_scores": scores}


def _read_weights(tmp_path):
    return json.loads((tmp_path / "strategy_weights.json").read_text())


# ── gating ──────────────────────────────────────────────────────────────────

def test_below_min_trades_is_noop(_isolate_files):
    tuner = WeightTuner({"technical": 0.5, "fundamental": 0.5})
    entries = [_entry("LONG", 100.0, {"technical": 60}) for _ in range(_MIN_TRADES - 1)]
    tuner._run(entries)
    assert not (_isolate_files / "strategy_weights.json").exists()


def test_ignores_unresolved_and_malformed(_isolate_files):
    tuner = WeightTuner({"technical": 0.5, "fundamental": 0.5})
    # 9 valid + a pile of junk that must be filtered out, leaving < MIN_TRADES.
    entries = [_entry("LONG", 100.0, {"technical": 60}) for _ in range(9)]
    entries += [
        {"decision": "LONG", "outcome_pnl": None, "agent_scores": {"technical": 60}},
        {"decision": "PASS", "outcome_pnl": 50.0, "agent_scores": {"technical": 60}},
        {"decision": "LONG", "outcome_pnl": 50.0, "agent_scores": {}},
    ]
    tuner._run(entries)
    assert not (_isolate_files / "strategy_weights.json").exists()


# ── per-agent accuracy → multipliers ─────────────────────────────────────────

def test_accurate_agent_boosted_inaccurate_penalised(_isolate_files):
    """A perfectly-correct agent earns 2x; an always-wrong agent floors at 0.1x."""
    tuner = WeightTuner({"good": 0.5, "bad": 0.5})
    entries = []
    for _ in range(6):  # LONG winners: good agrees (>=50), bad disagrees
        entries.append(_entry("LONG", 100.0, {"good": 60, "bad": 40}))
    for _ in range(4):  # LONG losers: good disagrees, bad agrees
        entries.append(_entry("LONG", -100.0, {"good": 40, "bad": 60}))

    tuner._run(entries)
    w = _read_weights(_isolate_files)

    assert w["agent_multipliers"]["good"] == 2.0   # accuracy 1.0 → 1 + 0.5*2
    assert w["agent_multipliers"]["bad"] == 0.1     # accuracy 0.0 → floored
    assert w["live_tuning_active"] is True
    assert w["win_rate_30d"] == 60.0
    assert w["sample_size"] == 10


def test_weights_renormalise_to_one(_isolate_files):
    tuner = WeightTuner({"good": 0.5, "bad": 0.5})
    entries = [_entry("LONG", 100.0, {"good": 60, "bad": 40}) for _ in range(6)]
    entries += [_entry("LONG", -100.0, {"good": 40, "bad": 60}) for _ in range(4)]
    tuner._run(entries)
    w = _read_weights(_isolate_files)
    assert abs(sum(w["agent_weights"].values()) - 1.0) < 0.01
    # good (2x) should out-weigh bad (0.1x) after renormalisation
    assert w["agent_weights"]["good"] > w["agent_weights"]["bad"]


def test_thin_agent_sample_stays_neutral(_isolate_files):
    """An agent seen on < 5 trades keeps a 1.0 multiplier (no premature judgement)."""
    tuner = WeightTuner({"technical": 0.5, "rare": 0.5})
    entries = [_entry("LONG", 100.0, {"technical": 60}) for _ in range(10)]
    entries[0]["agent_scores"]["rare"] = 10  # appears once
    tuner._run(entries)
    w = _read_weights(_isolate_files)
    assert w["agent_multipliers"]["rare"] == 1.0


# ── threshold adaptation ─────────────────────────────────────────────────────

def test_low_winrate_tightens_thresholds(_isolate_files):
    tuner = WeightTuner({"technical": 1.0})
    entries = [_entry("LONG", 100.0, {"technical": 60}) for _ in range(2)]
    entries += [_entry("LONG", -100.0, {"technical": 60}) for _ in range(8)]  # 20% wr
    tuner._run(entries)
    w = _read_weights(_isolate_files)
    assert w["long_threshold"] == 61.0   # 60 + NUDGE
    assert w["short_threshold"] == 39.0  # 40 - NUDGE


def test_high_winrate_loosens_thresholds(_isolate_files):
    tuner = WeightTuner({"technical": 1.0})
    entries = [_entry("LONG", 100.0, {"technical": 60}) for _ in range(8)]
    entries += [_entry("LONG", -100.0, {"technical": 60}) for _ in range(2)]  # 80% wr
    tuner._run(entries)
    w = _read_weights(_isolate_files)
    assert w["long_threshold"] == 59.0   # 60 - NUDGE
    assert w["short_threshold"] == 41.0  # 40 + NUDGE


def test_threshold_nudge_respects_existing_file_and_clamp(_isolate_files):
    (_isolate_files / "strategy_weights.json").write_text(
        json.dumps({"long_threshold": 75.0, "short_threshold": 25.0})
    )
    tuner = WeightTuner({"technical": 1.0})
    entries = [_entry("LONG", -100.0, {"technical": 60}) for _ in range(10)]  # 0% wr
    tuner._run(entries)
    w = _read_weights(_isolate_files)
    assert w["long_threshold"] == 75.0   # clamped at ceiling
    assert w["short_threshold"] == 25.0  # clamped at floor


# ── directional bias ─────────────────────────────────────────────────────────

def test_bias_detection(_isolate_files):
    tuner = WeightTuner({"technical": 1.0})
    entries = [_entry("LONG", 100.0, {"technical": 60}) for _ in range(5)]   # long 100%
    entries += [_entry("SHORT", -100.0, {"technical": 40}) for _ in range(5)]  # short 0%
    tuner._run(entries)
    w = _read_weights(_isolate_files)
    assert w["bias"] == "long"
    assert w["long_win_rate"] == 100.0
    assert w["short_win_rate"] == 0.0


# ── server-side entry point ──────────────────────────────────────────────────

def test_update_from_trades_adapts_api_shape(_isolate_files):
    """The api_server closed-trade shape feeds the same learning loop."""
    tuner = WeightTuner({"technical": 0.5, "fundamental": 0.5})
    trades = []
    for _ in range(6):
        trades.append({"direction": "LONG", "pnl": 100.0,
                       "evaluations": [{"role": "technical", "score": 60}]})
    for _ in range(4):
        trades.append({"direction": "LONG", "pnl": -100.0,
                       "evaluations": [{"role": "technical", "score": 40}]})
    tuner.update_from_trades(trades)
    w = _read_weights(_isolate_files)
    assert w["live_tuning_active"] is True
    assert w["agent_multipliers"]["technical"] == 2.0


def test_update_from_trades_skips_unscored_and_unresolved(_isolate_files):
    tuner = WeightTuner({"technical": 1.0})
    trades = [
        {"direction": "LONG", "pnl": None, "evaluations": [{"role": "technical", "score": 60}]},
        {"direction": "LONG", "pnl": 50.0, "evaluations": []},
        {"direction": "FLAT", "pnl": 50.0, "evaluations": [{"role": "technical", "score": 60}]},
    ]
    tuner.update_from_trades(trades)
    assert not (_isolate_files / "strategy_weights.json").exists()


# ── edge cases ───────────────────────────────────────────────────────────────

def test_all_wins_loosens_threshold_to_floor(_isolate_files):
    # 100% win rate satisfies the `> 65.0` branch; thresholds loosen.
    # After multiple tuning rounds from a stuck floor (55.0 / 45.0), they
    # must not cross the saturation bounds.
    (_isolate_files / "strategy_weights.json").write_text(
        json.dumps({"long_threshold": 55.0, "short_threshold": 45.0})
    )
    tuner = WeightTuner({"technical": 1.0})
    entries = [_entry("LONG", 100.0, {"technical": 60}) for _ in range(10)]  # 100% wr
    tuner._run(entries)
    w = _read_weights(_isolate_files)
    assert w["long_threshold"] >= 55.0   # cannot drop below floor
    assert w["short_threshold"] <= 45.0  # cannot rise above ceiling


def test_all_losses_tightens_threshold_to_ceiling(_isolate_files):
    # 0% win rate satisfies the `< 40.0` branch; thresholds tighten.
    # Already at the ceiling → clamped (no further movement).
    (_isolate_files / "strategy_weights.json").write_text(
        json.dumps({"long_threshold": 75.0, "short_threshold": 25.0})
    )
    tuner = WeightTuner({"technical": 1.0})
    entries = [_entry("LONG", -100.0, {"technical": 60}) for _ in range(10)]  # 0% wr
    tuner._run(entries)
    w = _read_weights(_isolate_files)
    assert w["long_threshold"] == 75.0   # clamped at ceiling
    assert w["short_threshold"] == 25.0  # clamped at floor


def test_single_direction_bias_defaults_to_prev(_isolate_files):
    # When all trades are in one direction, short_wr is None → bias cannot be
    # computed from win rates → must fall back to prev_bias (from existing file).
    (_isolate_files / "strategy_weights.json").write_text(
        json.dumps({"long_threshold": 60.0, "short_threshold": 40.0, "bias": "neutral"})
    )
    tuner = WeightTuner({"technical": 1.0})
    entries = [_entry("LONG", 100.0, {"technical": 60}) for _ in range(10)]  # only LONGs
    tuner._run(entries)
    w = _read_weights(_isolate_files)
    assert w["bias"] == "neutral"   # fallback to prev_bias since short_wr is None


# ── per-regime tuning ────────────────────────────────────────────────────────


def _entry_regime(decision, pnl, scores, regime):
    return {"decision": decision, "outcome_pnl": pnl, "agent_scores": scores, "regime": regime}


def test_regime_params_written_when_enough_trades(_isolate_files):
    """When a regime has >= _MIN_TRADES trades it gets its own tuned block."""
    tuner = WeightTuner({"technical": 1.0})
    entries = [_entry_regime("LONG", 100.0, {"technical": 60}, "risk_on") for _ in range(10)]
    tuner._run(entries)
    w = _read_weights(_isolate_files)
    assert "regime_params" in w
    assert "risk_on" in w["regime_params"]
    rp = w["regime_params"]["risk_on"]
    assert "long_threshold" in rp
    assert "win_rate_30d" in rp


def test_regime_params_absent_when_too_few_trades(_isolate_files):
    """A regime with fewer than _MIN_TRADES trades must NOT produce a regime block."""
    tuner = WeightTuner({"technical": 1.0})
    # 9 risk_on trades (< 10) + 1 neutral to satisfy global MIN_TRADES
    entries = [_entry_regime("LONG", 100.0, {"technical": 60}, "risk_on") for _ in range(9)]
    entries += [_entry_regime("LONG", 100.0, {"technical": 60}, "neutral") for _ in range(1)]
    tuner._run(entries)
    w = _read_weights(_isolate_files)
    # Neither regime has enough trades — regime_params must be empty or absent
    rp = w.get("regime_params", {})
    assert "risk_on" not in rp
    assert "neutral" not in rp


def test_regime_params_independent_thresholds(_isolate_files):
    """risk_on and risk_off each get their own thresholds tuned from their own trades."""
    tuner = WeightTuner({"technical": 1.0})
    # risk_on: 100% win rate → thresholds loosen
    risk_on = [_entry_regime("LONG", 100.0, {"technical": 60}, "risk_on") for _ in range(10)]
    # risk_off: 0% win rate → thresholds tighten
    risk_off = [_entry_regime("LONG", -100.0, {"technical": 60}, "risk_off") for _ in range(10)]
    tuner._run(risk_on + risk_off)
    w = _read_weights(_isolate_files)
    rp = w["regime_params"]
    assert rp["risk_on"]["long_threshold"] < rp["risk_off"]["long_threshold"]
    assert rp["risk_on"]["short_threshold"] > rp["risk_off"]["short_threshold"]


def test_regime_unknown_label_ignored(_isolate_files):
    """Entries with unknown regime labels must not pollute regime_params."""
    tuner = WeightTuner({"technical": 1.0})
    entries = [_entry_regime("LONG", 100.0, {"technical": 60}, "bogus_regime") for _ in range(10)]
    tuner._run(entries)
    w = _read_weights(_isolate_files)
    rp = w.get("regime_params", {})
    assert "bogus_regime" not in rp


def test_regime_params_prev_preserved_when_insufficient(_isolate_files):
    """Pre-existing regime_params for a regime not seen in this run are preserved."""
    (_isolate_files / "strategy_weights.json").write_text(json.dumps({
        "long_threshold": 60.0, "short_threshold": 40.0, "bias": "neutral",
        "regime_params": {
            "risk_on": {"long_threshold": 62.0, "short_threshold": 38.0},
        },
    }))
    tuner = WeightTuner({"technical": 1.0})
    # Only neutral trades — risk_on not updated
    entries = [_entry_regime("LONG", 100.0, {"technical": 60}, "neutral") for _ in range(10)]
    tuner._run(entries)
    w = _read_weights(_isolate_files)
    # risk_on block must be carried forward unchanged
    assert w["regime_params"]["risk_on"]["long_threshold"] == 62.0


# ── _append_history ───────────────────────────────────────────────────────────

def test_history_file_written_after_run(_isolate_files):
    """A successful _run must append one JSONL line to the history file."""
    tuner = WeightTuner({"technical": 1.0})
    entries = [_entry("LONG", 100.0, {"technical": 60}) for _ in range(10)]
    tuner._run(entries)
    history_file = _isolate_files / "learning_history.jsonl"
    assert history_file.exists()
    lines = [l for l in history_file.read_text().splitlines() if l.strip()]
    assert len(lines) >= 1


def test_history_snapshot_has_required_keys(_isolate_files):
    """Each history snapshot must carry the keys the learning dashboard needs."""
    tuner = WeightTuner({"technical": 0.5, "fundamental": 0.5})
    entries = [_entry("LONG", 100.0, {"technical": 60, "fundamental": 55}) for _ in range(10)]
    tuner._run(entries)
    history_file = _isolate_files / "learning_history.jsonl"
    snap = json.loads(history_file.read_text().splitlines()[-1])
    for key in ("ts", "win_rate", "bias", "long_threshold", "short_threshold",
                "sample_size", "weights", "multipliers"):
        assert key in snap, f"Missing key: {key}"


def test_history_appends_each_run(_isolate_files):
    """Calling _run N times must produce N lines in the history file."""
    tuner = WeightTuner({"technical": 1.0})
    for _ in range(3):
        entries = [_entry("LONG", 100.0, {"technical": 60}) for _ in range(10)]
        tuner._run(entries)
    history_file = _isolate_files / "learning_history.jsonl"
    lines = [l for l in history_file.read_text().splitlines() if l.strip()]
    assert len(lines) == 3


# ── rolling window (WINDOW=30) ────────────────────────────────────────────────

def test_rolling_window_only_uses_last_30(_isolate_files):
    """When more than _WINDOW trades exist the most recent win rate dominates."""
    from core.weight_tuner import _WINDOW
    tuner = WeightTuner({"technical": 1.0})

    # First 10 trades: 100% losers (these fall outside the 30-trade window)
    early = [_entry("LONG", -100.0, {"technical": 60}) for _ in range(10)]
    # Next 30 trades: 100% winners (fills the rolling window)
    recent = [_entry("LONG", 100.0, {"technical": 60}) for _ in range(_WINDOW)]
    tuner._run(early + recent)

    w = _read_weights(_isolate_files)
    # The window contains only winners → win_rate must be 100%
    assert w["win_rate_30d"] == pytest.approx(100.0)
