#!/usr/bin/env python3
"""Sync Granola AI meeting notes to a local folder as Markdown files."""

from __future__ import annotations

import html
import json
import logging
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

# --- Configuration ---

# As of mid-2026 Granola writes auth to stored-accounts.json. supabase.json is a
# legacy file from older app versions and is no longer updated for new sign-ins.
# We try the new file first, then fall back to the legacy file for older installs.
GRANOLA_STORED_ACCOUNTS = Path.home() / "Library/Application Support/Granola/stored-accounts.json"
GRANOLA_CREDENTIALS = Path.home() / "Library/Application Support/Granola/supabase.json"

# Target directory is configurable via env var. Default = ~/Documents/Granola Meetings
# Set GRANOLA_SYNC_TARGET_DIR to override (e.g. an iCloud or Google Drive path).
DEFAULT_TARGET_DIR = Path.home() / "Documents" / "Granola Meetings"
TARGET_DIR = Path(os.environ.get("GRANOLA_SYNC_TARGET_DIR", str(DEFAULT_TARGET_DIR))).expanduser()

SCRIPT_DIR = Path(__file__).parent
STATE_FILE = Path(
    os.environ.get("GRANOLA_SYNC_STATE_FILE", str(SCRIPT_DIR / ".sync_state.json"))
).expanduser()
LOG_FILE = Path(os.environ.get("GRANOLA_SYNC_LOG_FILE", "/tmp/granola-sync.log")).expanduser()
TOKEN_WARNING_MARKER = Path(
    os.environ.get("GRANOLA_SYNC_TOKEN_WARNING_MARKER", "/tmp/granola-sync-token-warning-last.txt")
).expanduser()
TOKEN_WARNING_INTERVAL_SECONDS = int(os.environ.get("GRANOLA_SYNC_TOKEN_WARNING_INTERVAL_SECONDS", str(6 * 3600)))

API_BASE = "https://api.granola.ai"
DOCUMENTS_URL = f"{API_BASE}/v2/get-documents"
TRANSCRIPT_URL = f"{API_BASE}/v1/get-document-transcript"
HEADERS_BASE = {
    "Content-Type": "application/json",
    "User-Agent": "Granola/5.354.0",
    "X-Client-Version": "5.354.0",
}
BATCH_SIZE = 50
INCLUDE_TRANSCRIPT = os.environ.get("GRANOLA_SYNC_INCLUDE_TRANSCRIPT", "1") not in ("0", "false", "False")

# --- Logging ---

# Ensure log dir exists (for non-/tmp custom paths)
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("granola-sync")

# --- Auth ---


def _load_tokens_from_stored_accounts() -> dict | None:
    """Parse the new stored-accounts.json format (mid-2026+)."""
    data = json.loads(GRANOLA_STORED_ACCOUNTS.read_text())
    accounts = json.loads(data["accounts"])
    if not accounts:
        raise KeyError("stored-accounts.json has no accounts")
    return json.loads(accounts[0]["tokens"])


def _load_tokens_from_supabase_json() -> dict | None:
    """Parse the legacy supabase.json format."""
    data = json.loads(GRANOLA_CREDENTIALS.read_text())
    return json.loads(data["workos_tokens"])


def get_access_token() -> str | None:
    """Read the current access token from Granola's local credential store."""
    tokens = None
    source = None
    if GRANOLA_STORED_ACCOUNTS.exists():
        try:
            tokens = _load_tokens_from_stored_accounts()
            source = GRANOLA_STORED_ACCOUNTS.name
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            log.warning("%s present but unreadable (%s); trying legacy file", GRANOLA_STORED_ACCOUNTS.name, e)
    if tokens is None:
        try:
            tokens = _load_tokens_from_supabase_json()
            source = GRANOLA_CREDENTIALS.name
        except (FileNotFoundError, KeyError, json.JSONDecodeError) as e:
            log.error("Failed to read Granola credentials: %s", e)
            return None

    obtained_at_ms = tokens.get("obtained_at", 0)
    expires_in = tokens.get("expires_in", 0)
    now_ms = int(time.time() * 1000)
    if now_ms > obtained_at_ms + (expires_in * 1000):
        # Throttle the warning so a stale token doesn't spam every 15-min run.
        now_s = int(time.time())
        last_warned = 0
        try:
            last_warned = int(TOKEN_WARNING_MARKER.read_text().strip())
        except (FileNotFoundError, ValueError, OSError):
            pass
        if now_s - last_warned >= TOKEN_WARNING_INTERVAL_SECONDS:
            log.warning(
                "Token expired (source: %s). Open Granola app to refresh. "
                "Skipping this sync run.",
                source,
            )
            try:
                TOKEN_WARNING_MARKER.write_text(str(now_s))
            except OSError:
                pass
        return None

    return tokens.get("access_token")


# --- API ---


