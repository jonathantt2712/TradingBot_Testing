"""
Push helper: clones itaitoker64/tradingbot2026, overlays our new files, and pushes.
Run from anywhere — double-click push_to_git.bat to invoke.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_URL   = "https://github.com/itaitoker64/tradingbot2026.git"
BRANCH     = "master"
WORKSPACE  = Path(r"C:\Users\itait\Claude\Projects\trading bot")
COMMIT_MSG = (
    "feat: AI4Trade social platform + live/challenge runners\n\n"
    "AI4Trade integration (ai4trade.ai):\n"
    "- Add AI4TradeClient - async REST client with auto register/login, Bearer auth\n"
    "- Add SocialSentimentAgent - community signal feed with exponential recency decay\n"
    "- Add MarketIntelNewsSource + CombinedNewsSource - fan-out news aggregator\n"
    "- Add SignalPublisher - publishes live trades and strategy posts to AI4Trade feed\n"
    "- Add live_runner.py - heartbeat-driven live mode (event-driven, not polling)\n"
    "- Add challenge_runner.py - auto-joins competitions, submits decisions, shows leaderboard\n\n"
    "Config / wiring:\n"
    "- AgentWeights now 5-way: fundamental 0.20, vision 0.15, technical 0.35, liquid 0.15, social 0.15\n"
    "- Settings: AI4TRADE_EMAIL/PASSWORD/BOT_NAME/PUBLISH, USE_LIQUID_BROKER, LIQUID_API_KEY\n"
    "- PortfolioManager: optional liquid + social agents, optional publisher\n"
    "- main.py: concurrent asyncio.gather evaluation, full agent stack wired\n\n"
    "Previous fixes also included:\n"
    "- load_dotenv() called before Settings import\n"
    "- risk_agent evaluates both LONG and SHORT, picks better R/R\n"
    "- Session VWAP resets at day boundary\n"
    "- Continuous MACD/EMA/VWAP signals instead of binary thresholds\n"
    "- asyncio.to_thread() wraps all sync I/O in async context\n"
    "- Add LiquidAgent, LiquidBroker, backtest_runner.py, dashboard\n\n"
    "requirements.txt: websockets added; .env.example fully documented"
)

# Files/dirs in WORKSPACE to copy into the cloned repo (relative paths)
COPY_ITEMS = [
    "trading_bot",
    ".env.example",
]

# Never let these reach the clone — secrets must not depend on .gitignore alone.
IGNORE = shutil.ignore_patterns(".env", ".env.*", "*.env", "__pycache__", "*.pyc", "*.log")


def run(cmd, cwd=None, check=True):
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.stderr.strip():
        print(result.stderr.strip())
    if check and result.returncode != 0:
        print(f"\nERROR: command exited with code {result.returncode}")
        sys.exit(result.returncode)
    return result


def main():
    tmpdir = tempfile.mkdtemp(prefix="tradingbot_push_")
    clone_dir = Path(tmpdir) / "repo"

    try:
        print(f"\n[1/5] Cloning {REPO_URL} (branch: {BRANCH}) ...")
        run(["git", "clone", "--branch", BRANCH, "--depth", "1",
             REPO_URL, str(clone_dir)])

        print("\n[2/5] Overlaying workspace files ...")
        for item_rel in COPY_ITEMS:
            src = WORKSPACE / item_rel
            dst = clone_dir / item_rel
            if not src.exists():
                print(f"  SKIP (not found): {src}")
                continue
            if src.is_dir():
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst, dirs_exist_ok=True, ignore=IGNORE)
                print(f"  copied dir  : {item_rel}")
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                print(f"  copied file : {item_rel}")

        # Belt-and-braces: verify no env file slipped into the clone.
        leaked = [p for p in clone_dir.rglob("*") if p.name == ".env" or p.suffix == ".env"]
        for p in leaked:
            print(f"  WARNING: removing leaked secret file {p}")
            p.unlink()

        print("\n[3/5] Staging changes ...")
        run(["git", "add", "."], cwd=clone_dir)
        status = run(["git", "status", "--short"], cwd=clone_dir, check=False)
        if not status.stdout.strip():
            print("  Nothing to commit — repo is already up to date.")
            return

        print("\n[4/5] Committing ...")
        msg = sys.argv[1] if len(sys.argv) > 1 else COMMIT_MSG
        run(["git", "commit", "-m", msg], cwd=clone_dir)

        print("\n[5/5] Pushing to origin/master ...")
        run(["git", "push", "origin", BRANCH], cwd=clone_dir)

        print("\n✓ Push successful!")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
