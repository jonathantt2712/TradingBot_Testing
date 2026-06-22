"""Quantitative Analyst — technical indicator convergence (day-trading edition).

Day-trading signals layered on top of the original RSI/MACD/EMA/VWAP stack:

  • Relative Strength vs SPY   — how much is this stock outperforming the market today?
  • Volume Surge               — current cumulative volume vs 20-day average daily volume
  • Intraday Momentum          — price vs today's open (directional + magnitude)
  • Day-Range Position         — where is price in today's high-low range?
  • Candlestick Patterns       — count of bullish/bearish chart patterns (pandas-ta)

All signals collapse into a single composite 1-100 score using a weighted mean.
Confidence is inversely proportional to signal disagreement (std-dev spread).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from core.base_agent import NEUTRAL_SCORE, BaseAgent, clamp_score
from core.enums import AgentRole
from core.models import AgentEvaluation, AnalysisContext

logger = logging.getLogger(__name__)

try:
    import pandas_ta as ta  # type: ignore
    _HAS_PANDAS_TA = True
except Exception:
    _HAS_PANDAS_TA = False

# cdl_pattern(name="all") prints "[i] Requires TA-Lib" for every pattern (~60
# lines) unless the native C library is installed. Gate on actual talib presence.
try:
    import talib as _talib  # type: ignore  # noqa: F401
    _HAS_TALIB_C = True
except Exception:
    _HAS_TALIB_C = False


# Signal weights (must sum to 1.0)
_WEIGHTS = {
    "rsi":           0.11,
    "macd":          0.10,
    "ema_cross":     0.09,
    "vwap":          0.10,
    "rel_strength":  0.11,
    "volume_surge":  0.09,
    "intraday_mom":  0.05,
    "day_range_pos": 0.03,
    "orb":           0.06,
    "vol_confirm":   0.05,
    "adx_filter":    0.05,
    "bb_squeeze":    0.05,
    "zscore":        0.04,
    "stochastic":    0.04,
    "divergence":    0.03,
    "gap":           0.04,
    "trend_join":    0.06,
}

# ── Research-derived thresholds ──────────────────────────────────────────────
# #1 PEAD: ignore entries in the first 30 min of RTH (9:30-10:00 ET)
_OPEN_NOISE_BARS = 6          # 6 × 5-min = 30 min
# #2 CPT: tighten SL when stock shows lottery-like profile
_LOTTERY_RECENT_BARS  = 20   # look back 20 bars (~1.5 hours)
_LOTTERY_PRICE_THRESH = 0.12  # 12% move in 20 bars = lottery territory
_LOTTERY_VOL_THRESH   = 2.5   # volume surge > 2.5× avg = retail frenzy
# #3 Transaction drag: minimum volume confirmation ratio
_VOL_CONFIRM_RATIO = 1.3      # entry bar volume must be >= 1.3× rolling avg
# #4 Retail attention: classify as retail-driven if both conditions met
_RETAIL_PRICE_THRESH = 0.08   # 8% move in 3 trading days
_RETAIL_VOL_THRESH   = 2.0    # volume surge > 2.0×


class TechnicalAgent(BaseAgent):
    role = AgentRole.TECHNICAL

    def __init__(
        self,
        *,
        weight: float = 0.5,
        min_bars: int = 50,
        spy_bars: Optional[pd.DataFrame] = None,    # injected by caller if available
        daily_bars: Optional[pd.DataFrame] = None,  # 1-day OHLCV; enables 200-SMA trend filter
    ) -> None:
        super().__init__(weight=weight)
        self.min_bars   = min_bars
        self.spy_bars   = spy_bars    # set externally each cycle: agent.spy_bars = spy_df
        self.daily_bars = daily_bars  # set externally: agent.daily_bars = daily_df

    async def evaluate(self, ctx: AnalysisContext) -> AgentEvaluation:
        bars = ctx.bars
        if bars is None or len(bars) < self.min_bars:
            return AgentEvaluation(
                role=self.role,
                score=NEUTRAL_SCORE,
                confidence=0.2,
                rationale=f"insufficient bars (<{self.min_bars})",
            )

        df = bars.copy()
        signals: dict[str, float] = {}
        h_bias = h_desc = h_agree = h_disagree = None
        _stale_note = ""  # set below if data is stale; carried into rationale

        # ── Stale-data guard ─────────────────────────────────────────────
        # Skip entirely in backtest mode: historical bars are always "old" vs now().
        # In live mode: if bars are >30 min old during regular trading hours AND
        # the market was actually open today (not a holiday), reduce confidence
        # rather than aborting — so the composite still reflects a real signal,
        # just with less weight.
        if not getattr(ctx, "backtest_mode", False):
          try:
            from datetime import datetime, date as _date
            from zoneinfo import ZoneInfo
            _ET = ZoneInfo("America/New_York")

            # NYSE observed holidays 2025-2027 (add new years as needed)
            _NYSE_HOLIDAYS = {
                # 2025
                _date(2025, 1, 1), _date(2025, 1, 20), _date(2025, 2, 17),
                _date(2025, 4, 18), _date(2025, 5, 26), _date(2025, 6, 19),
                _date(2025, 7, 4), _date(2025, 9, 1), _date(2025, 11, 27),
                _date(2025, 12, 25),
                # 2026
                _date(2026, 1, 1), _date(2026, 1, 19), _date(2026, 2, 16),
                _date(2026, 4, 3), _date(2026, 5, 25), _date(2026, 6, 19),
                _date(2026, 7, 3), _date(2026, 9, 7), _date(2026, 11, 26),
                _date(2026, 12, 25),
                # 2027
                _date(2027, 1, 1), _date(2027, 1, 18), _date(2027, 2, 15),
                _date(2027, 3, 26), _date(2027, 5, 31), _date(2027, 6, 18),
                _date(2027, 7, 5), _date(2027, 9, 6), _date(2027, 11, 25),
                _date(2027, 12, 24),
            }

            last_ts = df.index[-1]
            if last_ts.tzinfo is None:
                last_ts = last_ts.tz_localize("UTC")
            last_ts_et = last_ts.astimezone(_ET)
            now_et = datetime.now(_ET)
            age_min = (now_et - last_ts_et).total_seconds() / 60
            mkt_open  = now_et.replace(hour=9,  minute=30, second=0, microsecond=0)
            mkt_close = now_et.replace(hour=16, minute=0,  second=0, microsecond=0)
            today_et  = now_et.date()
            is_rth = (
                now_et.weekday() < 5
                and today_et not in _NYSE_HOLIDAYS
                and mkt_open <= now_et <= mkt_close
            )
            if is_rth and age_min > 30:
                logger.warning(
                    "TechnicalAgent: stale data for %s — last bar %.0fmin old",
                    ctx.ticker, age_min,
                )
                # Don't abort — continue computing the score but flag as stale.
                # The reduced confidence means the composite is barely affected.
                _stale_note = f"stale data: last bar {age_min:.0f}min old — "
          except Exception:
            pass  # tz parse error — skip guard

        # ── Research #1 (PEAD): Opening-noise guard ──────────────────────
        # Skip scoring during the first 30 min of RTH (9:30–10:00 ET).
        # Post-earnings announcement drift research shows the open is noisy
        # and generates many false signals that reverse quickly.
        if hasattr(df.index, "date") and len(df.index) > 0:
            today = df.index[-1].date()
            today_df = df[df.index.map(lambda x: x.date()) == today]
            if len(today_df) < _OPEN_NOISE_BARS:
                return AgentEvaluation(
                    role=self.role,
                    score=NEUTRAL_SCORE,
                    confidence=0.15,
                    rationale=(
                        f"open-noise guard: {len(today_df)}/{_OPEN_NOISE_BARS} bars "
                        "since open — waiting for 9:30–10:00 ET noise to clear"
                    ),
                )

        # ── Original indicators ──────────────────────────────────────────
        rsi = self._rsi(df["close"])
        signals["rsi"] = float(np.interp(rsi, [20, 50, 80], [80, 50, 20]))

        macd_hist = self._macd_hist(df["close"])
        macd_std = df["close"].diff().std() or 1.0
        signals["macd"] = float(np.clip(50 + (macd_hist / macd_std) * 25, 1, 100))

        ema_fast = df["close"].ewm(span=9, adjust=False).mean().iloc[-1]
        ema_slow = df["close"].ewm(span=21, adjust=False).mean().iloc[-1]
        ema_spread_pct = (ema_fast - ema_slow) / ema_slow * 100
        signals["ema_cross"] = float(np.clip(50 + ema_spread_pct * 10, 1, 100))

        vwap = self._session_vwap(df)
        last = float(df["close"].iloc[-1])
        vwap_spread_pct = (last - vwap) / vwap * 100
        signals["vwap"] = float(np.clip(50 + vwap_spread_pct * 10, 1, 100))

        # ── Day-trading signals ──────────────────────────────────────────
        # 1. Relative Strength vs SPY
        rs = self._relative_strength(df)
        if rs is not None:
            # RS > 1: outperforming → bullish; RS < 1: underperforming → bearish
            # Map: RS=2.0 → 80, RS=1.0 → 50, RS=0.0 → 20
            signals["rel_strength"] = float(np.clip(50 + (rs - 1.0) * 40, 1, 100))

        # 2. Volume Surge (current session volume vs 20-day avg)
        vol_ratio = self._volume_surge(df)
        if vol_ratio is not None:
            # Neutral at 1× avg. Bullish if price is up AND volume surging.
            day_chg = _day_change_pct(df)
            if vol_ratio >= 1.5:
                # high volume — direction from price change
                signals["volume_surge"] = float(np.clip(50 + day_chg * 8 * min(vol_ratio / 2, 2.0), 1, 100))
            else:
                signals["volume_surge"] = 50.0  # low volume = no edge

        # 3. Intraday Momentum (price vs today's open)
        day_chg_pct = _day_change_pct(df)
        # ±3% maps to ±30 points; clip to [1, 99]
        signals["intraday_mom"] = float(np.clip(50 + day_chg_pct * 10, 1, 99))

        # 4. Day Range Position (where is close in today's H-L?)
        drp = self._day_range_position(df)
        if drp is not None:
            # 0 = at low (bearish=20), 0.5 = mid (50), 1 = at high (bullish=80)
            signals["day_range_pos"] = float(np.clip(drp * 80 + 10, 10, 90))

        # 5. Opening Range Breakout (RVOL-gated: unconfirmed breakout capped if RVOL < 1.5)
        orb = self._orb_score(df, rvol=vol_ratio)
        if orb is not None:
            signals["orb"] = orb

        # 5b. Gap signal (today open vs prior close — fade small gaps, ride large ones)
        gap_val = self._gap_signal(df)
        if gap_val is not None:
            signals["gap"] = gap_val

        # 5c. Trend Join: prev-day high breakout + HOD + pre-market high
        tj_val = self._trend_join_score(df)
        if tj_val is not None:
            signals["trend_join"] = tj_val

        # 6. Candlestick Patterns (pandas-ta only)
        if _HAS_PANDAS_TA:
            pat_score = self._pattern_score(df)
            if pat_score is not None:
                signals["patterns"] = pat_score

        # 7. ADX Trend Strength
        adx_val = self._adx_signal(df)
        if adx_val is not None:
            signals["adx_filter"] = adx_val

        # 8. Bollinger Band Squeeze
        bb_val = self._bollinger_squeeze_signal(df)
        if bb_val is not None:
            signals["bb_squeeze"] = bb_val

        # 9. Z-Score Mean Reversion
        zs_val = self._zscore_signal(df)
        if zs_val is not None:
            signals["zscore"] = zs_val

        # 10. Stochastic Oscillator
        sto_val = self._stochastic_signal(df)
        if sto_val is not None:
            signals["stochastic"] = sto_val

        # 11. RSI Divergence
        div_val = self._divergence_signal(df)
        if div_val is not None:
            signals["divergence"] = div_val

        # ── Research-derived filters ─────────────────────────────────────
        # Research #3 (Barber & Odean): Volume confirmation gate.
        # Only count a signal as high-conviction if current bar volume
        # is at least 1.3× the 20-bar rolling average. Low-volume moves
        # carry high transaction-drag risk and should be discounted.
        vol_confirm = self._volume_confirm(df)
        signals["vol_confirm"] = vol_confirm

        # Research #2 (CPT / Lottery): detect retail-frenzy profile.
        # If the stock moved >12% in the last 20 bars AND volume > 2.5×,
        # classify as lottery stock and apply a score penalty.
        lottery_penalty = self._lottery_penalty(df)
        if lottery_penalty > 0:
            # Lottery penalty is a direct deduction applied AFTER weighting.
            # Store it separately so callers can use it for tighter SL sizing.
            signals["_lottery_penalty"] = lottery_penalty

        # Research #4 (Gao et al.): classify momentum driver.
        # Retail-attention-driven momentum requires stricter entry threshold.
        retail_driven = self._is_retail_driven(df)
        if retail_driven:
            # Apply a +5-point threshold surcharge (stored as negative signal)
            signals["_retail_surcharge"] = 5.0

        # ── Time-of-day phase weight adjustment ──────────────────────────
        try:
            _now_et = datetime.now(ZoneInfo("America/New_York"))
            _now_et_h = _now_et.hour + _now_et.minute / 60.0
            if 9.5 <= _now_et_h < 10.5:       # 9:30–10:30 ET: momentum phase
                _tod_mult = {
                    "orb": 1.3, "volume_surge": 1.3, "intraday_mom": 1.3, "rel_strength": 1.2,
                    "zscore": 0.7, "stochastic": 0.7, "divergence": 0.7,
                }
                _tod_phase = "momentum"
            elif 10.5 <= _now_et_h < 14.5:    # 10:30–14:30 ET: mean-reversion phase
                _tod_mult = {
                    "zscore": 1.3, "stochastic": 1.3, "divergence": 1.2, "vwap": 1.2,
                    "orb": 0.7, "intraday_mom": 0.8,
                }
                _tod_phase = "mean_reversion"
            elif 15.5 <= _now_et_h < 16.0:    # 15:30–16:00 ET: late-session dampening
                _tod_mult = {}
                _tod_phase = "late_session"
            else:
                _tod_mult = {}
                _tod_phase = "afternoon" if _now_et_h >= 14.5 else "other"
        except Exception:
            _tod_mult = {}
            _tod_phase = None

        # ── Composite score ──────────────────────────────────────────────
        clean = {k: v for k, v in signals.items() if not np.isnan(v)}
        if not clean:
            return AgentEvaluation(role=self.role, score=NEUTRAL_SCORE,
                                   confidence=0.2, rationale="no valid signals")

        # Extract meta-signals before weighting (prefixed with _)
        lottery_penalty  = clean.pop("_lottery_penalty",  0.0)
        retail_surcharge = clean.pop("_retail_surcharge", 0.0)

        # Weighted mean with time-of-day phase adjustment
        total_w = num = 0.0
        for key, val in clean.items():
            w = _WEIGHTS.get(key, 0.05) * _tod_mult.get(key, 1.0)
            num += val * w
            total_w += w
        raw_score = num / total_w if total_w else 50.0

        # Late-session dampening: after 15:30 ET pull score 30% toward neutral
        if _tod_phase == "late_session":
            raw_score = raw_score + (50.0 - raw_score) * 0.30

        # Research #2: subtract lottery penalty (pushes score toward 50 = neutral)
        if lottery_penalty > 0:
            # Pull score toward 50 proportionally to penalty magnitude
            raw_score = raw_score - (raw_score - 50.0) * min(lottery_penalty / 30.0, 0.6)

        score = clamp_score(raw_score)

        spread = float(np.std(list(clean.values())))
        confidence = float(max(0.3, min(0.90, 1.0 - spread / 50.0)))

        rationale = (
            f"RSI={rsi:.1f} MACD_h={macd_hist:.4f} "
            f"EMA({'↑' if ema_fast > ema_slow else '↓'}) "
            f"px{'>' if last > vwap else '<'}VWAP "
            f"day={day_chg_pct:+.1f}%"
        )
        if "rel_strength" in clean:
            rationale += f" RS={rs:.2f}"
        if "volume_surge" in clean and vol_ratio is not None:
            rationale += f" vol={vol_ratio:.1f}x"
        if "orb" in clean:
            orb_val = clean["orb"]
            orb_tag = "↑BRK" if orb_val > 55 else ("↓BRK" if orb_val < 45 else "=RNG")
            rationale += f" ORB{orb_tag}"
        if "gap" in clean:
            rationale += f" GAP={'FADE' if clean['gap'] < 50 else 'CONT'}"
        if _tod_phase:
            rationale += f" [{_tod_phase}]"
        if lottery_penalty > 0:
            rationale += f" [LOTTERY pen={lottery_penalty:.0f}]"
        if retail_surcharge > 0:
            rationale += " [RETAIL-DRIVEN +5thr]"
        if "adx_filter" in clean and abs(clean["adx_filter"] - 50) > 5:
            rationale += f" ADX={'trend' if clean['adx_filter'] > 55 else 'weak'}"
        if "bb_squeeze" in clean and (clean["bb_squeeze"] > 62 or clean["bb_squeeze"] < 38):
            rationale += f" BB={'BRK↑' if clean['bb_squeeze'] > 62 else 'BRK↓'}"
        if "zscore" in clean and (clean["zscore"] > 65 or clean["zscore"] < 35):
            rationale += f" Z={'oversold' if clean['zscore'] > 65 else 'overbought'}"
        if "divergence" in clean and (clean["divergence"] > 65 or clean["divergence"] < 35):
            rationale += f" {'BULL-DIV' if clean['divergence'] > 65 else 'BEAR-DIV'}"

        # ── Multi-Timeframe Gate ─────────────────────────────────────────
        # If 1-hour trend strongly disagrees with the 5-min composite,
        # pull the score toward neutral. Agreement boosts confidence.
        hourly = getattr(ctx, "hourly_bars", None)
        if hourly is not None and len(hourly) >= 10:
            h_bias, h_desc = self._hourly_direction(hourly)
            # Strong disagreement: 5m bullish but 1h bearish (or vice versa)
            h_disagree = (score > 60 and h_bias < 42) or (score < 40 and h_bias > 58)
            h_agree    = (score > 55 and h_bias > 55) or (score < 45 and h_bias < 45)

            if h_disagree:
                # Pull score 25% toward neutral (50)
                score = score + (50.0 - score) * 0.25
                score = clamp_score(score)
                confidence = max(0.30, confidence * 0.75)
                rationale += f" [MTF-CONFLICT: {h_desc}]"
            elif h_agree:
                # Boost confidence by up to 10%
                confidence = min(0.90, confidence * 1.10)
                rationale += f" [MTF-CONFIRM: {h_desc}]"
            else:
                rationale += f" [MTF: {h_desc}]"

        # ── Elaborate reasoning for dashboard/audit ──────────────────────────
        def _dir(s: float) -> str:
            return "bullish" if s > 60 else ("bearish" if s < 40 else "neutral")

        signal_details = []
        _meta: dict = {
            "rsi": (
                "RSI (14)",
                f"{rsi:.1f}",
                f"RSI {rsi:.1f} — {'oversold, potential reversal up' if rsi < 35 else 'overbought, potential reversal down' if rsi > 65 else 'neutral momentum zone'}",
            ),
            "macd": (
                "MACD Histogram",
                f"{macd_hist:.4f}",
                f"Histogram {'positive → upward momentum' if macd_hist > 0 else 'negative → downward momentum'}",
            ),
            "ema_cross": (
                "EMA Cross (9/21)",
                f"{ema_spread_pct:+.2f}%",
                f"Fast EMA {'above' if ema_fast > ema_slow else 'below'} slow EMA by {abs(ema_spread_pct):.2f}% — {'bullish' if ema_fast > ema_slow else 'bearish'} cross",
            ),
            "vwap": (
                "VWAP Deviation",
                f"price {vwap_spread_pct:+.2f}% vs VWAP",
                f"Price {'above' if last > vwap else 'below'} session VWAP ({vwap:.2f}) by {abs(vwap_spread_pct):.2f}%",
            ),
            "rel_strength": (
                "Relative Strength vs SPY",
                f"{rs:.2f}x" if rs is not None else "N/A (SPY flat)",
                f"{'Outperforming' if rs is not None and rs > 1 else 'Underperforming'} SPY by ratio {rs:.2f}" if rs is not None else "SPY too flat to compute RS",
            ),
            "volume_surge": (
                "Volume Surge",
                f"{vol_ratio:.1f}x 20-day avg" if vol_ratio is not None else "N/A",
                f"Today's projected volume is {vol_ratio:.1f}x the 20-day average — {'unusual activity' if vol_ratio is not None and vol_ratio >= 1.5 else 'normal volume'}" if vol_ratio is not None else "Insufficient history for volume comparison",
            ),
            "intraday_mom": (
                "Intraday Momentum",
                f"{day_chg_pct:+.1f}% from open",
                f"Stock is {'up' if day_chg_pct > 0 else 'down'} {abs(day_chg_pct):.1f}% from today's open",
            ),
            "day_range_pos": (
                "Day Range Position",
                f"{drp:.2f}" if drp is not None else "N/A",
                f"Price at {drp*100:.0f}% of today's high-low range (0%=at low, 100%=at high)" if drp is not None else "N/A",
            ),
            "orb": (
                "Opening Range Breakout",
                f"score {clean.get('orb', 50):.0f}",
                f"Price {'above ORB high → breakout' if clean.get('orb', 50) > 55 else 'below ORB low → breakdown' if clean.get('orb', 50) < 45 else 'inside opening range — no directional edge'}",
            ),
            "vol_confirm": (
                "Volume Confirmation",
                f"score {clean.get('vol_confirm', 50):.0f}",
                f"Current bar volume {'confirms' if clean.get('vol_confirm', 50) >= 60 else 'does NOT confirm'} the move (threshold: 1.3x rolling avg)",
            ),
            "patterns": (
                "Candlestick Patterns",
                f"score {clean.get('patterns', 50):.0f}",
                "Bullish patterns outweigh bearish" if clean.get("patterns", 50) > 55 else ("Bearish patterns outweigh bullish" if clean.get("patterns", 50) < 45 else "No significant candlestick pattern"),
            ),
            "adx_filter": (
                "ADX Trend Strength",
                f"{adx_val:.1f}" if adx_val is not None else "N/A",
                f"ADX {'trending (>25)' if adx_val is not None and abs(adx_val - 50) > 5 else 'ranging (<25)'}. Strong trend = directional signals weighted higher",
            ),
            "bb_squeeze": (
                "Bollinger Squeeze",
                "squeeze→breakout" if bb_val is not None and (bb_val > 60 or bb_val < 40) else "coiling/ranging",
                "Band squeeze resolving with directional breakout" if bb_val is not None and (bb_val > 60 or bb_val < 40) else "Bands still compressed — potential energy building",
            ),
            "zscore": (
                "Z-Score (50-bar)",
                "N/A" if zs_val is None else f"score {zs_val:.0f}",
                "Price deeply oversold vs 50-bar mean (bullish reversal candidate)" if zs_val is not None and zs_val > 65 else ("Price deeply overbought vs 50-bar mean (bearish reversal candidate)" if zs_val is not None and zs_val < 35 else "Price near 50-bar mean — no mean-reversion edge"),
            ),
            "stochastic": (
                "Stochastic (14,3,3)",
                "N/A" if sto_val is None else f"score {sto_val:.0f}",
                "Stochastic %K crossing %D — oversold/overbought condition and momentum cross",
            ),
            "divergence": (
                "RSI Divergence",
                "N/A" if div_val is None else ("bullish div" if div_val > 60 else ("bearish div" if div_val < 40 else "no divergence")),
                "Bullish divergence: price at lows but RSI making higher low — potential reversal" if div_val is not None and div_val > 60 else ("Bearish divergence: price at highs but RSI making lower high — potential reversal" if div_val is not None and div_val < 40 else "No RSI/price divergence detected"),
            ),
            "gap": (
                "Gap Signal",
                f"score {clean.get('gap', 50):.0f}",
                f"{'Fade bias — gap expected to fill (small gap <0.5%)' if clean.get('gap', 50) < 50 else 'Continuation bias — gap likely holds (large gap >2%)'}",
            ),
            "trend_join": (
                "Trend Join",
                f"score {clean.get('trend_join', 50):.0f}",
                f"{'Breaking above prev-day high / HOD — trend continuation' if clean.get('trend_join', 50) > 60 else 'Breaking below prev-day low / LOD — trend breakdown' if clean.get('trend_join', 50) < 40 else 'Price inside prior-day range — no structural breakout'}",
            ),
        }
        for key, sig_score in clean.items():
            if key not in _meta:
                continue
            display, raw_str, note = _meta[key]
            signal_details.append({
                "name": key,
                "display": display,
                "raw": raw_str,
                "score": round(sig_score, 1),
                "weight_pct": round(_WEIGHTS.get(key, 0.05) * 100, 1),
                "direction": _dir(sig_score),
                "note": note,
            })

        reasoning = {
            "signals": signal_details,
            "price": round(last, 4),
            "vwap": round(vwap, 4),
            "rsi": round(rsi, 1),
            "day_change_pct": round(day_chg_pct, 2),
            "flags": {
                "lottery_stock": lottery_penalty > 0,
                "lottery_penalty": round(lottery_penalty, 1) if lottery_penalty > 0 else None,
                "retail_driven": retail_surcharge > 0,
                "retail_surcharge": retail_surcharge if retail_surcharge > 0 else None,
                "tod_phase": _tod_phase,
            },
            "mtf": {
                "hourly_bias": round(h_bias, 1) if h_bias is not None else None,
                "hourly_desc": h_desc if h_desc is not None else "no hourly data",
                "agreement": "confirm" if h_agree else ("conflict" if h_disagree else "neutral"),
            } if hourly is not None else None,
        }

        if _stale_note:
            confidence = min(confidence, 0.15)  # reduce weight in composite
            rationale = _stale_note + rationale

        return AgentEvaluation(
            role=self.role,
            score=score,
            confidence=confidence,
            rationale=rationale,
            data={
                "signals": clean, "rsi": rsi, "vwap": vwap,
                "day_chg_pct": day_chg_pct,
                "lottery_penalty":  lottery_penalty,
                "retail_surcharge": retail_surcharge,
                "retail_driven":    retail_surcharge > 0,
            },
            reasoning=reasoning,
        )

    # ── Day-trading signal helpers ────────────────────────────────────────

    def _relative_strength(self, df: pd.DataFrame) -> Optional[float]:
        """Ratio of stock's intraday return to SPY's intraday return.

        RS > 1 means the stock is outperforming the market today.
        Returns None if SPY data not available or SPY flat.
        """
        spy = self.spy_bars
        if spy is None or spy.empty:
            return None
        stock_chg = _day_change_pct(df)
        spy_chg   = _day_change_pct(spy)
        if abs(spy_chg) < 0.01:   # SPY flat → RS undefined
            return None
        return (1 + stock_chg / 100) / (1 + spy_chg / 100)

    def _volume_surge(self, df: pd.DataFrame) -> Optional[float]:
        """Ratio of today's cumulative volume to 20-day average daily volume.

        A ratio >= 1.5 by session midpoint signals unusual interest.
        """
        today = df.index[-1].date()
        today_df = df[df.index.map(lambda x: x.date()) == today]
        if today_df.empty or len(df) < 40:
            return None

        cum_vol_today = float(today_df["volume"].sum())
        # 20-day avg: use prior days only
        prior = df[df.index.map(lambda x: x.date()) < today]
        if prior.empty:
            return None
        daily_vols = prior.groupby(prior.index.date)["volume"].sum()
        avg_daily = float(daily_vols.tail(20).mean())
        if avg_daily <= 0:
            return None
        # Normalise: if we're at midday (half a session), scale up
        bars_per_day = 78  # 6.5 h × 12 five-min bars
        fraction_of_day = min(len(today_df) / bars_per_day, 1.0)
        if fraction_of_day < 0.05:
            return None
        projected_vol = cum_vol_today / fraction_of_day
        return projected_vol / avg_daily

    # ── Research-derived signal helpers ──────────────────────────────────

    def _volume_confirm(self, df: pd.DataFrame) -> float:
        """Research #3 (Barber & Odean): transaction-drag gate.

        Returns a [1, 100] score: 80 if current bar volume >= 1.3× rolling
        avg (high conviction), 30 if below (low conviction / don't trade).
        This penalizes low-volume setups where slippage kills EV.
        """
        if len(df) < 25:
            return 50.0
        vol = df["volume"]
        rolling_avg = vol.iloc[-25:-1].mean()  # 24-bar avg excluding current
        if rolling_avg <= 0:
            return 50.0
        ratio = float(vol.iloc[-1]) / rolling_avg
        if ratio >= _VOL_CONFIRM_RATIO:
            return float(np.clip(50 + (ratio - 1.0) * 25, 60, 90))
        else:
            # Below threshold: penalise low-conviction entry
            return float(np.clip(50 - (1.0 - ratio) * 40, 20, 49))

    def _lottery_penalty(self, df: pd.DataFrame) -> float:
        """Research #2 (CPT): lottery stock detector.

        Returns a penalty [0, 30] subtracted from the raw score distance from 50.
        """
        if len(df) < _LOTTERY_RECENT_BARS + 5:
            return 0.0
        recent = df.iloc[-_LOTTERY_RECENT_BARS:]
        price_move = abs(
            (float(recent["close"].iloc[-1]) - float(recent["open"].iloc[0]))
            / max(float(recent["open"].iloc[0]), 0.01)
        )
        vol_ratio = self._volume_surge(df)
        if vol_ratio is None:
            vol_ratio = 1.0
        if price_move >= _LOTTERY_PRICE_THRESH and vol_ratio >= _LOTTERY_VOL_THRESH:
            penalty = min(price_move * 80 + (vol_ratio - 2.5) * 5, 30.0)
            return float(penalty)
        return 0.0

    def _is_retail_driven(self, df: pd.DataFrame) -> bool:
        """Research #4 (Gao et al.): retail-attention-driven momentum."""
        bars_3days = 234
        lookback = df.iloc[-bars_3days:] if len(df) >= bars_3days else df
        price_move = (
            (float(lookback["close"].iloc[-1]) - float(lookback["open"].iloc[0]))
            / max(float(lookback["open"].iloc[0]), 0.01)
        )
        vol_ratio = self._volume_surge(df)
        if vol_ratio is None:
            vol_ratio = 1.0
        return abs(price_move) >= _RETAIL_PRICE_THRESH and vol_ratio >= _RETAIL_VOL_THRESH

    def _day_range_position(self, df: pd.DataFrame) -> Optional[float]:
        """Position of current close within today's high-low range, 0..1."""
        today = df.index[-1].date()
        today_df = df[df.index.map(lambda x: x.date()) == today]
        if today_df.empty:
            return None
        hi = float(today_df["high"].max())
        lo = float(today_df["low"].min())
        last = float(today_df["close"].iloc[-1])
        if hi == lo:
            return 0.5
        return (last - lo) / (hi - lo)

    def _orb_score(self, df: pd.DataFrame, num_bars: int = 3, rvol: Optional[float] = None) -> Optional[float]:
        """Opening Range Breakout: first 3 five-min bars (9:30–9:45 ET) form the range.

        score > 55  → price above ORB high (bullish breakout)
        score < 45  → price below ORB low  (bearish breakdown)
        45–55       → price inside opening range (no directional edge)

        rvol: if supplied and < 1.5, caps breakout at 65 (unconfirmed breakout).
        Returns None if not enough bars in today's session to confirm the breakout
        (requires opening range bars + at least 2 follow-through bars).
        """
        today = df.index[-1].date()
        today_df = df[df.index.map(lambda x: x.date()) == today]

        # Need opening range bars PLUS at least 2 confirmed follow-through bars
        if len(today_df) <= num_bars + 1:
            return None

        orb_df    = today_df.iloc[:num_bars]
        orb_high  = float(orb_df["high"].max())
        orb_low   = float(orb_df["low"].min())
        orb_range = orb_high - orb_low
        if orb_range <= 0:
            return None

        last = float(today_df["close"].iloc[-1])

        if last > orb_high:
            # Breakout above: +% above high maps to score 65–90
            pct_above = (last - orb_high) / orb_high * 100
            brk = float(np.clip(65 + pct_above * 8, 65, 90))
            # RVOL gate: unconfirmed breakout (RVOL < 1.5) capped at 65
            if rvol is not None and rvol < 1.5:
                brk = min(brk, 65.0)
            return brk
        if last < orb_low:
            # Breakdown below: +% below low maps to score 10–35
            pct_below = (orb_low - last) / orb_low * 100
            bdn = float(np.clip(35 - pct_below * 8, 10, 35))
            # RVOL gate: unconfirmed breakdown (RVOL < 1.5) floored at 35
            if rvol is not None and rvol < 1.5:
                bdn = max(bdn, 35.0)
            return bdn
        # Inside range: neutral with slight position bias (45–55)
        pos_in_range = (last - orb_low) / orb_range
        return float(np.clip(45 + pos_in_range * 10, 45, 55))

    def _gap_signal(self, df: pd.DataFrame) -> Optional[float]:
        """Gap open vs prior close — fade small gaps, ride large ones.

        Research: |gap| < 0.5% fills 88% intraday → fade.
                  |gap| > 2.0% fills only 8% → continuation.
        """
        if not hasattr(df.index, "date") or len(df) < 40:
            return None
        try:
            today = df.index[-1].date()
            today_df = df[df.index.map(lambda x: x.date()) == today]
            prior_df  = df[df.index.map(lambda x: x.date()) < today]
            if today_df.empty or prior_df.empty:
                return None
            today_open = float(today_df["open"].iloc[0])
            prev_close = float(prior_df["close"].iloc[-1])
            if prev_close <= 0 or today_open <= 0:
                return None
            gap_pct = (today_open - prev_close) / prev_close * 100
            if abs(gap_pct) < 0.5:
                # Small gap: high fill probability — fade the direction
                return float(np.clip(50 - gap_pct * 15, 20, 80))
            elif abs(gap_pct) > 2.0:
                # Large gap: low fill probability — ride the continuation
                return float(np.clip(50 + gap_pct * 8, 20, 80))
            else:
                # Medium gap: mild continuation bias
                return float(np.clip(50 + gap_pct * 4, 30, 70))
        except Exception:
            return None

    def _trend_join_score(self, df: pd.DataFrame) -> Optional[float]:
        """Trend Join Long/Short: structural breakout above prior-day high.

        Bullish criteria (each = 1 pt):
          1. Current price > prev-day high (key structural level breakout)
          2. Price at/near intraday HOD (within 0.5% — strong upward momentum)
          3. Price > pre-market high (confirms sustained momentum through RTH open)
          4. Prev close > 200-day SMA via daily_bars (stock in primary uptrend)

        Score: 50 + (bull_met / bull_total) * 40  → [50, 90] when bullish
               50 - (bear_met / bear_total) * 40  → [10, 50] when bearish
        Returns None if data is insufficient.
        """
        try:
            et = ZoneInfo("America/New_York")
            last_price = float(df["close"].iloc[-1])

            # Partition intraday bars into today vs previous day
            dates = [x.astimezone(et).date() for x in df.index]
            all_dates = sorted(set(dates))
            if len(all_dates) < 2:
                return None

            today_date = all_dates[-1]
            prev_date  = all_dates[-2]
            today_bars = df[[d == today_date for d in dates]]
            prev_bars  = df[[d == prev_date  for d in dates]]

            if today_bars.empty or prev_bars.empty:
                return None

            prev_high  = float(prev_bars["high"].max())
            prev_low   = float(prev_bars["low"].min())
            today_high = float(today_bars["high"].max())
            today_low  = float(today_bars["low"].min())

            # Pre-market high: bars strictly before 09:30 ET today
            pm_high = None
            try:
                rth_open = today_bars.index[0].astimezone(et).replace(
                    hour=9, minute=30, second=0, microsecond=0
                )
                pm_bars = today_bars[
                    today_bars.index.map(lambda x: x.astimezone(et)) < rth_open
                ]
                if not pm_bars.empty:
                    pm_high = float(pm_bars["high"].max())
            except Exception:
                pass

            # 200-day SMA trend filter from daily_bars (optional)
            in_uptrend   = None
            in_downtrend = None
            daily = self.daily_bars
            if daily is not None and len(daily) >= 50:
                try:
                    sma_len = min(200, len(daily))
                    sma = float(daily["close"].rolling(sma_len).mean().iloc[-1])
                    last_daily = float(daily["close"].iloc[-1])
                    in_uptrend   = last_daily > sma
                    in_downtrend = last_daily < sma
                except Exception:
                    pass

            # --- Bullish criteria ---
            bull_met   = 0
            bull_total = 2  # criteria 1+2 always available

            if last_price > prev_high:
                bull_met += 1
            if last_price >= today_high * 0.995:
                bull_met += 1
            if pm_high is not None:
                bull_total += 1
                if last_price > pm_high:
                    bull_met += 1
            if in_uptrend is not None:
                bull_total += 1
                if in_uptrend:
                    bull_met += 1

            # --- Bearish criteria ---
            bear_met   = 0
            bear_total = 2

            if last_price < prev_low:
                bear_met += 1
            if last_price <= today_low * 1.005:
                bear_met += 1

            bull_frac = bull_met / bull_total if bull_total > 0 else 0.0
            bear_frac = bear_met / bear_total if bear_total > 0 else 0.0

            if bull_frac > 0 and bull_frac >= bear_frac:
                return float(np.clip(50.0 + bull_frac * 40.0, 50.0, 90.0))
            if bear_frac > 0:
                return float(np.clip(50.0 - bear_frac * 40.0, 10.0, 50.0))
            return 50.0

        except Exception:
            return None

    def _adx_signal(self, df: pd.DataFrame, length: int = 14) -> Optional[float]:
        """ADX trend strength. ADX>25 = trending market. Used to amplify trend signals."""
        if len(df) < length * 2:
            return None
        try:
            high, low, close = df["high"], df["low"], df["close"]
            prev_high = high.shift(1)
            prev_low  = low.shift(1)
            prev_close = close.shift(1)

            plus_dm  = (high - prev_high).clip(lower=0)
            minus_dm = (prev_low - low).clip(lower=0)
            # When both are positive, keep only the larger one
            mask = plus_dm <= minus_dm
            plus_dm[mask]  = 0.0
            mask2 = minus_dm <= plus_dm
            minus_dm[mask2] = 0.0

            tr = pd.concat([
                (high - low),
                (high - prev_close).abs(),
                (low  - prev_close).abs(),
            ], axis=1).max(axis=1)

            atr_smooth    = tr.ewm(alpha=1/length, adjust=False).mean()
            plus_di_raw   = plus_dm.ewm(alpha=1/length, adjust=False).mean()
            minus_di_raw  = minus_dm.ewm(alpha=1/length, adjust=False).mean()

            atr_safe = atr_smooth.replace(0, np.nan)
            plus_di  = 100 * plus_di_raw / atr_safe
            minus_di = 100 * minus_di_raw / atr_safe

            dx_denom = (plus_di + minus_di).replace(0, np.nan)
            dx  = 100 * (plus_di - minus_di).abs() / dx_denom
            adx = dx.ewm(alpha=1/length, adjust=False).mean().iloc[-1]
            pdi = float(plus_di.iloc[-1])
            mdi = float(minus_di.iloc[-1])

            if np.isnan(adx):
                return None

            adx_val = float(adx)
            if adx_val >= 25:
                # Strong trend — directional based on +DI vs -DI
                if pdi > mdi:
                    score = float(np.clip(55 + (adx_val - 25) * 0.8, 55, 85))
                else:
                    score = float(np.clip(45 - (adx_val - 25) * 0.8, 15, 45))
            else:
                # Weak/no trend (ADX < 25) — neutral, slight pull toward 50
                score = 50.0
            return score
        except Exception:
            return None

    def _bollinger_squeeze_signal(self, df: pd.DataFrame, length: int = 20) -> Optional[float]:
        """Bollinger Band squeeze detector.

        Squeeze: BB width below its 20-period rolling average.
        Signal fires when squeeze resolves (width expands) with directional price move.
        """
        if len(df) < length * 2:
            return None
        try:
            close = df["close"]
            sma   = close.rolling(length).mean()
            std   = close.rolling(length).std()
            upper = sma + 2 * std
            lower = sma - 2 * std
            bb_width = ((upper - lower) / sma).fillna(0)

            if len(bb_width) < length + 5:
                return None

            width_now  = float(bb_width.iloc[-1])
            width_avg  = float(bb_width.iloc[-length:].mean())
            prev_width = float(bb_width.iloc[-2])

            in_squeeze    = prev_width < width_avg
            breakout_now  = width_now > prev_width * 1.1  # width expanding ≥10%

            last_px = float(close.iloc[-1])
            mid_px  = float(sma.iloc[-1])

            if in_squeeze and breakout_now:
                # Squeeze resolving — direction from price vs midband
                if last_px > mid_px:
                    pct_above = (last_px - mid_px) / mid_px * 100
                    return float(np.clip(65 + pct_above * 5, 65, 88))
                else:
                    pct_below = (mid_px - last_px) / mid_px * 100
                    return float(np.clip(35 - pct_below * 5, 12, 35))
            elif in_squeeze:
                # Still coiling — neutral; slight bias from price position
                return float(np.clip(50 + (last_px - mid_px) / mid_px * 200, 42, 58))
            else:
                # No squeeze — mild signal from price position relative to bands
                ub = float(upper.iloc[-1])
                lb = float(lower.iloc[-1])
                if ub > lb:
                    position = (last_px - lb) / (ub - lb)
                    return float(np.clip(30 + position * 40, 30, 70))
                return None
        except Exception:
            return None

    def _zscore_signal(self, df: pd.DataFrame, length: int = 50) -> Optional[float]:
        """Z-Score mean reversion.

        Z < -2: deeply oversold → bullish.
        Z > +2: deeply overbought → bearish.
        """
        if len(df) < length + 5:
            return None
        try:
            close = df["close"]
            mu    = close.rolling(length).mean()
            sigma = close.rolling(length).std()

            if sigma.iloc[-1] == 0 or np.isnan(sigma.iloc[-1]):
                return None

            z = float((close.iloc[-1] - mu.iloc[-1]) / sigma.iloc[-1])

            if np.isnan(z):
                return None

            # Z < -2: oversold → bullish (score 70-85)
            # Z > +2: overbought → bearish (score 15-30)
            # |Z| < 1: near mean → neutral (50)
            if z <= -2.0:
                return float(np.clip(70 + (-z - 2.0) * 5, 70, 85))
            elif z >= 2.0:
                return float(np.clip(30 - (z - 2.0) * 5, 15, 30))
            else:
                # Linear interpolation toward neutral
                return float(np.clip(50 - z * 10, 30, 70))
        except Exception:
            return None

    def _stochastic_signal(self, df: pd.DataFrame, k_length: int = 14, d_length: int = 3) -> Optional[float]:
        """Slow Stochastic Oscillator (%K and %D).

        Buy: %K crosses above %D while both below 20 (oversold).
        Sell: %K crosses below %D while both above 80 (overbought).
        """
        if len(df) < k_length + d_length + 5:
            return None
        try:
            high  = df["high"].rolling(k_length).max()
            low   = df["low"].rolling(k_length).min()
            close = df["close"]

            denom = (high - low).replace(0, np.nan)
            fast_k = 100 * (close - low) / denom

            # Slow %K = 3-period SMA of fast %K
            slow_k = fast_k.rolling(3).mean()
            # Slow %D = 3-period SMA of slow %K
            slow_d = slow_k.rolling(d_length).mean()

            sk_now  = float(slow_k.iloc[-1])
            sd_now  = float(slow_d.iloc[-1])
            sk_prev = float(slow_k.iloc[-2])
            sd_prev = float(slow_d.iloc[-2])

            if any(np.isnan(x) for x in [sk_now, sd_now, sk_prev, sd_prev]):
                return None

            # Cross detection
            bullish_cross = sk_prev <= sd_prev and sk_now > sd_now  # K crossed above D
            bearish_cross = sk_prev >= sd_prev and sk_now < sd_now  # K crossed below D

            if bullish_cross and sd_now < 30:
                # Oversold cross up → bullish
                return float(np.clip(70 + (30 - sd_now) * 0.5, 70, 85))
            elif bearish_cross and sd_now > 70:
                # Overbought cross down → bearish
                return float(np.clip(30 - (sd_now - 70) * 0.5, 15, 30))
            elif sk_now > sd_now and sd_now < 20:
                # Above D in oversold territory → moderate bull
                return 65.0
            elif sk_now < sd_now and sd_now > 80:
                # Below D in overbought territory → moderate bear
                return 35.0
            else:
                # No actionable signal — interpolate K position
                return float(np.clip(sk_now * 0.6 + 50 * 0.4, 20, 80))
        except Exception:
            return None

    def _divergence_signal(self, df: pd.DataFrame, lookback: int = 10) -> Optional[float]:
        """Detect RSI/Price divergence over the last `lookback` bars.

        Bullish divergence: price lower low but RSI higher low → score 72-80.
        Bearish divergence: price higher high but RSI lower high → score 20-28.
        Returns neutral 50 when no divergence detected.
        """
        if len(df) < lookback + 20:
            return None
        try:
            close = df["close"]
            # Compute RSI for divergence comparison
            delta = close.diff()
            gain  = delta.clip(lower=0).rolling(14).mean()
            loss  = (-delta.clip(upper=0)).rolling(14).mean()
            rs    = gain / loss.replace(0, np.nan)
            rsi   = 100 - 100 / (1 + rs)

            recent_close = close.iloc[-lookback:]
            recent_rsi   = rsi.iloc[-lookback:]

            if recent_close.isna().any() or recent_rsi.isna().any():
                return None

            # Price and RSI extremes in the lookback window
            price_argmin = int(recent_close.values.argmin())
            price_argmax = int(recent_close.values.argmax())
            rsi_argmin   = int(recent_rsi.values.argmin())
            rsi_argmax   = int(recent_rsi.values.argmax())

            price_min = float(recent_close.iloc[price_argmin])
            price_max = float(recent_close.iloc[price_argmax])
            rsi_min   = float(recent_rsi.iloc[rsi_argmin])
            rsi_max   = float(recent_rsi.iloc[rsi_argmax])

            last_close = float(close.iloc[-1])
            last_rsi   = float(rsi.iloc[-1])

            if np.isnan(last_rsi):
                return None

            # Bullish divergence: new price low but RSI not confirming
            near_low = last_close <= price_min * 1.01   # within 1% of recent low
            rsi_higher = last_rsi > rsi_min + 3.0         # RSI made a higher low

            # Bearish divergence: new price high but RSI not confirming
            near_high  = last_close >= price_max * 0.99  # within 1% of recent high
            rsi_lower  = last_rsi < rsi_max - 3.0         # RSI made a lower high

            if near_low and rsi_higher and last_rsi < 45:
                divergence_strength = min((last_rsi - rsi_min) / 20.0, 1.0)
                return float(np.clip(72 + divergence_strength * 8, 72, 80))
            elif near_high and rsi_lower and last_rsi > 55:
                divergence_strength = min((rsi_max - last_rsi) / 20.0, 1.0)
                return float(np.clip(28 - divergence_strength * 8, 20, 28))
            else:
                return 50.0
        except Exception:
            return None

    def _pattern_score(self, df: pd.DataFrame) -> Optional[float]:
        """Candlestick pattern score via pandas-ta (requires TA-Lib C library)."""
        if not _HAS_TALIB_C or not _HAS_PANDAS_TA:
            return None
        if len(df) < 10:
            return None
        try:
            patterns = ta.cdl_pattern(
                df["open"], df["high"], df["low"], df["close"], name="all"
            )
            if patterns is None or patterns.empty:
                return None
            last_row = patterns.iloc[-1]
            bullish = int((last_row > 0).sum())
            bearish = int((last_row < 0).sum())
            total = bullish + bearish
            if total == 0:
                return 50.0
            return float(np.clip(50 + (bullish - bearish) / total * 30, 20, 80))
        except Exception as exc:
            logger.debug("pattern detection failed: %s", exc)
            return None

    # ---- Classic indicator helpers ----------------------------------------
    def _rsi(self, close: pd.Series, length: int = 14) -> float:
        if _HAS_PANDAS_TA:
            return float(ta.rsi(close, length=length).iloc[-1])
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(length).mean().iloc[-1]
        loss = (-delta.clip(upper=0)).rolling(length).mean().iloc[-1]
        if np.isnan(gain) or np.isnan(loss):
            return 50.0
        if loss == 0:
            return 100.0 if gain > 0 else 50.0
        return float(100 - 100 / (1 + gain / loss))

    def _macd_hist(self, close: pd.Series) -> float:
        if _HAS_PANDAS_TA:
            macd = ta.macd(close)
            # ta.macd returns None when the frame is too short for its 26+9 windows;
            # the histogram is the last column. Treat missing/NaN as flat (0.0) so the
            # agent stays neutral instead of crashing into the safe_evaluate fallback.
            if macd is None or macd.empty:
                return 0.0
            hist = float(macd.iloc[-1, -1])
            return 0.0 if np.isnan(hist) else hist
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        signal = (ema12 - ema26).ewm(span=9, adjust=False).mean()
        return float(((ema12 - ema26) - signal).iloc[-1])

    def _session_vwap(self, df: pd.DataFrame) -> float:
        """Session-only VWAP -- resets at day boundary."""
        if hasattr(df.index, "date") and len(df.index) > 0:
            today = df.index[-1].date()
            session = df[df.index.map(lambda x: x.date()) == today]
            df = session if len(session) >= 5 else df.tail(78)
        else:
            df = df.tail(78)
        typical = (df["high"] + df["low"] + df["close"]) / 3.0
        cum_vol = df["volume"].cumsum().replace(0, np.nan)
        return float((typical * df["volume"]).cumsum().iloc[-1] / cum_vol.iloc[-1])

    def _hourly_direction(self, hourly: pd.DataFrame) -> tuple[float, str]:
        """Compute a directional bias from 1-hour bars.

        Returns (bias_score 0–100, description) where:
          >60 = hourly bullish (rising trend, RSI above 50, price above VWAP)
          <40 = hourly bearish
          40–60 = neutral/choppy
        """
        if hourly is None or len(hourly) < 10:
            return 50.0, "no hourly data"

        close = hourly["close"]

        # 1. EMA 9/21 cross on hourly
        ema9  = close.ewm(span=9,  adjust=False).mean().iloc[-1]
        ema21 = close.ewm(span=21, adjust=False).mean().iloc[-1]
        ema_score = 65.0 if ema9 > ema21 else 35.0

        # 2. RSI on hourly
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss.replace(0, float("nan"))
        rsi_h = float((100 - 100 / (1 + rs)).iloc[-1]) if not loss.iloc[-1] == 0 else 50.0
        if float("nan") == rsi_h or rsi_h != rsi_h:
            rsi_h = 50.0
        rsi_score = float(np.interp(rsi_h, [30, 50, 70], [30, 50, 70]))

        # 3. Price vs 20-bar SMA on hourly
        sma20 = close.rolling(20).mean().iloc[-1]
        last  = float(close.iloc[-1])
        sma_score = 60.0 if last > sma20 else 40.0

        bias = float(np.mean([ema_score, rsi_score, sma_score]))

        if bias >= 60:
            desc = f"hourly bullish (EMA{'↑' if ema9 > ema21 else '↓'}, RSI={rsi_h:.0f})"
        elif bias <= 40:
            desc = f"hourly bearish (EMA{'↑' if ema9 > ema21 else '↓'}, RSI={rsi_h:.0f})"
        else:
            desc = f"hourly neutral (RSI={rsi_h:.0f})"

        return bias, desc


# ---- Module-level helper (used by RegimeAgent too) ----------------------

def _day_change_pct(df: pd.DataFrame) -> float:
    """% change from today's first bar open to latest close."""
    if hasattr(df.index, "date") and len(df.index) > 0:
        today = df.index[-1].date()
        today_df = df[df.index.map(lambda x: x.date()) == today]
        if today_df.empty:
            today_df = df.tail(78)
    else:
        today_df = df.tail(78)
    open_px = float(today_df["open"].iloc[0])
    last_px = float(today_df["close"].iloc[-1])
    return (last_px - open_px) / open_px * 100 if open_px else 0.0
