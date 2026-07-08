"""core.slippage — measured execution costs feeding the backtests."""
import json

import pytest

from core import slippage as sl


@pytest.fixture
def trades_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "data_dir", lambda: tmp_path)
    return tmp_path / "trades.json"


def _trades(bps_values):
    return [{"status": "closed", "entry_slippage_bps": b} for b in bps_values]


def test_default_when_no_file(trades_file):
    assert sl.measured_slippage_pct(0.0005) == 0.0005


def test_default_below_min_samples(trades_file):
    trades_file.write_text(json.dumps(_trades([5.0] * 9)))   # 9 < 10 samples
    assert sl.measured_slippage_pct(0.0005) == 0.0005


def test_median_of_measured_fills(trades_file):
    # 12 fills, median |slippage| = 8 bps -> 0.0008 per side
    trades_file.write_text(json.dumps(_trades([8.0] * 6 + [-8.0] * 6)))
    assert sl.measured_slippage_pct(0.0005) == pytest.approx(0.0008)


def test_outlier_day_capped_by_sanity_band(trades_file):
    # A broken recorder / halted-stock fills must not poison simulations.
    trades_file.write_text(json.dumps(_trades([900.0] * 20)))
    assert sl.measured_slippage_pct(0.0005) == 0.005          # ceiling

    trades_file.write_text(json.dumps(_trades([0.0] * 20)))
    assert sl.measured_slippage_pct(0.0005) == 0.0001         # floor


def test_trades_without_measurement_are_skipped(trades_file):
    mixed = _trades([10.0] * 12) + [{"status": "closed", "pnl": 5.0}] * 30
    trades_file.write_text(json.dumps(mixed))
    assert sl.measured_slippage_pct(0.0005) == pytest.approx(0.001)


def test_summary_reports_recent_window(trades_file):
    trades_file.write_text(json.dumps(_trades([4.0, 6.0, 20.0])))
    out = sl.slippage_summary()
    assert out == {"samples": 3, "median_bps": 6.0, "worst_bps": 20.0}
