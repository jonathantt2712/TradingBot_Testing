"""Vision Specialist — chart pattern recognition.

Sends a rendered chart image to a vision-capable model and asks for a
structured read of the setup (support/resistance, trend, breakout) mapped to
a 1..100 score. If no image or API key is available it degrades to neutral.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
from pathlib import Path

from core.base_agent import NEUTRAL_SCORE, BaseAgent, clamp_score
from core.enums import AgentRole
from core.models import AgentEvaluation, AnalysisContext

logger = logging.getLogger(__name__)


class VisionAgent(BaseAgent):
    role = AgentRole.VISION

    def __init__(
        self,
        *,
        weight: float = 0.2,
        anthropic_api_key: str = "",
        model: str = "claude-sonnet-4-6",
    ) -> None:
        super().__init__(weight=weight)
        self.api_key = anthropic_api_key
        self.model = model

    async def evaluate(self, ctx: AnalysisContext) -> AgentEvaluation:
        path = ctx.chart_image_path
        if not path or not Path(path).exists():
            return AgentEvaluation(
                role=self.role,
                score=NEUTRAL_SCORE,
                confidence=0.1,
                rationale="no chart image provided",
            )
        if not self.api_key:
            return AgentEvaluation(
                role=self.role,
                score=NEUTRAL_SCORE,
                confidence=0.1,
                rationale="no vision API key configured",
            )

        media_type = mimetypes.guess_type(path)[0] or "image/png"
        # FIX: read file bytes with asyncio.to_thread to avoid blocking the event loop
        raw = await asyncio.to_thread(Path(path).read_bytes)
        b64 = base64.standard_b64encode(raw).decode()

        import anthropic  # lazy import

        client = anthropic.AsyncAnthropic(api_key=self.api_key)
        instruction = (
            "You are a technical chart analyst. Assess this price chart for "
            f"{ctx.ticker}. Identify trend, key support/resistance, and any "
            "breakout/breakdown. Return ONLY JSON: "
            '{"score": <int 1-100, 1=bearish setup,100=bullish setup>, '
            '"pattern": "<short>", "reason": "<=25 words"}.'
        )
        try:
            resp = await client.messages.create(
                model=self.model,
                max_tokens=300,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                            {"type": "text", "text": instruction},
                        ],
                    }
                ],
            )
            text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
            parsed = json.loads(text)
            return AgentEvaluation(
                role=self.role,
                score=clamp_score(int(parsed["score"])),
                confidence=0.7,
                rationale=f"{parsed.get('pattern', '')}: {parsed.get('reason', '')}",
                data=parsed,
            )
        except Exception:  # noqa: BLE001
            logger.exception("vision analysis failed for %s", ctx.ticker)
            return AgentEvaluation(
                role=self.role,
                score=NEUTRAL_SCORE,
                confidence=0.0,
                rationale="vision error -> neutral",
            )
