"""Thin async LLM adapter — tries providers in priority order.

Priority: Gemini Flash (free) → Anthropic Claude → keyword fallback.

Usage:
    adapter = LLMAdapter()          # reads keys from env automatically
    result  = await adapter.chat("prompt text")
    result  = await adapter.vision(image_bytes, "prompt text")  # vision models only
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
from typing import Optional

from core import health

logger = logging.getLogger(__name__)

_LLM_JSON_RE = re.compile(r'\{[^{}]*"score"\s*:\s*\d+[^{}]*\}', re.DOTALL)


def parse_llm_json(raw: str) -> Optional[dict]:
    """Extract the first JSON object containing a 'score' key from LLM output.

    Handles markdown code fences, preamble text, and minor formatting variations.
    """
    if not raw:
        return None
    text = raw.strip()
    if "```" in text:
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = _LLM_JSON_RE.search(raw)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return None

# Defaults — override with GEMINI_MODEL / LLM_MODEL / LLM_VISION_MODEL env vars
# (LLM_MODEL is the same variable config.settings exposes as Settings.llm_model).
_GEMINI_MODEL_DEFAULT    = "gemini-2.0-flash"
_ANTHROPIC_MODEL_DEFAULT = "claude-haiku-4-5-20251001"
_VISION_MODEL_DEFAULT    = "claude-sonnet-4-6"

# Hard cap on any single LLM call so a hung provider can't stall the decision
# pipeline; on timeout we fall back to keyword scoring like any other failure.
_LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "30"))

# Substrings that mark an authentication/authorization failure — a bad/expired
# key that retrying can NEVER fix (unlike a timeout or a 429 quota blip).
# NOTE: do NOT use bare "401"/"403" — they false-match digit runs inside project
# IDs / numeric values in error payloads (that mislabelled a 429 as "key rejected").
_AUTH_MARKERS = (
    "unauthenticated", "permission_denied", "permission denied", "unauthorized",
    "forbidden", "api key not valid", "invalid api key", "invalid authentication",
    "access_token_type_unsupported", "api_key_invalid", "api key expired",
    "expired api key", "missing api key",
)
# Quota / rate-limit markers — the key is VALID, just throttled. Transient: never
# disable the provider, and never call it an auth error.
_QUOTA_MARKERS = (
    "resource_exhausted", "quota", "rate limit", "ratelimit",
    "too many requests", "exceeded your",
)


def _is_quota_error(exc: object) -> bool:
    return any(m in str(exc).lower() for m in _QUOTA_MARKERS)

# Providers whose key has been rejected this process; keyed by provider+fingerprint
# so a fixed key (after restart) starts clean. Shared across all LLMAdapter
# instances so one bad key disables it everywhere instead of every agent retrying.
_disabled_providers: set[str] = set()


def _is_auth_error(exc: object) -> bool:
    s = str(exc).lower()
    if any(m in s for m in _QUOTA_MARKERS):   # quota/rate-limit is NOT an auth failure
        return False
    return any(m in s for m in _AUTH_MARKERS)


def _fingerprint(provider: str, key: str) -> str:
    return f"{provider}:{key[:6]}" if key else provider


class LLMAdapter:
    """Provider-agnostic async LLM wrapper.

    Automatically selects the best available provider based on env keys:
      1. GEMINI_API_KEY   → Google Gemini Flash (free tier, text + vision)
      2. ANTHROPIC_API_KEY → Anthropic Claude Haiku (paid, text + vision)
      3. None             → returns None (caller falls back to keywords)
    """

    def __init__(
        self,
        gemini_key:    str = "",
        anthropic_key: str = "",
        *,
        gemini_model:    str = "",
        anthropic_model: str = "",
        vision_model:    str = "",
    ) -> None:
        self.gemini_key    = gemini_key    or os.getenv("GEMINI_API_KEY",    "")
        self.anthropic_key = anthropic_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.gemini_model    = gemini_model    or os.getenv("GEMINI_MODEL", _GEMINI_MODEL_DEFAULT)
        self.anthropic_model = anthropic_model or os.getenv("LLM_MODEL", _ANTHROPIC_MODEL_DEFAULT)
        self.vision_model    = vision_model    or os.getenv("LLM_VISION_MODEL", _VISION_MODEL_DEFAULT)

    def _disabled(self, provider: str) -> bool:
        key = self.gemini_key if provider == "gemini" else self.anthropic_key
        return _fingerprint(provider, key) in _disabled_providers

    def _disable(self, provider: str, exc: object) -> None:
        """Mark a provider's key rejected (auth failure) and tell the operator."""
        key = self.gemini_key if provider == "gemini" else self.anthropic_key
        _disabled_providers.add(_fingerprint(provider, key))
        env = "GEMINI_API_KEY" if provider == "gemini" else "ANTHROPIC_API_KEY"
        health.report_issue(
            f"llm_auth:{provider}",
            f"{env} was rejected by the provider (auth error: {str(exc)[:80]}).",
            remediation=(f"Set a valid {env} and restart. Until then the LLM agents "
                         "fall back to keyword/FinBERT scoring."),
        )

    @staticmethod
    def _report_quota(provider: str, exc: object) -> None:
        """Quota/rate-limit hit — the key is valid, just throttled. Don't disable."""
        health.report_issue(
            f"llm_quota:{provider}",
            f"{provider.title()} API quota / rate limit reached — the key is valid, "
            "just throttled.",
            remediation="LLM agents use keyword/FinBERT fallback until it resets; "
                        "raise your plan limit (or wait for the daily reset) to remove the cap.",
            severity="warning",
        )

    @property
    def has_llm(self) -> bool:
        return bool((self.gemini_key and not self._disabled("gemini"))
                    or (self.anthropic_key and not self._disabled("anthropic")))

    @property
    def has_vision(self) -> bool:
        return self.has_llm

    @property
    def provider(self) -> str:
        if self.gemini_key and not self._disabled("gemini"):
            return "gemini"
        if self.anthropic_key and not self._disabled("anthropic"):
            return "anthropic"
        return "none"

    # ── Text chat ─────────────────────────────────────────────────────────────

    async def chat(self, prompt: str, system: str = "") -> Optional[str]:
        """Send a text prompt and return the response string, or None on failure.

        Tries Gemini, then falls over to Anthropic; skips a provider whose key
        has already been rejected this run."""
        if self.gemini_key and not self._disabled("gemini"):
            out = await self._gemini_chat(prompt, system)
            if out is not None:
                return out
        if self.anthropic_key and not self._disabled("anthropic"):
            return await self._anthropic_chat(prompt, system)
        return None

    # ── Vision ────────────────────────────────────────────────────────────────

    async def vision(self, image_bytes: bytes, prompt: str, media_type: str = "image/png") -> Optional[str]:
        """Send an image + prompt and return the response string, or None on failure."""
        if self.gemini_key and not self._disabled("gemini"):
            out = await self._gemini_vision(image_bytes, prompt, media_type)
            if out is not None:
                return out
        if self.anthropic_key and not self._disabled("anthropic"):
            return await self._anthropic_vision(image_bytes, prompt, media_type)
        return None

    # ── Gemini implementation ─────────────────────────────────────────────────

    async def _gemini_chat(self, prompt: str, system: str) -> Optional[str]:
        for attempt in range(3):
            try:
                from google import genai  # type: ignore
                client = genai.Client(api_key=self.gemini_key)
                contents = (system + "\n\n" + prompt).strip() if system else prompt
                resp = await asyncio.wait_for(
                    asyncio.to_thread(
                        client.models.generate_content,
                        model=self.gemini_model,
                        contents=contents,
                    ),
                    timeout=_LLM_TIMEOUT_S,
                )
                text = (resp.text or "").strip()
                if text:
                    return text
                if attempt < 2:
                    await asyncio.sleep(1.0 * (attempt + 1))
            except Exception as exc:
                if _is_auth_error(exc):
                    self._disable("gemini", exc)   # bad key — retrying can't help
                    return None
                if _is_quota_error(exc):
                    self._report_quota("gemini", exc)  # valid key, throttled — fall back now
                    return None
                logger.warning("Gemini chat attempt %d failed: %s", attempt + 1, exc)
                if attempt < 2:
                    await asyncio.sleep(1.0 * (attempt + 1))
        return None

    async def _gemini_vision(self, image_bytes: bytes, prompt: str, media_type: str) -> Optional[str]:
        for attempt in range(3):
            try:
                from google import genai  # type: ignore
                from google.genai import types as genai_types  # type: ignore
                client = genai.Client(api_key=self.gemini_key)
                image_part = genai_types.Part.from_bytes(data=image_bytes, mime_type=media_type)
                resp = await asyncio.wait_for(
                    asyncio.to_thread(
                        client.models.generate_content,
                        model=self.gemini_model,
                        contents=[prompt, image_part],
                    ),
                    timeout=_LLM_TIMEOUT_S,
                )
                text = (resp.text or "").strip()
                if text:
                    return text
                if attempt < 2:
                    await asyncio.sleep(1.0 * (attempt + 1))
            except Exception as exc:
                if _is_auth_error(exc):
                    self._disable("gemini", exc)
                    return None
                if _is_quota_error(exc):
                    self._report_quota("gemini", exc)
                    return None
                logger.warning("Gemini vision attempt %d failed: %s", attempt + 1, exc)
                if attempt < 2:
                    await asyncio.sleep(1.0 * (attempt + 1))
        return None

    # ── Anthropic implementation ──────────────────────────────────────────────

    async def _anthropic_chat(self, prompt: str, system: str) -> Optional[str]:
        try:
            import anthropic  # type: ignore
            client = anthropic.AsyncAnthropic(api_key=self.anthropic_key, timeout=_LLM_TIMEOUT_S)
            kwargs: dict = dict(
                model=self.anthropic_model,
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            if system:
                kwargs["system"] = system
            resp = await client.messages.create(**kwargs)
            return resp.content[0].text.strip()
        except Exception as exc:
            if _is_auth_error(exc):
                self._disable("anthropic", exc)
            elif _is_quota_error(exc):
                self._report_quota("anthropic", exc)
            else:
                logger.debug("Anthropic chat failed: %s", exc)
            return None

    async def _anthropic_vision(self, image_bytes: bytes, prompt: str, media_type: str) -> Optional[str]:
        try:
            import anthropic  # type: ignore
            b64 = base64.standard_b64encode(image_bytes).decode()
            client = anthropic.AsyncAnthropic(api_key=self.anthropic_key, timeout=_LLM_TIMEOUT_S)
            resp = await client.messages.create(
                model=self.vision_model,
                max_tokens=300,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                        {"type": "text", "text": prompt},
                    ],
                }],
            )
            return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        except Exception as exc:
            if _is_auth_error(exc):
                self._disable("anthropic", exc)
            elif _is_quota_error(exc):
                self._report_quota("anthropic", exc)
            else:
                logger.debug("Anthropic vision failed: %s", exc)
            return None
