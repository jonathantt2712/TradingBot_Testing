# Claude Code tool stack — per-device setup

One script that installs the Claude Code add-ons you asked for and makes them
load **automatically on every session** — on each device you run it.

```bash
bash claude-tools/setup-claude-tools.sh
```

## Read this first: why it's "per-device", not "everywhere at once"

Claude Code plugins, MCP servers, and hooks live in your machine's `~/.claude/`
and `~/.claude.json`. They are **not** stored in a git repo and there is no way
to push them to your other computers remotely. So the honest mechanism for
"runs every time I open Claude on every device" is:

> **run this script once on each device.** After that, the tools auto-load on
> every session on that device — you don't run anything again.

This script is **idempotent** (safe to re-run) and **fail-soft** (a missing
prerequisite skips one step instead of aborting).

## What gets set up

| Tool | What it is | How it's installed | Runs every session? |
|------|------------|--------------------|---------------------|
| **superpowers** (`obra/superpowers`) | Auto-activating skill system (TDD, debugging, planning, code review) | `/plugin` (paste block) | Yes — skills load + trigger automatically |
| **superpowers-chrome** (`obra/superpowers-chrome`) | Browser automation via Chrome DevTools Protocol | `/plugin` (paste block) | Yes — skill available (needs **Chrome** installed) |
| **ruflo** (`ruvnet/ruflo`) — *light* | Multi-agent slash commands / agent defs | `/plugin` (paste block) | Yes — commands available |
| **claude-mem** (`thedotmack/claude-mem`) | Persistent memory across sessions (SessionStart/Stop hooks + worker on :37777) | `npx claude-mem install` (scripted) | **Yes — fires automatically** |
| **repomix** (`yamadashy/repomix`) | Packs a repo into one AI-friendly file (CLI + MCP) | `npm i -g` + `claude mcp add --scope user` (scripted) | Available on demand + as an MCP every session |
| **awesome-claude-skills** (`karanb192/...`) | A **curated list**, not a package | cloned for browsing only | No — pick skills to install yourself |
| **everything-claude-code (ECC)** | ⚠️ ships an opaque `.zip` installer | **quarantined, not run** | No — see warning |

Once installed, the plugins load and claude-mem's hooks fire on **every** new
session automatically. Repomix is a tool you (or Claude) invoke when needed and
is also exposed as an MCP server in every project.

## ⚠️ everything-claude-code (ECC) — why it is NOT auto-run

You asked to include ECC. I looked at its actual install method: its README's
only instructions are *"download the `.zip`, double-click the installer, find
**everything-claude-code** in your Applications/Programs and open it."* The
download is an **opaque binary** committed in the repo
(`docs/code_everything_claude_3.3.zip`) — **not** the plain-text agents / skills
/ hooks a normal Claude config collection ships.

Auto-downloading and running an unverified binary on every device, on every
launch, is the single riskiest thing you can do to a fleet of machines, so the
script **clones it read-only for inspection and refuses to execute it.** To
evaluate it safely (lists contents, does **not** run anything):

```bash
ls -la   ~/claude-tools/quarantine/everything-claude-code
unzip -l ~/claude-tools/quarantine/everything-claude-code/docs/code_everything_claude_3.3.zip
```

If, after inspecting it, you decide you trust it, run it yourself. I won't wire
execution of an unknown binary into an unattended every-device setup.

## Plugins step (the one manual bit)

There is no non-interactive shell command to install Claude Code **plugins** —
the `/plugin` commands run inside a Claude session. The script writes them to
`~/.claude/INSTALL-PLUGINS.txt` and prints them. Paste once per device:

```text
/plugin marketplace add obra/superpowers-marketplace
/plugin marketplace add ruvnet/ruflo
/plugin install superpowers@superpowers-marketplace
/plugin install superpowers-chrome@superpowers-marketplace
/plugin install ruflo-core@ruflo
/plugin install ruflo-swarm@ruflo
```

Then restart the session. They persist and auto-load from then on.

### Want ruflo's full power?

The script installs ruflo **light** (slash commands only). For the full
~210-tool MCP daemon (heavier; runs a background service every session):

```bash
claude mcp add --scope user ruflo -- npx ruflo@latest mcp start
```

## Notes & caveats

- **Overlap/overhead:** superpowers, ruflo, and claude-mem all hook into the
  session. Running all three is fine but adds startup latency and a couple of
  background services (claude-mem worker on :37777). If a session feels slow,
  disable one with `/plugin`.
- **Schema/versions:** plugin marketplace + install commands are the supported,
  version-stable path. (Declarative `enabledPlugins` keys in `settings.json`
  exist but vary by Claude Code version, so this setup uses the slash commands.)
- **Uninstall:** `/plugin` menu to remove plugins; `npx claude-mem uninstall`
  for claude-mem; `claude mcp remove repomix` / `claude mcp remove ruflo`.
