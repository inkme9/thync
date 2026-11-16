"""Notes edited recently in Obsidian vaults, read straight from the vault folders (docs/design/obsidian-source.md),
and the cards of one Obsidian Kanban board (docs/design/obsidian-board.md).

A note is a Markdown file whose modification time is recent. Its title is the file name; from the body only the
note links, tags, Jira keys and GitHub PR urls are kept, never the text. The Kanban board is the exception: its
cards are text meant to be shown, so their text is kept. thync only reads from a vault.
"""
import hashlib
import os
import re
import stat
import sys
import threading
import time
import unicodedata
import urllib.parse
from pathlib import Path

from ..config import CARDS_DIR, MAX_DAYS, OBSIDIAN_BOARD, OBSIDIAN_VAULTS
from ..util import BOARD_CARD, FRONT_MATTER, is_kanban
from .jira import TITLE_KEY

MAX_NOTES = 100           # newest notes kept, across all vaults
READ_BYTES = 256 * 1024   # only the head of a note is read
SCAN_SEC = 30             # pause between scans; also how stale a result may be before a caller scans itself
PER_NOTE = 10             # PR urls kept per note (graph.py applies the same limit to Jira keys)

FENCE = re.compile(r"^[ \t]*(```|~~~).*?(?:^[ \t]*\1[^\n]*$|\Z)", re.S | re.M)  # an open fence runs to the end
INLINE_CODE = re.compile(r"`[^`\n]*`")
FRONT = re.compile(r"\A---[ \t]*\n(.*?)^---[ \t]*$", re.S | re.M)
FRONT_TAGS = re.compile(r"^tags?:[ \t]*(.*)\n((?:[ \t]*-[ \t]*.*\n?)*)", re.M)
# [[name]], [[name|alias]], [[name#heading]], [[name^block]]; in a table the alias bar is escaped: [[name\|alias]].
# The closing ]] is required, so a link the 256KB cut left open is not read as a link to a shorter name.
LINK = re.compile(r"\[\[([^\[\]|#^\n\\]+)(?:[|#^\\][^\[\]\n]*)?\]\]")  # no [ inside (so [[note|see [1]]] is not read): a run of [[ stays linear
TAG = re.compile(r"(?:^|(?<=\s))#([\w/-]+)", re.M)  # at the start of a line or after whitespace: not a url fragment
# ASCII letters and digits only at the edges: a Korean particle or markup may touch the url (…/pull/5와, _…/pull/5_).
PULL = re.compile(r"(?<![A-Za-z0-9])https://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)(?![A-Za-z0-9])")

MAX_BOARD_CARDS = 200     # cards read from the Kanban board
LANE = re.compile(r"^##[ \t]+(.*\S)[ \t]*$")
ITEM = re.compile(r"^- (?:\[(.)\][ \t]+)?(.*\S)[ \t]*$")       # "- [ ] text", "- [x] text" or "- text", not indented
BOLD_LINE = re.compile(r"\*\*[^*\n]+\*\*")                      # the plugin's mark of a lane that completes its cards
BLOCK_ID = re.compile(r"[ \t]\^[\w-]+$")
WIKILINK = re.compile(r"\[\[(?:[^\]|\n]*\|)?([^\]|\n]+)\]\]")
WORD = re.compile(r"[^\W_]+")
DONE_WORDS = {"완료", "완료됨", "종료", "done", "complete", "completed", "closed"}
DOING_WORDS = {"진행", "진행중", "대기", "막힘", "확인", "검수", "doing", "progress", "blocked", "waiting", "review"}

_notes: tuple[float, list] = (0.0, [])
_board: tuple[tuple | None, list] = (None, [])  # (what lstat said of the board file, its cards)
_threaded = False  # a refresh thread owns the scanning; callers then only read its last result
_lock = threading.Lock()


def _unique(items) -> list:
    return list(dict.fromkeys(items))


def fold(name: str) -> str:
    """The form names are compared in. macOS can store a Korean file name decomposed (NFD) while the note that
    links to it has the composed form, and Obsidian matches names without regard to case."""
    return unicodedata.normalize("NFC", name).lower()


def _tag(raw: str) -> str:
    t = fold(raw.strip().strip("'\"").lstrip("#"))
    return t if re.fullmatch(r"[\w/-]+", t) and any(c.isalpha() for c in t) else ""


