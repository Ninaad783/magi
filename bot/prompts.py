"""
prompts.py — Per-trigger-kind prompt templates for the Vera bot.

Each template is a function that returns a fully-formed system + user prompt
tuple. The LLM is Claude (temperature=0). Prompts are designed to score 9-10
on each of the 5 judge dimensions:
  1. Specificity      — anchor on real numbers / dates / citations from context
  2. Category fit     — match the voice profile (clinical, warm, utility-first, etc.)
  3. Merchant fit     — personalise to this merchant's exact state
  4. Trigger relevance — make WHY NOW crystal-clear
  5. Engagement compulsion — single low-friction CTA, one strong lever
"""
from __future__ import annotations

import json
from typing import Optional


# ---------------------------------------------------------------------------#
# Shared system prompt (injected before every message)                       #
# ---------------------------------------------------------------------------#

SYSTEM_BASE = """You are Vera, magicpin's AI assistant for merchant growth. \
You send WhatsApp messages to merchants (or their customers) to help them grow \
their business.

## Hard rules
1. ONE clear CTA per message — never more.
2. Use the CTA type specified in the prompt (binary YES/STOP, open-ended, none, binary_confirm_cancel).
3. No URLs in the message body.
4. No fabricated data — only cite numbers, offers, dates, and research that \
   are present in the context JSON provided.
5. Keep the message concise (3-6 sentences) and natural for WhatsApp.
6. Match the merchant's language preference. If languages include "hi", use \
   Hindi-English code-mix (Hinglish) naturally. Pure English only if languages \
   is ["en"] only.
7. Address merchants by their owner_first_name (e.g. "Dr. Meera", "Lakshmi"). \
   Address customers by their first name.
8. Never use: "guaranteed", "100% safe", "best in city", "miracle".
9. No preamble like "I hope you're doing well" or "I'm reaching out today".
10. Never repeat the last message body verbatim.

## Output format
Return ONLY a JSON object with these keys:
{
  "body": "<the WhatsApp message text>",
  "cta": "<open_ended | binary_yes_no | binary_confirm_cancel | none | multi_choice_slot>",
  "send_as": "<vera | merchant_on_behalf>",
  "suppression_key": "<the suppression key string>",
  "rationale": "<1-2 sentence explanation of your choices>"
}
"""


# ---------------------------------------------------------------------------#
# Helper: truncate JSON for prompt (avoid token overflow)                    #
# ---------------------------------------------------------------------------#

def _j(obj, maxlen: int = 2000) -> str:
    s = json.dumps(obj, ensure_ascii=False, indent=None)
    if len(s) > maxlen:
        s = s[:maxlen] + "... [truncated]"
    return s


def _merchant_summary(merchant: dict) -> str:
    """Build a compact merchant fact block for prompt injection."""
    ident = merchant.get("identity", {})
    perf = merchant.get("performance", {})
    offers = merchant.get("offers", [])
    conv = merchant.get("conversation_history", [])
    agg = merchant.get("customer_aggregate", {})
    subs = merchant.get("subscription", {})
    signals = merchant.get("signals", [])
    review_themes = merchant.get("review_themes", [])

    active_offers = [o["title"] for o in offers if o.get("status") == "active"]
    last_conv = conv[-1] if conv else None

    lines = [
        f"Merchant: {ident.get('name')} | Owner: {ident.get('owner_first_name')}",
        f"City: {ident.get('city')}, {ident.get('locality')} | Languages: {ident.get('languages')}",
        f"Verified GBP: {ident.get('verified')} | Subscription: {subs.get('status')} ({subs.get('plan')}, {subs.get('days_remaining', 0)} days remaining)",
        f"30-day perf: views={perf.get('views')}, calls={perf.get('calls')}, CTR={perf.get('ctr')} | 7d delta: {perf.get('delta_7d')}",
        f"Active offers: {active_offers if active_offers else 'None'}",
        f"Customer aggregate: {_j(agg, 300)}",
        f"Signals: {signals}",
        f"Review themes: {review_themes}",
        f"Last Vera touch: {_j(last_conv, 400) if last_conv else 'None'}",
    ]
    return "\n".join(lines)


