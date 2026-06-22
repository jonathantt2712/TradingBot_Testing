"""Strategy scorecard: edge metrics and an honest, sample-size-aware confidence."""
import json
import math

from core.scorecard import build_scorecard, format_scorecard, one_line


def _write_trades(tmp_path, pnls, directions=None):
    directions = directions or ["LONG"] * len(pnls)
    rows = [
        {"status": "closed", "pnl": p, "direction": d, "ticker": "NVDA"}
        for p, d in zip(pnls, directions)
    ]
    f = tmp_path / "trades.json"
    f.write_text(json.dumps(rows), encoding="utf-8")
    return f


def _card(tmp_path, pnls, **kw):
    return build_scorecard(trades_path=_write_trades(tmp_path, pnls, **kw),
                           audit_path=tmp_path / "nope.jsonl")


# ── core metrics ──────────────────────────────────────────────────────────────

def test_basic_metrics(tmp_path):
    sc = _card(tmp_path, [100, -50, 100, -50])   # 2 wins, 2 losses
    assert sc.trades == 4 and sc.wins == 2 and sc.losses == 2
    assert sc.win_rate == 50.0
    assert sc.total_pnl == 100.0
    assert sc.expectancy == 25.0
    assert sc.avg_win == 100.0 and sc.avg_loss == -50.0
    assert sc.payoff_ratio == 2.0
    assert sc.profit_factor == 2.0                # 200 / 100


def test_profit_factor_infinite_when_no_losses(tmp_path):
    sc = _card(tmp_path, [10, 20, 30])
    assert math.isinf(sc.profit_factor)
    assert sc.as_dict()["profit_factor"] is None   # JSON-safe
    assert "∞" in format_scorecard(sc)


def test_max_drawdown(tmp_path):
    # cum: 100, 50, 150, 50 -> worst dip is 150 -> 50 = -100
    sc = _card(tmp_path, [100, -50, 100, -100])
    assert sc.max_drawdown == -100.0


def test_direction_breakdown(tmp_path):
    sc = _card(tmp_path, [100, -50, 100, 100],
               directions=["LONG", "LONG", "SHORT", "SHORT"])
    assert sc.long_trades == 2 and sc.short_trades == 2
    assert sc.long_win_rate == 50.0 and sc.short_win_rate == 100.0


# ── the honest confidence flag ────────────────────────────────────────────────

def test_no_trades_is_insufficient(tmp_path):
    sc = build_scorecard(trades_path=tmp_path / "missing.json",
                         audit_path=tmp_path / "missing.jsonl")
    assert sc.trades == 0
    assert sc.confidence == "insufficient"


def test_losing_strategy_has_no_edge(tmp_path):
    sc = _card(tmp_path, [-10] * 40 + [5] * 10)   # clearly negative expectancy
    assert sc.expectancy < 0
    assert sc.confidence == "none"
    assert "No edge" in sc.verdict


def test_small_winning_sample_is_insufficient(tmp_path):
    # Great-looking but tiny — must NOT be called an edge.
    sc = _card(tmp_path, [100, 120, 90, 110, 100])
    assert sc.expectancy > 0
    assert sc.confidence == "insufficient"
    assert "too" in sc.verdict.lower()


def test_weak_edge_is_low_confidence(tmp_path):
    # 40 trades, positive but noisy (big variance) -> t-stat < 2 -> "low".
    pnls = [300, -250] * 18 + [60, 40, 30, 20]
    sc = _card(tmp_path, pnls)
    assert sc.trades == 40 and sc.expectancy > 0
    assert sc.t_stat < 2.0
    assert sc.confidence == "low"


def test_strong_large_sample_is_high_confidence(tmp_path):
    # 120 trades, consistent positive expectancy, tight variance -> high.
    pnls = ([50] * 70 + [-30] * 50)   # win rate 58%, strong positive EV
    sc = _card(tmp_path, pnls)
    assert sc.trades == 120
    assert sc.t_stat >= 2.0
    assert sc.confidence == "high"


# ── slippage + rendering ──────────────────────────────────────────────────────

def test_slippage_pulled_from_audit(tmp_path):
    trades = _write_trades(tmp_path, [10, -5])
    audit = tmp_path / "decisions.jsonl"
    audit.write_text("\n".join(json.dumps(r) for r in [
        {"type": "fill", "slippage_bps": 4.0},
        {"type": "fill", "slippage_bps": 2.0},
        {"type": "decision", "decision": "LONG"},   # ignored
    ]), encoding="utf-8")
    sc = build_scorecard(trades_path=trades, audit_path=audit)
    assert sc.fills == 2 and sc.avg_slippage_bps == 3.0


def test_one_line_and_format_are_safe(tmp_path):
    sc = _card(tmp_path, [10, -5, 20])
    assert "Scorecard:" in one_line(sc)
    assert "STRATEGY SCORECARD" in format_scorecard(sc)
