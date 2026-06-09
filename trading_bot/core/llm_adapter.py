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
from typing import Optional

logger = logging.getLogger(__name__)

_GEMINI_MODEL    = "gemini-2.0-flash"
_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"


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
    ) -> None:
        self.gemini_key    = gemini_key    or os.getenv("GEMINI_API_KEY",    "")
        self.anthropic_key = anthropic_key or os.getenv("ANTHROPIC_API_KEY", "")

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
        try:
            import google.generativeai as genai  # type: ignore
            genai.configure(api_key=self.gemini_key)
            model = genai.GenerativeModel(
                _GEMINI_MODEL,
                system_instruction=system or None,
            )
            full = (system + "\n\n" + prompt).strip() if system and not hasattr(model, "_system_instruction") else prompt
            resp = await asyncio.to_thread(model.generate_content, full)
            return resp.text.strip()
        except Exception as exc:
            logger.debug("Gemini chat failed: %s", exc)
            return None

    async def _gemini_vision(self, image_bytes: bytes, prompt: str, media_type: str) -> Optional[str]:
        try:
            import google.generativeai as genai  # type: ignore
            from google.generativeai.types import Part  # type: ignore
            genai.configure(api_key=self.gemini_key)
            model = genai.GenerativeModel(_GEMINI_MODEL)
            image_part = {"mime_type": media_type, "data": image_bytes}
            resp = await asyncio.to_thread(model.generate_content, [prompt, image_part])
            return resp.text.strip()
        except Exception as exc:
            logger.debug("Gemini vision failed: %s", exc)
            return None

    # ── Anthropic implementation ──────────────────────────────────────────────

    async def _anthropic_chat(self, prompt: str, system: str) -> Optional[str]:
        try:
            import anthropic  # type: ignore
            client = anthropic.AsyncAnthropic(api_key=self.anthropic_key)
            kwargs: dict = dict(
                model=_ANTHROPIC_MODEL,
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
                model="claude-sonnet-4-6",
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
