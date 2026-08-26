# Vera Bot — magicpin AI Challenge Submission

## What this bot does

Vera is magicpin's AI assistant for merchant growth. This bot implements the message engine behind Vera — a deterministic `compose(category, merchant, trigger, customer?)` function exposed as a stateful HTTP API.

## Approach

### Architecture

```
FastAPI Server (port 8080)
├── /v1/context  → idempotent context store (versioned, thread-safe)
├── /v1/tick     → proactive message composition on trigger availability
├── /v1/reply    → multi-turn conversation handler
├── /v1/healthz  → liveness + context count
└── /v1/metadata → team metadata
```

### Composition Strategy: Trigger-Kind Routing

Rather than a single monolithic prompt, every `trigger.kind` gets its own focused prompt template in `prompts.py`. This produces:

- **Higher specificity** — each template extracts only the fields relevant to that trigger kind, avoiding prompt dilution
- **Tighter category voice** — clinical for dentists, warm-coach for gyms, utility-first for pharmacies
- **Reliable CTA shapes** — each template specifies the exact CTA type to use

Trigger kinds handled with dedicated prompts:
`research_digest`, `regulation_change`, `cde_opportunity`, `perf_dip`, `seasonal_perf_dip`, `perf_spike`, `recall_due`, `chronic_refill_due`, `festival_upcoming`, `ipl_match_today`, `active_planning_intent`, `winback_eligible`, `dormant_with_vera`, `customer_lapsed_hard`, `customer_lapsed_soft`, `competitor_opened`, `review_theme_emerged`, `milestone_reached`, `renewal_due`, `gbp_unverified`, `curious_ask_due`, `supply_alert`, `category_seasonal`, `trial_followup`, `appointment_tomorrow`, `wedding_package_followup`

### Model: Claude claude-sonnet-4-5

- `temperature=0` for full determinism
- `max_tokens=800` (keeps responses concise and within budget)
- Post-LLM JSON validation + automatic re-prompt on schema errors

### Multi-Turn Intelligence (reply_handler.py)

Pattern-based (no LLM needed for < 5ms decisions):

| Signal | Detection | Response |
|---|---|---|
| WA Business auto-reply | Regex + verbatim repeat | 1st: prompt owner → 2nd: wait 4h → 3rd: end |
| Explicit opt-out ("stop messaging") | Regex | `action=end` immediately |
| Frustration/abuse | Regex | Brief apology + end |
| Intent YES ("ok let's do it") | Regex | Switch to action mode — no more qualifying |
| Out-of-scope ("GST filing") | Regex | Polite decline + redirect |
| Normal reply | Default | LLM compose with full conversation history |

### Key Design Choices

1. **No fabrication** — prompts explicitly forbid inventing numbers, offers, or citations not in the provided context
2. **Suppression dedup** — each sent action marks its `suppression_key` in the store; duplicate sends are blocked
3. **Anti-repetition** — the last bot body is tracked per conversation; identical resends trigger a re-prompt
4. **Context versioning** — higher version atomically replaces lower; same version is a no-op (409)
5. **Stateful in-memory** — thread-safe `ContextStore` handles up to the full test dataset (255 contexts) with fast lookups

### Compulsion Levers Used

The prompts are designed to fire one or more of these levers per message:
- **Specificity/verifiability** — trial_n, percentage deltas, source page numbers, batch IDs
- **Loss aversion** — "you're below peer median CTR", "X customers lapsed"
- **Social proof** — peer_stats benchmarks ("3 dentists in your locality did X")
- **Effort externalization** — "I'll draft it, just say yes"
- **Curiosity** — the curious_ask_due cadence
- **Single binary commitment** — YES/STOP, not multi-choice for action triggers

## Tradeoffs

| Decision | Reason |
|---|---|
| In-memory store (not Redis) | Fast, no external dependencies; suits 60-min test window |
| claude-sonnet-4-5 (not Opus) | 3-5x faster per call; stays within 30s budget even for complex ticks |
| Pattern detection (not LLM) for reply routing | < 5ms vs 3-5s; auto-reply / opt-out signals are clear enough |
| Per-kind prompt routing | More code, dramatically higher specificity than a single generic prompt |

## What additional context would have helped most

1. **Real CTR / views data at the locality level** (not just the merchant) — peer comparisons would be far more specific ("3 dentists in Lajpat Nagar upgraded their recall protocol")
2. **Available appointment slots** for more merchants — makes recall/booking CTAs concrete instead of open-ended
3. **WhatsApp template approval status** — knowing which templates are pre-approved would let the bot pick the right first-message format

## Running the bot

```bash
cd bot/
pip install -r requirements.txt

# Set your API key
set ANTHROPIC_API_KEY=sk-ant-your-key-here   # Windows
export ANTHROPIC_API_KEY=sk-ant-your-key-here  # Linux/Mac

# Start the server
uvicorn main:app --host 0.0.0.0 --port 8080

# Health check
curl http://localhost:8080/v1/healthz
```

## Running the judge simulator

```bash
# From the challenge root (d:\submission\)
# Edit judge_simulator.py: set LLM_API_KEY and BOT_URL=http://localhost:8080
python judge_simulator.py
```
