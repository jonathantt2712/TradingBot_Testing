# Agents Page — Design Spec

## Goal

Add a new `/agents` dashboard page that shows, in plain English, what each
of the bot's 6 agents (technical, fundamental, vision, risk, social,
liquid) currently sees in the market — their general read on conditions,
not their per-trade buy/sell decisions. Also surface the market-regime
agent's reasoning (VIX, SPY/QQQ inputs, threshold rules) as a market-wide
"what does the market look like today" summary, plus an "agents last
updated" timestamp.

## Background: the new `reasoning` data

A commit (`fc6a0e2`, currently on the `testing/main` remote, not yet on
`main`) adds a `reasoning: Optional[dict]` field to `AgentEvaluation`
(`trading_bot/core/models.py`). Each agent now populates it with a
structured, human-readable breakdown:

- **technical**: `reasoning.signals` — array of `{name, display, raw,
  score, weight_pct, direction, note}` for every signal (RSI, MACD, EMA
  cross, VWAP deviation, relative strength, volume surge, etc.), plus
  `price`, `vwap`, `rsi`, `day_change_pct`, `flags` (lottery/retail).
- **fundamental**: `provider`, `articles_analyzed`, `headlines_sample`,
  `llm_rationale` (LLM path) or `bull_signals`/`bear_signals`/
  `bull_phrases_matched`/`bear_phrases_matched`/`bull_keywords_matched`/
  `bear_keywords_matched` (keyword fallback), plus `note`.
- **vision**: `provider`, `pattern_identified`, `analysis`, `raw_score`,
  `note`.
- **liquid**: `reasoning.signals` (same shape as technical),
  `relative_volume`, `intraday_direction`, `note`.
- **social**: `signals_analyzed`, `trade_signals`, `strategy_signals`,
  `bull_weight`, `bear_weight`, `sentiment_ratio`, `note`.
- **risk**: `veto`, `veto_reason`, `plan` (entry/stop/target/qty/R-R/risk
  USD), `sizing` (equity, max risk %, ATR, etc.), `thresholds`, `note`.

Additionally, `RegimeSnapshot.reasoning` (`agents/regime_agent.py`) is a
**market-wide** (not per-ticker) structured explanation: `regime`,
`rationale`, `inputs` (VIX, SPY/QQQ vs VWAP, day change), `threshold_shifts`
(`long_delta`/`short_delta`/`effect`), and `rules` (the risk_on/risk_off/
neutral text rules).

This data is not yet wired through to the dashboard. This spec covers both
the small backend wiring needed and the new frontend page.

## Backend changes (`trading_bot/`)

