# Installation

This guide has two sections: **For humans** and **For AI agents**. The AI agent section is structured so you can paste the whole thing into Claude Code, Cursor, or any other coding agent and it will perform the setup end-to-end.

---

## Prerequisites

- macOS (the script reads Granola's auth token from `~/Library/Application Support/Granola/`, which only exists on Mac).
- [Granola](https://granola.ai) Mac app installed, signed in, and **left running** (the script piggybacks on the app's auth token; the app keeps the token fresh).
- Python 3.9 or newer. macOS ships with Python 3, but the system Python in `/usr/bin/python3` is sandboxed and **cannot read files from `~/Library/CloudStorage/`**. If you store the script in iCloud Drive or Google Drive, install Python via [Homebrew](https://brew.sh): `brew install python@3.12`.
- A terminal. Apple's Terminal.app works; iTerm2 works; VS Code's integrated terminal works.

---

## For humans

### 1. Clone the repo

```bash
git clone https://github.com/tschenkster/granola-sync.git ~/granola-sync
cd ~/granola-sync
```

(You can put it anywhere — `~/granola-sync` is just a sensible default.)

### 2. Choose where notes should be saved

Default: `~/Documents/Granola Meetings/`. To use a different folder (e.g. iCloud or a Google Drive path):

```bash
export GRANOLA_SYNC_TARGET_DIR="$HOME/Library/Mobile Documents/com~apple~CloudDocs/Granola Meetings"
```

You'll bake this into the launchd config in step 4, so you only need to set it once.

### 3. Test the sync manually

```bash
python3 granola_sync.py
```

You should see log lines like `Fetched 23 documents from Granola` and `Saved: 2026-04-15 - Quarterly board prep.md`. If you get `Token expired`, open the Granola app and try again. If you get `Failed to read Granola credentials`, make sure Granola is installed and you're signed in.

Check the target folder — your meetings should be there as `.md` files.

### 4. Install the background schedule

```bash
./install.sh
```

This script:

- Copies a wrapper into `~/.local/bin/granola-sync` (the wrapper sets `PATH` and uses Homebrew Python — necessary because launchd's Python sandbox can't read CloudStorage paths).
- Generates a launchd plist at `~/Library/LaunchAgents/com.user.granola-sync.plist` with your `GRANOLA_SYNC_TARGET_DIR`.
- Loads the plist (`launchctl load …`).
- Confirms the job is registered.

After install, the sync runs every 15 minutes. Logs go to `/tmp/granola-sync.log` and `/tmp/granola-sync-stderr.log`.

### 5. Verify it's running

```bash
launchctl list | grep granola
tail -f /tmp/granola-sync.log
```

You should see `0` in the second column (last exit code = success). If you see a non-zero exit code, check `/tmp/granola-sync-stderr.log`.

### Uninstall

```bash
launchctl unload ~/Library/LaunchAgents/com.user.granola-sync.plist
rm ~/Library/LaunchAgents/com.user.granola-sync.plist
rm ~/.local/bin/granola-sync
```

The script and your synced `.md` files remain — you can keep using them, or delete the folder if you want to start fresh.

---

## For AI agents

You are an AI coding agent (Claude Code, Cursor, etc.) tasked with installing `granola-sync` on a user's Mac. Follow these steps in order. Do not skip preflight checks — they catch the most common failure modes.

### Step 0: Preflight

Run these checks and report results to the user. If any fail, stop and fix before proceeding.

```bash
# Check macOS
[ "$(uname)" = "Darwin" ] || echo "FAIL: not macOS"

# Check Granola is installed and the user has signed in
test -f "$HOME/Library/Application Support/Granola/supabase.json" \
  && echo "OK: Granola credentials found" \
  || echo "FAIL: Granola not installed or not signed in"

# Check Homebrew Python is available (needed for launchd, since /usr/bin/python3 is sandboxed)
which /opt/homebrew/bin/python3 \
  || echo "FAIL: Homebrew python3 not installed — run: brew install python@3.12"

# Check Python version >= 3.9
/opt/homebrew/bin/python3 -c "import sys; assert sys.version_info >= (3, 9)" \
  && echo "OK: Python version" \
  || echo "FAIL: Python too old"
```

### Step 1: Clone the repo

Default install location: `~/granola-sync/`. Ask the user if they want a different location before proceeding.

```bash
git clone https://github.com/tschenkster/granola-sync.git ~/granola-sync
```

### Step 2: Determine the target directory

Ask the user: _"Where should your Granola meeting notes be saved? (Default: `~/Documents/Granola Meetings`)"_

Common choices:

- Local only: `~/Documents/Granola Meetings`
- iCloud Drive (synced across Apple devices): `~/Library/Mobile Documents/com~apple~CloudDocs/Granola Meetings`
- Google Drive (if installed): `~/Library/CloudStorage/GoogleDrive-<email>/My Drive/Granola Meetings`
- Obsidian vault: `<vault path>/Meetings/Granola`

Save the chosen path in a shell variable for the next steps:

```bash
TARGET_DIR="<user's choice>"
```

If the path is in `~/Library/CloudStorage/`, **explicitly warn the user** that you must use Homebrew Python (not Apple's `/usr/bin/python3`) — the install script handles this, but mention it so they understand why the wrapper exists.

### Step 3: Test sync manually before installing the schedule

```bash
cd ~/granola-sync
GRANOLA_SYNC_TARGET_DIR="$TARGET_DIR" /opt/homebrew/bin/python3 granola_sync.py
```

Expected: log lines `Fetched N documents from Granola` and one `Saved: ...` per meeting. Confirm with `ls "$TARGET_DIR"` that `.md` files appeared.

If `Token expired` appears: tell the user to open the Granola app (it refreshes the token automatically), then retry.
If `Failed to read Granola credentials`: Granola is not installed or the user is not signed in.
If `API error 401`: token is invalid — same fix (open Granola).

### Step 4: Install the background schedule

```bash
cd ~/granola-sync
GRANOLA_SYNC_TARGET_DIR="$TARGET_DIR" ./install.sh
```

This script is idempotent — re-running it just updates the existing config.

### Step 5: Verify

```bash
launchctl list | grep granola-sync
```

Expected output: `<PID-or-dash>  0  com.user.granola-sync`. The middle column (`0`) is the last exit code — it should be `0`. If non-zero, read `/tmp/granola-sync-stderr.log` and report the error to the user.

### Step 6: Report back to the user

Tell the user:

- The target folder where notes will accumulate.
- That the sync runs every 15 minutes.
- That **Granola must remain open on their Mac** (or run on login) — otherwise the auth token expires and syncs will fail silently after ~6 hours.
- Where logs are: `/tmp/granola-sync.log` (info) and `/tmp/granola-sync-stderr.log` (errors).
- How to uninstall (see "For humans" → Uninstall section above).
