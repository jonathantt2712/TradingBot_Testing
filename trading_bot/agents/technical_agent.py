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
from typing import Optional

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


# Signal weights (must sum to 1.0)
_WEIGHTS = {
    "rsi":              0.14,
    "macd":             0.13,
    "ema_cross":        0.11,
    "vwap":             0.12,
    "rel_strength":     0.14,  # vs SPY
    "volume_surge":     0.12,  # unusual volume
    "intraday_mom":     0.07,  # price vs open (reduced to accommodate ORB)
    "day_range_pos":    0.04,  # where in today's H-L range (reduced)
    "orb":              0.07,  # Opening Range Breakout (9:30–9:45 ET range)
    "vol_confirm":      0.06,  # Research #3: volume confirmation gate
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
        spy_bars: Optional[pd.DataFrame] = None,   # injected by caller if available
    ) -> None:
        super().__init__(weight=weight)
        self.min_bars = min_bars
        self.spy_bars = spy_bars   # set externally each cycle: agent.spy_bars = spy_df

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

        # ── Stale-data guard ─────────────────────────────────────────────
        # If the most recent bar is more than 30 minutes old during RTH,
        # data is stale (broker connectivity issue) — return neutral.
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            _ET = ZoneInfo("America/New_York")
            last_ts = df.index[-1]
            if last_ts.tzinfo is None:
                last_ts = last_ts.tz_localize("UTC")
            last_ts_et = last_ts.astimezone(_ET)
            now_et = datetime.now(_ET)
            age_min = (now_et - last_ts_et).total_seconds() / 60
            mkt_open  = now_et.replace(hour=9,  minute=30, second=0, microsecond=0)
            mkt_close = now_et.replace(hour=16, minute=0,  second=0, microsecond=0)
            is_rth = now_et.weekday() < 5 and mkt_open <= now_et <= mkt_close
            if is_rth and age_min > 30:
                logger.warning(
                    "TechnicalAgent: stale data for %s — last bar %.0fmin old",
                    ctx.ticker, age_min,
                )
                return AgentEvaluation(
                    role=self.role,
                    score=NEUTRAL_SCORE,
                    confidence=0.05,
                    rationale=f"stale data: last bar {age_min:.0f}min old",
                )
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

        # 5. Opening Range Breakout
        orb = self._orb_score(df)
        if orb is not None:
            signals["orb"] = orb

        # 6. Candlestick Patterns (pandas-ta only)
        if _HAS_PANDAS_TA:
            pat_score = self._pattern_score(df)
            if pat_score is not None:
                signals["patterns"] = pat_score

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

        # ── Composite score ──────────────────────────────────────────────
        clean = {k: v for k, v in signals.items() if not np.isnan(v)}
        if not clean:
            return AgentEvaluation(role=self.role, score=NEUTRAL_SCORE,
                                   confidence=0.2, rationale="no valid signals")

        # Extract meta-signals before weighting (prefixed with _)
        lottery_penalty  = clean.pop("_lottery_penalty",  0.0)
        retail_surcharge = clean.pop("_retail_surcharge", 0.0)

        # Weighted mean (fall back to equal weight for signals not in _WEIGHTS)
        total_w = num = 0.0
        for key, val in clean.items():
            w = _WEIGHTS.get(key, 0.05)
            num += val * w
            total_w += w
        raw_score = num / total_w if total_w else 50.0

        # Research #2: subtract lottery penalty (pushes score toward 50 = neutral)
        if lottery_penalty > 0:
            # Pull score toward 50 proportionally to penalty magnitude
            raw_score = raw_score - (raw_score - 50.0) * min(lottery_penalty / 30.0, 0.6)

        score = clamp_score(raw_score)

        spread = float(np.std(list(clean.values())))
        confidence = float(max(0.3, 1.0 - spread / 50.0))

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
        if lottery_penalty > 0:
            rationale += f" [LOTTERY pen={lottery_penalty:.0f}]"
        if retail_surcharge > 0:
            rationale += " [RETAIL-DRIVEN +5thr]"

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

    def _orb_score(self, df: pd.DataFrame, num_bars: int = 3) -> Optional[float]:
        """Opening Range Breakout: first 3 five-min bars (9:30–9:45 ET) form the range.

        score > 55  → price above ORB high (bullish breakout)
        score < 45  → price below ORB low  (bearish breakdown)
        45–55       → price inside opening range (no directional edge)

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
            return float(np.clip(65 + pct_above * 8, 65, 90))
        if last < orb_low:
            # Breakdown below: +% below low maps to score 10–35
            pct_below = (orb_low - last) / orb_low * 100
            return float(np.clip(35 - pct_below * 8, 10, 35))
        # Inside range: neutral with slight position bias (45–55)
        pos_in_range = (last - orb_low) / orb_range
        return float(np.clip(45 + pos_in_range * 10, 45, 55))

    def _pattern_score(self, df: pd.DataFrame) -> Optional[float]:
        """Candlestick pattern score via pandas-ta."""
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
            return float(macd.iloc[-1, -1])
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
