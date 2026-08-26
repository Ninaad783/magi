"""
reply_handler.py — Multi-turn conversation intelligence for the Vera bot.

Handles:
- Auto-reply detection (canned WA Business responses)
- Hard-no / opt-out detection
- Intent transition (merchant says "yes let's do it" → switch to action mode)
- Hostile / off-topic handling
- Curveball questions (GST filing etc.) → polite redirect

All decisions are made locally (no LLM for detection — keeps response < 5s).
For the actual reply composition, we call back into composer.py.
"""
from __future__ import annotations

import re
import time
from typing import Optional

# ---------------------------------------------------------------------------#
# Pattern banks                                                               #
# ---------------------------------------------------------------------------#

# Canned auto-reply patterns (WhatsApp Business)
AUTO_REPLY_PATTERNS = [
    r"thank you for contacting",
    r"thanks for (reaching out|contacting|messaging)",
    r"our team will (respond|get back to you|contact you) (shortly|soon|within)",
    r"automated (message|reply|response)",
    r"we have received your (message|query|enquiry)",
    r"main ek automated assistant hoon",
    r"yeh ek automated (jawab|message|reply) hai",
    r"hum jald hi aapko (wapas|contact|reply) karenge",
    r"aapki jaankari ke liye.*shukriya.*team",
]

# Hard opt-out patterns
OPT_OUT_PATTERNS = [
    r"\b(stop|unsubscribe|opt.?out|remove me|don'?t (contact|message|text|call) me|not interested)\b",
    r"\b(band karo|band kar|rokh?\b|rokho|mat bhejo|mujhe mat|pareshan mat|bezati mat)\b",
    r"\b(blocking|block kar|block kr)\b",
    r"why are you (bothering|messaging|texting|spamming)",
    r"\buseless\b.*\bstop\b",
    r"\bstop messaging\b",
    r"\bstop sending\b",
]

# Positive intent / "let's do it" patterns
INTENT_YES_PATTERNS = [
    r"\b(ok|okay|yes|yeah|yep|haan|ha|sure|go ahead|let'?s do it|let'?s go|proceed|confirm|start)\b",
    r"\bkaro\b",
    r"\bchalu karo\b",
    r"\bshuru karo\b",
    r"\bwhat'?s next\b",
    r"\bhow do (i|we) (start|proceed|begin)\b",
    r"\bi'?m (in|ready|interested)\b",
    r"(send|draft|create|make|publish) (it|that|the (post|draft|campaign|message))",
]

# Off-topic / out-of-scope patterns
OUT_OF_SCOPE_PATTERNS = [
    r"\b(gst|income tax|tds|tcs|itr)\b",
    r"\b(loan|credit|emi|bank|insurance)\b",
    r"\b(visa|passport|immigration)\b",
    r"\b(legal|lawyer|court|lawsuit|case)\b",
    r"\b(astrology|horoscope|vastu)\b",
    r"\b(stock market|nifty|sensex|shares)\b",
]

# Abuse / frustration patterns
FRUSTRATION_PATTERNS = [
    r"\b(idiot|stupid|fool|bakwaas|chutiya|mc|bc|sala|saala|behen|harami|ullu)\b",
    r"\b(angry|frustrated|fed up|irritated|annoyed)\b.*\b(you|vera|magicpin)\b",
    r"\bwhy (the hell|tf|f)\b",
]


def _matches_any(text: str, patterns: list[str]) -> bool:
    text_lower = text.lower()
    return any(re.search(p, text_lower) for p in patterns)


def _is_auto_reply(message: str, conv_state: dict) -> bool:
    """Return True if this looks like a WA Business auto-reply."""
    if _matches_any(message, AUTO_REPLY_PATTERNS):
        return True
    # Same message seen 2+ times in this conversation
    turns = conv_state.get("turns", [])
    merchant_messages = [t["body"] for t in turns if t.get("from") == "merchant"]
    count = merchant_messages.count(message)
    return count >= 1  # if we've seen this exact message before in this conv


def _is_opt_out(message: str) -> bool:
    return _matches_any(message, OPT_OUT_PATTERNS)


def _is_frustration(message: str) -> bool:
    return _matches_any(message, FRUSTRATION_PATTERNS)


def _is_intent_yes(message: str) -> bool:
    return _matches_any(message, INTENT_YES_PATTERNS)


def _is_out_of_scope(message: str) -> bool:
    return _matches_any(message, OUT_OF_SCOPE_PATTERNS)


# ---------------------------------------------------------------------------#
# Reply decision logic                                                         #
# ---------------------------------------------------------------------------#

