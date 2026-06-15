#!/bin/bash
# Railway entrypoint: runs the live trading loop (live_runner.py) in the
# background and the dashboard API server (api_server.py) in the foreground.
# Both share this filesystem, so live_runner's writes to data/*.json are
# immediately visible to api_server's reads.
set -e

# Ensure CWD is the script's own directory (trading_bot/) regardless of
# where the caller invoked us from (Railway runs from the repo root).
cd "$(dirname "$0")"

python live_runner.py 2>&1 | sed -u 's/^/[live_runner] /' &

exec python api_server.py