1. **Merge the `reasoning` feature into `main`**: merge `testing/main`
   (PR #1, commits `0fec625` "improve agents: fix fragile LLM JSON
   parsing, propagate model/gemini_key" and `fc6a0e2` "feat: add elaborate
   per-agent reasoning for dashboard/audit") into `main`. `fc6a0e2`
   modifies `fundamental_agent.py`/`vision_agent.py` on top of the
   `parse_llm_json` extraction from `0fec625`, so both commits need to come
   in together. Together they touch `core/models.py`, `core/llm_adapter.py`
   (new), `bootstrap.py`, `config/settings.py`, all 6 `agents/*.py` files,
   and `execution/portfolio_manager.py`. The `reasoning` field is additive
   (`Optional[dict]`, default `None`) and doesn't change existing scoring
   behavior.

2. **`api_server.py` — `_run_market_scan`**: when building
   `evaluations_out` (around line 856-864), include each evaluation's
   `reasoning` field:
   ```python
   evaluations_out = [
       {
           "role":       ev.role.value if hasattr(ev.role, "value") else str(ev.role),
           "score":      round(float(ev.score), 1),
           "confidence": round(float(ev.confidence), 2),
           "rationale":  ev.rationale or "",
           "reasoning":  ev.reasoning,
       }
       for ev in agent_evals
   ]
   ```

3. **`api_server.py` — regime save**: build a `reasoning` dict alongside
   the existing regime fields and include it in `_save(REGIME_FILE, …)`,
   mirroring `RegimeSnapshot.reasoning`'s shape using the values already
   computed in `_run_market_scan` (`vix_level`, `vix_label`, `spy_chg`,
   `qqq_chg`, `regime_label`, `regime_rationale`):
   ```python
   _save(REGIME_FILE, {
       "regime":      regime_label,
       "vix_level":   vix_level,
       "spy_day_chg": spy_chg,
       "qqq_day_chg": qqq_chg,
       "rationale":   regime_rationale,
       "timestamp":   datetime.utcnow().isoformat(),
       "reasoning": {
           "regime": regime_label,
           "rationale": regime_rationale,
           "inputs": {
               "vix": vix_level,
               "vix_label": vix_label,
               "spy_day_chg_pct": spy_chg,
               "qqq_day_chg_pct": qqq_chg,
           },
           "rules": {
               "risk_on":  "SPY and QQQ both up > 0.5% intraday and VIX < 25",
               "risk_off": "SPY down > 0.5% intraday or VIX > 35",
               "choppy":   "SPY and QQQ both within ±0.3% intraday",
               "neutral":  "All other conditions",
           },
       },
   })
   ```
   `/api/regime` already returns the whole dict, so `reasoning` is
   exposed automatically. No new endpoint is needed.

4. `/api/recommendations` already returns `evaluations` verbatim from
   `RECS_FILE`, so the added `reasoning` field is exposed automatically
   once step 2 is in place.

## Frontend changes (`trading-dashboard/`)

### Types (`types/trading.ts`)

```typescript
export interface AgentEvaluation {
  role:       string
  score:      number   // 1-100
  confidence: number   // 0-1
  rationale?: string
  reasoning?: Record<string, any>
}

export interface RegimeInfo {
  regime:     Regime
  vix_level:  number
  spy_day_chg: number
  qqq_day_chg: number
  rationale:  string
  timestamp:  string
  reasoning?: Record<string, any>
}
```

(`Record<string, any>` is intentional — the shape varies per agent role
as documented above, and the renderer below handles each shape generically
or with role-specific known keys.)

### Shared agent metadata (`lib/agents.ts`, new file)

Extract `AGENT_ORDER`, `AGENT_LABELS`, `AGENT_BLURBS` from
`components/trades/RationaleModal.tsx` into this new shared module, and
have `RationaleModal` import them from there (avoids duplicating these
constants in the new Agents page).

```typescript
export const AGENT_ORDER = ['technical', 'fundamental', 'vision', 'risk', 'social', 'liquid'] as const

export const AGENT_LABELS: Record<string, string> = {
  technical:   'Technical',
  fundamental: 'Fundamental',
  vision:      'Vision (Chart)',
  risk:        'Risk',
  social:      'Social Sentiment',
  liquid:      'Liquidity Flow',
}

export const AGENT_BLURBS: Record<string, string> = {
  technical:   'Price action, VWAP, relative strength & volume',
  fundamental: 'News & earnings keyword signals',
  vision:      'Chart pattern recognition',
  risk:        'Position sizing, stop placement & R/R viability',
  social:      'Community / social sentiment chatter',
  liquid:      'Order flow & liquidity dynamics',
}
```

### Demo data (`lib/api.ts`)

Extend `demoRegime()` to include a `reasoning` object (matching the shape
in step 3 above), and extend each evaluation in `demoRecommendations()`
with a representative `reasoning` object for its role, so the page is
fully functional in demo/offline mode. No new `api.*` functions needed —
`api.recommendations()` and `api.regime()` already fetch the data this
page needs.

### New page: `app/agents/page.tsx`

Client component, following the data-fetching pattern of `app/trades/page.tsx`
(fetch on mount + 30s `setInterval`, `live`/`demo` indicator, manual refresh
button). Layout:

1. **Header**: "Agents" title, live/demo indicator, "Agents updated
   {relative time}" derived from the newest `timestamp` across
   `regime.timestamp` and all `recommendations[].timestamp`, and a manual
   refresh button.

2. **Market Regime card** (`components/agents/RegimeReasoningCard.tsx`):
   top-of-page card showing the current regime badge (reusing
   `regimeLabel`/`regimeColor` from `lib/utils`), the `rationale` text, and
   — if `regime.reasoning` is present — a small grid of the `inputs` (VIX,
   SPY day chg, QQQ day chg) plus the matched rule text from `rules` for
   the current regime. This is the market-wide "what the market looks like
   today" summary.

3. **Per-agent grid** (`components/agents/AgentOverviewCard.tsx`, one per
   `AGENT_ORDER` entry): for each agent role, aggregate that agent's
   evaluations across all current `recommendations`:
   - Average score and average confidence across tickers where that role
     has an evaluation → shown as a lean badge (bullish/neutral/bearish,
     reusing the `lean()`/`LeanIcon` pattern from `RationaleModal`) with
     the average score.
   - Count of tickers evaluated by this agent today.
   - `AGENT_LABELS[role]` as the title and `AGENT_BLURBS[role]` as a
     one-line description of what this agent looks at.
   - An expandable list (collapsed by default, `ChevronDown`/`ChevronUp`
     toggle) of per-ticker entries, each showing the ticker, that agent's
     score/confidence for it, and the full `reasoning` rendered via
     `ReasoningDetail`.
   - If no recommendations have an evaluation for this role (e.g. agent
     unavailable), show "No data for this agent right now" instead of an
     empty card.

4. **`components/agents/ReasoningDetail.tsx`** (new, shared by the
   per-ticker expandable entries): generic renderer for a `reasoning`
   object —
   - If `reasoning.signals` is an array, render each as a row: `display`
     name, `raw` value, a small score badge colored via
     `bgColorForScore`, and the `note` text below.
   - Render top-level `note` (if present) as an italic footer line.
   - Render other known scalar/array fields generically as label/value
     pairs (e.g. `headlines_sample` as a bulleted list, `llm_rationale`/
     `analysis`/`pattern_identified` as text, `veto`/`veto_reason` as a
     highlighted warning row when `veto` is true, `plan`/`sizing`/
     `thresholds` as small key/value grids).
   - If `reasoning` is missing/null, render nothing (the per-ticker entry
     still shows score/confidence/rationale).

### Sidebar (`components/layout/Sidebar.tsx`)

Add `{ href: '/agents', icon: Brain, label: 'Agents' }` to the `nav` array
(used by both the desktop sidebar and `MobileNav`), importing `Brain` from
`lucide-react`.

## Out of scope

- No changes to `/api/bot/*` Next.js proxy routes — existing
  `/api/bot/recommendations` and `/api/bot/regime` already proxy the
  underlying bot endpoints and will carry the new `reasoning` fields
  through unchanged (they pass through JSON verbatim).
- No changes to `RationaleModal`'s own content/layout beyond importing the
  three shared constants from `lib/agents.ts`.
- No new historical/trend view of agent reasoning over time — this page
  shows the current snapshot only.
