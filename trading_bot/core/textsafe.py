"""Sanitize untrusted external text before it enters an LLM prompt.

News headlines/summaries are attacker-writable (anyone who can place a story
a feed picks up). A crafted headline containing newlines and instruction-like
text can escape its "- headline: summary" slot and masquerade as new prompt
sections. Structural sanitization — collapse whitespace, strip control
characters, bound length — keeps external text inside its slot. It does not
try to detect "bad words": the defense is that text cannot break the format,
and the code-level risk veto binds regardless of what the LLM concludes.
"""
from __future__ import annotations

import re

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")   # includes \n, \r, \t
_WS      = re.compile(r"\s{2,}")


def sanitize_snippet(text: object, *, max_len: int = 200) -> str:
    """One safe, single-line snippet of untrusted text for prompt interpolation."""
    s = str(text or "")
    s = _CONTROL.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    if len(s) > max_len:
        s = s[: max_len - 1].rstrip() + "…"
    return s
