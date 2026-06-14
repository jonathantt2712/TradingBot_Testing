#!/bin/bash
# Railway entrypoint: runs the live trading loop (live_runner.py) in the
# background and the dashboard API server (api_server.py) in the foreground.
# Both share this filesystem, so live_runner's writes to data/*.json are
# immediately visible to api_server's reads.
set -e

python live_runner.py 2>&1 | sed -u 's/^/[live_runner] /' &

exec python api_server.py