def decide_reply(
    message: str,
    from_role: str,
    turn_number: int,
    conv_state: dict,
    merchant: Optional[dict] = None,
    category: Optional[dict] = None,
    trigger: Optional[dict] = None,
    customer: Optional[dict] = None,
) -> dict:
    """
    Decide how to respond to an incoming message WITHOUT calling the LLM.
    Returns one of:
      {"action": "send", "body": ..., "cta": ..., "rationale": ...}  → compose a reply
      {"action": "wait", "wait_seconds": N, "rationale": ...}
      {"action": "end", "rationale": ...}
      {"action": "compose", ...}  → signal to main.py to call full composer

    The "compose" action means the situation warrants a full LLM-generated reply.
    """
    # 1. Hard opt-out / frustration → end immediately
    if _is_opt_out(message):
        return {
            "action": "end",
            "rationale": "Merchant explicitly opted out. Closing conversation and suppressing further outreach."
        }

    if _is_frustration(message):
        # Send a brief apology then end
        ident = (merchant or {}).get("identity", {})
        name = ident.get("owner_first_name", "")
        body = f"Apologies for the interruption{', ' + name if name else ''}. I won't message again. If anything changes, just reply 'Hi Vera'. 🙏"
        return {
            "action": "send",
            "body": body,
            "cta": "none",
            "rationale": "Merchant expressed frustration — one-line apology + polite exit. Conversation closes after this.",
            "_end_after_send": True,
        }

    # 2. Auto-reply detection
    auto_reply_streak = conv_state.get("auto_reply_streak", 0)
    if _is_auto_reply(message, conv_state):
        new_streak = auto_reply_streak + 1
        if new_streak == 1:
            return {
                "action": "wait",
                "wait_seconds": 14400,  # 4 hours
                "rationale": "Detected WA Business auto-reply. Backing off 4 hours for owner to see message.",
                "_auto_reply_streak": new_streak,
            }
        else:
            return {
                "action": "end",
                "rationale": f"Auto-reply detected {new_streak}x in a row. Closing conversation.",
                "_auto_reply_streak": new_streak,
            }

    # 3. Out-of-scope ask → polite redirect
    if _is_out_of_scope(message):
        # Figure out what the original topic was
        turns = conv_state.get("turns", [])
        vera_turns = [t["body"] for t in turns if t.get("from") == "vera"]
        original_topic = "the previous topic"
        if vera_turns:
            # Use first 60 chars of last vera message as topic hint
            original_topic = vera_turns[-1][:60] + "..."
        return {
            "action": "send",
            "body": "That's outside what I can help with directly — you'd need your CA or a specialist for that. Coming back to our conversation — want to continue where we left off?",
            "cta": "binary_yes_no",
            "rationale": "Out-of-scope ask politely declined. Redirecting back to original thread.",
        }

    # 4. Intent YES → signal to caller to switch to action mode
    if _is_intent_yes(message) and turn_number <= 4:
        return {"action": "compose", "intent": "confirmed", "rationale": "Merchant confirmed intent — switch to action mode immediately."}

    # 5. Default: compose a full LLM reply
    return {"action": "compose", "rationale": "Standard reply — compose via LLM."}


# ---------------------------------------------------------------------------#
# LLM-based reply composition                                                 #
# ---------------------------------------------------------------------------#

def build_reply_prompt(
    message: str,
    from_role: str,
    turn_number: int,
    conv_state: dict,
    merchant: Optional[dict],
    category: Optional[dict],
    trigger: Optional[dict],
    customer: Optional[dict],
    intent_confirmed: bool = False,
) -> tuple[str, str]:
    """Build a system + user prompt for replying to a merchant/customer message."""
    from prompts import SYSTEM_BASE, _merchant_summary, _category_summary, _j

    turns_text = ""
    for t in conv_state.get("turns", [])[-6:]:  # last 6 turns for context
        role_label = "Vera" if t["from"] == "vera" else ("Merchant" if from_role == "merchant" else "Customer")
        turns_text += f"[{role_label}]: {t['body']}\n"

    merchant_block = _merchant_summary(merchant) if merchant else "N/A"
    category_block = _category_summary(category) if category else "N/A"
    trigger_block = _j(trigger, 400) if trigger else "N/A"
    cust_block = ""
    if customer:
        ci = customer.get("identity", {})
        cust_block = f"\nCUSTOMER: {ci.get('name')} | state={customer.get('state')} | lang={ci.get('language_pref')}"

    action_instruction = ""
    if intent_confirmed:
        action_instruction = """
IMPORTANT: The merchant just said YES / confirmed intent. 
DO NOT ask another qualifying question.
Switch immediately to action mode: draft the thing they agreed to, propose the next concrete step.
"""

    user_prompt = f"""
CATEGORY CONTEXT:
{category_block}

MERCHANT CONTEXT:
{merchant_block}
{cust_block}

TRIGGER CONTEXT: {trigger_block}

CONVERSATION SO FAR:
{turns_text}
[{from_role.title()}]: {message}

TURN NUMBER: {turn_number}
{action_instruction}

TASK: Compose Vera's next reply to the {from_role}'s message above.

Guidelines:
- Build directly on what was said — no re-introduction.
- Keep it concise (2-4 sentences).
- Match language preference from merchant/customer identity.
- ONE clear CTA only.
- No URLs.
- cta options: open_ended | binary_yes_no | binary_confirm_cancel | none | multi_choice_slot
- send_as: "vera" for merchant conversations, "merchant_on_behalf" for customer conversations.
- suppression_key: use the original trigger suppression_key or generate "reply:<conversation context>".

Return ONLY a JSON object: body, cta, send_as, suppression_key, rationale.
"""
    return SYSTEM_BASE, user_prompt.strip()
