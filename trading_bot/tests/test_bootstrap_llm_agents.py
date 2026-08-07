"""bootstrap.build_manager — LLM-agent opt-out wiring.

Settings.use_llm_agents (env USE_LLM_AGENTS) defaults to False: trade analysis
should run entirely on deterministic code (FundamentalAgent's keyword/FinBERT
fallback, no VisionAgent, no DecisionAgent — PortfolioManager falls back to
the weighted composite/threshold path) unless explicitly opted back in.
"""
import pytest

pytest.importorskip("fastapi")  # bootstrap pulls in api-server-adjacent deps

from bootstrap import build_manager  # noqa: E402
from config.settings import Settings  # noqa: E402


def _settings(**overrides) -> Settings:
    s = Settings()
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def test_llm_agents_off_by_default():
    s = Settings()
    assert s.use_llm_agents is False

    pm = build_manager(s, broker=None)

    assert pm.vision is None
    assert pm._decision_agent is None
    assert pm.fundamental._llm_enabled is False


def test_llm_agents_on_when_enabled():
    s = _settings(use_llm_agents=True)

    pm = build_manager(s, broker=None)

    assert pm.vision is not None
    assert pm._decision_agent is not None
    assert pm.fundamental._llm_enabled is True


def test_explicit_include_flags_override_the_setting_default():
    # backtest_intraday.py / optimize_strategy.py pass explicit True/False
    # (their own USE_LLM_BACKTEST knob) regardless of USE_LLM_AGENTS — the
    # setting is only a default for callers that don't specify.
    s = Settings()  # use_llm_agents defaults False
    assert s.use_llm_agents is False

    pm_on = build_manager(s, broker=None, include_vision=True, include_decision_agent=True)
    assert pm_on.vision is not None
    assert pm_on._decision_agent is not None

    s2 = _settings(use_llm_agents=True)
    pm_off = build_manager(s2, broker=None, include_vision=False, include_decision_agent=False)
    assert pm_off.vision is None
    assert pm_off._decision_agent is None
