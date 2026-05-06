# granola-sync

Sync your [Granola](https://granola.ai) AI meeting notes to a local folder as Markdown files — automatically, every 15 minutes, in the background.

## Why this exists

Granola is a fantastic AI notetaker, but it's a **cloud-first app**. Your meeting summaries and transcripts live on Granola's servers. The Mac app caches *some* metadata locally (in `~/Library/Application Support/Granola/`), but the actual readable content sits behind their API and inside an opaque LevelDB / Yjs CRDT store on disk — you cannot just `grep` your meeting notes.

That's a problem if you want to:

- **Pipe your notes into AI workflows** (Claude, ChatGPT, local RAG, Obsidian, etc.) — these all want plain text, not API access to a third-party service.
- **Own a local backup** — if Granola changes their pricing, sunsets your account, or has an outage, your meeting history shouldn't vanish with it.
- **Search across all meetings** with `grep`, `ripgrep`, or your editor of choice — instantly, offline, no per-query API call.
- **Feed notes into a personal knowledge system** like Obsidian, Logseq, or a custom Markdown vault.

This script solves that: it pulls every meeting from Granola via their (undocumented) API, converts the AI-enhanced HTML summary to clean Markdown, and writes one `.md` file per meeting to a folder of your choice.

## What it does

1. Reads your Granola auth token from the local app data (`~/Library/Application Support/Granola/supabase.json`) — no separate login needed.
2. Calls Granola's API to fetch all your documents (meetings) and, optionally, transcripts.
3. Converts the AI summary (HTML) to Markdown via a built-in HTMLParser — no external dependencies.
4. Writes each meeting to `<TARGET_DIR>/YYYY-MM-DD - Title.md` with a YAML frontmatter block (title, date, attendees, granola_id) so the files are structured and machine-readable.
5. Tracks already-synced meeting IDs in `.sync_state.json` so it doesn't re-sync (or overwrite) existing files.
6. Runs every 15 minutes via `launchd` once installed.

## Output format

Each file looks like this:

```markdown
---
title: "Quarterly board prep"
date: 2026-04-15
start: 2026-04-15T10:00:00+02:00
end: 2026-04-15T11:00:00+02:00
attendees: [Thomas Schenkelberg, Simone Becker]
type: meeting
granola_id: 4bc05f99-1db8-4b73-9a4b-0240bab27baf
updated: 2026-04-15T11:02:14.356Z
---

# Quarterly board prep

### Action items

- Finalize Q2 forecast by Apr 22
- ...
```

YAML frontmatter is consumed natively by Obsidian, Dataview, and most note tools — and gives AI assistants structured fields to filter on.

## Install

See **[INSTALL.md](INSTALL.md)** for the full setup walkthrough, including a section optimized for AI coding agents (Claude Code, Cursor, etc.) — paste it into your agent and it will install the tool end-to-end.

TL;DR for humans:

```bash
git clone https://github.com/tschenkster/granola-sync.git ~/granola-sync
cd ~/granola-sync
# 1. Set where notes should go (default: ~/Documents/Granola Meetings)
export GRANOLA_SYNC_TARGET_DIR="$HOME/Documents/Granola Meetings"
# 2. Test once
python3 granola_sync.py
# 3. Install background schedule (every 15 min)
./install.sh
```

## Configuration

All optional, all via environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `GRANOLA_SYNC_TARGET_DIR` | `~/Documents/Granola Meetings` | Where `.md` files are written. Set this to an iCloud or Google Drive path if you want sync across devices. |
| `GRANOLA_SYNC_INCLUDE_TRANSCRIPT` | `1` | Set to `0` to skip transcripts (smaller files, faster sync). |
| `GRANOLA_SYNC_STATE_FILE` | `<script-dir>/.sync_state.json` | Where the synced-ID ledger lives. |
| `GRANOLA_SYNC_LOG_FILE` | `/tmp/granola-sync.log` | Log file path. |

## Caveats

- **Uses reverse-engineered API endpoints.** Granola has not published an official API. These calls work today (Granola app v5.354.0) but could break with any backend change.
- **Token refresh requires the Granola app to be open.** The script reads tokens from the app's credential store but does not refresh them itself — keep Granola running on your Mac.
- **Token validity is ~6 hours.** When the cached token expires, the script logs a warning and skips that run. Open Granola, the app refreshes the token, and the next 15-min cycle picks up.
- **macOS only.** The auth-token path is Mac-specific (`~/Library/Application Support/Granola/`). Linux/Windows would need a different path.
- **Read-only.** This script never writes back to Granola — no risk of corrupting your account data.

## License

MIT — do whatever you want with it.

## Credits

Built by [Thomas Schenkelberg](https://cfo-team.de). If you find a bug or improve it, PRs welcome.
