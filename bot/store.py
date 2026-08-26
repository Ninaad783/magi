"""
store.py — In-memory context store for the Vera bot.

Handles:
- Category / Merchant / Customer / Trigger contexts (versioned, idempotent)
- Suppression key tracking (dedup across ticks)
- Conversation state (active, waiting, ended, opted_out)
- Auto-reply counter per conversation
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional


class ContextStore:
    """Thread-safe store for all four context types."""

    def __init__(self):
        self._lock = threading.RLock()
        # (scope, context_id) -> {"version": int, "payload": dict, "stored_at": str}
        self._contexts: dict[tuple[str, str], dict] = {}
        # suppression_key -> True  (keys that have been used/sent)
        self._suppressed: set[str] = set()
        # conversation_id -> ConversationState dict
        self._conversations: dict[str, dict] = {}

    # ------------------------------------------------------------------ #
    # Context CRUD                                                         #
    # ------------------------------------------------------------------ #

    def put_context(self, scope: str, context_id: str, version: int, payload: dict) -> dict:
        """
        Store or update a context. Returns:
          {"accepted": True, "ack_id": ..., "stored_at": ...}   on success
          {"accepted": False, "reason": "stale_version", "current_version": N}  on conflict
          {"accepted": False, "reason": "invalid_scope", "details": ...}  on bad scope
        """
        valid_scopes = {"category", "merchant", "customer", "trigger"}
        if scope not in valid_scopes:
            return {"accepted": False, "reason": "invalid_scope",
                    "details": f"scope must be one of {valid_scopes}"}

        key = (scope, context_id)
        stored_at = datetime.now(timezone.utc).isoformat()

        with self._lock:
            existing = self._contexts.get(key)
            if existing and existing["version"] >= version:
                return {"accepted": False, "reason": "stale_version",
                        "current_version": existing["version"]}
            self._contexts[key] = {
                "version": version,
                "payload": payload,
                "stored_at": stored_at,
            }

        ack_id = f"ack_{context_id}_v{version}"
        return {"accepted": True, "ack_id": ack_id, "stored_at": stored_at}

    def get_context(self, scope: str, context_id: str) -> Optional[dict]:
        """Return the payload for (scope, context_id), or None."""
        with self._lock:
            entry = self._contexts.get((scope, context_id))
            return entry["payload"] if entry else None

    def get_all_of_scope(self, scope: str) -> list[dict]:
        """Return all payloads for a given scope."""
        with self._lock:
            return [
                v["payload"]
                for (s, _), v in self._contexts.items()
                if s == scope
            ]

    def count_by_scope(self) -> dict[str, int]:
        """Return counts per scope for /healthz."""
        counts: dict[str, int] = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
        with self._lock:
            for (scope, _) in self._contexts:
                if scope in counts:
                    counts[scope] += 1
        return counts

    # ------------------------------------------------------------------ #
    # Suppression                                                          #
    # ------------------------------------------------------------------ #

    def is_suppressed(self, suppression_key: str) -> bool:
        with self._lock:
            return suppression_key in self._suppressed

    def suppress(self, suppression_key: str):
        with self._lock:
            self._suppressed.add(suppression_key)

    # ------------------------------------------------------------------ #
    # Conversations                                                        #
    # ------------------------------------------------------------------ #

    def get_conversation(self, conv_id: str) -> dict:
        """Return conversation state, creating it if absent."""
        with self._lock:
            if conv_id not in self._conversations:
                self._conversations[conv_id] = {
                    "status": "active",        # active | waiting | ended | opted_out
                    "turns": [],               # list of {"from": role, "body": text}
                    "auto_reply_streak": 0,
                    "last_bot_body": None,     # for anti-repetition check
                    "wait_until": None,        # epoch seconds, for "wait" action
                    "merchant_id": None,
                    "customer_id": None,
                    "trigger_id": None,
                }
            return self._conversations[conv_id]

    def update_conversation(self, conv_id: str, **kwargs):
        with self._lock:
            state = self.get_conversation(conv_id)
            state.update(kwargs)

    def append_turn(self, conv_id: str, from_role: str, body: str):
        with self._lock:
            state = self.get_conversation(conv_id)
            state["turns"].append({"from": from_role, "body": body})

    def is_conversation_active(self, conv_id: str) -> bool:
        with self._lock:
            state = self._conversations.get(conv_id)
            if not state:
                return True  # new conversations are active by default
            if state["status"] in ("ended", "opted_out"):
                return False
            if state["status"] == "waiting" and state.get("wait_until"):
                if time.time() < state["wait_until"]:
                    return False  # still in back-off window
                else:
                    state["status"] = "active"  # back-off expired
            return True

    def end_conversation(self, conv_id: str, reason: str = "ended"):
        with self._lock:
            state = self.get_conversation(conv_id)
            state["status"] = reason  # "ended" or "opted_out"

    def set_wait(self, conv_id: str, wait_seconds: int):
        with self._lock:
            state = self.get_conversation(conv_id)
            state["status"] = "waiting"
            state["wait_until"] = time.time() + wait_seconds

    # ------------------------------------------------------------------ #
    # Teardown                                                             #
    # ------------------------------------------------------------------ #

    def teardown(self):
        """Wipe all state (called on POST /v1/teardown)."""
        with self._lock:
            self._contexts.clear()
            self._suppressed.clear()
            self._conversations.clear()


# Singleton instance shared across the FastAPI app
store = ContextStore()
