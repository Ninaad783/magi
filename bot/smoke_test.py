#!/usr/bin/env python3
"""
smoke_test.py — Validates all 5 Vera bot endpoints without needing an LLM key.

Tests:
  1. GET /v1/healthz
  2. GET /v1/metadata
  3. POST /v1/context  (category, merchant, customer, trigger)
  4. POST /v1/context  (idempotent / stale version -> 409)
  5. POST /v1/context  (version bump -> 200)
  6. GET /v1/healthz   (counts should reflect pushes)
  7. POST /v1/tick     (no triggers -> empty actions)
  8. POST /v1/tick     (trigger available -> expect action OR empty)
  9. POST /v1/reply    (opt-out message -> action=end)
 10. POST /v1/reply    (auto-reply message -> wait or send)
 11. POST /v1/reply    (intent yes message -> compose or send)
 12. POST /v1/teardown (wipe state)
 13. GET /v1/healthz   (counts back to 0)
"""

import json
import sys
import urllib.request
import urllib.error

BOT_URL = "http://localhost:8080"
PASS = 0
FAIL = 0


def call(method: str, path: str, body=None, expected_status=200):
    url = BOT_URL + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.status
            result = json.loads(resp.read())
            return status, result
    except urllib.error.HTTPError as e:
        status = e.code
        try:
            result = json.loads(e.read())
        except Exception:
            result = {}
        return status, result
    except Exception as exc:
        return 0, {"error": str(exc)}


