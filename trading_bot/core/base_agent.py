"""Base class for all trading agents."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from core.enums import AgentRole
from core.models import AgentEvaluation, AnalysisContext

logger = logging.getLogger(__name__)

NEUTRAL_SCORE: float = 50.0


def clamp_score(score: float) -> float:
    """Clamp a score to the valid [1, 100] range."""
    return float(max(1.0, min(100.0, score)))


class BaseAgent(ABC):
    """Abstract base for all directional and risk agents."""

    role: AgentRole  # must be set by subclass

    def __init__(self, *, weight: float = 1.0) -> None:
        self.weight = weight

    @abstractmethod
    async def evaluate(self, ctx: AnalysisContext) -> AgentEvaluation:
        """Run the agent and return a scored evaluation."""

    async def safe_evaluate(self, ctx: AnalysisContext) -> AgentEvaluation:
        """Wrapper that catches exceptions and returns neutral score on failure."""
        try:
            return await self.evaluate(ctx)
        except Exception as exc:
            logger.warning("%s failed for %s: %s", self.__class__.__name__, ctx.ticker, exc)
            return AgentEvaluation(
                role=self.role,
                score=NEUTRAL_SCORE,
                confidence=0.1,
                rationale=f"error: {exc}",
            )
