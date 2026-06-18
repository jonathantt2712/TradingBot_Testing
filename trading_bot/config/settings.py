"""Centralised configuration -- reads from environment / .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping

from core.enums import RunMode


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)

def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default

def _env_bool(key: str, default: bool = False) -> bool:
    return os.environ.get(key, str(default)).lower() in ("1", "true", "yes")


@dataclass(slots=True)
class AgentWeights:
    fundamental: float = 0.18
    vision:      float = 0.14
    technical:   float = 0.32
    liquid:      float = 0.13
    social:      float = 0.13
    insider:     float = 0.10   # congressional trading intelligence
    squeeze:     float = 0.08   # FINRA short volume squeeze detector
    macro:       float = 0.10   # AI-Trader market-intel macro signals

    def as_map(self) -> Mapping[str, float]:
        raw = {
            "fundamental": self.fundamental,
            "vision":      self.vision,
            "technical":   self.technical,
            "liquid":      self.liquid,
            "social":      self.social,
            "insider":     self.insider,
            "squeeze":     self.squeeze,
            "macro":       self.macro,
        }
        total = sum(raw.values())
        if total <= 0:
            raise ValueError("agent weights must sum to a positive number")
        return {k: v / total for k, v in raw.items()}


@dataclass(slots=True)
class RiskConfig:
    max_risk_per_trade_pct: float = field(default_factory=lambda: _env_float("MAX_RISK_PER_TRADE_PCT", 0.01))
    min_risk_reward:        float = field(default_factory=lambda: _env_float("MIN_RISK_REWARD", 1.5))
    max_position_pct:       float = field(default_factory=lambda: _env_float("MAX_POSITION_PCT", 0.20))
    atr_stop_multiple:      float = field(default_factory=lambda: _env_float("ATR_STOP_MULTIPLE", 2.0))
    atr_target_multiple:    float = field(default_factory=lambda: _env_float("ATR_TARGET_MULTIPLE", 3.0))
    max_open_positions:     int   = field(default_factory=lambda: int(_env_float("MAX_OPEN_POSITIONS", 5)))
    max_daily_loss_pct:     float = field(default_factory=lambda: _env_float("MAX_DAILY_LOSS_PCT", 0.03))


@dataclass(slots=True)
class DecisionThresholds:
    long_above:     float = field(default_factory=lambda: _env_float("LONG_THRESHOLD", 60.0))
    short_below:    float = field(default_factory=lambda: _env_float("SHORT_THRESHOLD", 40.0))
    min_risk_score: float = field(default_factory=lambda: _env_float("MIN_RISK_SCORE", 35.0))


@dataclass(slots=True)
class ScannerConfig:
    enabled:         bool  = field(default_factory=lambda: _env_bool("SCANNER_ENABLED", True))
    top_n:           int   = field(default_factory=lambda: int(_env_float("SCANNER_TOP_N", 50)))
    min_price:       float = field(default_factory=lambda: _env_float("SCANNER_MIN_PRICE", 5.0))
    max_price:       float = field(default_factory=lambda: _env_float("SCANNER_MAX_PRICE", 2000.0))
    min_volume:      int   = field(default_factory=lambda: int(_env_float("SCANNER_MIN_VOLUME", 500000)))
    min_change_pct:  float = field(default_factory=lambda: _env_float("SCANNER_MIN_CHANGE_PCT", 0.5))
    prefilter_min:   float = field(default_factory=lambda: _env_float("SCANNER_PREFILTER_MIN", 45.0))
    prefilter_max:   float = field(default_factory=lambda: _env_float("SCANNER_PREFILTER_MAX", 55.0))
    prefilter_top_n: int   = field(default_factory=lambda: int(_env_float("SCANNER_PREFILTER_TOP_N", 12)))


@dataclass(slots=True)
class Settings:
    run_mode: RunMode = field(default_factory=lambda: RunMode(_env("RUN_MODE", "backtest")))

    alpaca_key_id: str  = field(default_factory=lambda: _env("ALPACA_API_KEY_ID"))
    alpaca_secret: str  = field(default_factory=lambda: _env("ALPACA_API_SECRET"))
    alpaca_paper:  bool = field(default_factory=lambda: _env_bool("ALPACA_PAPER", True))
    # "iex" (free, ~2-3% of consolidated volume) or "sip" (paid, full market).
    # Volume-based signals are calibrated for full volume — prefer sip if available.
    alpaca_data_feed: str = field(default_factory=lambda: _env("ALPACA_DATA_FEED", "iex").lower())

    # End-of-day flatten: close everything this many minutes before the 16:00 ET close.
    eod_flatten:             bool = field(default_factory=lambda: _env_bool("EOD_FLATTEN", True))
    eod_flatten_min_before:  int  = field(default_factory=lambda: int(_env_float("EOD_FLATTEN_MIN_BEFORE", 5)))

    broker:         str = field(default_factory=lambda: _env("BROKER", "alpaca").lower())
    ibkr_host:      str = field(default_factory=lambda: _env("IBKR_HOST", "127.0.0.1"))
    ibkr_port:      int = field(default_factory=lambda: int(_env("IBKR_PORT", "7497")))
    ibkr_client_id: int = field(default_factory=lambda: int(_env("IBKR_CLIENT_ID", "1")))

    use_liquid_broker: bool = field(default_factory=lambda: _env_bool("USE_LIQUID_BROKER", False))
    liquid_api_key:    str  = field(default_factory=lambda: _env("LIQUID_API_KEY"))

    ai4trade_email:    str  = field(default_factory=lambda: _env("AI4TRADE_EMAIL"))
    ai4trade_password: str  = field(default_factory=lambda: _env("AI4TRADE_PASSWORD"))
    ai4trade_bot_name: str  = field(default_factory=lambda: _env("AI4TRADE_BOT_NAME", "tradingbot2026"))
    ai4trade_publish:  bool = field(default_factory=lambda: _env_bool("AI4TRADE_PUBLISH", True))

    news_base_url:     str  = field(default_factory=lambda: _env("NEWS_BASE_URL", "https://www.polistock.app/"))
    news_api_key:      str  = field(default_factory=lambda: _env("NEWS_API_KEY"))
    anthropic_api_key: str  = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    gemini_api_key:    str  = field(default_factory=lambda: _env("GEMINI_API_KEY"))
    llm_model:         str  = field(default_factory=lambda: _env("LLM_MODEL", "claude-sonnet-4-6"))

    telegram_bot_token:    str   = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id:      str   = field(default_factory=lambda: _env("TELEGRAM_CHAT_ID"))
    premarket_gap_min_pct: float = field(default_factory=lambda: _env_float("PREMARKET_GAP_MIN_PCT", 5.0))
    premarket_min_volume:  int   = field(default_factory=lambda: int(_env_float("PREMARKET_MIN_VOLUME", 50000)))

    weights:    AgentWeights       = field(default_factory=AgentWeights)
    risk:       RiskConfig         = field(default_factory=RiskConfig)
    thresholds: DecisionThresholds = field(default_factory=DecisionThresholds)
    scanner:    ScannerConfig      = field(default_factory=ScannerConfig)


def load_settings() -> Settings:
    return Settings()
