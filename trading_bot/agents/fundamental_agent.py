"""Fundamental Analyst — news sentiment + earnings/catalyst scoring via Claude LLM.

Fetches recent news for the ticker, summarises it with an Anthropic model,
and returns a directional 1..100 score.

Score semantics:
  1  = strongly bearish (bad earnings, scandal, downgrade, macro headwind)
  50 = neutral / no signal
  100 = strongly bullish (beat + raise, M&A, positive catalyst)

Degrades to neutral (score=50, confidence=0.1) if:
  - No news available
  - No Anthropic API key configured
  - LLM call fails for any reason
"""
from __future__ import annotations

import json
import logging
from typing import Any

from core.base_agent import NEUTRAL_SCORE, BaseAgent, clamp_score
from core.enums import AgentRole
from core.models import AgentEvaluation, AnalysisContext

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a professional equity analyst specialising in short-term catalysts. "
    "Given a list of recent news headlines and summaries for a stock ticker, "
    "output ONLY valid JSON with this exact structure:\n"
    '{"score": <int 1-100>, "confidence": <float 0.0-1.0>, "rationale": "<25 words max>"}\n'
    "Score meaning: 1=strongly bearish, 50=neutral, 100=strongly bullish. "
    "Focus on: earnings beats/misses, guidance, analyst upgrades/downgrades, "
    "M&A, regulatory events, macro catalysts. "
    "If there is no news, return score=50 confidence=0.1 rationale='no news'."
)


class FundamentalAgent(BaseAgent):
    role = AgentRole.FUNDAMENTAL

    def __init__(
        self,
        news_source,          # NewsSource instance (AlpacaNewsSource, etc.)
        *,
        weight:           float = 0.20,
        anthropic_api_key: str  = "",
        model:             str  = "claude-haiku-4-5-20251001",
        max_articles:      int  = 8,
    ) -> None:
        super().__init__(weight=weight)
        self.news        = news_source
        self.api_key     = anthropic_api_key
        self.model       = model
        self.max_articles = max_articles

    async def evaluate(self, ctx: AnalysisContext) -> AgentEvaluation:
        # ── Fetch news ─────────────────────────────────────────────────────────
        try:
            articles = await self.news.get_news(ctx.ticker, limit=self.max_articles)
        except Exception as exc:
            logger.debug("News fetch failed for %s: %s", ctx.ticker, exc)
            articles = []

        if not articles:
            return AgentEvaluation(
                role=self.role,
                score=NEUTRAL_SCORE,
                confidence=0.15,
                rationale="no news available",
            )

        if not self.api_key:
            # No LLM — do a simple keyword sentiment as fallback
            return self._keyword_fallback(ctx.ticker, articles)

        # ── Build LLM prompt ───────────────────────────────────────────────────
        news_text = "\n".join(
            f"- {a.get('headline', a.get('title', ''))}: {a.get('summary', '')[:120]}"
            for a in articles[:self.max_articles]
        )
        user_msg = f"Ticker: {ctx.ticker}\n\nRecent news:\n{news_text}"

        # ── Call Anthropic ─────────────────────────────────────────────────────
        try:
            import anthropic  # lazy import
            client = anthropic.AsyncAnthropic(api_key=self.api_key)
            resp = await client.messages.create(
                model=self.model,
                max_tokens=256,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
            raw = resp.content[0].text.strip()
            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            data: dict[str, Any] = json.loads(raw)
            score      = clamp_score(float(data.get("score", 50)))
            confidence = float(max(0.1, min(1.0, data.get("confidence", 0.6))))
            rationale  = str(data.get("rationale", ""))
        except Exception as exc:
            logger.warning("Fundamental LLM call failed for %s: %s", ctx.ticker, exc)
            return self._keyword_fallback(ctx.ticker, articles)

        return AgentEvaluation(
            role=self.role,
            score=score,
            confidence=confidence,
            rationale=rationale,
        )

    # ── Keyword fallback (no API key) ─────────────────────────────────────────

    _BULL = {"beat", "raised", "upgrade", "bullish", "surge", "record", "strong",
             "growth", "buy", "outperform", "positive", "breakout", "rally"}
    _BEAR = {"miss", "cut", "downgrade", "bearish", "plunge", "warning", "weak",
             "loss", "sell", "underperform", "negative", "breakdown", "recall"}

    def _keyword_fallback(self, ticker: str, articles: list) -> AgentEvaluation:
        text = " ".join(
            (a.get("headline", "") + " " + a.get("summary", "")).lower()
            for a in articles
        )
        bull = sum(1 for w in self._BULL if w in text)
        bear = sum(1 for w in self._BEAR if w in text)
        total = bull + bear
        if total == 0:
            score = NEUTRAL_SCORE
            conf  = 0.2
        else:
            score = clamp_score(50 + (bull - bear) / total * 30)
            conf  = min(0.5, 0.1 + total * 0.05)
        return AgentEvaluation(
            role=self.role,
            score=score,
            confidence=conf,
            rationale=f"keyword: +{bull}/-{bear} signals",
        )
