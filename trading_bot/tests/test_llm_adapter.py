"""LLMAdapter: auth-error detection, fail-fast disable, and operator alerting."""
import asyncio

import pytest

from core import health, llm_adapter
from core.llm_adapter import LLMAdapter, _is_auth_error, _is_quota_error


@pytest.fixture(autouse=True)
def _clean():
    llm_adapter._disabled_providers.clear()
    health.reset()
    yield
    llm_adapter._disabled_providers.clear()
    health.reset()


@pytest.mark.parametrize("msg", [
    "401 UNAUTHENTICATED",
    "Error 403 PERMISSION_DENIED",
    "API key not valid. Please pass a valid API key.",
    "ACCESS_TOKEN_TYPE_UNSUPPORTED",
])
def test_auth_errors_detected(msg):
    assert _is_auth_error(Exception(msg)) is True


@pytest.mark.parametrize("msg", [
    "429 RESOURCE_EXHAUSTED quota",
    "503 service unavailable",
    "read timeout",
])
def test_non_auth_errors_not_flagged(msg):
    assert _is_auth_error(Exception(msg)) is False


def test_quota_429_is_not_auth():
    # The real bug: a 429 quota error (valid key, throttled) must NOT be treated
    # as a rejected key — even when a stray "401"/"403" appears inside an ID.
    msg = ("429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded "
           "your current quota', 'status': 'RESOURCE_EXHAUSTED', "
           "'details': [{'project': 'projects/403129955401'}]}}")
    assert _is_quota_error(Exception(msg)) is True
    assert _is_auth_error(Exception(msg)) is False


def test_quota_error_does_not_disable_provider():
    a = LLMAdapter(gemini_key="valid-key")
    a._report_quota("gemini", Exception("429 RESOURCE_EXHAUSTED"))
    assert a._disabled("gemini") is False          # key still usable once it resets
    assert a.has_llm is True
    assert any(i.key == "llm_quota:gemini" for i in health.active_issues())


def test_auth_failure_disables_provider_and_reports():
    a = LLMAdapter(gemini_key="bad-key-123")
    assert a.has_llm is True
    a._disable("gemini", Exception("401 UNAUTHENTICATED"))
    # Provider disabled → has_llm flips, so agents skip the LLM entirely.
    assert a._disabled("gemini") is True
    assert a.has_llm is False
    # And the operator is told what to fix.
    issues = health.active_issues()
    assert any(i.key == "llm_auth:gemini" for i in issues)
    assert "GEMINI_API_KEY" in issues[0].message


def test_chat_returns_none_when_all_providers_disabled():
    a = LLMAdapter(gemini_key="bad", anthropic_key="alsobad")
    a._disable("gemini", Exception("401"))
    a._disable("anthropic", Exception("403"))
    assert a.has_llm is False
    assert asyncio.run(a.chat("hi")) is None      # no network call attempted


def test_disable_is_shared_across_instances_for_same_key():
    a = LLMAdapter(gemini_key="same-key")
    b = LLMAdapter(gemini_key="same-key")
    a._disable("gemini", Exception("401"))
    assert b._disabled("gemini") is True          # one bad key disables everywhere


def test_no_keys_means_no_llm():
    assert LLMAdapter(gemini_key="", anthropic_key="").has_llm is False
