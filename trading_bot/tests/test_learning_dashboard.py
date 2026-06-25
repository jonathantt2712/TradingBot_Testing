"""Unit tests for learning_dashboard — load_history and build_learning_dashboard."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from learning_dashboard import build_learning_dashboard, load_history


# ── load_history ───────────────────────────────────────────────────────────────

class TestLoadHistory:
    def test_missing_file_returns_empty(self, tmp_path):
        result = load_history(tmp_path / "nonexistent.jsonl")
        assert result == []

    def test_empty_file_returns_empty(self, tmp_path):
        p = tmp_path / "h.jsonl"
        p.write_text("", encoding="utf-8")
        assert load_history(p) == []

    def test_valid_jsonl_returns_all_snapshots(self, tmp_path):
        p = tmp_path / "h.jsonl"
        snaps = [{"ts": f"2026-06-{i:02d}T10:00:00", "win_rate": 55.0} for i in range(1, 4)]
        p.write_text("\n".join(json.dumps(s) for s in snaps) + "\n", encoding="utf-8")
        result = load_history(p)
        assert len(result) == 3
        assert result[0]["ts"].startswith("2026-06-01")

    def test_blank_lines_skipped(self, tmp_path):
        p = tmp_path / "h.jsonl"
        p.write_text('{"ts":"2026-06-01T00:00:00","win_rate":50.0}\n\n\n', encoding="utf-8")
        assert len(load_history(p)) == 1

    def test_malformed_lines_skipped(self, tmp_path):
        p = tmp_path / "h.jsonl"
        p.write_text(
            '{"ts":"2026-06-01T00:00:00","win_rate":50.0}\n'
            'NOT JSON AT ALL\n'
            '{"ts":"2026-06-02T00:00:00","win_rate":52.0}\n',
            encoding="utf-8",
        )
        result = load_history(p)
        assert len(result) == 2  # malformed line skipped

    def test_preserves_order(self, tmp_path):
        p = tmp_path / "h.jsonl"
        rates = [40.0, 55.0, 65.0]
        p.write_text("\n".join(json.dumps({"ts": "2026-06-01", "win_rate": r}) for r in rates), encoding="utf-8")
        result = load_history(p)
        assert [s["win_rate"] for s in result] == rates


# ── build_learning_dashboard ───────────────────────────────────────────────────

def _snap(
    ts: str = "2026-06-16T10:00:00",
    win_rate: float = 55.0,
    long_thr: float = 60.0,
    short_thr: float = 40.0,
    bias: str = "neutral",
    sample_size: int = 20,
) -> dict:
    return {
        "ts": ts,
        "win_rate": win_rate,
        "long_win_rate": win_rate + 2,
        "short_win_rate": win_rate - 2,
        "long_threshold": long_thr,
        "short_threshold": short_thr,
        "bias": bias,
        "sample_size": sample_size,
        "weights": {"technical": 0.4, "fundamental": 0.3, "macro": 0.3},
        "multipliers": {"technical": 1.5, "fundamental": 1.0, "macro": 0.8},
    }


class TestBuildLearningDashboard:
    def test_returns_string(self):
        assert isinstance(build_learning_dashboard([]), str)

    def test_valid_html_doctype(self):
        html = build_learning_dashboard([])
        assert html.strip().startswith("<!DOCTYPE html>")

    def test_empty_history_shows_empty_note(self):
        html = build_learning_dashboard([])
        assert "No learning history yet" in html

    def test_non_empty_history_no_empty_note(self):
        html = build_learning_dashboard([_snap()])
        assert "No learning history yet" not in html

    def test_win_rate_appears_in_summary(self):
        html = build_learning_dashboard([_snap(win_rate=63.5)])
        assert "63.5%" in html

    def test_bias_appears_in_summary(self):
        html = build_learning_dashboard([_snap(bias="long")])
        assert "LONG" in html   # rendered uppercase

    def test_thresholds_in_summary(self):
        html = build_learning_dashboard([_snap(long_thr=62.0, short_thr=38.0)])
        assert "62.0" in html
        assert "38.0" in html

    def test_agent_weights_embedded_as_json(self):
        snap = _snap()
        html = build_learning_dashboard([snap])
        # weight series for "technical" should appear in the chart datasets
        assert "technical" in html

    def test_multipliers_embedded(self):
        snap = _snap()
        html = build_learning_dashboard([snap])
        assert "1.5" in html   # technical multiplier

    def test_multiple_snapshots_produces_multiple_labels(self):
        snaps = [_snap(ts=f"2026-06-{i:02d}T10:00:00") for i in range(1, 4)]
        html = build_learning_dashboard(snaps)
        assert "2026-06-01" in html
        assert "2026-06-03" in html

    def test_contains_chart_js_script(self):
        html = build_learning_dashboard([])
        assert "chart.umd.min.js" in html or "Chart" in html

    def test_tuning_steps_count_in_summary(self):
        snaps = [_snap() for _ in range(5)]
        html = build_learning_dashboard(snaps)
        assert "5" in html   # "5 Tuning Steps Logged"

    def test_win_rate_color_green_above_50(self):
        html = build_learning_dashboard([_snap(win_rate=60.0)])
        assert 'class="stat-value green"' in html

    def test_win_rate_color_red_below_50(self):
        html = build_learning_dashboard([_snap(win_rate=40.0)])
        assert 'class="stat-value red"' in html
