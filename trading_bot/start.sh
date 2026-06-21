#!/bin/bash
# Railway entrypoint: runs the live trading loop (live_runner.py) in the
# background and the dashboard API server (api_server.py) in the foreground.
# Both share this filesystem, so live_runner's writes to data/*.json are
# immediately visible to api_server's reads.
set -e

# Ensure CWD is the script's own directory regardless of where it's invoked
# from (Railway's Root Directory is trading_bot/, so this is normally a no-op).
cd "$(dirname "$0")"

python live_runner.py 2>&1 | sed -u 's/^/[live_runner] /' &

exec python api_server.py
