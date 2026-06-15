"""Fundamental Analyst — news sentiment + earnings/catalyst scoring via LLM.

Provider priority (automatic, based on available env keys):
  1. GEMINI_API_KEY    -> Google Gemini Flash (free tier)
  2. ANTHROPIC_API_KEY -> Anthropic Claude Haiku (paid)
  3. none              -> keyword sentiment fallback (always works, no cost)
"""
from __future__ import annotations

import logging

from core.base_agent import NEUTRAL_SCORE, BaseAgent, clamp_score
from core.enums import AgentRole
from core.llm_adapter import LLMAdapter, parse_llm_json
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
        news_source,
        *,
        weight:            float = 0.20,
        anthropic_api_key: str   = "",
        gemini_api_key:    str   = "",
        model:             str   = "",
        max_articles:      int   = 8,
    ) -> None:
        super().__init__(weight=weight)
        self.news         = news_source
        self.max_articles = max_articles
        self._llm         = LLMAdapter(
            gemini_key=gemini_api_key,
            anthropic_key=anthropic_api_key,
            anthropic_model=model,
        )

    async def evaluate(self, ctx: AnalysisContext) -> AgentEvaluation:
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

        if self._llm.has_llm:
            news_text = "\n".join(
                f"- {a.get('headline', a.get('title', ''))}: {a.get('summary', '')[:120]}"
                for a in articles[:self.max_articles]
            )
            user_msg = f"Ticker: {ctx.ticker}\n\nRecent news:\n{news_text}"
            try:
                raw = await self._llm.chat(user_msg, system=_SYSTEM_PROMPT)
                data = parse_llm_json(raw)
                if data is not None:
                    score      = clamp_score(float(data.get("score", 50)))
                    confidence = float(max(0.1, min(1.0, data.get("confidence", 0.6))))
                    rationale  = str(data.get("rationale", ""))
                    headlines  = [
                        a.get("headline", a.get("title", "")) for a in articles[:5]
                    ]
                    return AgentEvaluation(
                        role=self.role,
                        score=score,
                        confidence=confidence,
                        rationale=f"[{self._llm.provider}] {rationale}",
                        reasoning={
                            "provider": self._llm.provider,
                            "articles_analyzed": len(articles),
                            "headlines_sample": headlines,
                            "llm_rationale": rationale,
                            "score": score,
                            "confidence": round(confidence, 3),
                            "note": "Score 1=strongly bearish, 50=neutral, 100=strongly bullish based on recent news catalysts",
                        },
                    )
                logger.warning(
                    "Fundamental: could not parse LLM response for %s: %r",
                    ctx.ticker, (raw or "")[:200],
                )
            except Exception as exc:
                logger.warning("Fundamental LLM call failed for %s: %s", ctx.ticker, exc)

        return self._keyword_fallback(ctx.ticker, articles)

    # Expanded keyword sets — covers earnings, guidance, analyst, FDA/biotech, macro events
    _BULL = {
        # Earnings / guidance
        "beat", "beats", "raised", "raise", "topped", "exceeded", "blowout",
        "record", "strong", "strength", "solid",
        # Analyst / valuation
        "upgrade", "upgraded", "outperform", "buy", "overweight",
        "bullish", "initiate", "positive surprise",
        # Growth / momentum
        "growth", "accelerating", "expansion", "surge", "surges", "rally", "rallies",
        "breakout",
        # Corporate / M&A
        "acquisition", "buyback", "dividend", "approved", "approval", "cleared",
        "partnership", "contract", "deal", "awarded",
        # FDA / biotech
        "efficacy", "trial success", "positive data",
    }
    _BEAR = {
        # Earnings / guidance
        "miss", "missed", "cut", "cuts", "lowered", "below", "disappointed",
        "weak", "weakness", "soft", "deceleration",
        # Analyst / valuation
        "downgrade", "downgraded", "underperform", "sell", "underweight",
        "bearish", "negative surprise",
        # Losses / risk
        "loss", "losses", "plunge", "plunges", "collapse", "warning", "cautious",
        "concern", "probe", "investigation", "lawsuit", "recall",
        "restatement", "fraud", "default", "bankruptcy",
        # FDA / biotech
        "failed", "failure", "trial failure",
        # Macro / regulatory
        "tariff", "sanction", "delisted", "delisting",
    }
    # Multi-word phrases worth double-weight (more specific = more signal)
    _BULL_PHRASES = frozenset({
        "price target raised", "fda approval", "positive data", "trial success",
        "all-time high", "52-week high", "positive surprise", "earnings beat",
        "revenue beat", "raised guidance",
    })
    _BEAR_PHRASES = frozenset({
        "price target cut", "fda rejection", "trial failure", "safety concern",
        "clinical hold", "complete response letter", "sec subpoena",
        "negative surprise", "missed estimates", "lowered guidance",
    })

    def _keyword_fallback(self, ticker: str, articles: list) -> AgentEvaluation:
        text = " ".join(
            (a.get("headline", "") + " " + a.get("summary", "")).lower()
            for a in articles
        )
        # Multi-word phrases first (worth 2 hits each — more specific signal)
        bull_phrases_hit = [p for p in self._BULL_PHRASES if p in text]
        bear_phrases_hit = [p for p in self._BEAR_PHRASES if p in text]
        bull_words_hit   = [w for w in self._BULL if w in text]
        bear_words_hit   = [w for w in self._BEAR if w in text]

        bull = sum(2 for _ in bull_phrases_hit) + sum(1 for _ in bull_words_hit)
        bear = sum(2 for _ in bear_phrases_hit) + sum(1 for _ in bear_words_hit)

        total = bull + bear
        if total == 0:
            score = NEUTRAL_SCORE
            conf  = 0.15
        else:
            score = clamp_score(50 + (bull - bear) / total * 30)
            conf  = min(0.45, 0.1 + total * 0.04)

        headlines = [a.get("headline", a.get("title", "")) for a in articles[:5]]
        return AgentEvaluation(
            role=self.role,
            score=score,
            confidence=conf,
            rationale=f"[keyword] +{bull}/-{bear} signals",
            reasoning={
                "provider": "keyword_fallback",
                "articles_analyzed": len(articles),
                "headlines_sample": headlines,
                "bull_signals": bull,
                "bear_signals": bear,
                "bull_phrases_matched": bull_phrases_hit,
                "bear_phrases_matched": bear_phrases_hit,
                "bull_keywords_matched": bull_words_hit[:10],
                "bear_keywords_matched": bear_words_hit[:10],
                "note": "No LLM available — scoring via keyword matching. Confidence capped at 0.45.",
            },
        )
