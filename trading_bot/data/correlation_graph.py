"""Data-derived correlation graph for the concentration cap.

Replaces the hand-maintained ``_CORRELATION_GROUPS`` heuristic with clusters
computed from recent price action: two symbols are "correlated" when the
correlation of their returns over the lookback window is at least a threshold.
The PortfolioManager's concentration cap then counts how many *currently open*
positions actually co-move with a candidate, instead of relying on a static
membership list that drifts out of date.

Fail-soft: a symbol without enough overlapping data is simply absent from the
graph (``has()`` is False), and the caller falls back to the static groups.
"""
from __future__ import annotations

from typing import Mapping

import pandas as pd


class CorrelationGraph:
    """Undirected positive-correlation adjacency over symbols."""

    def __init__(self, neighbors: dict[str, set[str]]) -> None:
        self._neighbors = neighbors

    def has(self, symbol: str) -> bool:
        """True when the symbol had enough data to be placed in the graph."""
        return symbol.upper() in self._neighbors

    def correlated(self, a: str, b: str) -> bool:
        return a.upper() != b.upper() and b.upper() in self._neighbors.get(a.upper(), set())

    def correlated_with(self, symbol: str) -> set[str]:
        return set(self._neighbors.get(symbol.upper(), set()))

    @classmethod
    def build_from_bars(
        cls,
        bars_by_symbol: Mapping[str, pd.DataFrame],
        *,
        threshold: float = 0.7,
        min_points: int = 20,
    ) -> "CorrelationGraph":
        """Build the graph from per-symbol OHLCV frames.

        Closes are aligned on their timestamps, converted to returns, and a
        pairwise correlation matrix is taken. Only positive correlation matters
        for concentration risk (negatively correlated names diversify), so edges
        are added where ``corr >= threshold``.
        """
        closes: dict[str, pd.Series] = {}
        for sym, bars in bars_by_symbol.items():
            if bars is None or "close" not in getattr(bars, "columns", []):
                continue
            if len(bars) < min_points + 1:
                continue
            closes[sym.upper()] = bars["close"].astype(float)
        if len(closes) < 2:
            return cls({})

        price = pd.DataFrame(closes)          # aligns on the (timestamp) index
        rets = price.pct_change().dropna(how="all")
        if len(rets) < min_points:
            return cls({})

        corr = rets.corr(min_periods=min_points)
        syms = list(corr.columns)
        # Every symbol with usable data appears, even with no peers — that is a
        # real "no constraint" answer the caller should trust over static groups.
        neighbors: dict[str, set[str]] = {s: set() for s in syms}
        for i, a in enumerate(syms):
            for b in syms[i + 1:]:
                c = corr.at[a, b]
                if pd.notna(c) and c >= threshold:
                    neighbors[a].add(b)
                    neighbors[b].add(a)
        return cls(neighbors)
