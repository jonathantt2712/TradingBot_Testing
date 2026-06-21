# How the Trading Bot Works

A visual walkthrough of the system, from deployment topology down to a single
trade decision. Diagrams are [Mermaid](https://mermaid.js.org/) — they render
automatically on GitHub.

> TL;DR: a fleet of specialist **agents** each score a stock 1–100, the
> **PortfolioManager** blends those scores into a direction, a gauntlet of
> **risk gates** can shrink or veto the trade, and the surviving plan is sent
> to a **broker** as a bracket order. A dashboard shows everything; the user
> can require manual approval per trade.

---

## 1. Deployment topology

Where the pieces physically run.

```mermaid
flowchart LR
    user["👤 User<br/>(browser / mobile)"]

    subgraph cloud["☁️ Cloud"]
        vercel["Vercel<br/>Next.js dashboard"]
        render["Render<br/>api_server.py (FastAPI, 24/7)<br/>read-only proxy + history"]
    end

    subgraph pc["🖥️ One PC (EXECUTE_LIVE=true)"]
        bot["live_runner.py<br/>the bot — scans & trades"]
    end

    subgraph ext["External data & execution"]
        alpaca["Alpaca<br/>bars / account / orders"]
        ibkr["IBKR / Liquid<br/>(alt brokers)"]
        yahoo["Yahoo Finance<br/>VIX, macro ETFs"]
        finra["FINRA short volume"]
        hsw["House Stock Watcher<br/>congressional trades"]
        news["News (Alpaca / PoliStock)"]
    end

    user --> vercel
    vercel -->|"/api/bot proxy"| render
    render -.->|reads state/signals| bot
    bot --> alpaca
    bot --> ibkr
    bot --> yahoo
    bot --> finra
    bot --> hsw
    bot --> news

    classDef new fill:#fff
```

- **Only one PC** runs with `EXECUTE_LIVE=true` (shared Alpaca account).
- The dashboard never trades directly — it talks to the Render API, which
  surfaces the bot's signals and history. Live order routing happens on the PC.

---

## 2. Code map

```mermaid
flowchart TD
    subgraph entry["Entry points"]
        main["main.py<br/>one-shot scan"]
        live["live_runner.py<br/>live loops"]
        bt["backtest_runner.py<br/>walk-forward"]
        api["api_server.py<br/>dashboard backend"]
    end

    boot["bootstrap.py<br/>★ ALL composition<br/>build_broker / build_manager<br/>refresh_market_context<br/>eod_flatten / eod_report / correlation loops"]

    subgraph agents["agents/ — the analysts"]
        ag["fundamental · vision · technical<br/>liquid · insider · squeeze · macro<br/>regime · risk · decision"]
    end

    subgraph exec["execution/"]
        pm["portfolio_manager.py<br/>blend → gate → size → route"]
        brokers["alpaca / ibkr / liquid brokers"]
    end

    subgraph core["core/"]
        models["models · enums · base_agent<br/>freshness · trade_memory · trade_stats<br/>llm_adapter"]
    end

    subgraph data["data/"]
        d["universe/sector scanner · news_sources<br/>correlation_graph · chart_renderer<br/>telegram/dashboard publishers"]
    end

    main & live & bt & api --> boot
    boot --> agents & exec & data
    pm --> agents
    pm --> brokers
    agents --> core
```

Everything is wired in **`bootstrap.py`** so the runners can't drift apart.

---

## 3. The core pipeline — per ticker

The heart of the system: from raw bars to a routed order.

```mermaid
flowchart TD
    A["Pick tickers<br/>(UniverseScanner or CLI)"] --> B["Fetch 5-min + 1-hr bars<br/>+ account equity<br/>+ render chart PNG"]
    B --> C["Build AnalysisContext"]
    C --> D{{"Run agents CONCURRENTLY"}}

    D --> E1["Fundamental"]
    D --> E2["Vision"]
    D --> E3["Technical"]
    D --> E4["Liquid"]
    D --> E5["Insider"]
    D --> E6["Squeeze"]
    D --> E7["Macro"]
    D --> E8["Risk (gate + plan)"]

    E1 & E2 & E3 & E4 & E5 & E6 & E7 --> F["Blend → composite 1-100<br/>(LLM DecisionAgent, or<br/>weighted+regime fallback)"]
    F --> G["Map to LONG / SHORT / PASS<br/>via regime-shifted thresholds"]
    E8 --> H

    G --> H["⚖️ Risk & sizing gauntlet<br/>(see §6)"]
    H -->|"PASS"| X["No trade — audit only"]
    H -->|"actionable plan"| I["🛡️ Entry guard (see §6)"]
    I -->|"blocked"| X
    I -->|"allowed"| J["📤 submit bracket order<br/>(entry + stop + take-profit)"]
    J --> K["Record decision + track fill/slippage"]
```

Once-per-cycle context (shared by all tickers): the **RegimeAgent** sets the
market regime and the **MacroAgent** caches macro signals; SPY bars are injected
into the TechnicalAgent for relative strength.

---

## 4. The agent ensemble

Each agent returns an `AgentEvaluation` — a score (1 = max bearish, 50 = neutral,
100 = max bullish), a confidence, an optional veto, and rationale.

| Agent | Default weight | What it reads | Signal |
|-------|:---:|---|---|
| **Technical** | 0.32 | bars (+SPY) | RSI/MACD/EMA/VWAP, rel-strength, volume surge, intraday momentum, candlesticks |
| **Fundamental** | 0.18 | news + LLM | news sentiment, earnings/catalyst scoring (keyword fallback) |
| **Vision** | 0.14 | chart PNG + vision LLM | chart-pattern recognition from the rendered candlestick image |
| **Liquid** | 0.13 | bars | relative volume, spread quality, momentum proxy |
| **Insider** | 0.10 | House Stock Watcher | congressional buying (needs ≥2 technical confirmations) |
| **Macro** | 0.10 | Yahoo (BTC/QQQ/XLP/GLD/UUP) | risk-on/off regime, cached 30 min, shared by all tickers |
| **Squeeze** | 0.08 | FINRA short volume | short-squeeze setups (short ratio + rel-vol) |
| **Risk** | gate (0) | bars + account | sizing, SL/TP, R/R, **veto authority** — not part of the directional blend |
| **Regime** | per-cycle | Yahoo VIX + SPY/QQQ | tightens/loosens LONG & SHORT thresholds |
| **Decision** | meta | all evals + memory | LLM "Chief Decision Officer" — debates bull/bear, rules LONG/SHORT/PASS |

```mermaid
flowchart LR
    subgraph dir["Directional agents (weighted)"]
        t["Technical .32"]
        f["Fundamental .18"]
        v["Vision .14"]
        l["Liquid .13"]
        i["Insider .10"]
        m["Macro .10"]
        s["Squeeze .08"]
    end

    t & f & v & l & i & m & s --> blend["weight × regime-multiplier × confidence<br/>→ composite 1-100"]
    blend --> dec["DecisionAgent (LLM)<br/>bull vs bear → ruling"]
    dec --> out["LONG / SHORT / PASS"]

    risk["Risk agent<br/>(gate, weight 0)"] -. "veto / score" .-> out
    regime["Regime<br/>(per cycle)"] -. "threshold shift" .-> out
```

---

## 5. One evaluation cycle (sequence)

```mermaid
sequenceDiagram
    participant R as live_runner
    participant B as Broker
    participant PM as PortfolioManager
    participant AG as Agents (parallel)
    participant DA as DecisionAgent (LLM)
    participant RK as RiskAgent

    R->>B: get_bars + get_account
    B-->>R: OHLCV + equity
    R->>PM: run_once(ctx)
    PM->>PM: _check_daily_loss (kill switch)
    PM->>AG: safe_evaluate(ctx)  (gathered)
    AG-->>PM: AgentEvaluations
    PM->>RK: evaluate → score / veto / plan
    PM->>DA: decide(evals, regime)
    DA-->>PM: LONG/SHORT/PASS + composite
    PM->>PM: gauntlet (veto, regime, sizing, haircuts)
    alt actionable & entry allowed
        PM->>B: get_positions / get_open_orders
        B-->>PM: portfolio state
        PM->>B: submit_bracket(decision)
        B-->>PM: OrderReceipt
        PM-->>R: TradeDecision (executed)
    else PASS or blocked
        PM-->>R: TradeDecision (no trade)
    end
    PM->>PM: append decisions.jsonl (audit)
```

---

## 6. The risk & sizing gauntlet

Every potential trade runs this gauntlet. Boxes marked **★ NEW** were added in
the recent MiroFish-inspired work. Anything that hits a 🛑 becomes a PASS.

```mermaid
flowchart TD
    start(["composite + direction"]) --> stale

    stale{"★ Stale data?<br/>(RiskAgent freshness veto)"}
    stale -->|"stale feed / halt"| pass1["🛑 PASS"]
    stale -->|"fresh"| veto

    veto{"Risk veto?<br/>(R/R floor, qty=0, no data)"}
    veto -->|"yes"| pass2["🛑 PASS"]
    veto -->|"no"| score

    score{"Risk score ≥<br/>MIN_RISK_SCORE?"}
    score -->|"no"| pass3["🛑 PASS"]
    score -->|"yes"| regime

    regime{"RISK_OFF regime<br/>& LONG & composite<75?"}
    regime -->|"yes"| pass4["🛑 PASS"]
    regime -->|"no"| plan

    plan["Build plan: entry, ATR stop,<br/>structure-capped target, qty<br/>(1% equity risk, ≤20% exposure,<br/>fractional Kelly)"] --> vix

    vix["VIX scaling<br/>>40 → ×0.5, >30 → ×0.7"] --> conv
    conv["Conviction boost<br/>far from threshold → up to +20%"] --> hair
    hair["★ Disagreement haircut<br/>agent score std ≥18 → ×0.75, ≥25 → ×0.5"] --> viable

    viable{"qty>0 & R/R ≥ min?"}
    viable -->|"no"| pass5["🛑 PASS"]
    viable -->|"yes"| guard

    subgraph guardbox["🛡️ Entry guard (pre-order)"]
        guard["daily-loss kill switch?<br/>re-entry cooldown?<br/>stoploss-guard halt?<br/>duplicate position / order?<br/>max open positions?<br/>★ correlation concentration cap?"]
    end

    guard -->|"any trips"| pass6["🛑 PASS"]
    guard -->|"clear"| order(["📤 bracket order"])
```

**Sizing math (RiskAgent):** `risk_$ = equity × 1% × volatility_mult × kelly_mult`,
stop = `ATR × stop_multiple`, target capped at session high/low so R/R stays
variable (a constant R/R would make the min-R/R veto a no-op). **Fail closed:**
no verified equity ⇒ no plan.

---

## 7. Live runner: concurrent loops

`live_runner.py` runs many `asyncio` loops at once after the initial scan.

```mermaid
flowchart TD
    boot["startup: scan universe →<br/>build broker + manager →<br/>refresh regime + protections →<br/>initial concurrent scan"]

    boot --> loops{{"asyncio.gather"}}
    loops --> l1["rescan_loop<br/>(30 min) — re-pick universe, evaluate"]
    loops --> l2["breakout_monitor_loop<br/>(5 min) — intraday breakouts"]
    loops --> l3["strategy_refresh_loop<br/>(60 min) — reload tuned params"]
    loops --> l4["★ eod_report_loop<br/>(near close) — desk note → Telegram"]
    loops --> l5["★ correlation_refresh_loop<br/>(60 min) — rebuild correlation graph"]
    loops --> l6["eod_flatten_loop<br/>(close-5min) — flatten book*"]
    loops --> l7["breakeven_lock_loop<br/>— trail stops to breakeven*"]

    note["* live-only (EXECUTE_LIVE=true)"]
```

Per-ticker evaluations are throttled by a semaphore (max 10 concurrent). Orders
fire automatically **only** when `EXECUTE_LIVE=true` **and** the dashboard's
auto-execute toggle is on; otherwise signals are published for manual approval.

---

## 8. Safety layers (defense in depth)

```mermaid
flowchart LR
    subgraph perTrade["Per-trade"]
        a["Stale-data veto ★"]
        b["R/R + sizing veto"]
        c["Min risk score"]
        d["Disagreement haircut ★"]
        e["VIX scaling"]
    end
    subgraph portfolio["Portfolio"]
        f["Duplicate-position guard"]
        g["Max open positions"]
        h["Correlation cap ★"]
        i["Re-entry cooldown"]
        j["Stoploss-guard (loss streak)"]
    end
    subgraph account["Account / session"]
        k["Daily-loss kill switch"]
        l["Intraday drawdown halt"]
        m["EOD flatten"]
    end
    perTrade --> portfolio --> account
```

Guiding principle throughout: **fail closed** — when state is unknown (no
equity, stale bars, broker error), the bot refuses to trade rather than guessing.

---

## 9. Recent additions (MiroFish-inspired)

| ★ Feature | Where | Effect |
|---|---|---|
| **Stale-data veto** | `core/freshness.py`, RiskAgent | refuses to size against halted / stale / weekend bars |
| **Disagreement haircut** | PortfolioManager | shrinks size when agents strongly conflict (low conviction) |
| **EOD ReportAgent** | `agents/report_agent.py`, `bootstrap.eod_report_loop` | daily desk note from the audit log + trade stats + memory |
| **Correlation graph** | `data/correlation_graph.py`, `bootstrap.correlation_refresh_loop` | concentration cap uses real return-correlation, not a static list |

See `.claude/completions/2026-06-21-mirofish-inspired-risk-and-reporting.md`.
