"""CorrelationGraph: data-derived clusters and PortfolioManager integration."""
import numpy as np
import pandas as pd

from data.correlation_graph import CorrelationGraph

from conftest import make_session_bars


def _bars_from_closes(closes):
    return make_session_bars(list(closes))


def test_correlated_pair_detected():
    base = np.cumsum(np.random.default_rng(1).normal(0, 1, 60)) + 100
    a = _bars_from_closes(base)
    b = _bars_from_closes(base + 0.01)          # near-identical path → corr ~1
    g = CorrelationGraph.build_from_bars({"AAA": a, "BBB": b}, threshold=0.7, min_points=10)
    assert g.correlated("AAA", "BBB")
    assert g.correlated_with("AAA") == {"BBB"}


def test_uncorrelated_pair_not_linked():
    rng = np.random.default_rng(2)
    a = _bars_from_closes(np.cumsum(rng.normal(0, 1, 60)) + 100)
    b = _bars_from_closes(np.cumsum(rng.normal(0, 1, 60)) + 100)
    g = CorrelationGraph.build_from_bars({"AAA": a, "BBB": b}, threshold=0.9, min_points=10)
    assert g.has("AAA") and g.has("BBB")        # both have data
    assert not g.correlated("AAA", "BBB")       # but no strong edge


def test_insufficient_data_absent_from_graph():
    a = _bars_from_closes([100.0] * 5)          # too few points
    b = _bars_from_closes([100.0] * 5)
    g = CorrelationGraph.build_from_bars({"AAA": a, "BBB": b}, min_points=20)
    assert not g.has("AAA")
    assert g.correlated_with("ZZZ") == set()    # unknown symbol → empty


def test_inverse_correlation_not_blocked():
    # Negatively correlated names diversify (long AAA + long BBB = hedge), so
    # the graph must NOT link them. Only positive correlation matters for
    # concentration risk.
    base = np.cumsum(np.random.default_rng(42).normal(0, 1, 60)) + 100
    a = _bars_from_closes(base)
    b = _bars_from_closes(-base + 200)   # perfect negative correlation
    g = CorrelationGraph.build_from_bars({"AAA": a, "BBB": b}, threshold=0.7, min_points=10)
    assert g.has("AAA") and g.has("BBB")
    assert not g.correlated("AAA", "BBB")   # negatively correlated → NOT an edge
    assert g.correlated_with("AAA") == set()


def test_correlated_is_symmetric_and_self_false():
    base = np.cumsum(np.random.default_rng(3).normal(0, 1, 60)) + 100
    g = CorrelationGraph.build_from_bars(
        {"AAA": _bars_from_closes(base), "BBB": _bars_from_closes(base)},
        threshold=0.7, min_points=10,
    )
    assert g.correlated("aaa", "bbb") == g.correlated("BBB", "AAA")
    assert not g.correlated("AAA", "AAA")


# ── PortfolioManager integration ─────────────────────────────────────────────

def _pm_with_graph(graph, **risk):
    from test_portfolio_manager import FakeBroker, make_pm
    pm = make_pm(max_correlated_positions=2, **risk)
    pm.set_correlation_graph(graph)
    return pm


def test_data_graph_blocks_correlated_stack():
    import asyncio
    from test_portfolio_manager import FakeBroker

    graph = CorrelationGraph({"NEW": {"AAA", "BBB"}, "AAA": {"NEW"}, "BBB": {"NEW"}})
    pm = _pm_with_graph(graph)
    pm.broker = FakeBroker(positions=[{"symbol": "AAA"}, {"symbol": "BBB"}])
    # NEW co-moves with both open names; cap=2 → blocked.
    assert asyncio.run(pm._entry_allowed("NEW")) is False


def test_data_graph_allows_uncorrelated_name():
    import asyncio
    from test_portfolio_manager import FakeBroker

    graph = CorrelationGraph({"NEW": set(), "AAA": set(), "BBB": set()})
    pm = _pm_with_graph(graph)
    pm.broker = FakeBroker(positions=[{"symbol": "AAA"}, {"symbol": "BBB"}])
    # Graph covers NEW and finds no correlated open positions → allowed.
    assert asyncio.run(pm._entry_allowed("NEW")) is True


def test_falls_back_to_static_groups_when_symbol_uncovered():
    import asyncio
    from test_portfolio_manager import FakeBroker

    # Graph does NOT cover the candidate → static mega_tech groups apply.
    graph = CorrelationGraph({"AAA": set()})
    pm = _pm_with_graph(graph)
    pm.broker = FakeBroker(positions=[{"symbol": "NVDA"}, {"symbol": "AAPL"}])
    assert asyncio.run(pm._entry_allowed("MSFT")) is False   # 3rd mega-cap, cap=2
