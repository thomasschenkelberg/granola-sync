#!/usr/bin/env zsh
# Install granola-sync as a launchd job that runs every 15 minutes.
#
# Reads GRANOLA_SYNC_TARGET_DIR from the environment (set it before running),
# or falls back to ~/Documents/Granola Meetings.

set -e

SCRIPT_DIR="${0:A:h}"  # absolute path to this script's directory
SCRIPT_PATH="$SCRIPT_DIR/granola_sync.py"
TARGET_DIR="${GRANOLA_SYNC_TARGET_DIR:-$HOME/Documents/Granola Meetings}"

WRAPPER_PATH="$HOME/.local/bin/granola-sync"
PLIST_PATH="$HOME/Library/LaunchAgents/com.user.granola-sync.plist"

# --- Preflight ---

if [[ "$(uname)" != "Darwin" ]]; then
    echo "ERROR: This installer only works on macOS." >&2
    exit 1
fi

if [[ ! -f "$HOME/Library/Application Support/Granola/supabase.json" ]]; then
    echo "ERROR: Granola is not installed or you're not signed in." >&2
    echo "Install Granola from https://granola.ai and sign in first." >&2
    exit 1
fi

if [[ ! -x "/opt/homebrew/bin/python3" ]]; then
    echo "ERROR: Homebrew Python is required (Apple's sandboxed python3 cannot read iCloud/Drive paths)." >&2
    echo "Install with: brew install python@3.12" >&2
    exit 1
fi

if [[ ! -f "$SCRIPT_PATH" ]]; then
    echo "ERROR: Cannot find granola_sync.py at $SCRIPT_PATH" >&2
    exit 1
fi

# --- Install wrapper ---

mkdir -p "$HOME/.local/bin"

cat > "$WRAPPER_PATH" <<EOF
#!/bin/zsh
# Wrapper for granola-sync. launchd cannot reliably read scripts from
# ~/Library/CloudStorage when invoked via Apple's sandboxed /usr/bin/python3,
# so we explicitly use Homebrew python and exec the real script.
export PATH="\$HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export GRANOLA_SYNC_TARGET_DIR="$TARGET_DIR"
exec /opt/homebrew/bin/python3 "$SCRIPT_PATH" "\$@"
EOF
chmod +x "$WRAPPER_PATH"

echo "OK: wrapper installed at $WRAPPER_PATH"
echo "    target dir = $TARGET_DIR"

# --- Install launchd plist ---

mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.user.granola-sync</string>
    <key>ProgramArguments</key>
    <array>
        <string>$WRAPPER_PATH</string>
    </array>
    <key>StartInterval</key>
    <integer>900</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/granola-sync-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/granola-sync-stderr.log</string>
</dict>
</plist>
EOF

echo "OK: plist installed at $PLIST_PATH"

# --- Load launchd job (unload first if already loaded; idempotent) ---

launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load "$PLIST_PATH"

echo "OK: launchd job loaded"

# --- Verify ---

if launchctl list | grep -q "com.user.granola-sync"; then
    echo ""
    echo "✓ granola-sync is installed and scheduled to run every 15 minutes."
    echo ""
    echo "  Target dir:  $TARGET_DIR"
    echo "  Logs:        /tmp/granola-sync.log"
    echo "               /tmp/granola-sync-stderr.log"
    echo ""
    echo "  Keep the Granola app open so the auth token stays fresh."
    echo ""
    echo "  To uninstall:"
    echo "    launchctl unload $PLIST_PATH"
    echo "    rm $PLIST_PATH $WRAPPER_PATH"
else
    echo "ERROR: launchd job did not register. Check the plist." >&2
    exit 1
fi
