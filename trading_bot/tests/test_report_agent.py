"""EODReportAgent: bounded EOD note over the bot's recorded activity."""
import asyncio
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from agents.report_agent import EODReportAgent, _read_today_audit, _summarize_audit
from core.trade_memory import TradeMemory

_ET = ZoneInfo("America/New_York")


def _write_audit(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")


def _audit_records(ts: str):
    return [
        {"ts": ts, "type": "decision", "ticker": "NVDA", "decision": "LONG",
         "executed": True, "agents": [{"role": "risk", "veto": False}]},
        {"ts": ts, "type": "decision", "ticker": "TSLA", "decision": "PASS",
         "executed": False, "agents": [{"role": "risk", "veto": True}]},
        {"ts": ts, "type": "fill", "ticker": "NVDA", "slippage_bps": 4.0},
    ]


def test_read_today_audit_filters_by_et_day(tmp_path):
    now = datetime.now(timezone.utc)
    f = tmp_path / "decisions.jsonl"
    _write_audit(f, _audit_records(now.isoformat()) + [
        {"ts": "2020-01-01T12:00:00+00:00", "type": "decision", "decision": "LONG"},
    ])
    # The 2020 record must be excluded; today's three kept.
    today_et = now.astimezone(_ET).date()
    assert len(_read_today_audit(f, today_et)) == 3


def test_summarize_audit_counts():
    now = datetime.now(timezone.utc)
    summary = _summarize_audit(_audit_records(now.isoformat()))
    assert summary["evaluated"] == 2
    assert summary["by_direction"]["LONG"] == 1
    assert summary["by_direction"]["PASS"] == 1
    assert summary["executed"] == ["NVDA"]
    assert summary["vetoed"] == 1
    assert summary["fills"] == 1
    assert summary["avg_slippage_bps"] == 4.0


def test_generate_deterministic_without_llm(tmp_path):
    now = datetime.now(timezone.utc)
    f = tmp_path / "decisions.jsonl"
    _write_audit(f, _audit_records(now.isoformat()))
    agent = EODReportAgent(
        audit_file=f,
        trades_file=tmp_path / "trades.json",          # missing → empty history
        memory=TradeMemory(path=tmp_path / "mem.json"),
    )
    assert agent._llm.has_llm is False                  # no keys → deterministic path
    report = asyncio.run(agent.generate(now=now))
    assert "evaluated 2" in report
    assert "1 executed" in report
    assert "1 risk vetoes" in report
    assert "NVDA" in report


def test_generate_handles_empty_day(tmp_path):
    agent = EODReportAgent(
        audit_file=tmp_path / "nope.jsonl",            # no file at all
        trades_file=tmp_path / "trades.json",
        memory=TradeMemory(path=tmp_path / "mem.json"),
    )
    report = asyncio.run(agent.generate(now=datetime.now(timezone.utc)))
    assert "evaluated 0" in report
