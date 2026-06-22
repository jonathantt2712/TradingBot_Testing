"""FundamentalAgent — the deterministic, always-available paths.

We don't exercise the LLM or FinBERT branches (network / heavy deps); we pin
the keyword fallback (the zero-cost path that always runs) and the article
freshness filter. The agent is constructed with no API keys so ``has_llm`` is
False and FinBERT is absent in CI, so ``evaluate`` deterministically lands on
the keyword fallback.
"""
from datetime import datetime, timedelta, timezone

import asyncio

from agents.fundamental_agent import FundamentalAgent
from core.enums import AgentRole
from core.models import AnalysisContext


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


# ── no-news path ─────────────────────────────────────────────────────────────

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


def test_keyword_confidence_capped():
    # pile on signals — confidence must stay <= 0.45 per the fallback cap
    headline = " ".join(["beat", "upgrade", "record", "surge", "growth", "rally",
                          "buyback", "approval", "partnership", "contract"])
    ev = _run(_agent([_article(headline)]))
    assert ev.confidence <= 0.45


def test_score_clamped_to_valid_range():
    ev = _run(_agent([_article("miss cut downgrade fraud bankruptcy collapse plunge lawsuit recall")]))
    assert 1.0 <= ev.score <= 100.0


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
