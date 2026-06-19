"""TradeMemory: decision/outcome recording and the reflection lessons block."""
from core.trade_memory import TradeMemory


def _mem(tmp_path):
    return TradeMemory(path=tmp_path / "decision_memory.json")


def test_records_only_directional_decisions(tmp_path):
    m = _mem(tmp_path)
    m.record_decision("NVDA", "LONG", 72.0)
    m.record_decision("AAPL", "PASS", 50.0)   # PASS is not remembered
    entries = m._load()
    assert len(entries) == 1
    assert entries[0]["ticker"] == "NVDA"
    assert entries[0]["outcome_pnl"] is None


def test_record_outcome_resolves_latest_open_decision(tmp_path):
    m = _mem(tmp_path)
    m.record_decision("NVDA", "LONG", 70.0)
    m.record_decision("NVDA", "LONG", 80.0)   # later, still open
    m.record_outcome("NVDA", 143.0)
    entries = m._load()
    # The most recent open decision gets the outcome; the older stays unresolved.
    assert entries[1]["outcome_pnl"] == 143.0
    assert entries[0]["outcome_pnl"] is None


def test_record_outcome_no_match_is_noop(tmp_path):
    m = _mem(tmp_path)
    m.record_outcome("TSLA", -50.0)           # nothing to attach to
    assert m._load() == []


def test_recent_lessons_empty_until_resolved(tmp_path):
    m = _mem(tmp_path)
    assert m.recent_lessons() == ""           # no history
    m.record_decision("NVDA", "LONG", 70.0)
    assert m.recent_lessons() == ""           # unresolved → still nothing


def test_recent_lessons_formats_wins_and_losses(tmp_path):
    m = _mem(tmp_path)
    m.record_decision("NVDA", "LONG", 72.0)
    m.record_outcome("NVDA", 143.0)
    m.record_decision("TSLA", "SHORT", 31.0)
    m.record_outcome("TSLA", -88.0)
    block = m.recent_lessons()
    assert "RECENT OUTCOMES" in block
    assert "NVDA LONG" in block and "WON" in block and "+143" in block
    assert "TSLA SHORT" in block and "LOST" in block and "-88" in block
    assert "1 won / 1 lost" in block


def test_memory_is_bounded(tmp_path):
    m = TradeMemory(path=tmp_path / "m.json", max_entries=5)
    for i in range(20):
        m.record_decision(f"T{i}", "LONG", 60.0)
    assert len(m._load()) == 5


def test_corrupt_file_is_tolerated(tmp_path):
    p = tmp_path / "m.json"
    p.write_text("{ not json", encoding="utf-8")
    m = TradeMemory(path=p)
    assert m._load() == []                     # swallowed, not raised
    m.record_decision("NVDA", "LONG", 70.0)    # still works (overwrites)
    assert len(m._load()) == 1
