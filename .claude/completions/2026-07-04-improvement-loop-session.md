# Continuous improvement loop — session record (2026-07-04)

15 commits on `claude/app-end-to-end-review-u8qi7a` awaiting merge.
Suite grew 407 → **469 pytest** and 56 → **66 vitest**; CI added.

## Shipped, in order

| Commit | Change |
|---|---|
| 88873e8 | Always-on learning loop: nightly walk-forward optimizer + validated auto-apply |
| 8948cf1 | IBKR breakeven-lock parity (avg_entry_price, in-place stop replace) |
| 8d24bab | CI: pytest + vitest/tsc on every push/PR |
| adc6849 | Broker↔trades.json reconciliation loop; fixed `nested=true` exit-detection bug |
| f3e2a7e | Backtests costed at measured real fill slippage |
| 929b5a8 | Luck screen (sign-flip randomization) on auto-applies; JSONL log rotation |
| 4d810a8 | Owner/viewer roles — shared-bot controls owner-only |
| c084d48 | Self-improvement timeline panel (Learning page + /api/improvement-history) |
| 8e911c6 | Live-runner heartbeat watchdog (crash → health board + Telegram) |
| 7163bbd | Prompt-injection hardening for news text; slippage summary in /api/stats |
| 3330373 | Scanner: most-active candidates were unrankable — snapshot enrichment |
| d9b855e | trades.json nightly compaction; ARCHITECTURE_MAP refreshed |
| 9183aeb | LLM quota alarms resolve on recovery; vision cache bounded |
| ed81da9 | Backtest fill simulator: gap-aware stop/target fills + first direct tests |

## Audits completed (no further high-value autonomous work identified)

Universe scanner, LLM adapter, vision agent, backtest fill engine —
each produced fixes above. Remaining modules (report agent, telegram
publisher, sector scanner, chart renderer) are small, low-risk, and
covered indirectly by existing tests.

## Blocked on operator decisions

1. **Merge** — 15 commits pending; deploy steps: `prisma migrate deploy`
   (temp-password + role migrations), verify first-created account is the
   intended owner, redeploy Vercel/Railway, restart PC bot.
2. **Per-user copy-trading** — execute every signal into each user's own
   account with per-user guards (answers "why do we get different trades").
   Architectural; needs a yes.
3. **Cross-venue learning sync** — Railway and the PC currently learn
   separately from their own fills (intentional: different fill quality).
   Sharing one brain needs a design choice.
