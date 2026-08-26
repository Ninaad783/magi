"""
main.py — FastAPI bot for the magicpin AI Challenge (Vera).

Exposes 5 endpoints:
  GET  /v1/healthz
  GET  /v1/metadata
  POST /v1/context
  POST /v1/tick
  POST /v1/reply
  POST /v1/teardown  (optional, per spec §11)
"""
from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from store import store
from composer import compose
from reply_handler import decide_reply, build_reply_prompt

# ---------------------------------------------------------------------------#
# App bootstrap                                                               #
# ---------------------------------------------------------------------------#

app = FastAPI(title="Vera Bot", version="1.0.0")
START_TIME = time.time()

# ---------------------------------------------------------------------------#
# Metadata (customise before submission)                                      #
# ---------------------------------------------------------------------------#

TEAM_NAME = os.environ.get("TEAM_NAME", "Vera Bot")
TEAM_MEMBERS = os.environ.get("TEAM_MEMBERS", "Submission").split(",")
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "submission@example.com")
SUBMITTED_AT = os.environ.get("SUBMITTED_AT", datetime.now(timezone.utc).isoformat())

# ---------------------------------------------------------------------------#
# Models                                                                      #
# ---------------------------------------------------------------------------#


class ContextBody(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: dict[str, Any]
    delivered_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class TickBody(BaseModel):
    now: str
    available_triggers: list[str] = []


class ReplyBody(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str = "merchant"
    message: str
    received_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    turn_number: int = 1


# ---------------------------------------------------------------------------#
# GET /v1/healthz                                                              #
# ---------------------------------------------------------------------------#


@app.get("/v1/healthz")
async def healthz():
    counts = store.count_by_scope()
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - START_TIME),
        "contexts_loaded": counts,
    }


# ---------------------------------------------------------------------------#
# GET /v1/metadata                                                             #
# ---------------------------------------------------------------------------#


@app.get("/v1/metadata")
async def metadata():
    return {
        "team_name": TEAM_NAME,
        "team_members": TEAM_MEMBERS,
        "model": os.environ.get("VERA_MODEL", "claude-sonnet-4-5"),
        "approach": (
            "Trigger-kind routing → specialised Claude prompt per kind "
            "(temperature=0, deterministic). "
            "Pattern-based auto-reply/intent detection in reply handler. "
            "Post-LLM validation + auto-retry on schema errors."
        ),
        "contact_email": CONTACT_EMAIL,
        "version": "1.0.0",
        "submitted_at": SUBMITTED_AT,
    }


# ---------------------------------------------------------------------------#
# POST /v1/context                                                             #
# ---------------------------------------------------------------------------#


@app.post("/v1/context")
async def push_context(body: ContextBody):
    result = store.put_context(
        scope=body.scope,
        context_id=body.context_id,
        version=body.version,
        payload=body.payload,
    )
    if not result.get("accepted"):
        # Return 409 for stale_version, 400 for invalid_scope
        status_code = 409 if result.get("reason") == "stale_version" else 400
        return JSONResponse(status_code=status_code, content=result)
    return result


# ---------------------------------------------------------------------------#
# POST /v1/tick                                                                #
# ---------------------------------------------------------------------------#


