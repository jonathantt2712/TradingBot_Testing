"""Alpaca paper backtest + live recommendations runner.

Usage:
    # Run backtest over last 60 days, then open dashboard
    python backtest_runner.py AAPL MSFT NVDA TSLA

    # Just get today's recommendations (no historical simulation)
    python backtest_runner.py --recommend-only AAPL MSFT NVDA

    # Custom lookback
    python backtest_runner.py --days 90 AAPL NVDA

Requires .env with ALPACA_API_KEY_ID + ALPACA_API_SECRET (paper keys are fine).
Outputs dashboard/index.html — open it in any browser.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import webbrowser
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

import numpy as np
import pandas as pd

from bootstrap import build_manager
from config.settings import load_settings
from core.models import AnalysisContext, TradeDecision
from core.enums import Decision
from data.chart_renderer import render_chart
from execution.portfolio_manager import PortfolioManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s | %(message)s")
logger = logging.getLogger("backtest")

DASHBOARD_DIR = Path(__file__).parent.parent / "dashboard"


# ---------------------------------------------------------------------------
# Trade record
# ---------------------------------------------------------------------------

@dataclass
class TradeRecord:
    ticker: str
    direction: str        # LONG / SHORT
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    qty: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    outcome: str          # TP_HIT / SL_HIT / TIMEOUT
    pnl_usd: float
    pnl_pct: float
    composite_score: float
    agent_scores: dict    # role -> score
    agent_confidences: dict


# ---------------------------------------------------------------------------
# Alpaca data fetch
# ---------------------------------------------------------------------------

async def fetch_historical_bars(
    ticker: str,
    *,
    days: int = 60,
    timeframe: str = "5Min",
) -> Optional[pd.DataFrame]:
    """Fetch multiple days of 5-min bars from Alpaca historical API."""
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    key_id = os.environ.get("ALPACA_API_KEY_ID", "")
    secret = os.environ.get("ALPACA_API_SECRET", "")
    if not key_id or not secret:
        logger.error("ALPACA_API_KEY_ID / ALPACA_API_SECRET not set in .env")
        return None

    client = StockHistoricalDataClient(key_id, secret)
    end = datetime.now(tz=timezone.utc)
    start = end - timedelta(days=days + 5)  # extra buffer for weekends

    tf_map = {
        "1Min": TimeFrame(1, TimeFrameUnit.Minute),
        "5Min": TimeFrame(5, TimeFrameUnit.Minute),
        "15Min": TimeFrame(15, TimeFrameUnit.Minute),
        "1H": TimeFrame(1, TimeFrameUnit.Hour),
    }
    tf = tf_map.get(timeframe, TimeFrame(5, TimeFrameUnit.Minute))

    req = StockBarsRequest(symbol_or_symbols=ticker, timeframe=tf, start=start, end=end)
    try:
        bars = await asyncio.to_thread(client.get_stock_bars, req)
        df = bars.df
        if isinstance(df.index, pd.MultiIndex):
            df = df.loc[ticker] if ticker in df.index.get_level_values(0) else df.droplevel(0)
        df = df.rename(columns={
            "open": "open", "high": "high", "low": "low",
            "close": "close", "volume": "volume",
        })
        df.index = pd.to_datetime(df.index, utc=True)
        logger.info("Fetched %d bars for %s", len(df), ticker)
        return df[["open", "high", "low", "close", "volume"]]
    except Exception:
        logger.exception("Failed to fetch bars for %s", ticker)
        return None


# ---------------------------------------------------------------------------
# Fill simulator
# ---------------------------------------------------------------------------

def simulate_fill(
    bars_after_entry: pd.DataFrame,
    *,
    direction: Decision,
    entry: float,
    stop_loss: float,
    take_profit: float,
    qty: float,
    max_bars: int = 288,  # ~1 trading day of 5-min bars
) -> TradeRecord | None:
    """Walk forward bar-by-bar and check if TP or SL is hit."""
    for i, (ts, bar) in enumerate(bars_after_entry.head(max_bars).iterrows()):
        high = float(bar["high"])
        low = float(bar["low"])

        if direction is Decision.LONG:
            sl_hit = low <= stop_loss
            tp_hit = high >= take_profit
        else:
            sl_hit = high >= stop_loss
            tp_hit = low <= take_profit

        if tp_hit and sl_hit:
            # Both in same bar — assume worst case (SL)
            outcome, exit_px = "SL_HIT", stop_loss
        elif tp_hit:
            outcome, exit_px = "TP_HIT", take_profit
        elif sl_hit:
            outcome, exit_px = "SL_HIT", stop_loss
        else:
            continue

        mult = 1 if direction is Decision.LONG else -1
        pnl = mult * (exit_px - entry) * qty
        pnl_pct = mult * (exit_px - entry) / entry * 100
        return outcome, exit_px, str(ts), pnl, pnl_pct

    # No hit within max_bars — exit at last bar's close
    last_ts = bars_after_entry.index[min(max_bars - 1, len(bars_after_entry) - 1)]
    last_close = float(bars_after_entry["close"].iloc[min(max_bars - 1, len(bars_after_entry) - 1)])
    mult = 1 if direction is Decision.LONG else -1
    pnl = mult * (last_close - entry) * qty
    pnl_pct = mult * (last_close - entry) / entry * 100
    return "TIMEOUT", last_close, str(last_ts), pnl, pnl_pct


# ---------------------------------------------------------------------------
# Backtest core
# ---------------------------------------------------------------------------

async def backtest_ticker(
    pm: PortfolioManager,
    ticker: str,
    all_bars: pd.DataFrame,
    *,
    lookback: int = 200,
    step_bars: int = 78,   # ~half day of 5-min bars between evaluations
) -> list[TradeRecord]:
    """Walk-forward: evaluate every ~half-day, simulate fills."""
    records: list[TradeRecord] = []
    n = len(all_bars)

    for start_idx in range(lookback, n - step_bars, step_bars):
        window = all_bars.iloc[start_idx - lookback: start_idx]
        entry_ts = all_bars.index[start_idx]

        # Build context
        chart = render_chart(ticker, window)
        ctx = AnalysisContext(
            ticker=ticker,
            bars=window,
            account={"equity": 100_000.0},  # paper account
            chart_image_path=chart,
            as_of=entry_ts,
        )

        decision = await pm.decide(ctx)

        if not decision.is_actionable or not decision.risk:
            continue

        # Simulate fill on bars after entry point
        future_bars = all_bars.iloc[start_idx:]
        entry_price = float(future_bars["open"].iloc[0])
        r = decision.risk
        fill = simulate_fill(
            future_bars.iloc[1:],  # bars after entry bar
            direction=decision.decision,
            entry=entry_price,
            stop_loss=r.stop_loss,
            take_profit=r.take_profit,
            qty=r.qty,
        )
        if fill is None:
            continue

        outcome, exit_price, exit_ts, pnl, pnl_pct = fill

        records.append(TradeRecord(
            ticker=ticker,
            direction=decision.decision.value,
            entry_time=str(entry_ts),
            exit_time=exit_ts,
            entry_price=round(entry_price, 4),
            exit_price=round(exit_price, 4),
            qty=r.qty,
            stop_loss=r.stop_loss,
            take_profit=r.take_profit,
            risk_reward=r.risk_reward,
            outcome=outcome,
            pnl_usd=round(pnl, 2),
            pnl_pct=round(pnl_pct, 4),
            composite_score=decision.composite_score,
            agent_scores={e.role.value: e.score for e in decision.evaluations},
            agent_confidences={e.role.value: round(e.confidence, 2) for e in decision.evaluations},
        ))
        logger.info(
            "  [%s] %s %s entry=%.2f exit=%.2f outcome=%s pnl=$%.2f",
            ticker, entry_ts.date(), decision.decision.value,
            entry_price, exit_price, outcome, pnl,
        )

    return records


# ---------------------------------------------------------------------------
# Recommendations (current signals, no historical simulation)
# ---------------------------------------------------------------------------

async def get_recommendations(pm: PortfolioManager, tickers: list[str]) -> list[dict]:
    """Run agents on the latest bars and return current trade ideas."""
    from execution.alpaca_broker import AlpacaBroker
    settings = load_settings()
    broker = AlpacaBroker(settings.alpaca_key_id, settings.alpaca_secret, paper=True)

    recs = []
    async with broker:
        for ticker in tickers:
            try:
                bars = await broker.get_bars(ticker, timeframe="5Min", limit=200)
                account = await broker.get_account()
                chart = render_chart(ticker, bars)
                ctx = AnalysisContext(ticker=ticker, bars=bars, account=account, chart_image_path=chart)
                decision = await pm.decide(ctx)
                recs.append({
                    "ticker": ticker,
                    "direction": decision.decision.value,
                    "composite_score": decision.composite_score,
                    "is_actionable": decision.is_actionable,
                    "risk": {
                        "entry": decision.risk.entry if decision.risk else None,
                        "stop_loss": decision.risk.stop_loss if decision.risk else None,
                        "take_profit": decision.risk.take_profit if decision.risk else None,
                        "qty": decision.risk.qty if decision.risk else None,
                        "risk_reward": decision.risk.risk_reward if decision.risk else None,
                    } if decision.risk else None,
                    "agent_scores": {e.role.value: e.score for e in decision.evaluations},
                    "agent_confidences": {e.role.value: round(e.confidence, 2) for e in decision.evaluations},
                    "rationales": {e.role.value: e.rationale for e in decision.evaluations},
                    "as_of": datetime.now(tz=timezone.utc).isoformat(),
                })
            except Exception:
                logger.exception("recommendation failed for %s", ticker)
    return recs


# ---------------------------------------------------------------------------
# Dashboard generator
# ---------------------------------------------------------------------------

def build_dashboard(trades: list[TradeRecord], recommendations: list[dict], tickers: list[str]) -> Path:
    """Write dashboard/index.html with all data embedded."""
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)

    trades_json = json.dumps([asdict(t) for t in trades], default=str)
    recs_json = json.dumps(recommendations, default=str)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Trading Bot Dashboard</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
  :root {{
    --bg: #0d1117; --surface: #161b22; --border: #30363d;
    --text: #e6edf3; --muted: #8b949e;
    --green: #3fb950; --red: #f85149; --blue: #58a6ff;
    --yellow: #d29922; --purple: #a371f7;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text); font-family: -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; font-size: 14px; }}
  .header {{ background: var(--surface); border-bottom: 1px solid var(--border); padding: 16px 24px; display: flex; align-items: center; gap: 12px; }}
  .header h1 {{ font-size: 18px; font-weight: 600; }}
  .badge {{ background: #388bfd26; color: var(--blue); padding: 2px 8px; border-radius: 12px; font-size: 12px; }}
  .badge.paper {{ background: #3fb95026; color: var(--green); }}
  .container {{ max-width: 1400px; margin: 0 auto; padding: 24px; }}
  .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }}
  .grid-4 {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }}
  .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 16px; }}
  .card h2 {{ font-size: 13px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: .05em; margin-bottom: 12px; }}
  .stat-value {{ font-size: 28px; font-weight: 700; }}
  .stat-label {{ font-size: 12px; color: var(--muted); margin-top: 4px; }}
  .green {{ color: var(--green); }} .red {{ color: var(--red); }} .blue {{ color: var(--blue); }} .muted {{ color: var(--muted); }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ text-align: left; padding: 8px 12px; color: var(--muted); font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: .05em; border-bottom: 1px solid var(--border); }}
  td {{ padding: 10px 12px; border-bottom: 1px solid var(--border); }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: #ffffff08; }}
  .dir-long {{ background: #3fb95020; color: var(--green); padding: 2px 6px; border-radius: 4px; font-weight: 600; font-size: 11px; }}
  .dir-short {{ background: #f8514920; color: var(--red); padding: 2px 6px; border-radius: 4px; font-weight: 600; font-size: 11px; }}
  .dir-pass {{ background: #8b949e20; color: var(--muted); padding: 2px 6px; border-radius: 4px; font-weight: 600; font-size: 11px; }}
  .outcome-tp {{ color: var(--green); }} .outcome-sl {{ color: var(--red); }} .outcome-to {{ color: var(--muted); }}
  .score-bar {{ display: flex; align-items: center; gap: 6px; }}
  .score-bar-fill {{ height: 6px; border-radius: 3px; background: var(--blue); }}
  .tabs {{ display: flex; gap: 4px; margin-bottom: 16px; border-bottom: 1px solid var(--border); }}
  .tab {{ padding: 8px 16px; cursor: pointer; color: var(--muted); border-bottom: 2px solid transparent; font-weight: 500; margin-bottom: -1px; }}
  .tab.active {{ color: var(--text); border-bottom-color: var(--blue); }}
  .tab-content {{ display: none; }} .tab-content.active {{ display: block; }}
  .rec-card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 16px; margin-bottom: 12px; display: grid; grid-template-columns: auto 1fr auto; gap: 16px; align-items: start; }}
  .rec-ticker {{ font-size: 20px; font-weight: 700; }}
  .rec-detail {{ font-size: 12px; color: var(--muted); margin-top: 4px; }}
  .agent-grid {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; }}
  .agent-chip {{ background: #ffffff0a; border: 1px solid var(--border); border-radius: 4px; padding: 4px 8px; font-size: 11px; }}
  .action-btn {{ background: var(--green); color: #000; padding: 8px 16px; border-radius: 6px; font-weight: 600; font-size: 12px; border: none; cursor: pointer; }}
  .action-btn.short {{ background: var(--red); color: #fff; }}
  .action-btn.pass {{ background: var(--border); color: var(--muted); }}
  canvas {{ max-height: 300px; }}
  @media (max-width: 768px) {{ .grid-2, .grid-4 {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<div class="header">
  <h1>🤖 Trading Bot Dashboard</h1>
  <span class="badge paper">Alpaca Paper</span>
  <span class="badge" id="as-of"></span>
</div>

<div class="container">
  <!-- Stats row -->
  <div class="grid-4" id="stats-row"></div>

  <!-- Tabs -->
  <div class="tabs">
    <div class="tab active" onclick="switchTab('recommendations')">📡 Recommendations</div>
    <div class="tab" onclick="switchTab('trades')">📋 Trade Log</div>
    <div class="tab" onclick="switchTab('equity')">📈 Equity Curve</div>
    <div class="tab" onclick="switchTab('tickers')">🏷️ By Ticker</div>
  </div>

  <!-- Recommendations -->
  <div class="tab-content active" id="tab-recommendations">
    <div id="recs-container"></div>
  </div>

  <!-- Trade log -->
  <div class="tab-content" id="tab-trades">
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
        <h2>All Trades</h2>
        <input type="text" id="filter-input" placeholder="Filter ticker…"
          style="background:var(--bg);border:1px solid var(--border);color:var(--text);padding:4px 10px;border-radius:6px;font-size:13px;"
          oninput="renderTradeTable(this.value)">
      </div>
      <div style="overflow-x:auto"><table id="trade-table">
        <thead><tr>
          <th>Date</th><th>Ticker</th><th>Direction</th>
          <th>Entry</th><th>Exit</th><th>Qty</th>
          <th>SL</th><th>TP</th><th>R/R</th>
          <th>Outcome</th><th>P&L $</th><th>P&L %</th><th>Score</th>
        </tr></thead>
        <tbody id="trade-tbody"></tbody>
      </table></div>
    </div>
  </div>

  <!-- Equity curve -->
  <div class="tab-content" id="tab-equity">
    <div class="card">
      <h2>Equity Curve (cumulative P&L)</h2>
      <canvas id="equity-chart"></canvas>
    </div>
    <div class="grid-2" style="margin-top:16px">
      <div class="card">
        <h2>P&L Distribution</h2>
        <canvas id="pnl-hist"></canvas>
      </div>
      <div class="card">
        <h2>Outcome Breakdown</h2>
        <canvas id="outcome-pie"></canvas>
      </div>
    </div>
  </div>

  <!-- By ticker -->
  <div class="tab-content" id="tab-tickers">
    <div class="card">
      <h2>Per-Ticker Performance</h2>
      <canvas id="ticker-chart"></canvas>
    </div>
  </div>
</div>

<script>
const TRADES = $trades_json;
const RECS   = $recs_json;
const TICKERS = {json.dumps(tickers)};

// --- utils ---
const fmt2 = v => v >= 0 ? '+$' + v.toFixed(2) : '-$' + Math.abs(v).toFixed(2);
const fmtPct = v => (v >= 0 ? '+' : '') + v.toFixed(2) + '%';
const dirClass = d => d === 'LONG' ? 'dir-long' : d === 'SHORT' ? 'dir-short' : 'dir-pass';
const scoreColor = s => s >= 60 ? 'var(--green)' : s <= 40 ? 'var(--red)' : 'var(--muted)';

// --- tabs ---
function switchTab(name) {{
  document.querySelectorAll('.tab').forEach((t,i) => {{
    const names = ['recommendations','trades','equity','tickers'];
    t.classList.toggle('active', names[i] === name);
  }});
  document.querySelectorAll('.tab-content').forEach(c => {{
    c.classList.toggle('active', c.id === 'tab-' + name);
  }});
}}

// --- stats ---
function renderStats() {{
  const totalPnl = TRADES.reduce((s,t) => s + t.pnl_usd, 0);
  const wins = TRADES.filter(t => t.outcome === 'TP_HIT').length;
  const total = TRADES.length;
  const winRate = total ? (wins / total * 100) : 0;
  const avgRR = total ? TRADES.reduce((s,t) => s + t.risk_reward, 0) / total : 0;
  const maxDD = computeMaxDrawdown();
  document.getElementById('stats-row').innerHTML = `
    <div class="card"><div class="stat-value ${{totalPnl >= 0 ? 'green' : 'red'}}">${{fmt2(totalPnl)}}</div><div class="stat-label">Total P&L</div></div>
    <div class="card"><div class="stat-value ${{winRate >= 50 ? 'green' : 'red'}}">${{winRate.toFixed(1)}}%</div><div class="stat-label">Win Rate (${{wins}}/${{total}} trades)</div></div>
    <div class="card"><div class="stat-value blue">${{avgRR.toFixed(2)}}</div><div class="stat-label">Avg R/R</div></div>
    <div class="card"><div class="stat-value red">${{fmt2(-maxDD)}}</div><div class="stat-label">Max Drawdown</div></div>
  `;
  document.getElementById('as-of').textContent = RECS.length ? 'Updated ' + new Date(RECS[0].as_of).toLocaleString() : '';
}}

function computeMaxDrawdown() {{
  let peak = 0, cum = 0, maxDD = 0;
  TRADES.forEach(t => {{ cum += t.pnl_usd; if (cum > peak) peak = cum; maxDD = Math.max(maxDD, peak - cum); }});
  return maxDD;
}}

// --- recommendations ---
function renderRecs() {{
  const el = document.getElementById('recs-container');
  if (!RECS.length) {{ el.innerHTML = '<p style="color:var(--muted);padding:24px">No recommendations yet. Run backtest_runner.py to generate.</p>'; return; }}
  el.innerHTML = RECS.map(r => {{
    const scores = r.agent_scores;
    const chips = Object.entries(scores).map(([k,v]) => `
      <div class="agent-chip">
        ${{k}} <strong style="color:${{scoreColor(v)}}">${{v}}</strong>
        <span style="color:var(--muted)"> (${{(r.agent_confidences[k]*100).toFixed(0)}}%)</span>
      </div>`).join('');
    const risk = r.risk;
    const riskHtml = risk ? `
      Entry <strong>${{risk.entry?.toFixed(2)}}</strong> &nbsp;|&nbsp;
      SL <strong class="red">${{risk.stop_loss?.toFixed(2)}}</strong> &nbsp;|&nbsp;
      TP <strong class="green">${{risk.take_profit?.toFixed(2)}}</strong> &nbsp;|&nbsp;
      R/R <strong class="blue">${{risk.risk_reward?.toFixed(2)}}</strong> &nbsp;|&nbsp;
      Qty <strong>${{risk.qty}}</strong>` : 'No viable trade plan';
    return `<div class="rec-card">
      <div>
        <div class="rec-ticker">${{r.ticker}}</div>
        <span class="${{dirClass(r.direction)}}">${{r.direction}}</span>
      </div>
      <div>
        <div class="rec-detail" style="margin-bottom:6px">${{riskHtml}}</div>
        <div class="agent-grid">${{chips}}</div>
      </div>
      <div style="text-align:right">
        <div style="font-size:24px;font-weight:700;color:${{scoreColor(r.composite_score)}}">${{r.composite_score.toFixed(1)}}</div>
        <div style="font-size:11px;color:var(--muted)">composite</div>
        <button class="${{r.direction === 'LONG' ? 'action-btn' : r.direction === 'SHORT' ? 'action-btn short' : 'action-btn pass'}}" style="margin-top:8px" onclick="alert('Open Alpaca or Liquid to execute this trade.')">${{r.is_actionable ? '→ Execute' : 'No Signal'}}</button>
      </div>
    </div>`;
  }}).join('');
}}

// --- trade table ---
function renderTradeTable(filter = '') {{
  const filtered = filter ? TRADES.filter(t => t.ticker.toUpperCase().includes(filter.toUpperCase())) : TRADES;
  document.getElementById('trade-tbody').innerHTML = filtered.map(t => `
    <tr>
      <td class="muted">${{t.entry_time.slice(0,16).replace('T',' ')}}</td>
      <td><strong>${{t.ticker}}</strong></td>
      <td><span class="${{dirClass(t.direction)}}">${{t.direction}}</span></td>
      <td>${{t.entry_price.toFixed(2)}}</td>
      <td>${{t.exit_price.toFixed(2)}}</td>
      <td class="muted">${{t.qty}}</td>
      <td class="red">${{t.stop_loss.toFixed(2)}}</td>
      <td class="green">${{t.take_profit.toFixed(2)}}</td>
      <td class="blue">${{t.risk_reward.toFixed(2)}}</td>
      <td class="${{t.outcome === 'TP_HIT' ? 'outcome-tp' : t.outcome === 'SL_HIT' ? 'outcome-sl' : 'outcome-to'}}">${{t.outcome}}</td>
      <td class="${{t.pnl_usd >= 0 ? 'green' : 'red'}}">${{fmt2(t.pnl_usd)}}</td>
      <td class="${{t.pnl_pct >= 0 ? 'green' : 'red'}}">${{fmtPct(t.pnl_pct)}}</td>
      <td><div class="score-bar"><div class="score-bar-fill" style="width:${{t.composite_score}}px;background:${{scoreColor(t.composite_score)}}"></div><span style="color:${{scoreColor(t.composite_score)}}">${{t.composite_score.toFixed(0)}}</span></div></td>
    </tr>`).join('');
}}

// --- equity chart ---
let equityChart, pnlHist, outcomePie, tickerChart;
function renderCharts() {{
  // Equity curve
  const sorted = [...TRADES].sort((a,b) => a.entry_time < b.entry_time ? -1 : 1);
  let cum = 0;
  const labels = [], data = [];
  sorted.forEach(t => {{ cum += t.pnl_usd; labels.push(t.entry_time.slice(0,10)); data.push(+cum.toFixed(2)); }});
  equityChart = new Chart(document.getElementById('equity-chart'), {{
    type: 'line',
    data: {{ labels, datasets: [{{ label: 'Cumulative P&L ($)', data, borderColor: cum >= 0 ? '#3fb950' : '#f85149', backgroundColor: 'transparent', tension: 0.3, pointRadius: 2 }}] }},
    options: {{ plugins: {{ legend: {{ display: false }} }}, scales: {{ x: {{ ticks: {{ color: '#8b949e', maxTicksLimit: 8 }}, grid: {{ color: '#30363d' }} }}, y: {{ ticks: {{ color: '#8b949e' }}, grid: {{ color: '#30363d' }} }} }} }}
  }});

  // P&L distribution
  const bins = Array.from({{length: 20}}, (_, i) => i);
  const pnls = TRADES.map(t => t.pnl_usd);
  const min = Math.min(...pnls), max = Math.max(...pnls);
  const step = (max - min) / 20 || 1;
  const hist = Array(20).fill(0);
  pnls.forEach(p => {{ const idx = Math.min(19, Math.floor((p - min) / step)); hist[idx]++; }});
  pnlHist = new Chart(document.getElementById('pnl-hist'), {{
    type: 'bar',
    data: {{ labels: bins.map(i => '$' + (min + i * step).toFixed(0)), datasets: [{{ data: hist, backgroundColor: hist.map((_, i) => i > 9 ? '#3fb950' : '#f85149') }}] }},
    options: {{ plugins: {{ legend: {{ display: false }} }}, scales: {{ x: {{ ticks: {{ color: '#8b949e', maxRotation: 45 }}, grid: {{ display: false }} }}, y: {{ ticks: {{ color: '#8b949e' }}, grid: {{ color: '#30363d' }} }} }} }}
  }});

  // Outcome pie
  const tp = TRADES.filter(t => t.outcome === 'TP_HIT').length;
  const sl = TRADES.filter(t => t.outcome === 'SL_HIT').length;
  const to = TRADES.filter(t => t.outcome === 'TIMEOUT').length;
  outcomePie = new Chart(document.getElementById('outcome-pie'), {{
    type: 'doughnut',
    data: {{ labels: ['TP Hit', 'SL Hit', 'Timeout'], datasets: [{{ data: [tp, sl, to], backgroundColor: ['#3fb950', '#f85149', '#8b949e'] }}] }},
    options: {{ plugins: {{ legend: {{ labels: {{ color: '#e6edf3' }} }} }} }}
  }});

  // Per-ticker bar
  const byTicker = {{}};
  TRADES.forEach(t => {{ byTicker[t.ticker] = (byTicker[t.ticker] || 0) + t.pnl_usd; }});
  const tks = Object.keys(byTicker), vals = Object.values(byTicker);
  tickerChart = new Chart(document.getElementById('ticker-chart'), {{
    type: 'bar',
    data: {{ labels: tks, datasets: [{{ label: 'P&L ($)', data: vals, backgroundColor: vals.map(v => v >= 0 ? '#3fb950' : '#f85149') }}] }},
    options: {{ plugins: {{ legend: {{ display: false }} }}, scales: {{ x: {{ ticks: {{ color: '#8b949e' }}, grid: {{ display: false }} }}, y: {{ ticks: {{ color: '#8b949e' }}, grid: {{ color: '#30363d' }} }} }} }}
  }});
}}

// --- init ---
renderStats();
renderRecs();
renderTradeTable();
renderCharts();
</script>
</body>
</html>"""

    out = DASHBOARD_DIR / "index.html"
    out.write_text(html, encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(tickers: list[str], days: int, recommend_only: bool) -> None:
    settings = load_settings()
    # Same composition as live (bootstrap) so backtest results reflect the
    # strategy that actually trades. Social/liquid agents are excluded — their
    # feeds report current platform state (look-ahead on historical bars).
    pm = build_manager(settings, broker=None, include_live_only_agents=False)

    trades: list[TradeRecord] = []

    if not recommend_only:
        for ticker in tickers:
            logger.info("Fetching historical bars for %s (%d days)…", ticker, days)
            all_bars = await fetch_historical_bars(ticker, days=days)
            if all_bars is None or len(all_bars) < 250:
                logger.warning("Not enough data for %s, skipping backtest", ticker)
                continue
            logger.info("Running walk-forward backtest for %s…", ticker)
            ticker_trades = await backtest_ticker(pm, ticker, all_bars)
            trades.extend(ticker_trades)
            logger.info("  %s: %d trades, PnL=$%.2f", ticker, len(ticker_trades),
                        sum(t.pnl_usd for t in ticker_trades))

    logger.info("Fetching live recommendations…")
    recs = await get_recommendations(pm, tickers)

    dashboard_path = build_dashboard(trades, recs, tickers)
    logger.info("Dashboard written to %s", dashboard_path)
    webbrowser.open(dashboard_path.as_uri())


def main() -> None:
    parser = argparse.ArgumentParser(description="Trading bot backtest + recommendations")
    parser.add_argument("tickers", nargs="+", help="Ticker symbols, e.g. AAPL MSFT NVDA")
    parser.add_argument("--days", type=int, default=60, help="Historical lookback days (default 60)")
    parser.add_argument("--recommend-only", action="store_true",
                        help="Skip backtest, only show current recommendations")
    args = parser.parse_args()
    asyncio.run(run([t.upper() for t in args.tickers], args.days, args.recommend_only))


if __name__ == "__main__":
    main()