def _category_summary(category: dict) -> str:
    """Build a compact category fact block."""
    voice = category.get("voice", {})
    peer = category.get("peer_stats", {})
    digest = category.get("digest", [])
    seasonal = category.get("seasonal_beats", [])
    trends = category.get("trend_signals", [])

    lines = [
        f"Category: {category.get('slug')} | Tone: {voice.get('tone')} | Register: {voice.get('register')}",
        f"Allowed vocab: {voice.get('vocab_allowed', [])}",
        f"Taboo words: {voice.get('vocab_taboo', [])}",
        f"Peer stats: {_j(peer, 300)}",
        f"Latest digest items: {_j(digest[:3], 600)}",
        f"Seasonal beats: {seasonal}",
        f"Trend signals: {trends}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------#
# PROMPT BUILDERS — one per trigger.kind                                     #
# ---------------------------------------------------------------------------#

def build_research_digest_prompt(category: dict, merchant: dict, trigger: dict, category_slug: str) -> str:
    """research_digest, cde_opportunity, regulation_change"""
    tpayload = trigger.get("payload", {})
    top_item_id = tpayload.get("top_item_id") or tpayload.get("digest_item_id")
    digest_items = category.get("digest", [])
    item = next((d for d in digest_items if d.get("id") == top_item_id), None)
    if not item and digest_items:
        item = digest_items[0]

    kind = trigger.get("kind", "research_digest")
    suppression_key = trigger.get("suppression_key", f"research:{category_slug}:auto")

    prompt = f"""
CATEGORY CONTEXT:
{_category_summary(category)}

MERCHANT CONTEXT:
{_merchant_summary(merchant)}

TRIGGER: kind={kind} | urgency={trigger.get('urgency')} | suppression_key={suppression_key}
Digest item to use: {_j(item, 600) if item else 'Use the most relevant digest item from the category.'}

TASK: Compose a Vera → merchant WhatsApp message about this research/compliance/CDE item.

Guidelines for this trigger kind:
- Lead with the specific finding: trial size, percentage, source page number.
- Connect it to THIS merchant's patient mix or signals (e.g. "your high-risk adult cohort").
- CTA: offer to pull the abstract, draft a patient-ed post, or help with compliance action. Ask once, binary.
- Tone: peer_clinical. You are one colleague sharing a useful finding with another.
- CTA type: "open_ended"
- send_as: "vera"
- suppression_key: "{suppression_key}"

Return the JSON object only.
"""
    return prompt.strip()


def build_perf_dip_prompt(category: dict, merchant: dict, trigger: dict) -> str:
    tpayload = trigger.get("payload", {})
    metric = tpayload.get("metric", "calls")
    delta_pct = tpayload.get("delta_pct", -0.3)
    window = tpayload.get("window", "7d")
    is_seasonal = tpayload.get("is_expected_seasonal", False)
    season_note = tpayload.get("season_note", "")
    suppression_key = trigger.get("suppression_key", "perf_dip:auto")

    prompt = f"""
CATEGORY CONTEXT:
{_category_summary(category)}

MERCHANT CONTEXT:
{_merchant_summary(merchant)}

TRIGGER: kind=perf_dip | metric={metric} | delta={delta_pct*100:.0f}% over {window}
is_expected_seasonal={is_seasonal} | season_note={season_note}
suppression_key={suppression_key}

TASK: Compose a Vera → merchant WhatsApp message about a performance dip.

Guidelines:
- State the exact number and change (e.g. "calls dropped 50% this week").
- If is_expected_seasonal=True, lead with the reframe: "this is normal — here's what to do instead of panicking".
- Propose one concrete action tied to their current signals or offers.
- CTA: binary YES/NO action proposal.
- send_as: "vera"
- suppression_key: "{suppression_key}"

Return JSON only.
"""
    return prompt.strip()


def build_perf_spike_prompt(category: dict, merchant: dict, trigger: dict) -> str:
    tpayload = trigger.get("payload", {})
    metric = tpayload.get("metric", "views")
    delta_pct = tpayload.get("delta_pct", 0.2)
    likely_driver = tpayload.get("likely_driver", "")
    suppression_key = trigger.get("suppression_key", "perf_spike:auto")

    prompt = f"""
CATEGORY CONTEXT:
{_category_summary(category)}

MERCHANT CONTEXT:
{_merchant_summary(merchant)}

TRIGGER: kind=perf_spike | metric={metric} | delta=+{delta_pct*100:.0f}% | driver={likely_driver}
suppression_key={suppression_key}

TASK: Compose a Vera → merchant WhatsApp celebrating the spike and proposing to capitalise on it.

Guidelines:
- Start with the concrete number and the likely driver.
- Propose one action to convert the spike into more bookings/calls (e.g. "Want me to post a campaign now?").
- Keep the tone warm and energetic — but not over-the-top.
- CTA: "binary_yes_no"
- send_as: "vera"
- suppression_key: "{suppression_key}"

Return JSON only.
"""
    return prompt.strip()


def build_recall_due_prompt(category: dict, merchant: dict, trigger: dict, customer: dict) -> str:
    tpayload = trigger.get("payload", {})
    service_due = tpayload.get("service_due", "recall")
    available_slots = tpayload.get("available_slots", [])
    cust_ident = customer.get("identity", {})
    cust_rel = customer.get("relationship", {})
    cust_state = customer.get("state", "lapsed_soft")
    lang_pref = cust_ident.get("language_pref", "en")
    slot_labels = [s.get("label") for s in available_slots if s.get("label")]
    merchant_offers = [o["title"] for o in merchant.get("offers", []) if o.get("status") == "active"]
    suppression_key = trigger.get("suppression_key", f"recall:{customer.get('customer_id')}:auto")

    prompt = f"""
CATEGORY CONTEXT:
{_category_summary(category)}

MERCHANT CONTEXT:
{_merchant_summary(merchant)}

CUSTOMER CONTEXT:
Name: {cust_ident.get('name')} | Language pref: {lang_pref} | State: {cust_state}
Last visit: {cust_rel.get('last_visit')} | Visits total: {cust_rel.get('visits_total')}
Services received: {cust_rel.get('services_received', [])}
Preferences: {customer.get('preferences', {})}
Consent scope: {customer.get('consent', {}).get('scope', [])}

TRIGGER: kind=recall_due | service_due={service_due}
Available slots: {slot_labels}
suppression_key={suppression_key}

TASK: Compose a message from the MERCHANT to this CUSTOMER (send_as=merchant_on_behalf).
The customer has opted in to recall_reminders.

Guidelines:
- Address the customer by their first name.
- State the recall reason clearly but warmly (not clinical-sounding).
- Offer 2 specific slots if available. Use the "Reply 1 / Reply 2" format.
- Include the active offer price if relevant.
- Language: match lang_pref — for "hi-en mix", weave Hindi phrases naturally.
- CTA: "multi_choice_slot" if slots available, else "binary_yes_no"
- send_as: "merchant_on_behalf"
- suppression_key: "{suppression_key}"

Return JSON only.
"""
    return prompt.strip()


def build_chronic_refill_prompt(category: dict, merchant: dict, trigger: dict, customer: dict) -> str:
    tpayload = trigger.get("payload", {})
    molecules = tpayload.get("molecule_list", [])
    runs_out = tpayload.get("stock_runs_out_iso", "")
    delivery_saved = tpayload.get("delivery_address_saved", False)
    cust_ident = customer.get("identity", {})
    merchant_offers = [o["title"] for o in merchant.get("offers", []) if o.get("status") == "active"]
    suppression_key = trigger.get("suppression_key", f"refill:{customer.get('customer_id')}:auto")
    lang_pref = cust_ident.get("language_pref", "en")

    prompt = f"""
CATEGORY CONTEXT:
{_category_summary(category)}

MERCHANT CONTEXT:
{_merchant_summary(merchant)}

CUSTOMER CONTEXT:
Name: {cust_ident.get('name')} | Language pref: {lang_pref} | Age band: {cust_ident.get('age_band', '')}
Active medicines: {molecules}
Stock runs out: {runs_out} | Delivery address saved: {delivery_saved}

TRIGGER: kind=chronic_refill_due
Active offers: {merchant_offers}
suppression_key={suppression_key}

TASK: Compose a message from the PHARMACY to this CUSTOMER (send_as=merchant_on_behalf).

Guidelines:
- Be precise about molecule names (all of them), run-out date.
- Mention the active offer discount (e.g. Senior 15% OFF) and final price if computable from context.
- Offer home delivery if delivery_address_saved=True.
- For elderly customers: use respectful salutation (Namaste / "ji" suffix).
- CTA: "binary_confirm_cancel" — "Reply CONFIRM to dispatch"
- send_as: "merchant_on_behalf"
- suppression_key: "{suppression_key}"

Return JSON only.
"""
    return prompt.strip()


def build_festival_upcoming_prompt(category: dict, merchant: dict, trigger: dict) -> str:
    tpayload = trigger.get("payload", {})
    festival = tpayload.get("festival", "")
    days_until = tpayload.get("days_until", 0)
    date_str = tpayload.get("date", "")
    suppression_key = trigger.get("suppression_key", f"festival:{festival}:auto")
    merchant_offers = [o["title"] for o in merchant.get("offers", []) if o.get("status") == "active"]

    prompt = f"""
CATEGORY CONTEXT:
{_category_summary(category)}

MERCHANT CONTEXT:
{_merchant_summary(merchant)}

TRIGGER: kind=festival_upcoming | festival={festival} | date={date_str} | days_until={days_until}
suppression_key={suppression_key}

TASK: Compose a Vera → merchant message about upcoming {festival} opportunity.

Guidelines:
- Be specific about the festival, the date, and how many days remain.
- Tie it to this category's typical demand pattern at this time (e.g. salons = bridal/party bookings, restaurants = group dining).
- Propose one concrete action (create a festival offer, GBP post, WhatsApp campaign).
- Use existing active offers if present: {merchant_offers}.
- CTA: "binary_yes_no"
- send_as: "vera"
- suppression_key: "{suppression_key}"

Return JSON only.
"""
    return prompt.strip()


def build_ipl_match_prompt(category: dict, merchant: dict, trigger: dict) -> str:
    tpayload = trigger.get("payload", {})
    match = tpayload.get("match", "")
    venue = tpayload.get("venue", "")
    match_time = tpayload.get("match_time_iso", "")
    is_weeknight = tpayload.get("is_weeknight", True)
    suppression_key = trigger.get("suppression_key", "ipl:auto")
    merchant_offers = [o["title"] for o in merchant.get("offers", []) if o.get("status") == "active"]

    prompt = f"""
CATEGORY CONTEXT:
{_category_summary(category)}

MERCHANT CONTEXT:
{_merchant_summary(merchant)}

TRIGGER: kind=ipl_match_today | match={match} | venue={venue} | time={match_time}
is_weeknight={is_weeknight}
suppression_key={suppression_key}

TASK: Compose a Vera → merchant message about today's IPL match.

Guidelines:
- If is_weeknight=False (weekend match), use the contrarian insight: Saturday/Sunday IPL matches often REDUCE restaurant footfall by ~12% (people watch at home); recommend delivery push instead of in-store promo.
- If is_weeknight=True, use the opportunity angle: match nights drive food delivery orders.
- Be specific: name the match, the venue, the time.
- Propose leveraging their existing active offers: {merchant_offers} if any.
- CTA: "binary_yes_no"
- send_as: "vera"
- suppression_key: "{suppression_key}"

Return JSON only.
"""
    return prompt.strip()


def build_active_planning_prompt(category: dict, merchant: dict, trigger: dict) -> str:
    """For active_planning_intent — merchant has already said YES, just draft it."""
    tpayload = trigger.get("payload", {})
    intent_topic = tpayload.get("intent_topic", "")
    last_msg = tpayload.get("merchant_last_message", "")
    suppression_key = trigger.get("suppression_key", "planning:auto")

    prompt = f"""
CATEGORY CONTEXT:
{_category_summary(category)}

MERCHANT CONTEXT:
{_merchant_summary(merchant)}

TRIGGER: kind=active_planning_intent | topic={intent_topic}
Merchant's last message: "{last_msg}"
suppression_key={suppression_key}

TASK: The merchant has already expressed intent. Do NOT qualify further. 
Switch immediately to action mode: draft the plan/artifact they asked for.

Guidelines:
- Produce a concrete, usable draft (e.g. pricing tiers, program structure, post copy).
- Include specific numbers (price, dates, counts) drawn from the merchant context.
- End with one low-friction next step.
- CTA: "binary_confirm_cancel" or "open_ended"
- send_as: "vera"
- suppression_key: "{suppression_key}"

Return JSON only.
"""
    return prompt.strip()


def build_winback_prompt(category: dict, merchant: dict, trigger: dict) -> str:
    tpayload = trigger.get("payload", {})
    days_since = tpayload.get("days_since_expiry", tpayload.get("days_since_last_merchant_message", 0))
    perf_dip = tpayload.get("perf_dip_pct", 0)
    suppression_key = trigger.get("suppression_key", "winback:auto")

    prompt = f"""
CATEGORY CONTEXT:
{_category_summary(category)}

MERCHANT CONTEXT:
{_merchant_summary(merchant)}

TRIGGER: kind=winback/dormant | days_since={days_since} | perf_dip={perf_dip}
suppression_key={suppression_key}

TASK: Re-engage a lapsed or dormant merchant. Keep it short, show what they're missing.

Guidelines:
- Acknowledge the gap without guilt-tripping.
- Lead with one concrete missed-opportunity number (e.g. "X customers searched for Y in your area").
- Propose one action to re-activate. Make it easy.
- CTA: "binary_yes_no"
- send_as: "vera"
- suppression_key: "{suppression_key}"

Return JSON only.
"""
    return prompt.strip()


def build_customer_lapsed_prompt(category: dict, merchant: dict, trigger: dict, customer: dict) -> str:
    tpayload = trigger.get("payload", {})
    days_since = tpayload.get("days_since_last_visit", 0)
    prev_focus = tpayload.get("previous_focus", "")
    months_member = tpayload.get("previous_membership_months", 0)
    cust_ident = customer.get("identity", {})
    merchant_offers = [o["title"] for o in merchant.get("offers", []) if o.get("status") == "active"]
    suppression_key = trigger.get("suppression_key", "winback:customer:auto")
    lang_pref = cust_ident.get("language_pref", "en")

    prompt = f"""
CATEGORY CONTEXT:
{_category_summary(category)}

MERCHANT CONTEXT:
{_merchant_summary(merchant)}

CUSTOMER CONTEXT:
Name: {cust_ident.get('name')} | Language pref: {lang_pref}
Days since last visit: {days_since} | Previous focus: {prev_focus}
Membership duration: {months_member} months

TRIGGER: kind=customer_lapsed | days_since_last_visit={days_since}
Active offers: {merchant_offers}
suppression_key={suppression_key}

TASK: Compose a warm winback message from the MERCHANT to this CUSTOMER.

Guidelines:
- No guilt-tripping. Warm, no-judgment tone ("it happens, we've got something new for you").
- Reference their previous focus ({prev_focus}) to personalise.
- Mention a specific new offering or class/service that fits their goal.
- Offer a free trial or no-commitment slot. Reply YES = low effort.
- CTA: "binary_yes_no"
- send_as: "merchant_on_behalf"
- suppression_key: "{suppression_key}"

Return JSON only.
"""
    return prompt.strip()


def build_competitor_prompt(category: dict, merchant: dict, trigger: dict) -> str:
    tpayload = trigger.get("payload", {})
    comp_name = tpayload.get("competitor_name", "a new competitor")
    distance = tpayload.get("distance_km", 0)
    their_offer = tpayload.get("their_offer", "")
    opened_date = tpayload.get("opened_date", "")
    suppression_key = trigger.get("suppression_key", "competitor:auto")
    merchant_offers = [o["title"] for o in merchant.get("offers", []) if o.get("status") == "active"]

    prompt = f"""
CATEGORY CONTEXT:
{_category_summary(category)}

MERCHANT CONTEXT:
{_merchant_summary(merchant)}

TRIGGER: kind=competitor_opened | competitor={comp_name} | distance={distance}km
Their offer: {their_offer} | Opened: {opened_date}
suppression_key={suppression_key}

TASK: Inform the merchant about the new competitor and help them respond.

Guidelines:
- Be factual. Name the competitor, distance, their offer (only if in context).
- Focus on differentiation: what does this merchant do better? (Reviews, retention, location).
- Propose one concrete counter-action (e.g. reactivate an expired offer, push a GBP post).
- Do NOT fabricate competitor weaknesses. Use only context data.
- CTA: "binary_yes_no"
- send_as: "vera"
- suppression_key: "{suppression_key}"

Return JSON only.
"""
    return prompt.strip()


def build_review_theme_prompt(category: dict, merchant: dict, trigger: dict) -> str:
    tpayload = trigger.get("payload", {})
    theme = tpayload.get("theme", "")
    occurrences = tpayload.get("occurrences_30d", 0)
    trend = tpayload.get("trend", "")
    common_quote = tpayload.get("common_quote", "")
    suppression_key = trigger.get("suppression_key", "review_theme:auto")

    prompt = f"""
CATEGORY CONTEXT:
{_category_summary(category)}

MERCHANT CONTEXT:
{_merchant_summary(merchant)}

TRIGGER: kind=review_theme_emerged | theme={theme} | occurrences_30d={occurrences} | trend={trend}
Common quote from customers: "{common_quote}"
suppression_key={suppression_key}

TASK: Alert the merchant about an emerging review theme and offer a fix.

Guidelines:
- State the theme clearly with the count (e.g. "4 reviews in last 30 days mention delivery time").
- Include the verbatim customer quote if provided.
- Propose one specific operational fix.
- CTA: "binary_yes_no" or "open_ended"
- send_as: "vera"
- suppression_key: "{suppression_key}"

Return JSON only.
"""
    return prompt.strip()


def build_milestone_prompt(category: dict, merchant: dict, trigger: dict) -> str:
    tpayload = trigger.get("payload", {})
    metric = tpayload.get("metric", "review_count")
    value_now = tpayload.get("value_now", 0)
    milestone = tpayload.get("milestone_value", 0)
    imminent = tpayload.get("is_imminent", False)
    suppression_key = trigger.get("suppression_key", "milestone:auto")

    prompt = f"""
CATEGORY CONTEXT:
{_category_summary(category)}

MERCHANT CONTEXT:
{_merchant_summary(merchant)}

TRIGGER: kind=milestone_reached | metric={metric} | current={value_now} | milestone={milestone}
imminent={imminent}
suppression_key={suppression_key}

TASK: Celebrate or build anticipation around an upcoming milestone.

Guidelines:
- If imminent (is_imminent=True), create urgency: "X reviews to your {milestone}-review milestone — want me to help you cross it this week?"
- If already reached: celebrate and propose next action (share on GBP, social post).
- CTA: "binary_yes_no"
- send_as: "vera"
- suppression_key: "{suppression_key}"

Return JSON only.
"""
    return prompt.strip()


def build_renewal_prompt(category: dict, merchant: dict, trigger: dict) -> str:
    tpayload = trigger.get("payload", {})
    days_rem = tpayload.get("days_remaining", 12)
    plan = tpayload.get("plan", "Pro")
    amount = tpayload.get("renewal_amount", 0)
    suppression_key = trigger.get("suppression_key", "renewal:auto")

    prompt = f"""
CATEGORY CONTEXT:
{_category_summary(category)}

MERCHANT CONTEXT:
{_merchant_summary(merchant)}

TRIGGER: kind=renewal_due | days_remaining={days_rem} | plan={plan} | amount=₹{amount}
suppression_key={suppression_key}

TASK: Renewal nudge — make the value concrete, not just a reminder.

Guidelines:
- Lead with what the merchant gets from the subscription (views, calls, leads in 30d from their perf data).
- State the days remaining and cost clearly.
- Propose renewal with a single binary CTA.
- Do NOT be pushy — one ask only.
- CTA: "binary_yes_no"
- send_as: "vera"
- suppression_key: "{suppression_key}"

Return JSON only.
"""
    return prompt.strip()


def build_gbp_unverified_prompt(category: dict, merchant: dict, trigger: dict) -> str:
    tpayload = trigger.get("payload", {})
    uplift = tpayload.get("estimated_uplift_pct", 0.30)
    verification_path = tpayload.get("verification_path", "postcard_or_phone_call")
    suppression_key = trigger.get("suppression_key", "unverified:auto")

    prompt = f"""
CATEGORY CONTEXT:
{_category_summary(category)}

MERCHANT CONTEXT:
{_merchant_summary(merchant)}

TRIGGER: kind=gbp_unverified | estimated_uplift={uplift*100:.0f}% | path={verification_path}
suppression_key={suppression_key}

TASK: Nudge merchant to verify their Google Business Profile.

Guidelines:
- Be specific about the expected impact (estimated uplift % from context).
- Explain the verification path briefly.
- CTA: "binary_yes_no" — "Want me to walk you through the verification steps?"
- send_as: "vera"
- suppression_key: "{suppression_key}"

Return JSON only.
"""
    return prompt.strip()


def build_curious_ask_prompt(category: dict, merchant: dict, trigger: dict) -> str:
    tpayload = trigger.get("payload", {})
    ask_template = tpayload.get("ask_template", "what_service_in_demand_this_week")
    suppression_key = trigger.get("suppression_key", "curious_ask:auto")

    prompt = f"""
CATEGORY CONTEXT:
{_category_summary(category)}

MERCHANT CONTEXT:
{_merchant_summary(merchant)}

TRIGGER: kind=curious_ask_due | ask_template={ask_template}
suppression_key={suppression_key}

TASK: Compose a short, curious, low-stakes question to engage the merchant.

Guidelines:
- Ask one simple question ("What service has been most asked for this week?")
- Offer to turn the answer into something useful (GBP post, WhatsApp reply template, etc.)
- Keep it under 3 sentences.
- CTA: "open_ended"
- send_as: "vera"
- suppression_key: "{suppression_key}"

Return JSON only.
"""
    return prompt.strip()


def build_supply_alert_prompt(category: dict, merchant: dict, trigger: dict) -> str:
    tpayload = trigger.get("payload", {})
    molecule = tpayload.get("molecule", "")
    batches = tpayload.get("affected_batches", [])
    manufacturer = tpayload.get("manufacturer", "")
    suppression_key = trigger.get("suppression_key", "supply_alert:auto")
    agg = merchant.get("customer_aggregate", {})
    chronic_count = agg.get("chronic_rx_count", 0)

    prompt = f"""
CATEGORY CONTEXT:
{_category_summary(category)}

MERCHANT CONTEXT:
{_merchant_summary(merchant)}

TRIGGER: kind=supply_alert | molecule={molecule} | batches={batches} | manufacturer={manufacturer}
Total chronic-Rx customers: {chronic_count}
suppression_key={suppression_key}

TASK: Alert pharmacist about a supply/recall issue.

Guidelines:
- Lead with urgency: molecule name, batch numbers, manufacturer.
- Derive how many of this merchant's chronic-Rx customers may be affected (if data allows).
- Offer to draft the customer notification + replacement workflow.
- Keep tone: trustworthy-precise, no alarm.
- CTA: "binary_yes_no"
- send_as: "vera"
- suppression_key: "{suppression_key}"

Return JSON only.
"""
    return prompt.strip()


def build_category_seasonal_prompt(category: dict, merchant: dict, trigger: dict) -> str:
    tpayload = trigger.get("payload", {})
    season = tpayload.get("season", "")
    trends = tpayload.get("trends", [])
    suppression_key = trigger.get("suppression_key", "seasonal:auto")

    prompt = f"""
CATEGORY CONTEXT:
{_category_summary(category)}

MERCHANT CONTEXT:
{_merchant_summary(merchant)}

TRIGGER: kind=category_seasonal | season={season} | trends={trends}
suppression_key={suppression_key}

TASK: Inform merchant about a seasonal demand shift and propose one action.

Guidelines:
- Highlight the 2-3 most relevant trends (from the trends list) with the percentages.
- Propose one shelf/stock/offer action.
- CTA: "binary_yes_no"
- send_as: "vera"
- suppression_key: "{suppression_key}"

Return JSON only.
"""
    return prompt.strip()


def build_trial_followup_prompt(category: dict, merchant: dict, trigger: dict, customer: dict) -> str:
    tpayload = trigger.get("payload", {})
    trial_date = tpayload.get("trial_date", "")
    next_sessions = tpayload.get("next_session_options", [])
    slot_labels = [s.get("label") for s in next_sessions if s.get("label")]
    cust_ident = customer.get("identity", {})
    lang_pref = cust_ident.get("language_pref", "en")
    suppression_key = trigger.get("suppression_key", "trial_followup:auto")

    prompt = f"""
CATEGORY CONTEXT:
{_category_summary(category)}

MERCHANT CONTEXT:
{_merchant_summary(merchant)}

CUSTOMER CONTEXT:
Name: {cust_ident.get('name')} | Language pref: {lang_pref}
Trial date: {trial_date} | Available next slots: {slot_labels}

TRIGGER: kind=trial_followup
suppression_key={suppression_key}

TASK: Follow up after a trial session to convert to paid membership.

Guidelines:
- Acknowledge the trial date specifically.
- Offer the next available slot.
- Make it easy to say yes — no commitment pitch yet.
- CTA: "binary_yes_no" or "multi_choice_slot"
- send_as: "merchant_on_behalf"
- suppression_key: "{suppression_key}"

Return JSON only.
"""
    return prompt.strip()


def build_appointment_tomorrow_prompt(category: dict, merchant: dict, trigger: dict, customer: Optional[dict]) -> str:
    tpayload = trigger.get("payload", {})
    suppression_key = trigger.get("suppression_key", "appointment:auto")
    cust_name = customer.get("identity", {}).get("name", "Customer") if customer else "Customer"
    lang_pref = customer.get("identity", {}).get("language_pref", "en") if customer else "en"

    prompt = f"""
CATEGORY CONTEXT:
{_category_summary(category)}

MERCHANT CONTEXT:
{_merchant_summary(merchant)}

TRIGGER: kind=appointment_tomorrow | payload={_j(tpayload, 300)}
Customer name: {cust_name} | Language pref: {lang_pref}
suppression_key={suppression_key}

TASK: Appointment reminder sent from merchant to customer.

Guidelines:
- Be brief: confirm the appointment (service, time, place).
- Include any prep instructions relevant to the service type.
- CTA: "binary_confirm_cancel"
- send_as: "merchant_on_behalf"
- suppression_key: "{suppression_key}"

Return JSON only.
"""
    return prompt.strip()


# ---------------------------------------------------------------------------#
# FALLBACK generic prompt                                                     #
# ---------------------------------------------------------------------------#

def build_generic_prompt(category: dict, merchant: dict, trigger: dict, customer: Optional[dict]) -> str:
    suppression_key = trigger.get("suppression_key", "generic:auto")
    cust_block = ""
    if customer:
        cust_ident = customer.get("identity", {})
        cust_block = f"\nCUSTOMER: {cust_ident.get('name')} | state={customer.get('state')} | lang={cust_ident.get('language_pref')}"

    prompt = f"""
CATEGORY CONTEXT:
{_category_summary(category)}

MERCHANT CONTEXT:
{_merchant_summary(merchant)}
{cust_block}

TRIGGER: {_j(trigger, 500)}
suppression_key={suppression_key}

TASK: Compose the next best WhatsApp message for this trigger.

Guidelines:
- Use the trigger kind to determine WHY this message is being sent now.
- Be specific: pull real numbers from the merchant context.
- One clear CTA.
- send_as: "vera" (unless customer context present, then "merchant_on_behalf")
- suppression_key: "{suppression_key}"

Return JSON only.
"""
    return prompt.strip()


# ---------------------------------------------------------------------------#
# ROUTER — pick the right prompt builder                                      #
# ---------------------------------------------------------------------------#

def route(
    category: dict,
    merchant: dict,
    trigger: dict,
    customer: Optional[dict] = None,
) -> tuple[str, str]:
    """
    Returns (system_prompt, user_prompt) for the given trigger kind.
    """
    kind = trigger.get("kind", "")
    cat_slug = category.get("slug", "")

    if kind in ("research_digest", "regulation_change", "cde_opportunity"):
        user = build_research_digest_prompt(category, merchant, trigger, cat_slug)
    elif kind == "perf_dip":
        user = build_perf_dip_prompt(category, merchant, trigger)
    elif kind in ("perf_spike",):
        user = build_perf_spike_prompt(category, merchant, trigger)
    elif kind == "recall_due" and customer:
        user = build_recall_due_prompt(category, merchant, trigger, customer)
    elif kind == "chronic_refill_due" and customer:
        user = build_chronic_refill_prompt(category, merchant, trigger, customer)
    elif kind == "festival_upcoming":
        user = build_festival_upcoming_prompt(category, merchant, trigger)
    elif kind == "ipl_match_today":
        user = build_ipl_match_prompt(category, merchant, trigger)
    elif kind == "active_planning_intent":
        user = build_active_planning_prompt(category, merchant, trigger)
    elif kind in ("winback_eligible", "dormant_with_vera"):
        user = build_winback_prompt(category, merchant, trigger)
    elif kind in ("customer_lapsed_hard", "customer_lapsed_soft") and customer:
        user = build_customer_lapsed_prompt(category, merchant, trigger, customer)
    elif kind == "competitor_opened":
        user = build_competitor_prompt(category, merchant, trigger)
    elif kind == "review_theme_emerged":
        user = build_review_theme_prompt(category, merchant, trigger)
    elif kind == "milestone_reached":
        user = build_milestone_prompt(category, merchant, trigger)
    elif kind == "renewal_due":
        user = build_renewal_prompt(category, merchant, trigger)
    elif kind == "gbp_unverified":
        user = build_gbp_unverified_prompt(category, merchant, trigger)
    elif kind == "curious_ask_due":
        user = build_curious_ask_prompt(category, merchant, trigger)
    elif kind == "supply_alert":
        user = build_supply_alert_prompt(category, merchant, trigger)
    elif kind == "category_seasonal":
        user = build_category_seasonal_prompt(category, merchant, trigger)
    elif kind == "trial_followup" and customer:
        user = build_trial_followup_prompt(category, merchant, trigger, customer)
    elif kind == "appointment_tomorrow":
        user = build_appointment_tomorrow_prompt(category, merchant, trigger, customer)
    elif kind == "seasonal_perf_dip":
        # seasonal dip is a perf_dip variant
        trigger_copy = dict(trigger)
        if "is_expected_seasonal" not in trigger_copy.get("payload", {}):
            trigger_copy.setdefault("payload", {})["is_expected_seasonal"] = True
        user = build_perf_dip_prompt(category, merchant, trigger_copy)
    elif kind == "wedding_package_followup" and customer:
        user = build_trial_followup_prompt(category, merchant, trigger, customer)
    else:
        user = build_generic_prompt(category, merchant, trigger, customer)

    return SYSTEM_BASE, user