@app.post("/v1/tick")
async def tick(body: TickBody):
    actions = []

    for trg_id in body.available_triggers:
        if len(actions) >= 20:
            break

        # Load trigger
        trg = store.get_context("trigger", trg_id)
        if not trg:
            continue

        # Check suppression
        suppression_key = trg.get("suppression_key", "")
        if suppression_key and store.is_suppressed(suppression_key):
            continue

        # Resolve merchant
        merchant_id = trg.get("merchant_id")
        if not merchant_id:
            continue
        merchant = store.get_context("merchant", merchant_id)
        if not merchant:
            continue

        # Resolve category
        cat_slug = merchant.get("category_slug")
        category = store.get_context("category", cat_slug) if cat_slug else None
        if not category:
            continue

        # Resolve customer (optional)
        customer_id = trg.get("customer_id")
        customer = store.get_context("customer", customer_id) if customer_id else None

        # Check if we already have an active conversation for this (merchant, trigger)
        conv_id = f"conv_{merchant_id}_{trg_id}"
        if not store.is_conversation_active(conv_id):
            continue

        # Check last sent body for anti-repetition
        conv_state = store.get_conversation(conv_id)
        last_body = conv_state.get("last_bot_body")

        # Compose
        try:
            result = compose(category, merchant, trg, customer, last_body=last_body)
        except Exception as exc:
            # Don't crash the whole tick — skip this trigger
            print(f"[WARN] compose failed for {trg_id}: {exc}")
            continue

        if not result.get("body"):
            continue

        # Mark suppression key as used
        sk = result.get("suppression_key") or suppression_key
        if sk:
            store.suppress(sk)

        # Update conversation state
        store.update_conversation(
            conv_id,
            merchant_id=merchant_id,
            customer_id=customer_id,
            trigger_id=trg_id,
            last_bot_body=result["body"],
        )
        store.append_turn(conv_id, "vera", result["body"])

        # Determine template name from trigger kind
        kind = trg.get("kind", "generic")
        template_name = f"vera_{kind}_v1"
        merchant_name = merchant.get("identity", {}).get("name", "")
        owner_name = merchant.get("identity", {}).get("owner_first_name", merchant_name)

        action = {
            "conversation_id": conv_id,
            "merchant_id": merchant_id,
            "customer_id": customer_id,
            "send_as": result.get("send_as", "vera"),
            "trigger_id": trg_id,
            "template_name": template_name,
            "template_params": [owner_name, result["body"][:120]],
            "body": result["body"],
            "cta": result.get("cta", "open_ended"),
            "suppression_key": sk,
            "rationale": result.get("rationale", ""),
        }
        actions.append(action)

    return {"actions": actions}


# ---------------------------------------------------------------------------#
# POST /v1/reply                                                               #
# ---------------------------------------------------------------------------#


