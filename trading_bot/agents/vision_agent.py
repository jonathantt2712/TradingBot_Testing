"""Vision Specialist — chart pattern recognition via vision LLM.

Provider priority (automatic, based on available env keys):
  1. GEMINI_API_KEY   → Google Gemini Flash vision (free tier)
  2. ANTHROPIC_API_KEY → Anthropic Claude Sonnet vision (paid)
  3. none             → degrades to neutral (no cost)

Renders a candlestick chart PNG and asks the model to score the setup 1-100.
"""
from __future__ import annotations

import asyncio
import logging
import mimetypes
from pathlib import Path

from core.base_agent import NEUTRAL_SCORE, BaseAgent, clamp_score
from core.enums import AgentRole
from core.llm_adapter import LLMAdapter, parse_llm_json
from core.models import AgentEvaluation, AnalysisContext

logger = logging.getLogger(__name__)

_VISION_PROMPT = (
    "You are a technical chart analyst. Assess this price chart. "
    "Identify trend, key support/resistance, and any breakout/breakdown pattern. "
    "Return ONLY valid JSON: "
    '{"score": <int 1-100, 1=strong bearish setup, 100=strong bullish setup>, '
    '"pattern": "<short pattern name>", "reason": "<25 words max>"}. '
)


class VisionAgent(BaseAgent):
    role = AgentRole.VISION

    def __init__(
        self,
        *,
        weight:            float = 0.15,
        anthropic_api_key: str   = "",
        gemini_api_key:    str   = "",
        model:             str   = "",
    ) -> None:
        super().__init__(weight=weight)
        self._llm = LLMAdapter(
            gemini_key=gemini_api_key,
            anthropic_key=anthropic_api_key,
            anthropic_model=model,
        )

    async def evaluate(self, ctx: AnalysisContext) -> AgentEvaluation:
        path = ctx.chart_image_path
        if not path or not Path(path).exists():
            return AgentEvaluation(
                role=self.role,
                score=NEUTRAL_SCORE,
                confidence=0.1,
                rationale="no chart image provided",
            )

        if not self._llm.has_vision:
            return AgentEvaluation(
                role=self.role,
                score=NEUTRAL_SCORE,
                confidence=0.1,
                rationale="no vision API key configured",
            )

        media_type = mimetypes.guess_type(path)[0] or "image/png"
        raw_bytes  = await asyncio.to_thread(Path(path).read_bytes)

        prompt = _VISION_PROMPT + f"\n\nTicker: {ctx.ticker}"
        try:
            text = await self._llm.vision(raw_bytes, prompt, media_type)
            if not text:
                raise ValueError("empty response")
            parsed = parse_llm_json(text)
            if parsed is None:
                raise ValueError(f"unparseable vision response: {text[:120]!r}")
            raw_score = clamp_score(int(parsed["score"]))
            pattern   = parsed.get("pattern", "")
            reason    = parsed.get("reason", "")
            return AgentEvaluation(
                role=self.role,
                score=raw_score,
                confidence=0.7,
                rationale=f"[{self._llm.provider}] {pattern}: {reason}",
                data=parsed,
                reasoning={
                    "provider": self._llm.provider,
                    "pattern_identified": pattern,
                    "analysis": reason,
                    "raw_score": raw_score,
                    "note": "Score 1=strong bearish chart setup, 50=neutral, 100=strong bullish chart setup",
                },
            )
        except Exception as exc:
            logger.debug("VisionAgent failed for %s: %s", ctx.ticker, exc)
            return AgentEvaluation(
                role=self.role,
                score=NEUTRAL_SCORE,
                confidence=0.0,
                rationale="vision error -> neutral",
            )