def api_post(url: str, token: str, body: dict) -> dict | list | None:
    """Make an authenticated POST request to the Granola API."""
    headers = {**HEADERS_BASE, "Authorization": f"Bearer {token}"}
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers=headers,
        method="POST",
    )
    # Accept gzip
    req.add_header("Accept-Encoding", "gzip")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            # Decompress if gzipped
            if resp.headers.get("Content-Encoding") == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            log.warning("Auth failed (401). Token may be expired. Open Granola to refresh.")
        else:
            log.error("API error %d: %s", e.code, e.reason)
        return None
    except Exception as e:
        log.error("Request failed: %s", e)
        return None


def fetch_documents(token: str) -> list[dict]:
    """Fetch all documents from Granola."""
    all_docs = []
    offset = 0
    while True:
        result = api_post(
            DOCUMENTS_URL,
            token,
            {
                "limit": BATCH_SIZE,
                "offset": offset,
                "include_last_viewed_panel": True,
            },
        )
        if not result or "docs" not in result:
            break
        docs = result["docs"]
        if not docs:
            break
        all_docs.extend(docs)
        if len(docs) < BATCH_SIZE:
            break
        offset += BATCH_SIZE
    return all_docs


def fetch_transcript(token: str, doc_id: str) -> list[dict]:
    """Fetch transcript segments for a document."""
    result = api_post(TRANSCRIPT_URL, token, {"document_id": doc_id})
    if isinstance(result, list):
        return result
    return []


# --- HTML to Markdown (simple) ---


def html_to_markdown(html_str: str) -> str:
    """Convert HTML (as returned by Granola) to Markdown with nested list support."""
    if not html_str:
        return ""

    from html.parser import HTMLParser

    class MarkdownConverter(HTMLParser):
        def __init__(self):
            super().__init__()
            self.output = []
            self.list_depth = -1
            self.in_li = False
            self.heading_level = 0

        def handle_starttag(self, tag, attrs):
            attrs_dict = dict(attrs)
            if tag in ("ul", "ol"):
                self.list_depth += 1
            elif tag == "li":
                self.in_li = True
                indent = "  " * self.list_depth
                self.output.append(f"\n{indent}- ")
            elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                self.heading_level = int(tag[1])
                self.output.append(f"\n{'#' * self.heading_level} ")
            elif tag == "strong" or tag == "b":
                self.output.append("**")
            elif tag == "em" or tag == "i":
                self.output.append("*")
            elif tag == "a":
                self.output.append("[")
            elif tag == "p":
                if self.list_depth < 0:
                    self.output.append("\n")
            elif tag == "br":
                self.output.append("\n")
            elif tag == "code":
                self.output.append("`")
            elif tag == "pre":
                self.output.append("\n```\n")
            self._last_tag = tag
            self._last_attrs = attrs_dict

        def handle_endtag(self, tag):
            if tag in ("ul", "ol"):
                self.list_depth -= 1
                if self.list_depth < 0:
                    self.output.append("\n")
            elif tag == "li":
                self.in_li = False
            elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                self.heading_level = 0
                self.output.append("\n")
            elif tag == "strong" or tag == "b":
                self.output.append("**")
            elif tag == "em" or tag == "i":
                self.output.append("*")
            elif tag == "a":
                href = getattr(self, "_last_attrs", {}).get("href", "")
                self.output.append(f"]({href})")
            elif tag == "p":
                if self.list_depth < 0:
                    self.output.append("\n")
            elif tag == "code":
                self.output.append("`")
            elif tag == "pre":
                self.output.append("```\n")

        def handle_data(self, data):
            # Skip whitespace-only text between tags inside lists
            if self.list_depth >= 0 and not data.strip():
                return
            self.output.append(data)

        def get_markdown(self):
            text = "".join(self.output)
            text = html.unescape(text)
            text = re.sub(r"\n{3,}", "\n\n", text)
            return text.strip()

    converter = MarkdownConverter()
    converter.feed(html_str)
    return converter.get_markdown()


# --- Transcript formatting ---


def format_transcript(segments: list[dict]) -> str:
    """Format transcript segments into readable text."""
    if not segments:
        return ""
    lines = []
    for seg in segments:
        ts = seg.get("start_timestamp", "")
        # Extract just the time portion
        if "T" in ts:
            time_part = ts.split("T")[1][:8]
        else:
            time_part = ts
        text = seg.get("text", "").strip()
        source = seg.get("source", "")
        if text:
            if source and source != "system":
                lines.append(f"[{time_part}] **{source}**: {text}")
            else:
                lines.append(f"[{time_part}] {text}")
    return "\n".join(lines)


# --- State ---


