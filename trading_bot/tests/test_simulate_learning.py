"""Unit tests for simulate_learning — trade generator, score helper, and _tag_simulated."""
from __future__ import annotations

import json
import random

import pytest

from simulate_learning import _build_trades, _score_for, _AGENT_SKILL, _tag_simulated
import simulate_learning as _sl_mod
from core import weight_tuner


# ── _score_for ─────────────────────────────────────────────────────────────────

class TestScoreFor:
    def _many(self, agent: str, won: bool, direction: str, n: int = 200) -> list[float]:
        rng = random.Random(42)
        return [_score_for(agent, won, direction, rng) for _ in range(n)]

    def test_score_in_range(self):
        rng = random.Random(0)
        for agent in _AGENT_SKILL:
            for direction in ("LONG", "SHORT"):
                for won in (True, False):
                    s = _score_for(agent, won, direction, rng)
                    assert 0.0 < s < 100.0, f"{agent}/{direction}/won={won}: {s}"

    def test_high_skill_agent_leans_bullish_on_winning_long(self):
        """High-skill agent (technical, 64%) should give mostly bullish (>50) scores
        when the LONG trade won."""
        scores = self._many("technical", won=True, direction="LONG")
        above = sum(s > 50 for s in scores)
        assert above / len(scores) > 0.55   # majority bullish

    def test_high_skill_agent_leans_bearish_on_losing_long(self):
        """High-skill agent should give mostly bearish (<50) scores when LONG lost."""
        scores = self._many("technical", won=False, direction="LONG")
        below = sum(s < 50 for s in scores)
        assert below / len(scores) > 0.55

    def test_low_skill_agent_near_random(self):
        """Low-skill agent (insider, 44%) should be close to 50/50."""
        scores = self._many("insider", won=True, direction="LONG", n=500)
        above = sum(s > 50 for s in scores)
        ratio = above / len(scores)
        assert 0.30 < ratio < 0.70   # coin-flip territory

    def test_bullish_vote_for_winning_short(self):
        """For a winning SHORT, a high-skill agent should give a bearish (<50) score
        (bearish vote aligns with the short direction)."""
        scores = self._many("technical", won=True, direction="SHORT")
        below = sum(s < 50 for s in scores)
        assert below / len(scores) > 0.55


# ── _build_trades ──────────────────────────────────────────────────────────────

class TestBuildTrades:
    def setup_method(self):
        self.rng = random.Random(7)
        self.trades = _build_trades(50, self.rng)

    def test_count_matches_request(self):
        assert len(self.trades) == 50

    def test_all_closed(self):
        assert all(t["status"] == "closed" for t in self.trades)

    def test_direction_is_long_or_short(self):
        assert all(t["direction"] in ("LONG", "SHORT") for t in self.trades)

    def test_pnl_is_numeric(self):
        for t in self.trades:
            assert isinstance(t["pnl"], (int, float))

    def test_evaluations_have_all_agents(self):
        expected = set(_AGENT_SKILL.keys())
        for t in self.trades:
            roles = {e["role"] for e in t["evaluations"]}
            assert roles == expected

    def test_evaluation_scores_in_range(self):
        for t in self.trades:
            for ev in t["evaluations"]:
                assert 0.0 < ev["score"] < 100.0, f"score out of range: {ev}"

    def test_closed_at_is_present(self):
        assert all("closed_at" in t for t in self.trades)

    def test_long_bias_roughly_68_pct(self):
        longs = sum(1 for t in self.trades if t["direction"] == "LONG")
        # P(LONG) = 0.68; allow wide range for small N
        assert 0.40 < longs / len(self.trades) < 0.90

    def test_timestamps_monotonically_increasing(self):
        times = [t["closed_at"] for t in self.trades]
        assert times == sorted(times)

    def test_deterministic_with_same_seed(self):
        trades_a = _build_trades(20, random.Random(42))
        trades_b = _build_trades(20, random.Random(42))
        assert [t["pnl"] for t in trades_a] == [t["pnl"] for t in trades_b]

    def test_different_seeds_differ(self):
        trades_a = _build_trades(20, random.Random(1))
        trades_b = _build_trades(20, random.Random(2))
        assert [t["pnl"] for t in trades_a] != [t["pnl"] for t in trades_b]

    def test_win_rate_drifts_upward(self):
        """Early trades should have lower win rate than late trades (drift encoded)."""
        n = 200
        trades = _build_trades(n, random.Random(99))
        early_wins  = sum(1 for t in trades[:50] if t["pnl"] > 0)
        late_wins   = sum(1 for t in trades[-50:] if t["pnl"] > 0)
        # Drift of +0.12 across the run means late should usually beat early
        assert late_wins >= early_wins - 5   # generous tolerance for randomness


