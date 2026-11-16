"""Files thync writes under STATE_DIR: access token, renamed nodes, kanban cards, handed-off sessions.

Kanban cards are one Markdown file per card: id, column and title in the front matter, the note in the
body. Any editor (or Obsidian) can change them, and a new file becomes a hand-made card.
"""
import hashlib
import os
import re
import secrets
import sys
import threading
from pathlib import Path

from .config import CARDS_DIR, STATE_DIR
from .util import is_kanban, read_json, write_json, write_text

TOKEN_FILE = STATE_DIR / "token"
NAMES_FILE = STATE_DIR / "names.json"
REFLEX_FILE = STATE_DIR / "reflex.json"

BOARD_COLUMNS = {"todo", "doing", "done"}  # a file still saying "background" falls back to the automatic column
WRITE_LOCK = threading.Lock()  # read-modify-write of these files happens on request threads
CARD_KEYS = ("id", "title", "column", "profile", "manual")


# --- Access token ---

def load_token() -> str:
    """Return the token in STATE_DIR/token, creating it (readable only by me) on first run."""
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    if not TOKEN_FILE.exists() or not TOKEN_FILE.read_text(encoding="utf-8").strip():  # an empty token would let anyone in
        TOKEN_FILE.write_text(secrets.token_urlsafe(24), encoding="utf-8")
    os.chmod(TOKEN_FILE, 0o600)
    return TOKEN_FILE.read_text(encoding="utf-8").strip()


# --- Names changed in the UI (node id -> name) ---

def load_names() -> dict:
    return read_json(NAMES_FILE, {})


def save_name(nid: str, name: str) -> None:
    with WRITE_LOCK:
        _save_name(nid, name)


def _save_name(nid: str, name: str) -> None:
    names = load_names()
    if name:
        names[nid] = name
    else:
        names.pop(nid, None)  # empty name = back to the automatic name
    write_json(NAMES_FILE, names)


# --- Sessions handed off to Claude (PR url -> record) ---

def load_reflexes() -> dict:
    return read_json(REFLEX_FILE, {})


def save_reflex(url: str, record: dict) -> None:
    with WRITE_LOCK:
        reflexes = load_reflexes()
        reflexes[url] = record
        write_json(REFLEX_FILE, reflexes)


# --- Kanban cards as Markdown files ---

def parse_card(text: str) -> tuple[dict, str]:
    """'---\\nkey: value\\n---\\nbody' -> (meta, body). Values are single-line strings."""
    meta: dict[str, str] = {}
    if text.startswith("---\n") and "\n---" in text[4:]:
        head, body = text[4:].split("\n---", 1)
        for line in head.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":  # a value quoted by hand, YAML style
                    v = v[1:-1]
                meta[k.strip()] = v
        return meta, body.lstrip("\n")
    return meta, text


_skipped: set[Path] = set()  # files in the card folder that are not card files, so each is reported once


def _card_text(path: Path) -> tuple[str | None, str]:
    """(text, '') of a card file, or (None, why) when the file is not one and must not be written over.
    A board of the Obsidian Kanban plugin: read as a card it would show every card's text as one note, and
    saving that card would rewrite the board's front matter. A file that is not UTF-8: thync cannot tell what
    it holds. A file that is missing or unreadable is (None, '')."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None, "not UTF-8"
    except OSError:
        return None, ""
    return (None, "an Obsidian Kanban board") if is_kanban(text) else (text, "")


def read_cards() -> dict[str, dict]:
    cards = {}
    for path in sorted(CARDS_DIR.glob("*.md")):
        text, why = _card_text(path)
        if why and path not in _skipped:  # once per file for as long as the server runs
            _skipped.add(path)
            print(f"thync: {path} is {why}, not a card file; skipped", file=sys.stderr, flush=True)
        if text is None:
            continue
        meta, note = parse_card(text)
        cid = meta.get("id") or f"manual:{path.stem}"
        cards[cid] = {"path": path, "meta": meta, "note": note.strip()}
    return cards


def card_path(cid: str) -> Path:
    # The hash keeps ids that differ only after the cut, or only in punctuation, in separate files.
    slug = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", cid).strip("_")[:60] or "card"
    return CARDS_DIR / f"{slug}-{hashlib.sha1(cid.encode()).hexdigest()[:8]}.md"


def save_card(cid: str, change: dict) -> None:
    """Apply a change (column, title, note, reset, ...) to a card file. A card with nothing left is deleted."""
    with WRITE_LOCK:
        _save_card(cid, change)


def _save_card(cid: str, change: dict) -> None:
    cards = read_cards()
    card = cards.get(cid, {"path": card_path(cid), "meta": {"id": cid}, "note": ""})
    meta, note = dict(card["meta"]), card["note"]
    meta["id"] = cid
    if change.get("reset"):
        meta.pop("column", None)  # back to the automatic column
    for key in ("column", "title", "profile", "manual"):
        if change.get(key):
            meta[key] = change[key]
    if "title" in change and not change["title"]:
        meta.pop("title", None)
    if "note" in change:
        note = change["note"]
    path = card["path"]
    # read_cards() skips these files, so a card id whose file name is one of them would otherwise land on it.
    why = _card_text(path)[1]
    if why:
        raise ValueError(f"{path} is {why}")
    if not note and set(meta) <= {"id"}:
        path.unlink(missing_ok=True)
        return
    CARDS_DIR.mkdir(parents=True, exist_ok=True)
    head = "\n".join(f"{k}: {' '.join(str(meta[k]).split())}" for k in CARD_KEYS if meta.get(k))
    write_text(path, f"---\n{head}\n---\n\n{note.strip()}\n")