def extract(text: str) -> dict:
    """What a note's text adds to the graph: {links, tags, keys, prs}. Code is skipped."""
    tags = []
    text = text.replace("\r\n", "\n")
    front = FRONT.match(text)
    if front:
        m = FRONT_TAGS.search(front.group(1) + "\n")
        if m:
            inline = m.group(1).strip().strip("[]")
            tags += inline.split(",") if inline else [line.split("-", 1)[1] for line in m.group(2).splitlines() if "-" in line]
    body = INLINE_CODE.sub(" ", FENCE.sub(" ", text))
    tags += TAG.findall(body)
    links = [Path(t.strip()).name for t in LINK.findall(body)]
    return {"links": _unique(fold(n[:-3] if n.lower().endswith(".md") else n) for n in links if n),
            "tags": _unique(t for t in map(_tag, tags) if t),
            "keys": _unique(TITLE_KEY.findall(body)),
            "prs": _unique((f"https://github.com/{o}/{r}/pull/{n}", r, int(n)) for o, r, n in PULL.findall(body))[:PER_NOTE]}


def _recent_files(vault: dict, since: float, cards: str, skip: tuple | None = None) -> list[tuple[float, dict, str]]:
    """(mtime, vault, path) of the vault's Markdown files changed after `since`. Symlinks are never followed.
    `skip` is the (device, inode) of a file to leave out: the Kanban board, which is no note."""
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
            if stat.S_ISREG(st.st_mode) and st.st_mtime > since and (st.st_dev, st.st_ino) != skip:
                found.append((st.st_mtime, vault, path))
    return found