def load_state() -> set[str]:
    """Load set of already-synced document IDs."""
    try:
        data = json.loads(STATE_FILE.read_text())
        return set(data.get("synced_ids", []))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_state(synced_ids: set[str]) -> None:
    """Save synced document IDs."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(
            {
                "synced_ids": sorted(synced_ids),
                "last_sync": datetime.utcnow().isoformat() + "Z",
            },
            indent=2,
        )
    )


# --- File writing ---


def sanitize_filename(name: str) -> str:
    """Make a string safe for use as a filename."""
    # Replace problematic characters
    name = re.sub(r'[<>:"/\\|?*]', "", name)
    name = re.sub(r"\s+", " ", name).strip()
    # Truncate
    if len(name) > 100:
        name = name[:100].strip()
    return name


def build_markdown(doc: dict, transcript_text: str) -> str:
    """Build a complete markdown file from a Granola document."""
    title = doc.get("title", "Untitled")
    created = doc.get("created_at", "")
    updated = doc.get("updated_at", "")
    doc_id = doc.get("id", "")
    doc_type = doc.get("type", "")

    # Extract date
    date_str = created[:10] if created else ""

    # Extract attendees
    people = doc.get("people", {})
    attendees = []
    if people:
        creator = people.get("creator", {})
        if creator.get("name"):
            attendees.append(creator["name"])
        for att in people.get("attendees", []):
            name = att.get("name", att.get("email", ""))
            if name:
                attendees.append(name)

    # Calendar event info
    cal = doc.get("google_calendar_event", {})
    start_time = ""
    end_time = ""
    if cal:
        start = cal.get("start", {}).get("dateTime", "")
        end = cal.get("end", {}).get("dateTime", "")
        if start:
            start_time = start
        if end:
            end_time = end

    # Build frontmatter
    fm_lines = [
        "---",
        f'title: "{title}"',
        f"date: {date_str}",
    ]
    if start_time:
        fm_lines.append(f"start: {start_time}")
    if end_time:
        fm_lines.append(f"end: {end_time}")
    if attendees:
        fm_lines.append(f"attendees: [{', '.join(attendees)}]")
    fm_lines.append(f"type: {doc_type}")
    fm_lines.append(f"granola_id: {doc_id}")
    fm_lines.append(f"updated: {updated}")
    fm_lines.append("---")
    frontmatter = "\n".join(fm_lines)

    # Build body
    sections = [frontmatter, "", f"# {title}"]

    # Enhanced notes (AI summary) from last_viewed_panel
    lvp = doc.get("last_viewed_panel")
    if lvp:
        enhanced_html = lvp.get("original_content", "")
        if enhanced_html:
            enhanced_md = html_to_markdown(enhanced_html)
            if enhanced_md:
                sections.append("")
                sections.append(enhanced_md)

    # User's own notes
    user_notes = doc.get("notes_markdown", "")
    if user_notes and user_notes.strip():
        sections.append("")
        sections.append("## My Notes")
        sections.append("")
        sections.append(user_notes.strip())

    # Transcript
    if transcript_text:
        sections.append("")
        sections.append("## Transcript")
        sections.append("")
        sections.append(transcript_text)

    return "\n".join(sections) + "\n"


def write_note(doc: dict, transcript_text: str) -> Path | None:
    """Write a single meeting note to the target directory."""
    title = doc.get("title", "Untitled")
    created = doc.get("created_at", "")
    date_str = created[:10] if created else "undated"

    filename = sanitize_filename(f"{date_str} - {title}") + ".md"
    filepath = TARGET_DIR / filename

    # Don't overwrite existing files
    if filepath.exists():
        return filepath

    content = build_markdown(doc, transcript_text)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(content, encoding="utf-8")
    return filepath


# --- Main ---


def sync() -> None:
    """Run a single sync cycle."""
    log.info("Starting Granola sync (target=%s)", TARGET_DIR)

    token = get_access_token()
    if not token:
        return

    # Fetch documents
    docs = fetch_documents(token)
    log.info("Fetched %d documents from Granola", len(docs))

    if not docs:
        return

    # Load state
    synced_ids = load_state()
    new_count = 0

    for doc in docs:
        doc_id = doc.get("id", "")
        if not doc_id:
            continue

        # Skip already synced
        if doc_id in synced_ids:
            continue

        # Skip deleted
        if doc.get("deleted_at"):
            continue

        # Fetch transcript if enabled
        transcript_text = ""
        if INCLUDE_TRANSCRIPT and doc.get("transcribe", False):
            segments = fetch_transcript(token, doc_id)
            transcript_text = format_transcript(segments)

        # Write the note
        filepath = write_note(doc, transcript_text)
        if filepath:
            synced_ids.add(doc_id)
            new_count += 1
            log.info("Saved: %s", filepath.name)

    # Save state
    save_state(synced_ids)
    log.info("Sync complete. %d new notes saved.", new_count)


if __name__ == "__main__":
    try:
        sync()
    except Exception as e:
        log.exception("Sync failed: %s", e)
        sys.exit(1)