# ── _tag_simulated ─────────────────────────────────────────────────────────────

class TestTagSimulated:
    @pytest.fixture(autouse=True)
    def _patch_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_sl_mod, "_HISTORY_FILE", tmp_path / "h.jsonl")
        monkeypatch.setattr(_sl_mod, "_WEIGHTS_FILE", tmp_path / "w.json")
        self.tmp = tmp_path

    def test_history_lines_get_simulated_flag(self):
        snaps = [{"ts": f"2026-06-{i:02d}", "win_rate": 50.0} for i in range(1, 4)]
        (self.tmp / "h.jsonl").write_text(
            "\n".join(json.dumps(s) for s in snaps) + "\n", encoding="utf-8"
        )
        _tag_simulated()
        lines = [
            json.loads(l) for l in (self.tmp / "h.jsonl").read_text().splitlines() if l.strip()
        ]
        assert all(l.get("simulated") is True for l in lines)

    def test_weights_file_gets_simulated_flag(self):
        (self.tmp / "w.json").write_text(json.dumps({"win_rate_30d": 55.0}), encoding="utf-8")
        _tag_simulated()
        w = json.loads((self.tmp / "w.json").read_text())
        assert w.get("simulated") is True

    def test_original_data_preserved_after_tag(self):
        snap = {"ts": "2026-06-01", "win_rate": 63.0}
        (self.tmp / "h.jsonl").write_text(json.dumps(snap) + "\n", encoding="utf-8")
        _tag_simulated()
        result = json.loads((self.tmp / "h.jsonl").read_text().strip())
        assert result["win_rate"] == 63.0
        assert result["ts"] == "2026-06-01"

    def test_malformed_history_lines_are_dropped(self):
        content = '{"ts":"2026-06-01","win_rate":50.0}\nNOT JSON\n{"ts":"2026-06-02","win_rate":52.0}\n'
        (self.tmp / "h.jsonl").write_text(content, encoding="utf-8")
        _tag_simulated()
        lines = [l for l in (self.tmp / "h.jsonl").read_text().splitlines() if l.strip()]
        assert len(lines) == 2   # malformed line is silently dropped

    def test_missing_files_are_noop(self):
        _tag_simulated()   # no files exist — must not raise


# ── run_simulation ─────────────────────────────────────────────────────────────

class TestRunSimulation:
    @pytest.fixture(autouse=True)
    def _patch_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_sl_mod, "_HISTORY_FILE", tmp_path / "h.jsonl")
        monkeypatch.setattr(_sl_mod, "_WEIGHTS_FILE", tmp_path / "w.json")
        monkeypatch.setattr(weight_tuner, "_HISTORY_FILE", tmp_path / "h.jsonl")
        monkeypatch.setattr(weight_tuner, "_WEIGHTS_FILE", tmp_path / "w.json")
        self.tmp = tmp_path

    def test_returns_dict_with_expected_keys(self):
        result = _sl_mod.run_simulation(n_trades=15, seed=42)
        assert "trades" in result
        assert "steps" in result
        assert "simulated" in result

    def test_simulated_flag_is_true(self):
        result = _sl_mod.run_simulation(n_trades=15, seed=42)
        assert result["simulated"] is True

    def test_trade_count_matches_request(self):
        result = _sl_mod.run_simulation(n_trades=15, seed=42)
        assert result["trades"] == 15

    def test_steps_nonzero_with_enough_trades(self):
        """n_trades > _MIN_TRADES must produce at least one history snapshot."""
        result = _sl_mod.run_simulation(n_trades=weight_tuner._MIN_TRADES + 5, seed=42)
        assert result["steps"] >= 1

    def test_fewer_than_min_trades_produces_no_steps(self):
        """n_trades < _MIN_TRADES means the tuner never fires → 0 steps."""
        result = _sl_mod.run_simulation(n_trades=weight_tuner._MIN_TRADES - 1, seed=42)
        assert result["steps"] == 0

    def test_history_lines_tagged_simulated(self):
        """Every snapshot written by run_simulation must carry simulated=true."""
        _sl_mod.run_simulation(n_trades=15, seed=42)
        h = self.tmp / "h.jsonl"
        if h.exists():
            for line in h.read_text().splitlines():
                if line.strip():
                    snap = json.loads(line)
                    assert snap.get("simulated") is True
