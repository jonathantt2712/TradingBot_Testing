"""Operator health board: dedup, alert queue, formatting."""
import pytest

from core import health


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
