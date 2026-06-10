# Working together on this project

Repo: https://github.com/itaitoker64/tradingbot2026 (branch `main`)

## One-time setup

### Owner (itaitoker64): invite your friend
1. Open https://github.com/itaitoker64/tradingbot2026/settings/access
2. **Add people** → enter your friend's GitHub username → Invite
3. (Recommended) Make the repo **private**: Settings → General → Danger Zone
   → Change visibility. It's currently PUBLIC — anyone can read your strategy.

### Friend: get the project running
1. Accept the invite (email from GitHub).
2. Install: [Git](https://git-scm.com), [Python 3.11+](https://python.org),
   [Node.js LTS](https://nodejs.org).
3. In cmd:
   ```cmd
   git clone https://github.com/itaitoker64/tradingbot2026.git
   cd tradingbot2026
   pip install -r trading_bot\requirements.txt
   cd trading-dashboard && npm install && cd ..
   ```
4. **Secrets are NOT in git** (on purpose). Get these two files from the owner
   privately (WhatsApp/Signal — never commit them):
   - `.env`              (project root — Alpaca keys, thresholds, etc.)
   - `trading-dashboard\.env.local`
5. Run `START.bat`. Done.

## Daily workflow (the simple version)

Think of it as: **pull before you start, push when you stop.**

```cmd
git pull                          ← ALWAYS do this before you start working
... work on the code ...
git add -A
git commit -m "what you changed"
git push
```

If `git push` is rejected ("fetch first"), your friend pushed while you were
working — run `git pull`, fix any conflict (or ask Claude Code to), then
`git push` again.

### Rules that save friendships
1. **Pull before you start. Push when you stop.** Long-lived local changes are
   how painful conflicts happen.
2. **Tell each other what you're working on** — if one does the Python bot and
   the other does the dashboard, you'll almost never conflict.
3. **Only ONE PC runs the bot with `EXECUTE_LIVE=true`.** You share one Alpaca
   account — two bots trading it at once means duplicate orders. The other
   person runs dry-run (default) or backtests.
4. **Never commit `.env` files.** The `.gitignore` blocks it, don't fight it.
5. Run the tests before pushing bot changes:
   `cd trading_bot && python -m pytest tests -q`

### When you're more comfortable (optional upgrade)
Work on branches and review each other's changes as Pull Requests:
```cmd
git checkout -b my-feature        ← create a branch
... work, commit ...
git push -u origin my-feature     ← push the branch
```
Then open a PR on GitHub and the other person reviews + merges. Claude Code
can drive all of this for you — just ask.

## Who owns the deployment?
- The Vercel site + ngrok tunnel are tied to the OWNER's accounts/PC.
- The friend's START.bat will say the tunnel failed — that's fine; their copy
  works locally at http://localhost:3000. (Or they make their own free ngrok
  account for a second tunnel.)

## Obsolete files
`push.bat`, `push_to_git.bat`, `push_helper.py`, `push_helper.ps1` are from the
pre-git era (overlay-copy-push). Don't use them anymore — plain `git push` does
the right thing now.
