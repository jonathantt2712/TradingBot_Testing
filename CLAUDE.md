# Project guidelines

## Trading bot specifics

- Run tests before pushing bot changes: `cd trading_bot && python -m pytest tests -q`
- Never commit `.env` files — secrets are shared privately between collaborators.
- Fail closed: brokers return `{}` when account state is unknown; RiskAgent
  refuses to size without verified equity. Keep this convention.
- Shared composition lives in `trading_bot/bootstrap.py` — don't duplicate
  wiring in main.py / live_runner.py.
- Only ONE PC runs the bot with `EXECUTE_LIVE=true` (shared Alpaca account).

## Coding guidelines (Karpathy skills)

Source: https://github.com/multica-ai/andrej-karpathy-skills — guidelines to
reduce common LLM coding mistakes. Bias toward caution over speed; for trivial
tasks, use judgment.

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- Remove imports/variables/functions that YOUR changes made unused;
  don't remove pre-existing dead code unless asked.

Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan with a verify step per item.
