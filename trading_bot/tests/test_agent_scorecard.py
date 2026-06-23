"""Per-agent scorecard — directional hit rate over the tuner's window.

Mirrors WeightTuner's correctness rule (score >= 50 = leaned LONG, <= 50 =
leaned SHORT; correct when that lean matches the winning outcome) so the numbers
the dashboard shows line up with the multipliers actually applied.
"""
import json

from core.agent_scorecard import compute_agent_scorecards


def _trade(direction, pnl, **agent_scores):
    return {
        "status": "closed",
        "direction": direction,
        "pnl": pnl,
        "evaluations": [{"role": r, "score": s} for r, s in agent_scores.items()],
    }


def test_empty_history_yields_no_cards():
    assert compute_agent_scorecards([]) == []


def test_hit_rate_counts_directional_correctness():
    # tech leans LONG (70) on both; one LONG wins, one LONG loses → 1/2 correct.
    trades = [
        _trade("LONG", 100, tech=70),
        _trade("LONG", -50, tech=70),
    ]
    card = {c["agent"]: c for c in compute_agent_scorecards(trades)}["tech"]
    assert card["samples"] == 2
    assert card["hit_rate"] == 50.0


def test_short_lean_scored_correctly():
    # score 30 = leaned SHORT. SHORT trade that won → agent was correct.
    trades = [_trade("SHORT", 80, macro=30)]
    card = {c["agent"]: c for c in compute_agent_scorecards(trades)}["macro"]
    assert card["hit_rate"] == 100.0


def test_perfect_and_zero_agents_separated():
    trades = [
        _trade("LONG", 100, good=80, bad=20),   # win: good(LONG) right, bad(SHORT) wrong
        _trade("LONG", 100, good=80, bad=20),
    ]
    cards = {c["agent"]: c for c in compute_agent_scorecards(trades)}
    assert cards["good"]["hit_rate"] == 100.0
    assert cards["bad"]["hit_rate"] == 0.0


def test_avg_pnl_when_agreed_only_counts_agreeing_trades():
    trades = [
        _trade("LONG", 100, tech=70),   # agreed (LONG), pnl 100
        _trade("LONG", -40, tech=30),   # disagreed (leaned SHORT) — excluded from avg
    ]
    card = {c["agent"]: c for c in compute_agent_scorecards(trades)}["tech"]
    assert card["avg_pnl_when_agreed"] == 100.0


def test_window_limits_to_last_n():
    trades = [_trade("LONG", 100, tech=70) for _ in range(5)]
    cards = compute_agent_scorecards(trades, window=3)
    assert cards[0]["samples"] == 3


def test_live_weight_and_multiplier_surfaced():
    trades = [_trade("LONG", 100, tech=70)]
    weights = {"agent_weights": {"tech": 0.42}, "agent_multipliers": {"tech": 1.5}}
    card = {c["agent"]: c for c in compute_agent_scorecards(trades, weights)}["tech"]
    assert card["weight"] == 0.42 and card["multiplier"] == 1.5


def test_trades_without_evaluations_ignored():
    trades = [{"status": "closed", "direction": "LONG", "pnl": 100, "evaluations": []}]
    assert compute_agent_scorecards(trades) == []


def test_refresh_helper_persists_file(tmp_path, monkeypatch):
    import api_server

    trades_f = tmp_path / "trades.json"
    cards_f = tmp_path / "agent_scorecards.json"
    weights_f = tmp_path / "strategy_weights.json"
    trades_f.write_text(json.dumps([_trade("LONG", 100, tech=70)]))
    weights_f.write_text(json.dumps({"agent_weights": {"tech": 0.5}, "agent_multipliers": {"tech": 1.2}}))
    monkeypatch.setattr(api_server, "TRADES_FILE", trades_f)
    monkeypatch.setattr(api_server, "AGENT_SCORECARDS_FILE", cards_f)
    monkeypatch.setattr(api_server, "LEARNING_WEIGHTS_FILE", weights_f)

    cards = api_server._refresh_agent_scorecards()
    assert cards[0]["agent"] == "tech"
    written = json.loads(cards_f.read_text())
    assert written["sample_trades"] == 1
    assert written["agents"][0]["weight"] == 0.5