@app.post("/v1/reply")
async def reply(body: ReplyBody):
    conv_id = body.conversation_id
    merchant_id = body.merchant_id
    customer_id = body.customer_id
    message = body.message.strip()
    from_role = body.from_role
    turn_number = body.turn_number

    # Load conversation state
    conv_state = store.get_conversation(conv_id)

    # Guard: ended conversations
    if conv_state.get("status") in ("ended", "opted_out"):
        return {"action": "end", "rationale": "Conversation previously ended; no further sends."}

    # Resolve contexts from store (may have been updated since tick)
    resolved_merchant_id = merchant_id or conv_state.get("merchant_id")
    resolved_customer_id = customer_id or conv_state.get("customer_id")
    trigger_id = conv_state.get("trigger_id")

    merchant = store.get_context("merchant", resolved_merchant_id) if resolved_merchant_id else None
    cat_slug = (merchant or {}).get("category_slug")
    category = store.get_context("category", cat_slug) if cat_slug else None
    customer = store.get_context("customer", resolved_customer_id) if resolved_customer_id else None
    trigger = store.get_context("trigger", trigger_id) if trigger_id else None

    # Append incoming message to turn history
    store.append_turn(conv_id, from_role, message)

    # Update auto-reply streak tracking
    auto_reply_streak = conv_state.get("auto_reply_streak", 0)

    # Run local decision engine first
    decision = decide_reply(
        message=message,
        from_role=from_role,
        turn_number=turn_number,
        conv_state=conv_state,
        merchant=merchant,
        category=category,
        trigger=trigger,
        customer=customer,
    )

    # Update auto-reply streak if provided
    if "_auto_reply_streak" in decision:
        store.update_conversation(conv_id, auto_reply_streak=decision["_auto_reply_streak"])

    # Handle each decision kind
    if decision["action"] == "end":
        store.end_conversation(conv_id)
        return {"action": "end", "rationale": decision["rationale"]}

    if decision["action"] == "wait":
        store.set_wait(conv_id, decision.get("wait_seconds", 3600))
        return {
            "action": "wait",
            "wait_seconds": decision.get("wait_seconds", 3600),
            "rationale": decision["rationale"],
        }

    if decision["action"] == "send":
        body_text = decision.get("body", "")
        store.append_turn(conv_id, "vera", body_text)
        store.update_conversation(conv_id, last_bot_body=body_text)
        # End conversation after send if flagged
        if decision.get("_end_after_send"):
            store.end_conversation(conv_id)
        return {
            "action": "send",
            "body": body_text,
            "cta": decision.get("cta", "open_ended"),
            "rationale": decision["rationale"],
        }

    # action == "compose": call LLM
    intent_confirmed = decision.get("intent") == "confirmed"
    last_body = conv_state.get("last_bot_body")

    # If trigger missing but merchant present, build fallback trigger
    if not trigger and merchant:
        trigger = {
            "id": f"trg_intent_{merchant.get('merchant_id')}",
            "kind": "active_planning_intent" if intent_confirmed else "curious_ask_due",
            "scope": "merchant",
            "merchant_id": merchant.get("merchant_id"),
            "payload": {"merchant_last_message": message},
            "suppression_key": f"reply:{merchant.get('merchant_id')}"
        }

    # If intent confirmed AND we have merchant context, compose an action-mode message
    if intent_confirmed and merchant and category:
        action_trigger = dict(trigger) if trigger else {}
        action_trigger["kind"] = "active_planning_intent"
        action_trigger.setdefault("payload", {})["merchant_last_message"] = message
        action_trigger.setdefault("suppression_key", f"intent:{merchant.get('merchant_id')}")
        try:
            result = compose(category, merchant, action_trigger, customer, last_body=last_body)
        except Exception as exc:
            result = {
                "body": f"Great, {merchant.get('identity', {}).get('owner_first_name', 'there')}! I've prepared your offer draft. Reply CONFIRM to schedule it.",
                "cta": "binary_confirm_cancel",
                "send_as": "vera",
                "suppression_key": f"intent:{merchant.get('merchant_id')}",
                "rationale": f"Intent confirmed, compose fallback: {exc}",
            }
    elif merchant and category and trigger:
        try:
            sys_p, usr_p = build_reply_prompt(
                message=message,
                from_role=from_role,
                turn_number=turn_number,
                conv_state=conv_state,
                merchant=merchant,
                category=category,
                trigger=trigger,
                customer=customer,
                intent_confirmed=intent_confirmed,
            )
            from composer import _call_llm, _extract_json, _validate, _fix_prompt
            raw = _call_llm(sys_p, usr_p)
            result = _extract_json(raw)
            errors = _validate(result, last_body)
            if errors:
                retry = _fix_prompt(usr_p, errors)
                raw2 = _call_llm(sys_p, retry)
                try:
                    result = _extract_json(raw2)
                except Exception:
                    pass
        except Exception as exc:
            result = {
                "body": "Let me look into that and get back to you shortly.",
                "cta": "none",
                "send_as": "vera",
                "suppression_key": trigger.get("suppression_key", "reply:auto") if trigger else "reply:auto",
                "rationale": f"LLM reply compose failed: {exc}",
            }
    else:
        # No context available — generic acknowledgment
        result = {
            "body": "Thanks for your message! Let me check and get back to you.",
            "cta": "none",
            "send_as": "vera",
            "suppression_key": "reply:generic",
            "rationale": "No merchant/category/trigger context available for this conversation.",
        }

    body_text = result.get("body", "")
    store.append_turn(conv_id, "vera", body_text)
    store.update_conversation(conv_id, last_bot_body=body_text)

    return {
        "action": "send",
        "body": body_text,
        "cta": result.get("cta", "open_ended"),
        "rationale": result.get("rationale", ""),
    }


# ---------------------------------------------------------------------------#
# POST /v1/teardown (optional)                                                 #
# ---------------------------------------------------------------------------#


@app.post("/v1/teardown")
async def teardown():
    store.teardown()
    return {"status": "wiped", "message": "All context and conversation state cleared."}


# ---------------------------------------------------------------------------#
# Dev entry point                                                              #
# ---------------------------------------------------------------------------#

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=False)
