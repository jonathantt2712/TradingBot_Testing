"""Vision Specialist — chart pattern recognition.

Provider priority (automatic, based on available env keys, only when
``llm_enabled=True``):
  1. GEMINI_API_KEY   → Google Gemini Flash vision (free tier)
  2. ANTHROPIC_API_KEY → Anthropic Claude Sonnet vision (paid)

Renders a candlestick chart PNG and asks the model to score the setup 1-100.

Whenever the LLM path isn't taken — ``llm_enabled=False`` (the default; see
Settings.use_llm_agents), no vision key configured, no chart image available,
or the LLM call itself fails — this agent does NOT pass with a flat neutral
score. It falls back to ``_structure_score``: a deterministic swing-high/
swing-low read of the same OHLCV bars every other agent uses (trend
structure, and whether price has broken through the nearest support/
resistance level) — a genuine, always-available, zero-cost signal rather
than a placeholder.
"""
from __future__ import annotations

import asyncio
import logging
import mimetypes
import time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

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

# Structure-read tuning (code-based fallback)
_SWING_WINDOW = 3     # a bar is a swing high/low if it's the extreme within ±this many bars
_MIN_BARS     = 30    # need enough history for a meaningful swing-point read


class VisionAgent(BaseAgent):
    role = AgentRole.VISION

    def __init__(
        self,
        *,
        weight:            float = 0.15,
        anthropic_api_key: str   = "",
        gemini_api_key:    str   = "",
        model:             str   = "",
        cache_ttl_min:     float = 60.0,
        llm_enabled:       bool  = True,
    ) -> None:
        super().__init__(weight=weight)
        self._llm_enabled = llm_enabled
        self._llm = LLMAdapter(
            gemini_key=gemini_api_key,
            anthropic_key=anthropic_api_key,
            anthropic_model=model,
        )
        # Per-ticker score cache so repeat scans within the TTL don't re-call the
        # vision model — keeps Vision contributing while staying inside the free
        # tier. ticker -> (monotonic_ts, evaluation). 0 disables. Only used for
        # the LLM path; the code-based structure read is cheap enough to redo
        # every scan and stays maximally fresh.
        self._cache_ttl = max(0.0, cache_ttl_min) * 60.0
        self._cache: dict[str, tuple[float, AgentEvaluation]] = {}

    async def evaluate(self, ctx: AnalysisContext) -> AgentEvaluation:
        if self._llm_enabled and self._llm.has_vision:
            cached = self._cached(ctx.ticker)
            if cached is not None:
                return cached

            path = ctx.chart_image_path
            if path and Path(path).exists():
                try:
                    return await self._evaluate_via_llm(ctx, path)
                except Exception as exc:
                    logger.warning("VisionAgent LLM failed for %s: %s — falling back "
                                   "to code-based structure read", ctx.ticker, exc)

        return self._structure_evaluation(ctx)

    async def _evaluate_via_llm(self, ctx: AnalysisContext, path: str) -> AgentEvaluation:
        media_type = mimetypes.guess_type(path)[0] or "image/png"
        raw_bytes  = await asyncio.to_thread(Path(path).read_bytes)

        prompt = _VISION_PROMPT + f"\n\nTicker: {ctx.ticker}"
        text = await self._llm.vision(raw_bytes, prompt, media_type)
        if not text:
            raise ValueError("empty response")
        parsed = parse_llm_json(text)
        if parsed is None:
            raise ValueError(f"unparseable vision response: {text[:120]!r}")
        raw_score = clamp_score(int(parsed["score"]))
        pattern   = parsed.get("pattern", "")
        reason    = parsed.get("reason", "")
        result = AgentEvaluation(
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
        # Cache only real reads — a fallback/error result must retry next scan
        # (e.g. once a transient quota limit clears).
        if self._cache_ttl > 0:
            self._cache[ctx.ticker.upper()] = (time.monotonic(), result)
            # Evict expired entries so a rotating universe can't grow the
            # cache without bound over a long session.
            if len(self._cache) > 200:
                now = time.monotonic()
                self._cache = {k: v for k, v in self._cache.items()
                               if now - v[0] < self._cache_ttl}
        return result

    def _cached(self, ticker: str) -> Optional[AgentEvaluation]:
        """Return a still-fresh cached evaluation for the ticker, or None."""
        if self._cache_ttl <= 0:
            return None
        hit = self._cache.get(ticker.upper())
        if hit is None:
            return None
        ts, evaluation = hit
        if (time.monotonic() - ts) >= self._cache_ttl:
            return None
        return evaluation

    # ── Code-based structure read (no LLM, no chart image) ──────────────────

    def _structure_evaluation(self, ctx: AnalysisContext) -> AgentEvaluation:
        bars = ctx.bars
        if bars is None or len(bars) < _MIN_BARS:
            return AgentEvaluation(
                role=self.role,
                score=NEUTRAL_SCORE,
                confidence=0.1,
                rationale=f"insufficient bars for structure read (<{_MIN_BARS})",
            )

        score, pattern, detail = self._structure_score(bars)
        n_swings = detail["swing_high_count"] + detail["swing_low_count"]
        # More confirmed swing points → more reliable trend/S-R read. Capped
        # below the LLM path's 0.7 — a genuine visual read of the full chart
        # (gaps, candle shapes, volume-at-price) sees more than swing points
        # extracted from OHLC alone.
        confidence = float(np.clip(0.30 + n_swings * 0.03, 0.30, 0.65))

        return AgentEvaluation(
            role=self.role,
            score=score,
            confidence=round(confidence, 2),
            rationale=f"[structure] {pattern}: {detail['reason']}",
            data={"pattern": pattern, "reason": detail["reason"]},
            reasoning={
                "provider": "code_structure",
                "pattern_identified": pattern,
                "analysis": detail["reason"],
                "raw_score": score,
                "swing_highs": detail["swing_high_count"],
                "swing_lows":  detail["swing_low_count"],
                "support":     detail["support"],
                "resistance":  detail["resistance"],
                "note": ("Deterministic swing-high/low structure read (no LLM): trend from "
                         "the last two swing highs/lows, position vs. the nearest support/"
                         "resistance level. Score 1=strong bearish, 50=neutral, 100=strong bullish."),
            },
        )

    @staticmethod
    def _dedupe_adjacent(indices: list[int]) -> list[int]:
        """Collapse a run of consecutive indices (a plateau/near-tie extremum
        matched at several adjacent bars) into its first index — otherwise the
        "last two swing highs" trend comparison can compare two bars from the
        SAME peak instead of two distinct peaks."""
        out: list[int] = []
        for i in indices:
            if not out or i - out[-1] > 1:
                out.append(i)
        return out

    @classmethod
    def _swing_points(cls, bars: pd.DataFrame, window: int = _SWING_WINDOW) -> tuple[list[int], list[int]]:
        """Indices of local swing highs/lows: the extreme within ±window bars."""
        highs = bars["high"].to_numpy()
        lows  = bars["low"].to_numpy()
        n = len(bars)
        swing_highs = [
            i for i in range(window, n - window)
            if highs[i] >= highs[i - window:i + window + 1].max()
        ]
        swing_lows = [
            i for i in range(window, n - window)
            if lows[i] <= lows[i - window:i + window + 1].min()
        ]
        return cls._dedupe_adjacent(swing_highs), cls._dedupe_adjacent(swing_lows)

    def _structure_score(self, bars: pd.DataFrame) -> tuple[float, str, dict]:
        """Trend structure (higher highs/lows vs. lower highs/lows) plus
        breakout/breakdown through the nearest swing-based support/resistance.
        Pure function of ``bars`` — deterministic, no external calls.
        """
        swing_highs, swing_lows = self._swing_points(bars)
        last_price = float(bars["close"].iloc[-1])
        highs = bars["high"]
        lows  = bars["low"]

        # Trend from the last two swing highs and the last two swing lows.
        higher_highs = len(swing_highs) >= 2 and highs.iloc[swing_highs[-1]] > highs.iloc[swing_highs[-2]]
        higher_lows  = len(swing_lows)  >= 2 and lows.iloc[swing_lows[-1]]   > lows.iloc[swing_lows[-2]]
        lower_highs  = len(swing_highs) >= 2 and highs.iloc[swing_highs[-1]] < highs.iloc[swing_highs[-2]]
        lower_lows   = len(swing_lows)  >= 2 and lows.iloc[swing_lows[-1]]   < lows.iloc[swing_lows[-2]]

        if higher_highs and higher_lows:
            trend = "uptrend"
        elif lower_highs and lower_lows:
            trend = "downtrend"
        else:
            trend = "range"

        resistance = float(highs.iloc[swing_highs[-1]]) if swing_highs else None
        support    = float(lows.iloc[swing_lows[-1]])   if swing_lows  else None

        score = 50.0
        if trend == "uptrend":
            score += 15.0
        elif trend == "downtrend":
            score -= 15.0

        if resistance is not None and last_price > resistance:
            score += 20.0
            pattern = "resistance_breakout"
            reason = f"price {last_price:.2f} above last swing high {resistance:.2f}"
        elif support is not None and last_price < support:
            score -= 20.0
            pattern = "support_breakdown"
            reason = f"price {last_price:.2f} below last swing low {support:.2f}"
        elif support is not None and resistance is not None and resistance > support:
            pos = (last_price - support) / (resistance - support)  # 0=at support, 1=at resistance
            score += (pos - 0.5) * 20.0
            pattern = f"{trend}_structure" if trend != "range" else "range_consolidation"
            reason = (f"price {pos*100:.0f}% of the way from support {support:.2f} "
                     f"to resistance {resistance:.2f}, {trend}")
        else:
            pattern = f"{trend}_structure" if trend != "range" else "range_consolidation"
            reason = f"{trend} structure, insufficient swing points for a support/resistance band"

        return clamp_score(score), pattern, {
            "reason": reason,
            "support": round(support, 4) if support is not None else None,
            "resistance": round(resistance, 4) if resistance is not None else None,
            "swing_high_count": len(swing_highs),
            "swing_low_count": len(swing_lows),
        }
