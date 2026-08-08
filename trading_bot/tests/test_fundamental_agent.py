"""FundamentalAgent — news scoring without an LLM.

Order of preference is FinBERT, then the keyword lists. The real model is never
built here (conftest sets FINBERT_DISABLED — a ~440 MB download), so these tests
pin the keyword path by default and stub the pipeline where the FinBERT branch
itself is under test.
"""
from datetime import datetime, timedelta, timezone

import asyncio

import pytest

import agents.fundamental_agent as fa
from agents.fundamental_agent import FundamentalAgent
from core.enums import AgentRole
from core.models import AnalysisContext

# Captured before the autouse fixture stubs it out, for the loader's own test.
_REAL_LOAD_FINBERT = fa._load_finbert


class _FakeNews:
    def __init__(self, articles):
        self._articles = articles

    async def get_news(self, ticker, limit=15):
        return list(self._articles)


def _agent(articles):
    return FundamentalAgent(_FakeNews(articles))


def _ctx():
    return AnalysisContext(ticker="NVDA", bars=None, account={"equity": 1.0})


def _run(agent):
    return asyncio.run(agent.evaluate(_ctx()))


def _article(headline, summary="", **extra):
    return {"headline": headline, "summary": summary, **extra}


@pytest.fixture(autouse=True)
def _no_finbert(monkeypatch):
    """Default every test to the keyword path; FinBERT tests opt in explicitly."""
    monkeypatch.setattr(fa, "_load_finbert", lambda: None)


def _stub_finbert(monkeypatch, labels):
    """Install a fake pipeline returning ``labels`` (list of (label, score))."""
    def _pipe(headlines):
        return [{"label": lbl, "score": sc} for lbl, sc in labels]
    monkeypatch.setattr(fa, "_load_finbert", lambda: _pipe)


# ── no-news path ─────────────────────────────────────────────────────────────

# ── llm_enabled=False must skip the LLM branch even with a key present ──────

def test_llm_enabled_false_never_calls_the_llm():
    agent = FundamentalAgent(
        _FakeNews([_article("Company beats earnings, analyst upgrade and record growth")]),
        anthropic_api_key="fake-key-present",  # has_llm would be True on its own
        llm_enabled=False,
    )
    assert agent._llm.has_llm is True  # sanity: the gate, not has_llm, must decide

    async def _boom(*a, **k):
        raise AssertionError("LLM must not be called when llm_enabled=False")
    agent._llm.chat = _boom

    ev = _run(agent)
    assert "[keyword]" in ev.rationale


def test_no_articles_is_neutral():
    ev = _run(_agent([]))
    assert ev.role is AgentRole.FUNDAMENTAL
    assert ev.score == 50.0
    assert "no news" in ev.rationale


# ── keyword fallback scoring ─────────────────────────────────────────────────

def test_bullish_keywords_score_above_neutral():
    ev = _run(_agent([_article("Company beats earnings, analyst upgrade and record growth")]))
    assert ev.score > 50.0
    assert "[keyword]" in ev.rationale
    assert ev.reasoning["bull_signals"] > ev.reasoning["bear_signals"]


def test_bearish_keywords_score_below_neutral():
    ev = _run(_agent([_article("Company missed estimates, downgrade and fraud probe")]))
    assert ev.score < 50.0
    assert ev.reasoning["bear_signals"] > ev.reasoning["bull_signals"]


def test_neutral_when_no_keywords():
    ev = _run(_agent([_article("Company holds annual shareholder meeting downtown")]))
    assert ev.score == 50.0
    assert ev.confidence == 0.15


def test_phrases_weighted_double():
    ev = _run(_agent([_article("raised guidance and earnings beat reported")]))
    # two bull phrases × 2 each contribute even before single keywords
    assert ev.reasoning["bull_phrases_matched"]
    assert ev.score > 50.0


def test_phrase_not_also_double_counted_as_single_keyword():
    # "trial success" is a _BULL_PHRASES entry (worth 2 hits). It must not ALSO
    # sit in the plain _BULL word set, or a headline containing it would score
    # 1 (word) + 2 (phrase) = 3 hits instead of the intended 2.
    ev = _run(_agent([_article("Drug trial success announced")]))
    assert ev.reasoning["bull_phrases_matched"] == ["trial success"]
    assert ev.reasoning["bull_signals"] == 2
    assert ev.reasoning["bear_signals"] == 0


def test_keyword_confidence_capped():
    # pile on signals — confidence must stay <= 0.45 per the fallback cap
    headline = " ".join(["beat", "upgrade", "record", "surge", "growth", "rally",
                          "buyback", "approval", "partnership", "contract"])
    ev = _run(_agent([_article(headline)]))
    assert ev.confidence <= 0.45


def test_score_clamped_to_valid_range():
    ev = _run(_agent([_article("miss cut downgrade fraud bankruptcy collapse plunge lawsuit recall")]))
    assert 1.0 <= ev.score <= 100.0


# ── FinBERT branch (stubbed pipeline — the real model is never built here) ───

def test_finbert_preferred_over_keywords(monkeypatch):
    _stub_finbert(monkeypatch, [("positive", 0.95)])
    # Bearish WORDS, positive model verdict: proves FinBERT decided, not keywords.
    ev = _run(_agent([_article("Company missed estimates, downgrade and fraud probe")]))
    assert "[finbert]" in ev.rationale
    assert ev.reasoning["provider"] == "finbert"
    assert ev.score > 50.0


def test_finbert_negative_scores_bearish(monkeypatch):
    _stub_finbert(monkeypatch, [("negative", 0.9)])
    ev = _run(_agent([_article("Some headline")]))
    assert ev.score < 50.0


def test_falls_back_to_keywords_when_finbert_unavailable():
    # The autouse fixture makes _load_finbert return None — a missing/failed
    # model must never cost us a reading.
    ev = _run(_agent([_article("Company beats earnings, analyst upgrade and record growth")]))
    assert "[keyword]" in ev.rationale
    assert ev.score > 50.0


def test_finbert_not_built_when_there_is_nothing_to_score(monkeypatch):
    called = []
    monkeypatch.setattr(fa, "_load_finbert", lambda: called.append(1))
    _run(_agent([]))                      # no articles at all
    assert called == []


def test_load_finbert_honours_the_disable_switch(monkeypatch):
    monkeypatch.setattr(fa, "_finbert_state", "unloaded")
    monkeypatch.setattr(fa, "_finbert_model", None)
    monkeypatch.setenv("FINBERT_DISABLED", "true")
    assert _REAL_LOAD_FINBERT() is None
    assert fa._finbert_state == "unavailable"


# ── freshness filter ─────────────────────────────────────────────────────────

def test_stale_articles_dropped_leaving_no_news():
    old_ts = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    ev = _run(_agent([_article("strong beat upgrade", created_at=old_ts)]))
    # the only article is stale → filtered → "no news available"
    assert ev.score == 50.0
    assert "no news" in ev.rationale


def test_fresh_article_kept():
    fresh_ts = datetime.now(timezone.utc).isoformat()
    ev = _run(_agent([_article("strong beat upgrade record growth", created_at=fresh_ts)]))
    assert ev.score > 50.0


def test_article_without_timestamp_is_kept():
    ev = _run(_agent([_article("strong beat upgrade record growth")]))  # no ts
    assert ev.score > 50.0
