"""Notes edited recently in Obsidian vaults, read straight from the vault folders (docs/design/obsidian-source.md).

A note is a Markdown file whose modification time is recent. Its title is the file name; from the body only the
note links, tags, Jira keys and GitHub PR urls are kept, never the text. thync only reads from a vault.
"""
import os
import re
import stat
import sys
import threading
import time
import urllib.parse
from pathlib import Path

from ..config import CARDS_DIR, MAX_DAYS, OBSIDIAN_VAULTS
from .jira import TITLE_KEY

MAX_NOTES = 100           # newest notes kept, across all vaults
READ_BYTES = 256 * 1024   # only the head of a note is read
SCAN_SEC = 30             # pause between scans; also how stale a result may be before a caller scans itself
PER_NOTE = 10             # PR urls kept per note (graph.py applies the same limit to Jira keys)

FENCE = re.compile(r"^[ \t]*(```|~~~).*?(?:^[ \t]*\1[^\n]*$|\Z)", re.S | re.M)  # an open fence runs to the end
INLINE_CODE = re.compile(r"`[^`\n]*`")
FRONT = re.compile(r"\A---[ \t]*\n(.*?)^---[ \t]*$", re.S | re.M)
FRONT_TAGS = re.compile(r"^tags?:[ \t]*(.*)\n((?:[ \t]*-[ \t]*.*\n?)*)", re.M)
LINK = re.compile(r"\[\[([^\]|#^\n]+)")
TAG = re.compile(r"(?:^|(?<=\s))#([\w/-]+)", re.M)  # at the start of a line or after whitespace: not a url fragment
PULL = re.compile(r"https://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)")

_notes: tuple[float, list] = (0.0, [])
_threaded = False  # a refresh thread owns the scanning; callers then only read its last result
_lock = threading.Lock()


def _unique(items) -> list:
    return list(dict.fromkeys(items))


def _tag(raw: str) -> str:
    t = raw.strip().strip("'\"").lstrip("#").lower()
    return t if re.fullmatch(r"[\w/-]+", t) and not t.isdigit() else ""


def extract(text: str) -> dict:
    """What a note's text adds to the graph: {links, tags, keys, prs}. Code is skipped."""
    tags = []
    front = FRONT.match(text)
    if front:
        m = FRONT_TAGS.search(front.group(1) + "\n")
        if m:
            inline = m.group(1).strip().strip("[]")
            tags += inline.split(",") if inline else [line.split("-", 1)[1] for line in m.group(2).splitlines() if "-" in line]
    body = INLINE_CODE.sub(" ", FENCE.sub(" ", text))
    tags += TAG.findall(body)
    links = [Path(t.strip()).name for t in LINK.findall(body)]
    return {"links": _unique(n[:-3].lower() if n.lower().endswith(".md") else n.lower() for n in links if n),
            "tags": _unique(t for t in map(_tag, tags) if t),
            "keys": _unique(TITLE_KEY.findall(body)),
            "prs": _unique((f"https://github.com/{o}/{r}/pull/{n}", r, int(n)) for o, r, n in PULL.findall(body))[:PER_NOTE]}


def _recent_files(vault: dict, since: float, cards: str) -> list[tuple[float, dict, str]]:
    """(mtime, vault, path) of the vault's Markdown files changed after `since`. Symlinks are never followed."""
    found = []
    for folder, subfolders, files in os.walk(vault["path"], followlinks=False):
        subfolders[:] = [d for d in subfolders if not d.startswith(".") and not os.path.islink(os.path.join(folder, d))
                         and os.path.realpath(os.path.join(folder, d)) != cards]
        for name in files:
            if not name.lower().endswith(".md"):
                continue
            path = os.path.join(folder, name)
            try:
                st = os.lstat(path)
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode) and st.st_mtime > since:
                found.append((st.st_mtime, vault, path))
    return found


def scan(now: float) -> list[dict]:
    """The newest MAX_NOTES notes of all vaults, in vault order and then by path."""
    cards = os.path.realpath(CARDS_DIR)
    files = []
    for vault in OBSIDIAN_VAULTS:
        if os.path.isdir(vault["path"]) and os.path.realpath(vault["path"]) != cards:
            files += _recent_files(vault, now - MAX_DAYS * 86400, cards)
    notes = []
    for at, vault, path in sorted(files, key=lambda f: f[0], reverse=True)[:MAX_NOTES]:
        try:
            with open(path, "rb") as f:
                text = f.read(READ_BYTES).decode("utf-8", errors="ignore")
        except OSError:
            continue
        rel = Path(path).relative_to(vault["path"]).as_posix()
        notes.append({"vault": vault["name"], "rel": rel, "title": Path(path).stem, "profile": vault["profile"], "at": at,
                      "url": "obsidian://open?path=" + urllib.parse.quote(path, safe=""), **extract(text)})
    order = {v["name"]: i for i, v in enumerate(OBSIDIAN_VAULTS)}
    return sorted(notes, key=lambda n: (order[n["vault"]], n["rel"]))


def recent_notes(now: float) -> list[dict]:
    """The last scan's notes. Without a refresh thread (the MCP server, tests) a stale result is rescanned here."""
    global _notes
    if not OBSIDIAN_VAULTS:
        return []
    if not _threaded and now - _notes[0] > SCAN_SEC and _lock.acquire(blocking=False):
        try:
            _notes = (now, scan(now))
        finally:
            _lock.release()
    return _notes[1]


def refresh_forever() -> None:
    global _notes
    while OBSIDIAN_VAULTS:
        try:
            _notes = (time.time(), scan(time.time()))
        except Exception as e:  # noqa: BLE001 — the thread must keep running; the next pass tries again
            print(f"thync: Obsidian scan failed ({type(e).__name__})", file=sys.stderr, flush=True)
        time.sleep(SCAN_SEC)


def start() -> None:
    """Scan in the background from now on, so a request never waits for a vault walk."""
    global _threaded
    if OBSIDIAN_VAULTS:
        _threaded = True
        threading.Thread(target=refresh_forever, daemon=True).start()