def check(label: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


print("\n" + "="*60)
print("  Vera Bot — Smoke Tests")
print("="*60)

# ── 1. healthz (cold) ──────────────────────────────────────────
print("\n[1] GET /v1/healthz (cold start)")
status, body = call("GET", "/v1/healthz")
check("status 200", status == 200)
check("status=ok", body.get("status") == "ok")
check("uptime_seconds present", "uptime_seconds" in body)
check("contexts_loaded present", "contexts_loaded" in body)

# ── 2. metadata ────────────────────────────────────────────────
print("\n[2] GET /v1/metadata")
status, body = call("GET", "/v1/metadata")
check("status 200", status == 200)
check("team_name present", "team_name" in body)
check("model present", "model" in body)
check("approach present", "approach" in body)

# ── 3. Push contexts ───────────────────────────────────────────
print("\n[3] POST /v1/context — push category")
cat_payload = {
    "slug": "dentists", "display_name": "Dentists",
    "voice": {"tone": "peer_clinical", "vocab_taboo": ["guaranteed"]},
    "offer_catalog": [{"id": "den_001", "title": "Dental Cleaning @ ₹299", "value": "299", "audience": "new_user", "type": "service_at_price"}],
    "peer_stats": {"avg_rating": 4.4, "avg_ctr": 0.030, "avg_views_30d": 1820, "avg_calls_30d": 12},
    "digest": [{"id": "d_W17_jida", "kind": "research", "title": "3-mo fluoride recall cuts caries 38%", "source": "JIDA Oct 2026, p.14", "trial_n": 2100, "patient_segment": "high_risk_adults", "summary": "Multi-center trial..."}],
    "patient_content_library": [], "seasonal_beats": [], "trend_signals": []
}
status, body = call("POST", "/v1/context", {"scope": "category", "context_id": "dentists", "version": 1, "payload": cat_payload, "delivered_at": "2026-04-26T09:45:00Z"})
check("status 200", status == 200)
check("accepted=True", body.get("accepted") is True)
check("ack_id present", bool(body.get("ack_id")))

print("\n[4] POST /v1/context — push merchant")
merchant_payload = {
    "merchant_id": "m_001_drmeera_dentist_delhi",
    "category_slug": "dentists",
    "identity": {"name": "Dr. Meera's Dental Clinic", "city": "Delhi", "locality": "Lajpat Nagar", "verified": True, "languages": ["en", "hi"], "owner_first_name": "Meera"},
    "subscription": {"status": "active", "plan": "Pro", "days_remaining": 82},
    "performance": {"window_days": 30, "views": 2410, "calls": 18, "directions": 45, "ctr": 0.021, "delta_7d": {"views_pct": 0.18, "calls_pct": -0.05}},
    "offers": [{"id": "o_meera_001", "title": "Dental Cleaning @ ₹299", "status": "active"}],
    "conversation_history": [],
    "customer_aggregate": {"total_unique_ytd": 540, "lapsed_180d_plus": 78, "retention_6mo_pct": 0.38, "high_risk_adult_count": 124},
    "signals": ["ctr_below_peer_median", "high_risk_adult_cohort"]
}
status, body = call("POST", "/v1/context", {"scope": "merchant", "context_id": "m_001_drmeera_dentist_delhi", "version": 1, "payload": merchant_payload, "delivered_at": "2026-04-26T09:46:00Z"})
check("status 200", status == 200)
check("accepted=True", body.get("accepted") is True)

print("\n[5] POST /v1/context — push trigger")
trigger_payload = {
    "id": "trg_001_research_digest_dentists",
    "scope": "merchant", "kind": "research_digest", "source": "external",
    "merchant_id": "m_001_drmeera_dentist_delhi", "customer_id": None,
    "payload": {"category": "dentists", "top_item_id": "d_W17_jida"},
    "urgency": 2, "suppression_key": "research:dentists:2026-W17",
    "expires_at": "2026-05-03T00:00:00Z"
}
status, body = call("POST", "/v1/context", {"scope": "trigger", "context_id": "trg_001_research_digest_dentists", "version": 1, "payload": trigger_payload, "delivered_at": "2026-04-26T10:32:00Z"})
check("status 200", status == 200)
check("accepted=True", body.get("accepted") is True)

# ── Idempotency check ──────────────────────────────────────────
print("\n[6] POST /v1/context — same version -> 409")
status, body = call("POST", "/v1/context", {"scope": "trigger", "context_id": "trg_001_research_digest_dentists", "version": 1, "payload": trigger_payload, "delivered_at": "2026-04-26T10:32:00Z"}, expected_status=409)
check("status 409", status == 409, f"got {status}")
check("accepted=False", body.get("accepted") is False)
check("reason=stale_version", body.get("reason") == "stale_version")

# ── Version bump ───────────────────────────────────────────────
print("\n[7] POST /v1/context — higher version -> 200 (replaces)")
status, body = call("POST", "/v1/context", {"scope": "trigger", "context_id": "trg_001_research_digest_dentists", "version": 2, "payload": trigger_payload, "delivered_at": "2026-04-26T11:00:00Z"})
check("status 200", status == 200)
check("accepted=True", body.get("accepted") is True)

# ── healthz with counts ────────────────────────────────────────
print("\n[8] GET /v1/healthz — counts reflect pushes")
status, body = call("GET", "/v1/healthz")
counts = body.get("contexts_loaded", {})
check("category count >= 1", counts.get("category", 0) >= 1, str(counts))
check("merchant count >= 1", counts.get("merchant", 0) >= 1, str(counts))
check("trigger count >= 1", counts.get("trigger", 0) >= 1, str(counts))

# ── tick (no trigger -> empty) ─────────────────────────────────
print("\n[9] POST /v1/tick — empty available_triggers -> []")
status, body = call("POST", "/v1/tick", {"now": "2026-04-26T10:00:00Z", "available_triggers": []})
check("status 200", status == 200)
check("actions is list", isinstance(body.get("actions"), list))
check("actions is empty", body.get("actions") == [])

# ── tick (with trigger — may call LLM or return empty if no key) ──
print("\n[10] POST /v1/tick — with trigger (may be empty if no API key)")
status, body = call("POST", "/v1/tick", {"now": "2026-04-26T10:35:00Z", "available_triggers": ["trg_001_research_digest_dentists"]})
check("status 200", status == 200)
check("actions is list", isinstance(body.get("actions"), list))
if body.get("actions"):
    a = body["actions"][0]
    check("action has conversation_id", bool(a.get("conversation_id")))
    check("action has merchant_id", bool(a.get("merchant_id")))
    check("action has body", bool(a.get("body")))
    check("action has cta", bool(a.get("cta")))
    check("action has suppression_key", bool(a.get("suppression_key")))
    check("action has rationale", bool(a.get("rationale")))
    print(f"\n  === Sample composed message ===")
    print(f"  {a.get('body', '')[:300]}")
    print(f"  CTA: {a.get('cta')} | send_as: {a.get('send_as')}")
    print(f"  Rationale: {a.get('rationale', '')[:200]}")
else:
    print("  (No action returned — LLM API key not set, tick returned empty)")

# ── reply — opt-out ───────────────────────────────────────────
print("\n[11] POST /v1/reply — opt-out message -> action=end")
status, body = call("POST", "/v1/reply", {
    "conversation_id": "conv_test_optout",
    "merchant_id": "m_001_drmeera_dentist_delhi",
    "from_role": "merchant",
    "message": "Stop messaging me. Not interested.",
    "received_at": "2026-04-26T10:45:00Z",
    "turn_number": 2
})
check("status 200", status == 200)
check("action=end", body.get("action") == "end", f"got action={body.get('action')}")

# ── reply — auto-reply ────────────────────────────────────────
print("\n[12] POST /v1/reply — WA auto-reply -> wait or send")
status, body = call("POST", "/v1/reply", {
    "conversation_id": "conv_test_autoreply",
    "merchant_id": "m_001_drmeera_dentist_delhi",
    "from_role": "merchant",
    "message": "Thank you for contacting us! Our team will respond shortly.",
    "received_at": "2026-04-26T10:46:00Z",
    "turn_number": 2
})
check("status 200", status == 200)
check("action is send or wait", body.get("action") in ("send", "wait"), f"got {body.get('action')}")

# ── reply — intent yes ────────────────────────────────────────
print("\n[13] POST /v1/reply — 'Yes let's do it' -> compose/send (no end/wait)")
status, body = call("POST", "/v1/reply", {
    "conversation_id": "conv_m_001_drmeera_trg_001",  # matches the tick conv_id pattern
    "merchant_id": "m_001_drmeera_dentist_delhi",
    "from_role": "merchant",
    "message": "Yes please, go ahead and draft the patient WhatsApp.",
    "received_at": "2026-04-26T10:47:00Z",
    "turn_number": 2
})
check("status 200", status == 200)
check("action is send (or compose)", body.get("action") in ("send", "compose"), f"got {body.get('action')}")

# ── teardown ──────────────────────────────────────────────────
print("\n[14] POST /v1/teardown — wipe state")
status, body = call("POST", "/v1/teardown")
check("status 200", status == 200)
check("status=wiped", body.get("status") == "wiped")

print("\n[15] GET /v1/healthz — counts back to 0 after teardown")
status, body = call("GET", "/v1/healthz")
counts = body.get("contexts_loaded", {})
check("all counts 0", all(v == 0 for v in counts.values()), str(counts))

# ── Summary ───────────────────────────────────────────────────
print("\n" + "="*60)
print(f"  Results: {PASS} passed / {FAIL} failed / {PASS+FAIL} total")
print("="*60)
sys.exit(0 if FAIL == 0 else 1)
