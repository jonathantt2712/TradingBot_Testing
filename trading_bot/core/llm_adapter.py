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

    @property
    def has_llm(self) -> bool:
        return bool(self.gemini_key or self.anthropic_key)

    @property
    def has_vision(self) -> bool:
        return bool(self.gemini_key or self.anthropic_key)

    @property
    def provider(self) -> str:
        if self.gemini_key:
            return "gemini"
        if self.anthropic_key:
            return "anthropic"
        return "none"

    # ── Text chat ─────────────────────────────────────────────────────────────

    async def chat(self, prompt: str, system: str = "") -> Optional[str]:
        """Send a text prompt and return the response string, or None on failure."""
        if self.gemini_key:
            return await self._gemini_chat(prompt, system)
        if self.anthropic_key:
            return await self._anthropic_chat(prompt, system)
        return None

    # ── Vision ────────────────────────────────────────────────────────────────

    async def vision(self, image_bytes: bytes, prompt: str, media_type: str = "image/png") -> Optional[str]:
        """Send an image + prompt and return the response string, or None on failure."""
        if self.gemini_key:
            return await self._gemini_vision(image_bytes, prompt, media_type)
        if self.anthropic_key:
            return await self._anthropic_vision(image_bytes, prompt, media_type)
        return None

    # ── Gemini implementation ─────────────────────────────────────────────────

    async def _gemini_chat(self, prompt: str, system: str) -> Optional[str]:
        for attempt in range(3):
            try:
                from google import genai  # type: ignore
                client = genai.Client(api_key=self.gemini_key)
                contents = (system + "\n\n" + prompt).strip() if system else prompt
                resp = await asyncio.to_thread(
                    client.models.generate_content,
                    model=self.gemini_model,
                    contents=contents,
                )
                text = (resp.text or "").strip()
                if text:
                    return text
                if attempt < 2:
                    await asyncio.sleep(1.0 * (attempt + 1))
            except Exception as exc:
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
                resp = await asyncio.to_thread(
                    client.models.generate_content,
                    model=self.gemini_model,
                    contents=[prompt, image_part],
                )
                text = (resp.text or "").strip()
                if text:
                    return text
                if attempt < 2:
                    await asyncio.sleep(1.0 * (attempt + 1))
            except Exception as exc:
                logger.warning("Gemini vision attempt %d failed: %s", attempt + 1, exc)
                if attempt < 2:
                    await asyncio.sleep(1.0 * (attempt + 1))
        return None

    # ── Anthropic implementation ──────────────────────────────────────────────

    async def _anthropic_chat(self, prompt: str, system: str) -> Optional[str]:
        try:
            import anthropic  # type: ignore
            client = anthropic.AsyncAnthropic(api_key=self.anthropic_key)
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
            logger.debug("Anthropic chat failed: %s", exc)
            return None

    async def _anthropic_vision(self, image_bytes: bytes, prompt: str, media_type: str) -> Optional[str]:
        try:
            import anthropic  # type: ignore
            b64 = base64.standard_b64encode(image_bytes).decode()
            client = anthropic.AsyncAnthropic(api_key=self.anthropic_key)
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
            logger.debug("Anthropic vision failed: %s", exc)
            return None
