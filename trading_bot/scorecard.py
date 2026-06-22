"""CLI: print the live strategy scorecard.

    cd trading_bot && python scorecard.py

Reads data/trades.json + logs/decisions.jsonl — no API keys or network needed.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core.scorecard import build_scorecard, format_scorecard  # noqa: E402

if __name__ == "__main__":
    print(format_scorecard(build_scorecard()))
