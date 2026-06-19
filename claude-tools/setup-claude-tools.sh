#!/usr/bin/env bash
# ============================================================================
# setup-claude-tools.sh — one-time-per-device installer for the Claude Code
# tool stack. Run this ONCE on each machine where you use Claude Code; the
# tools then load automatically on every session on that machine.
#
#   bash claude-tools/setup-claude-tools.sh
#
# WHY PER-DEVICE: Claude Code plugins / MCP servers / hooks live in your
# machine's ~/.claude (and ~/.claude.json). They are NOT stored in a repo and
# cannot be pushed to your other devices remotely. Run this on each device.
#
# SAFE & IDEMPOTENT: re-running is fine. It never deletes your data and refuses
# to auto-run anything it cannot verify (see the ECC section).
# ============================================================================
set -uo pipefail

CLAUDE_DIR="${HOME}/.claude"
QUARANTINE="${HOME}/claude-tools/quarantine"
BROWSE="${HOME}/claude-tools/browse"

bold(){ printf '\033[1m%s\033[0m\n' "$*"; }
ok(){   printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn(){ printf '  \033[33m!\033[0m %s\n' "$*"; }
skip(){ printf '  \033[90m·\033[0m %s\n' "$*"; }
have(){ command -v "$1" >/dev/null 2>&1; }

mkdir -p "${CLAUDE_DIR}"

bold "Claude Code tool stack — per-device setup"
echo

# --- 0. prerequisites -------------------------------------------------------
bold "0) Checking prerequisites"
have node && ok "node $(node -v)" || warn "node not found — install Node.js (needed for repomix & claude-mem)"
have npm  && ok "npm  $(npm -v)"  || warn "npm not found"
have git  && ok "git present"     || warn "git not found — needed to clone skill repos"
if have claude; then ok "claude CLI present"
else warn "claude CLI not on PATH — MCP auto-registration is skipped; the /plugin commands you paste inside Claude still work"; fi
echo

# --- 1. repomix (CLI + MCP) -------------------------------------------------
bold "1) Repomix — pack a repo into one AI-friendly file"
if have npm; then
  if npm install -g repomix >/dev/null 2>&1; then ok "installed repomix globally"
  else warn "global install failed (try: sudo npm i -g repomix)"; fi
else
  skip "skipped (no npm)"
fi
if have claude && have repomix; then
  if claude mcp add --scope user repomix -- repomix --mcp >/dev/null 2>&1; then
    ok "registered repomix MCP at user scope (available in every project)"
  else
    skip "repomix MCP already registered or registration skipped"
  fi
fi
echo

# --- 2. claude-mem (persistent cross-session memory) ------------------------
bold "2) claude-mem — persistent memory across sessions (hooks + local worker :37777)"
if have npx; then
  warn "running 'npx claude-mem install' — it may prompt and will edit ~/.claude settings"
  npx --yes claude-mem install || warn "claude-mem install returned non-zero — re-run manually: npx claude-mem install"
  ok "claude-mem install attempted (registers SessionStart/Stop hooks → runs every session)"
else
  skip "skipped (no npx)"
fi
echo

# --- 3. Plugins (superpowers, superpowers-chrome, ruflo light) --------------
# There is no non-interactive shell CLI for installing Claude Code plugins; the
# /plugin commands run INSIDE a Claude session. We write them to a file and
# print them — paste them ONCE in any Claude Code session on this device. Once
# installed they auto-load on every future session here.
bold "3) Plugins — paste these ONCE inside a Claude Code session on this device"
PLUGCMDS="${CLAUDE_DIR}/INSTALL-PLUGINS.txt"
cat > "${PLUGCMDS}" <<'PLUG'
/plugin marketplace add obra/superpowers-marketplace
/plugin marketplace add ruvnet/ruflo
/plugin install superpowers@superpowers-marketplace
/plugin install superpowers-chrome@superpowers-marketplace
/plugin install ruflo-core@ruflo
/plugin install ruflo-swarm@ruflo
PLUG
sed 's/^/    /' "${PLUGCMDS}"
ok "also saved to ${PLUGCMDS}"
warn "superpowers-chrome needs Google Chrome installed to actually drive a browser"
skip "ruflo = LIGHT plugin only (slash commands/agents). For the heavy ~210-tool MCP daemon"
skip "  run separately:  claude mcp add --scope user ruflo -- npx ruflo@latest mcp start"
echo

# --- 4. awesome-claude-skills (a CURATED LIST, not a package) ---------------
bold "4) awesome-claude-skills — a directory of skills (nothing auto-installs)"
mkdir -p "${BROWSE}"
if have git; then
  if [ -d "${BROWSE}/awesome-claude-skills/.git" ]; then
    git -C "${BROWSE}/awesome-claude-skills" pull --ff-only >/dev/null 2>&1 && ok "updated browse copy" || skip "browse copy present"
  else
    git clone --depth 1 https://github.com/karanb192/awesome-claude-skills "${BROWSE}/awesome-claude-skills" >/dev/null 2>&1 \
      && ok "cloned to ${BROWSE}/awesome-claude-skills" \
      || warn "clone failed"
  fi
  skip "browse it, then install any skill you want with:"
  skip "  git clone <skill-repo> ~/.claude/skills/<skill-name>"
else
  skip "skipped (no git)"
fi
echo

# --- 5. everything-claude-code (ECC) — QUARANTINED, NOT auto-run ------------
bold "5) everything-claude-code (ECC) — ⚠ ships an opaque .zip 'installer', not config files"
warn "ECC is distributed as a double-click binary, not inspectable Claude config."
warn "This script will NOT auto-run it. It clones it read-only so YOU can inspect first."
mkdir -p "${QUARANTINE}"
if have git; then
  if [ -d "${QUARANTINE}/everything-claude-code/.git" ]; then
    skip "already cloned to ${QUARANTINE}/everything-claude-code"
  else
    git clone --depth 1 https://github.com/arabicapp/everything-claude-code "${QUARANTINE}/everything-claude-code" >/dev/null 2>&1 \
      && ok "cloned (NOT run) to ${QUARANTINE}/everything-claude-code" \
      || warn "clone failed"
  fi
  echo "    Inspect before trusting it — list the zip's contents WITHOUT running it:"
  echo "      ls -la ${QUARANTINE}/everything-claude-code"
  echo "      unzip -l ${QUARANTINE}/everything-claude-code/docs/code_everything_claude_3.3.zip"
else
  skip "skipped (no git)"
fi
echo

bold "Done."
echo "Next on THIS device:"
echo "  1) open Claude Code and paste the commands from ${PLUGCMDS}"
echo "  2) restart the session so plugins + hooks load"
echo "Re-run this script on every other device to get the same setup there."