def scan(now: float) -> list[dict]:
    """The newest MAX_NOTES notes of all vaults, in vault order and then by path."""
    cards = os.path.realpath(CARDS_DIR)
    skip = None
    if OBSIDIAN_BOARD:
        try:
            # Device and inode, not the path: a vault reached through a symlink, or a Korean file name stored
            # decomposed, still names the same file.
            st = os.lstat(OBSIDIAN_BOARD["path"])
            skip = (st.st_dev, st.st_ino)
        except OSError:
            pass
    files = []
    for vault in OBSIDIAN_VAULTS:
        if os.path.isdir(vault["path"]) and os.path.realpath(vault["path"]) != cards:
            files += _recent_files(vault, now - MAX_DAYS * 86400, cards, skip)
    notes = []
    for at, vault, path in sorted(files, key=lambda f: f[0], reverse=True)[:MAX_NOTES]:
        try:
            # O_NOFOLLOW: a file swapped for a symlink since the listing must not lead out of the vault.
            with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb") as f:
                data = f.read(READ_BYTES + 1)
        except OSError:
            continue
        text = data[:READ_BYTES].decode("utf-8", errors="ignore")
        if len(data) > READ_BYTES:
            # The cut can split a word: ABC-123 would read as ABC-12, another ticket. Drop what follows the last
            # whitespace, so every key, url and tag that is kept is whole.
            text = text[:max((text.rfind(c) for c in " \n\t"), default=-1) + 1]
        rel = Path(path).relative_to(vault["path"]).as_posix()
        # A clock set wrong or a sync tool can date a file in the future; it was edited now at the latest.
        notes.append({"vault": vault["name"], "rel": rel, "title": Path(path).stem, "profile": vault["profile"],
                      "at": min(at, now),
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


# --- The Kanban board ---

def board_column(lane: str, complete: bool, checked: bool) -> str:
    """The thync column of a card: done when it is checked or its lane completes cards, else by the words of the
    lane's name. Whole words only, so 미완료 and Incomplete are not done."""
    words = set(WORD.findall(lane.lower()))
    if checked or complete or words & DONE_WORDS:
        return "done"
    return "doing" if words & DOING_WORDS else "todo"


EMPHASIS = re.compile(r"(\*\*|~~|==|\*)(?=[^\s*~=])(.+?)(?<=[^\s*~=])\1")  # marks around text, not a line of marks


def _plain(text: str) -> str:
    """Card text without its markup: links and embeds as their names, emphasis without its marks."""
    text = WIKILINK.sub(r"\1", text.replace("![[", "[["))
    for _ in range(3):  # **bold *and italic***
        text = EMPHASIS.sub(r"\2", text)
    return text


def parse_board(text: str, profile: str, path: str, updated: int) -> list[dict]:
    """The cards of a Kanban board file, in board order. Not a board (no kanban-plugin key): no cards."""
    text = text.replace("\r\n", "\n")
    if not is_kanban(text):
        return []
    items: list[dict] = []
    lane = item = None
    for line in text[FRONT_MATTER.match(text).end():].split("\n"):
        if line.startswith("%% kanban:settings") or line.rstrip() == "***":  # the settings block; the archive (column 0)
            break
        heading, entry = LANE.match(line), ITEM.match(line)
        if heading:
            lane, item = {"name": heading.group(1), "complete": False, "cards": 0}, None
        elif lane and entry and re.fullmatch(r"\[.\]", entry.group(2)):
            item = None  # a checkbox with no text is no card
        elif lane and entry:
            item = {"lane": lane, "checked": (entry.group(1) or "") in "xX" and bool(entry.group(1)), "lines": [entry.group(2)]}
            lane["cards"] += 1
            items.append(item)
        elif item and line[:1] in " \t" and line.strip():
            item["lines"].append(line.strip())  # a card of several lines
        elif lane and not lane["cards"] and BOLD_LINE.fullmatch(line.strip()):
            lane["complete"] = True
    url = "obsidian://open?path=" + urllib.parse.quote(path, safe="")
    cards, seen = [], {}
    for it in items[:MAX_BOARD_CARDS]:
        first, rest = BLOCK_ID.sub("", it["lines"][0]), it["lines"][1:]
        # The id comes from the text alone, so checking a card or moving it to another lane keeps it.
        digest = hashlib.sha1(" ".join(" ".join([first, *rest]).split()).encode()).hexdigest()[:12]
        seen[digest] = seen.get(digest, 0) + 1
        bold = re.match(r"\*\*(.+?)\*\*[ \t]*(.*)$", first)
        title, lead = (bold.group(1), bold.group(2).lstrip("—–-:· \t")) if bold else (first, "")
        column = board_column(it["lane"]["name"], it["lane"]["complete"], it["checked"])
        cards.append({"id": BOARD_CARD + digest + (f"-{seen[digest]}" if seen[digest] > 1 else ""),
                      "title": " ".join(_plain(title).split())[:200] or "(제목 없음)",
                      "note": "\n".join(_plain(x) for x in ([lead] if lead else []) + rest)[:4000],
                      "sub": "", "column": column, "auto_column": column, "moved": False, "profile": profile,
                      "updated": updated, "focus": "", "file": path, "url": url, "readonly": True,
                      "tags": [it["lane"]["name"], "Obsidian"]})
    return cards


def _read_board(board: dict, st: os.stat_result) -> list[dict] | None:
    """The board's cards; [] when the file is no board; None when it could not be read (try again next time)."""
    try:
        # O_NOFOLLOW and O_NONBLOCK: between the lstat and here the file may have become a symlink or a FIFO.
        fd = os.open(board["path"], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):  # or a folder, which opens but cannot be read
            os.close(fd)
            return None
        with os.fdopen(fd, "rb") as f:
            data = f.read(READ_BYTES + 1)
    except OSError:
        return None
    if len(data) > READ_BYTES:
        data = data[:data.rfind(b"\n", 0, READ_BYTES) + 1]  # whole lines only
    try:
        return parse_board(data.decode("utf-8"), board["profile"], board["path"], int(st.st_mtime))
    except UnicodeDecodeError:
        return []


def board_cards() -> list[dict]:
    """The cards of the configured Kanban board. The file is read again only when lstat says it changed."""
    global _board
    if not OBSIDIAN_BOARD:
        return []
    try:
        st = os.lstat(OBSIDIAN_BOARD["path"])
    except OSError:
        return []
    if not stat.S_ISREG(st.st_mode):  # a symlink, a folder, a FIFO
        return []
    key = (st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_ino, st.st_dev)
    if _board[0] != key:
        cards = _read_board(OBSIDIAN_BOARD, st)
        if cards is None:  # a failed read is not kept: the same file may be readable a moment later
            return []
        _board = (key, cards)  # "not a board" is kept too, so it is not read every time
    return [{**c, "tags": list(c["tags"])} for c in _board[1]]  # copies: the kept cards are shared between requests


def start() -> None:
    """Scan in the background from now on, so a request never waits for a vault walk."""
    global _threaded
    if OBSIDIAN_VAULTS:
        _threaded = True
        threading.Thread(target=refresh_forever, daemon=True).start()
