"""DecisionAgent: bull/bear debate prompt, reflection injection, fallbacks."""
import asyncio

from agents.decision_agent import DecisionAgent
from core.enums import AgentRole, Decision
from core.models import AgentEvaluation, AnalysisContext
from core.trade_memory import TradeMemory


class FakeLLM:
    def __init__(self, response):
        self.response = response
        self.last_prompt = None
        self.last_system = None
        self.has_llm = True

    async def chat(self, prompt, system=""):
        self.last_prompt = prompt
        self.last_system = system
        return self.response


_EVALS = [
    AgentEvaluation(role=AgentRole.TECHNICAL, score=75, confidence=0.8, rationale="strong uptrend"),
    AgentEvaluation(role=AgentRole.RISK, score=70, confidence=0.9, rationale="R/R 2.0"),
]
_CTX = AnalysisContext(ticker="NVDA")

_DEBATE_JSON = (
    '{"bull_case":"momentum strong, volume confirms","bear_case":"extended into resistance",'
    '"decision":"LONG","composite_score":72,"rationale":"trend intact",'
    '"key_factors":["technical"],"concerns":["rsi high"]}'
)


def _run(agent):
    return asyncio.run(agent.decide(_CTX, _EVALS, "risk_on", "calm tape"))


def _agent(tmp_path, response, *, debate=True):
    a = DecisionAgent(gemini_api_key="")
    a._llm = FakeLLM(response)
    a._debate = debate
    a._memory = TradeMemory(path=tmp_path / "m.json")
    return a


def test_debate_prompt_and_parsed_cases(tmp_path):
    a = _agent(tmp_path, _DEBATE_JSON)
    decision, composite, meta = _run(a)
    assert decision is Decision.LONG
    assert composite == 72.0
    assert meta["bull_case"].startswith("momentum")
    assert meta["bear_case"].startswith("extended")
    assert "DELIBERATE AS A PANEL" in a._llm.last_prompt


def test_recent_lessons_injected_into_prompt(tmp_path):
    a = _agent(tmp_path, _DEBATE_JSON)
    a._memory.record_decision("NVDA", "LONG", 70.0)
    a._memory.record_outcome("NVDA", 100.0)
    _run(a)
    assert "RECENT OUTCOMES" in a._llm.last_prompt


def test_no_lessons_when_history_empty(tmp_path):
    a = _agent(tmp_path, _DEBATE_JSON)
    _run(a)
    assert "RECENT OUTCOMES" not in a._llm.last_prompt


def test_legacy_prompt_when_debate_disabled(tmp_path):
    a = _agent(tmp_path, _DEBATE_JSON, debate=False)
    _run(a)
    assert "INSTRUCTIONS:" in a._llm.last_prompt
    assert "DELIBERATE AS A PANEL" not in a._llm.last_prompt


def test_empty_llm_response_falls_back_to_pass(tmp_path):
    a = _agent(tmp_path, "")
    decision, composite, meta = _run(a)
    assert decision is Decision.PASS
    assert composite == 50.0
    assert "error" in meta


def test_invalid_decision_string_falls_back_to_pass(tmp_path):
    # LLM hallucination: valid JSON with an unrecognised decision value.
    resp = '{"decision":"BUY","composite_score":70,"rationale":"test","key_factors":[],"concerns":[]}'
    a = _agent(tmp_path, resp)
    decision, composite, _ = _run(a)
    assert decision is Decision.PASS   # invalid value → safe fallback


def test_out_of_range_composite_score_is_clamped(tmp_path):
    # LLM returns a score far outside [1, 100]; must be clamped, not propagated.
    resp = '{"decision":"LONG","composite_score":250,"rationale":"test","key_factors":[],"concerns":[]}'
    a = _agent(tmp_path, resp)
    _, composite, _ = _run(a)
    assert composite == 100.0

    resp_low = '{"decision":"SHORT","composite_score":-10,"rationale":"test","key_factors":[],"concerns":[]}'
    a2 = _agent(tmp_path, resp_low)
    _, composite_low, _ = _run(a2)
    assert composite_low == 1.0


def test_non_json_response_falls_back_to_pass(tmp_path):
    # LLM returns prose instead of JSON — parse_llm_json + json.loads both fail.
    a = _agent(tmp_path, "I think you should go LONG because the trend looks good.")
    decision, composite, meta = _run(a)
    assert decision is Decision.PASS
    assert composite == 50.0
    assert "error" in meta
