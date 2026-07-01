"""One-time AI cleanup for HeyReach fallback messages that lost a custom variable.

FIRST_NAME/COMPANY have deterministic fallback text ("there" / "your company"), so
to_heyreach_message() never needs AI help for those. But a custom variable like
{PERSONALIZED_FIRST_LINE} mid-sentence has no safe generic replacement — stripping
it can leave a run-on or double space. Rather than call an LLM per lead (expensive,
non-deterministic, adds latency to every send), this runs once per campaign at
HeyReach-creation time and the result is baked into the sequence sent to HeyReach.

Cost control:
- Uses Haiku (cheapest tier), not the app's configured ANTHROPIC_MODEL, since this
  is a single-sentence cleanup, not a creative writing task.
- Only called when a heuristic detects a stripped-variable artifact (double space,
  leading punctuation, orphaned comma) — most bodies never trigger a call.
- Identical fallback text is cached and only rewritten once per build_linkedin_sequence
  call, mirroring the spintax_service dedupe pattern.
"""

import re

from anthropic import Anthropic

from app.config import settings

_HAIKU_MODEL = "claude-haiku-4-5-20251001"

# Signs a variable was stripped and left the fallback looking broken: doubled spaces,
# a comma/period with nothing meaningful before it, or a line that starts with punctuation.
_BROKEN_FALLBACK_RE = re.compile(r"  +|^[,.!?]|(?<=\n)[,.!?]|\s[,.!?]{2,}")

_REWRITE_SYSTEM_PROMPT = """You clean up a single fallback message for a LinkedIn outreach tool. \
The fallback is shown when a personalization variable (like a custom opening line) could not be \
generated for a lead — you already removed that variable's placeholder text, and the sentence now \
reads awkwardly (double spaces, orphaned punctuation, a run-on).

Rewrite ONLY to fix the awkwardness left by the removed variable. Do not add new claims, change the \
offer, or alter the tone. Keep every other sentence exactly as-is. Keep it short — this is a fallback, \
not a rewrite exercise. Output ONLY the corrected fallback text, no commentary, no quotes."""


def looks_broken(fallback: str) -> bool:
    """Heuristic: did stripping an unknown variable leave visible damage?"""
    return bool(_BROKEN_FALLBACK_RE.search(fallback or ""))


def rewrite_fallback(fallback: str) -> str:
    """One Haiku call to smooth a broken fallback. Caller should cache/dedupe."""
    client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=_HAIKU_MODEL,
        max_tokens=300,
        system=_REWRITE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": fallback}],
    )
    return response.content[0].text.strip()


def clean_fallbacks_in_sequence(sequence: dict) -> tuple[dict, int]:
    """Walk a HeyReach sequence tree, rewriting any MESSAGE/CONNECTION_REQUEST
    fallbackMessage that looks broken. Returns (sequence, ai_calls_made).

    Identical fallback strings are deduped via cache so a 3-message sequence with
    the same broken pattern only costs one API call, not three.
    """
    cache: dict[str, str] = {}
    calls = 0

    def walk(node):
        nonlocal calls
        if not isinstance(node, dict):
            return
        payload = node.get("payload")
        if isinstance(payload, dict) and "fallbackMessage" in payload:
            fb = payload["fallbackMessage"]
            if fb and looks_broken(fb):
                if fb in cache:
                    payload["fallbackMessage"] = cache[fb]
                else:
                    rewritten = rewrite_fallback(fb)
                    cache[fb] = rewritten
                    payload["fallbackMessage"] = rewritten
                    calls += 1
        for key in ("conditionalNode", "unconditionalNode"):
            walk(node.get(key))

    walk(sequence)
    return sequence, calls
