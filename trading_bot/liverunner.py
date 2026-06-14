"""Alias entry-point — delegates to live_runner.py."""
from live_runner import main
import asyncio, sys

if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
