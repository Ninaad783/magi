"""
composer.py — LLM-powered message composition engine for the Vera bot.

Supports Groq (default), Gemini, Anthropic, and OpenAI providers.
Post-LLM validation catches bad CTA shapes, URLs, and empty bodies.
Re-prompts once on failure.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
import urllib.error
from typing import Optional

from prompts import route, SYSTEM_BASE

# ---------------------------------------------------------------------------#
# Configuration                                                               #
# ---------------------------------------------------------------------------#

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", os.environ.get("LLM_API_KEY", ""))
GROQ_MODEL = os.environ.get("VERA_MODEL", "qwen/qwen3.6-27b")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

MAX_TOKENS = 800
TEMPERATURE = 0.0  # deterministic


# ---------------------------------------------------------------------------#
# Multi-provider LLM call                                                     #
# ---------------------------------------------------------------------------#

def _call_groq(system: str, user: str) -> str:
    """Call Groq API using standard urllib (zero dependencies)."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})

    payload = {
        "model": GROQ_MODEL,
        "messages": messages,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS
    }

    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
    )

    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"].strip()


def _call_gemini(system: str, user: str) -> str:
    """Call Google Gemini API."""
    full_prompt = f"{system}\n\n{user}" if system else user
    payload = {
        "contents": [{"parts": [{"text": full_prompt}]}],
        "generationConfig": {"temperature": TEMPERATURE, "maxOutputTokens": MAX_TOKENS}
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()


def _call_anthropic(system: str, user: str) -> str:
    """Call Anthropic Claude API."""
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return response.content[0].text.strip()


def _call_llm(system: str, user: str) -> str:
    """Route to available provider."""
    if GROQ_API_KEY:
        return _call_groq(system, user)
    elif GEMINI_API_KEY:
        return _call_gemini(system, user)
    elif ANTHROPIC_API_KEY:
        return _call_anthropic(system, user)
    else:
        raise RuntimeError("No LLM API key set. Set GROQ_API_KEY, GEMINI_API_KEY, or ANTHROPIC_API_KEY.")


# ---------------------------------------------------------------------------#
# JSON extraction + validation                                                #
# ---------------------------------------------------------------------------#

VALID_CTA_TYPES = {
    "open_ended",
    "binary_yes_no",
    "binary_confirm_cancel",
    "none",
    "multi_choice_slot",
}

VALID_SEND_AS = {"vera", "merchant_on_behalf"}

URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)


def _extract_json(raw: str) -> dict:
    """Extract JSON from LLM response (strips thinking tags and code fences)."""
    # Remove thinking tags if model uses <think>...</think>
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    # Strip markdown code fences
    raw = re.sub(r"```(?:json)?\s*", "", raw)
    raw = re.sub(r"```\s*$", "", raw)
    raw = raw.strip()
    # Find the first {...} block
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in LLM response: {raw[:200]}")
    return json.loads(match.group(0))


def _validate(result: dict, last_body: Optional[str] = None) -> list[str]:
    """Return a list of validation errors (empty = valid)."""
    errors = []
    body = result.get("body", "").strip()
    if not body:
        errors.append("body is empty")
    if len(body) < 10:
        errors.append(f"body too short ({len(body)} chars)")
    if URL_PATTERN.search(body):
        errors.append("body contains a URL — not allowed in WhatsApp template messages")
    if result.get("cta") not in VALID_CTA_TYPES:
        errors.append(f"invalid cta: {result.get('cta')!r} — must be one of {VALID_CTA_TYPES}")
    if result.get("send_as") not in VALID_SEND_AS:
        errors.append(f"invalid send_as: {result.get('send_as')!r}")
    if not result.get("suppression_key"):
        errors.append("suppression_key is missing")
    if last_body and body == last_body:
        errors.append("body is identical to the last sent message (anti-repetition)")
    return errors


def _fix_prompt(original_user: str, errors: list[str]) -> str:
    """Append error instructions to the prompt for a re-try."""
    error_text = "\n".join(f"- {e}" for e in errors)
    return (
        original_user
        + f"\n\nYour previous response had these issues — fix all of them:\n{error_text}"
        + "\n\nReturn ONLY the corrected JSON object."
    )


# ---------------------------------------------------------------------------#
# Public compose function                                                      #
# ---------------------------------------------------------------------------#

def compose(
    category: dict,
    merchant: dict,
    trigger: dict,
    customer: Optional[dict] = None,
    last_body: Optional[str] = None,
) -> dict:
    """
    Compose a message from the 4 contexts.

    Returns a dict with keys: body, cta, send_as, suppression_key, rationale.
    """
    system_prompt, user_prompt = route(category, merchant, trigger, customer)

    raw = _call_llm(system_prompt, user_prompt)
    try:
        result = _extract_json(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        # Try once more with stricter instructions
        retry_prompt = user_prompt + "\n\nReturn ONLY a valid JSON object, nothing else."
        raw = _call_llm(system_prompt, retry_prompt)
        result = _extract_json(raw)

    errors = _validate(result, last_body)
    if errors:
        retry_prompt = _fix_prompt(user_prompt, errors)
        try:
            raw = _call_llm(system_prompt, retry_prompt)
            result = _extract_json(raw)
        except (json.JSONDecodeError, ValueError):
            pass  # use original result
        errors_after = _validate(result, last_body)
        if errors_after:
            result["rationale"] = (
                result.get("rationale", "")
                + f" [validation warnings: {'; '.join(errors_after)}]"
            )

    # Ensure required keys are present with safe defaults
    result.setdefault("body", "")
    result.setdefault("cta", "open_ended")
    result.setdefault("send_as", "vera")
    result.setdefault("suppression_key", trigger.get("suppression_key", "auto"))
    result.setdefault("rationale", "Composed from trigger + merchant + category context.")

    return result
