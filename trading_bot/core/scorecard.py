"""Strategy scorecard — does the live track record actually show an edge?

Pulls realised trades (data/trades.json) and fill records (logs/decisions.jsonl)
and reports the metrics that matter — expectancy, profit factor, payoff,
drawdown — plus an HONEST confidence flag.

The confidence flag is the point. It's driven by sample size and a one-sample
t-test on per-trade P&L (t = expectancy / standard-error), so a great-looking
12-trade run is correctly flagged "insufficient" rather than "it works". An edge
is only called real once the expectancy is positive AND statistically
distinguishable from noise (t >= 2) on a meaningful sample.
"""
from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.trade_stats import load_closed_trades

_AUDIT_FILE = Path(__file__).parents[2] / "logs" / "decisions.jsonl"


# ── helpers ───────────────────────────────────────────────────────────────────

def _max_drawdown(pnls: list[float]) -> float:
    """Largest peak-to-trough dip of the cumulative P&L curve (<= 0)."""
    cum = peak = 0.0
    mdd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)
    return mdd


def _wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for the win rate (robust at small n)."""
    if n == 0:
        return (0.0, 0.0)
    phat = wins / n
    denom = 1 + z * z / n
    centre = (phat + z * z / (2 * n)) / denom
    margin = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denom
    return (round(100 * max(0.0, centre - margin), 1),
            round(100 * min(1.0, centre + margin), 1))


def _read_slippage(path: Path) -> list[float]:
    out: list[float] = []
    try:
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") == "fill" and rec.get("slippage_bps") is not None:
                    out.append(float(rec["slippage_bps"]))
    except Exception:
        pass
    return out


def _judge(n: int, expectancy: float, t_stat: float, profit_factor: float) -> tuple[str, str]:
    """Return (confidence_label, verdict_sentence) — deliberately conservative."""
    if n == 0:
        return ("insufficient", "No closed trades yet — nothing to evaluate.")
    if expectancy <= 0:
        return ("none",
                f"Losing money over {n} trades (expectancy ${expectancy:+.2f}/trade). "
                "No edge — fix or stop before risking more.")
    if n < 30:
        return ("insufficient",
                f"Positive so far (${expectancy:+.2f}/trade) but only {n} trades — far too "
                "few to trust. Need ~30 to say anything, ~100 to be confident.")
    if t_stat < 2.0:
        return ("low",
                f"Positive expectancy (${expectancy:+.2f}/trade, PF {profit_factor:.2f}) but the "
                f"edge isn't yet distinguishable from noise (t={t_stat:.1f} < 2). Keep going.")
    if n < 100:
        return ("medium",
                f"Real positive edge (${expectancy:+.2f}/trade, PF {profit_factor:.2f}, "
                f"t={t_stat:.1f}) on a modest {n}-trade sample. Promising — confirm past 100.")
    if t_stat >= 3.0:
        return ("high",
                f"Robust positive edge (${expectancy:+.2f}/trade, PF {profit_factor:.2f}, "
                f"t={t_stat:.1f}) over {n} trades. Statistically solid.")
    return ("high",
            f"Positive edge (${expectancy:+.2f}/trade, PF {profit_factor:.2f}, t={t_stat:.1f}) "
            f"over {n} trades.")


# ── scorecard ─────────────────────────────────────────────────────────────────

@dataclass
class Scorecard:
    trades:           int   = 0
    wins:             int   = 0
    losses:           int   = 0
    win_rate:         float = 0.0          # %
    win_rate_ci:      tuple = (0.0, 0.0)   # 95% Wilson, %
    expectancy:       float = 0.0          # $/trade (mean P&L)
    total_pnl:        float = 0.0
    avg_win:          float = 0.0
    avg_loss:         float = 0.0          # negative
    payoff_ratio:     float = 0.0          # avg_win / |avg_loss|
    profit_factor:    float = 0.0          # gross profit / gross loss (inf if no losses)
    max_drawdown:     float = 0.0          # <= 0
    sharpe_per_trade: float = 0.0          # mean / stdev of per-trade P&L
    t_stat:           float = 0.0          # expectancy / standard-error
    long_trades:      int   = 0
    short_trades:     int   = 0
    long_win_rate:    Optional[float] = None
    short_win_rate:   Optional[float] = None
    fills:            int   = 0
    avg_slippage_bps: Optional[float] = None
    confidence:       str   = "insufficient"
    verdict:          str   = ""

    def as_dict(self) -> dict:
        """JSON-safe dict (inf profit factor / payoff -> None, tuple -> list)."""
        d = self.__dict__.copy()
        if not math.isfinite(d["profit_factor"]):
            d["profit_factor"] = None
        if not math.isfinite(d["payoff_ratio"]):
            d["payoff_ratio"] = None
        d["win_rate_ci"] = list(d["win_rate_ci"])
        return d


def _dir_win_rate(trades: list[dict], direction: str) -> tuple[int, Optional[float]]:
    rows = [t for t in trades if str(t.get("direction", "")).upper() == direction]
    if not rows:
        return (0, None)
    wins = sum(1 for t in rows if float(t.get("pnl") or 0) > 0)
    return (len(rows), round(100 * wins / len(rows), 1))


def build_scorecard(trades_path: Optional[Path] = None,
                    audit_path: Optional[Path] = None) -> Scorecard:
    trades = load_closed_trades(trades_path)
    pnls = [float(t.get("pnl") or 0.0) for t in trades]
    n = len(pnls)
    slips = _read_slippage(audit_path or _AUDIT_FILE)
    avg_slip = round(sum(slips) / len(slips), 1) if slips else None

    if n == 0:
        conf, verdict = _judge(0, 0.0, 0.0, 0.0)
        return Scorecard(fills=len(slips), avg_slippage_bps=avg_slip,
                         confidence=conf, verdict=verdict)

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_profit, gross_loss = sum(wins), -sum(losses)
    avg_win = gross_profit / len(wins) if wins else 0.0
    avg_loss = -gross_loss / len(losses) if losses else 0.0
    expectancy = sum(pnls) / n
    stdev = statistics.stdev(pnls) if n >= 2 else 0.0
    se = stdev / math.sqrt(n) if (n >= 2 and stdev > 0) else 0.0
    t_stat = expectancy / se if se > 0 else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else math.inf
    payoff = (avg_win / abs(avg_loss)) if avg_loss else math.inf

    long_n, long_wr = _dir_win_rate(trades, "LONG")
    short_n, short_wr = _dir_win_rate(trades, "SHORT")
    conf, verdict = _judge(n, expectancy, t_stat, profit_factor)

    return Scorecard(
        trades=n, wins=len(wins), losses=len(losses),
        win_rate=round(100 * len(wins) / n, 1),
        win_rate_ci=_wilson_ci(len(wins), n),
        expectancy=round(expectancy, 2),
        total_pnl=round(sum(pnls), 2),
        avg_win=round(avg_win, 2), avg_loss=round(avg_loss, 2),
        payoff_ratio=round(payoff, 2) if math.isfinite(payoff) else payoff,
        profit_factor=round(profit_factor, 2) if math.isfinite(profit_factor) else profit_factor,
        max_drawdown=round(_max_drawdown(pnls), 2),
        sharpe_per_trade=round(expectancy / stdev, 3) if stdev > 0 else 0.0,
        t_stat=round(t_stat, 2),
        long_trades=long_n, short_trades=short_n,
        long_win_rate=long_wr, short_win_rate=short_wr,
        fills=len(slips), avg_slippage_bps=avg_slip,
        confidence=conf, verdict=verdict,
    )


# ── rendering ─────────────────────────────────────────────────────────────────

def _pf(value: float) -> str:
    return "∞" if not math.isfinite(value) else f"{value:.2f}"


def one_line(sc: Scorecard) -> str:
    """Compact one-liner for the EOD report / Telegram."""
    return (f"Scorecard: {sc.confidence.upper()} — {sc.trades} trades, "
            f"exp ${sc.expectancy:+.2f}/trade, PF {_pf(sc.profit_factor)}, "
            f"win {sc.win_rate}%, t={sc.t_stat}.")


def format_scorecard(sc: Scorecard) -> str:
    """Full human-readable scorecard."""
    line = "─" * 52
    rows = [
        "STRATEGY SCORECARD — live paper track record",
        line,
        f"Sample          : {sc.trades} closed trades   "
        f"(LONG {sc.long_trades} / SHORT {sc.short_trades})",
    ]
    if sc.trades:
        rows += [
            f"Win rate        : {sc.win_rate}%   "
            f"(95% CI {sc.win_rate_ci[0]}–{sc.win_rate_ci[1]}%)",
            f"Expectancy      : ${sc.expectancy:+.2f} / trade",
            f"Total P&L       : ${sc.total_pnl:+.2f}",
            f"Avg win / loss  : ${sc.avg_win:+.2f} / ${sc.avg_loss:+.2f}   "
            f"(payoff {_pf(sc.payoff_ratio)})",
            f"Profit factor   : {_pf(sc.profit_factor)}",
            f"Max drawdown    : ${sc.max_drawdown:+.2f}",
            f"Per-trade Sharpe: {sc.sharpe_per_trade}   (t-stat {sc.t_stat})",
        ]
        if sc.long_win_rate is not None or sc.short_win_rate is not None:
            rows.append(
                f"By direction    : LONG {sc.long_win_rate}% / SHORT {sc.short_win_rate}%")
    if sc.avg_slippage_bps is not None:
        rows.append(f"Avg slippage    : {sc.avg_slippage_bps:+.1f} bps   (over {sc.fills} fills)")
    rows += [
        line,
        f"Confidence      : {sc.confidence.upper()}",
        f"Verdict         : {sc.verdict}",
    ]
    return "\n".join(rows)
