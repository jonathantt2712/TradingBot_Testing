"""VisionAgent: per-ticker TTL caching keeps it within the free tier."""
import asyncio

import pytest

from agents.vision_agent import VisionAgent
from core.models import AnalysisContext


def _agent(tmp_path, *, ttl=60.0, response='{"score": 80, "pattern": "p", "reason": "r"}'):
    """VisionAgent with a stubbed vision call that counts invocations."""
    chart = tmp_path / "chart.png"
    chart.write_bytes(b"\x89PNG fake-bytes")
    agent = VisionAgent(gemini_api_key="dummy-key", cache_ttl_min=ttl)
    calls = {"n": 0}

    async def fake_vision(_raw, _prompt, _media):
        calls["n"] += 1
        return response

    agent._llm.vision = fake_vision          # stub the network call
    return agent, calls, str(chart)


def _ctx(ticker, chart):
    return AnalysisContext(ticker=ticker, chart_image_path=chart)


def test_repeat_scan_uses_cache(tmp_path):
    agent, calls, chart = _agent(tmp_path)
    e1 = asyncio.run(agent.evaluate(_ctx("NVDA", chart)))
    e2 = asyncio.run(agent.evaluate(_ctx("NVDA", chart)))
    assert calls["n"] == 1                    # second scan served from cache
    assert e1.score == e2.score == 80
    assert e2.confidence == 0.7               # real read, not a neutral fallback


def test_ttl_zero_disables_cache(tmp_path):
    agent, calls, chart = _agent(tmp_path, ttl=0)
    asyncio.run(agent.evaluate(_ctx("NVDA", chart)))
    asyncio.run(agent.evaluate(_ctx("NVDA", chart)))
    assert calls["n"] == 2                     # every scan calls the model


def test_cache_is_per_ticker(tmp_path):
    agent, calls, chart = _agent(tmp_path)
    asyncio.run(agent.evaluate(_ctx("NVDA", chart)))
    asyncio.run(agent.evaluate(_ctx("AAPL", chart)))
    assert calls["n"] == 2                     # different tickers, separate reads


def test_expired_entry_refetches(tmp_path):
    agent, calls, chart = _agent(tmp_path)
    asyncio.run(agent.evaluate(_ctx("NVDA", chart)))
    # Force the cached entry to look stale.
    ts, ev = agent._cache["NVDA"]
    agent._cache["NVDA"] = (ts - agent._cache_ttl - 1, ev)
    asyncio.run(agent.evaluate(_ctx("NVDA", chart)))
    assert calls["n"] == 2


def test_failures_are_not_cached(tmp_path):
    # Empty response -> neutral fallback; must retry next scan, not be cached.
    agent, calls, chart = _agent(tmp_path, response="")
    e1 = asyncio.run(agent.evaluate(_ctx("NVDA", chart)))
    e2 = asyncio.run(agent.evaluate(_ctx("NVDA", chart)))
    assert calls["n"] == 2
    assert e1.score == e2.score == 50.0        # neutral, not cached
