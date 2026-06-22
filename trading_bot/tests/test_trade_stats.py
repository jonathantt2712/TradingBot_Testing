"""trade_stats — the summary behind the dashboard History tab and the
optimizer's "learn from what actually happened" inputs. Pure functions, so
tested directly."""
from core.trade_stats import format_block, load_closed_trades, summarize


def _t(pnl, direction="LONG", ticker="NVDA", status="closed"):
    return {"pnl": pnl, "direction": direction, "ticker": ticker, "status": status}


# ── loading ──────────────────────────────────────────────────────────────────

def test_load_filters_open_and_unresolved(tmp_path):
    import json
    f = tmp_path / "trades.json"
    f.write_text(json.dumps([
        _t(100), _t(-50),
        {"status": "open", "pnl": None, "direction": "LONG", "ticker": "X"},
        {"status": "closed", "pnl": None, "direction": "LONG", "ticker": "Y"},
    ]))
    closed = load_closed_trades(f)
    assert len(closed) == 2


def test_load_missing_file_is_empty(tmp_path):
    assert load_closed_trades(tmp_path / "nope.json") == []


# ── summarize ────────────────────────────────────────────────────────────────

def test_empty_history():
    assert summarize([]) == {"closed": 0}


def test_basic_metrics():
    s = summarize([_t(100), _t(-50), _t(100), _t(-50)])
    assert s["closed"] == 4
    assert s["win_rate"] == 50.0
    assert s["total_pnl"] == 100.0
    assert s["avg_pnl"] == 25.0


def test_direction_bias_requires_20pt_gap():
    # LONG 100% vs SHORT 50% → 50pt gap → long bias
    trades = [_t(10, "LONG"), _t(10, "LONG"), _t(10, "SHORT"), _t(-10, "SHORT")]
    s = summarize(trades)
    assert s["long_win_rate"] == 100.0 and s["short_win_rate"] == 50.0
    assert s["bias"] == "long"


def test_direction_bias_neutral_when_close():
    # 50% vs 50% → within 20pt → neutral
    trades = [_t(10, "LONG"), _t(-10, "LONG"), _t(10, "SHORT"), _t(-10, "SHORT")]
    assert summarize(trades)["bias"] == "neutral"


def test_recent_loss_streak_counts_trailing_losses():
    # chronological; last three are losses
    trades = [_t(100), _t(50), _t(-10), _t(-20), _t(-30)]
    assert summarize(trades)["recent_loss_streak"] == 3


def test_loss_streak_resets_on_recent_win():
    trades = [_t(-10), _t(-20), _t(100)]   # most recent is a win
    assert summarize(trades)["recent_loss_streak"] == 0


def test_by_ticker_breakdown():
    trades = [_t(100, ticker="AAA"), _t(-50, ticker="AAA"), _t(200, ticker="BBB")]
    s = summarize(trades)
    assert s["by_ticker"]["AAA"]["trades"] == 2
    assert s["by_ticker"]["AAA"]["pnl"] == 50.0
    assert s["by_ticker"]["AAA"]["win_rate"] == 50.0
    assert s["by_ticker"]["BBB"]["win_rate"] == 100.0


# ── rendering ────────────────────────────────────────────────────────────────

def test_format_block_no_trades():
    assert "no closed trades" in format_block({"closed": 0})


def test_format_block_surfaces_streak_and_extremes():
    trades = [_t(300, ticker="WIN"), _t(-10, ticker="LOSE"),
              _t(-20, ticker="LOSE"), _t(-30, ticker="LOSE")]
    block = format_block(summarize(trades))
    assert "WIN" in block and "LOSE" in block
    assert "consecutive losing exits" in block
