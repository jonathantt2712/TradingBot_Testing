"""Operator health board: dedup, alert queue, formatting."""
import asyncio

import pytest

from core import health
from core.base_agent import BaseAgent
from core.enums import AgentRole
from core.models import AnalysisContext


@pytest.fixture(autouse=True)
def _clean():
    health.reset()
    yield
    health.reset()


def test_report_dedups_and_counts():
    for _ in range(500):
        health.report_issue("k", "boom", remediation="fix it")
    issues = health.active_issues()
    assert len(issues) == 1
    assert issues[0].count == 500
    assert issues[0].message == "boom"


def test_take_unsent_returns_once():
    health.report_issue("a", "AAA")
    health.report_issue("b", "BBB", severity="warning")
    first = health.take_unsent()
    assert {i.key for i in first} == {"a", "b"}
    assert health.take_unsent() == []          # already drained
    health.report_issue("a", "AAA")            # same message → not re-queued
    assert health.take_unsent() == []


def test_changed_message_requeues():
    health.report_issue("a", "first")
    health.take_unsent()
    health.report_issue("a", "second")         # message changed → re-alert
    new = health.take_unsent()
    assert len(new) == 1 and new[0].message == "second"


def test_resolve_removes_issue():
    health.report_issue("a", "AAA")
    health.resolve("a")
    assert health.active_issues() == []
    assert health.format_block() == ""


def test_format_block_lists_issues():
    health.report_issue("a", "Alpaca keys missing", remediation="set them")
    block = health.format_block()
    assert "NEEDS ATTENTION" in block
    assert "Alpaca keys missing" in block
    assert "set them" in block


# ── agent degradation surfaces on the board ──────────────────────────────────

class _BoomAgent(BaseAgent):
    role = AgentRole.TECHNICAL

    async def evaluate(self, ctx):
        raise RuntimeError("data source down")


def test_failing_agent_reports_and_returns_neutral():
    ev = asyncio.run(_BoomAgent().safe_evaluate(AnalysisContext(ticker="X")))
    assert ev.score == 50.0                       # neutral fallback (the flat value)
    issues = {i.key: i for i in health.active_issues()}
    assert "agent:technical" in issues
    assert "data source down" in issues["agent:technical"].message


def test_risk_no_equity_reports_issue(flat_bars):
    from agents.risk_agent import RiskAgent
    from config.settings import RiskConfig
    from core.enums import Decision

    plan = RiskAgent(RiskConfig()).build_plan(
        AnalysisContext(ticker="X", bars=flat_bars, account={"equity": 0.0}),
        intended=Decision.LONG,
    )
    assert plan is None                            # fail closed
    assert any(i.key == "risk:no_equity" for i in health.active_issues())
