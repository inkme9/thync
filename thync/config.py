"""Paths, user settings and tuning constants.

Everything personal (company folders, Jira URL, tokens, collected data) lives outside the
repository in THYNC_HOME (default ~/.thync). See config.example.json.
"""
import json
import os
import shutil
import sys
from pathlib import Path

HOME = Path.home()
STATE_DIR = Path(os.environ.get("THYNC_HOME", HOME / ".thync")).expanduser()
CONFIG_FILE = STATE_DIR / "config.json"
CLAUDE_LOGS = HOME / ".claude" / "projects"
CODEX_LOGS = HOME / ".codex" / "sessions"
WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def _load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


CONFIG = _load_config()
# Sessions started under these folders belong to the work profile; the rest are personal.
# The same folders are searched for local clones when work is handed off to Claude.
COMPANY_ROOTS = [str(Path(p).expanduser()) for p in CONFIG.get("company_roots", [])]
JIRA_BROWSE = CONFIG.get("jira_browse_url", "")  # e.g. https://example.atlassian.net/browse/
JIRA_EMAIL = CONFIG.get("jira_email", "")  # with an API token, turns on fetching ticket summaries
CARDS_DIR = Path(CONFIG.get("cards_dir", STATE_DIR / "cards")).expanduser()
# Claude Code skills started when a PR is handed off: my PR -> "sent", someone else's PR -> "review".
REFLEX_SKILL = {"sent": "pr-review-watch", **CONFIG.get("reflex_skills", {})}
# Where work comes from: "claude_code" and "codex" (session logs), "github" (reviews, my PRs, my commits), "jira",
# "notion", "obsidian" (notes edited in the vaults of `obsidian_vaults`).
SOURCES = set(CONFIG.get("sources", ["claude_code", "codex", "github", "jira", "notion", "obsidian"]))
# Other machines whose sessions this thync shows too. Each runs its own thync and is reached over SSH:
# [{"name": "devbox1", "ssh": "devbox1"}] (docs/configuration.md, "여러 서버를 한 화면에서").
REMOTES = [{"name": str(r["name"]), "ssh": str(r["ssh"]), "port": int(r.get("port", 8765)),
            "thync_home": str(r.get("thync_home", "~/.thync"))}
           for r in CONFIG.get("remotes", []) if isinstance(r, dict) and r.get("name") and r.get("ssh")]
# The remote that runs PRs handed off to Claude (its clones, its tmux); "" runs them on this machine.
REFLEX_HOST = str(CONFIG.get("reflex_host", ""))
if REFLEX_HOST not in {r["name"] for r in REMOTES}:
    REFLEX_HOST = ""
SESSIONS = bool({"claude_code", "codex"} & SOURCES) or bool(REMOTES)
NOTION_USER = CONFIG.get("notion_user_id", "")  # only pages last edited by this Notion user; empty = every page
# Handing PRs off to Claude needs the claude CLI and the session logs it writes.
HANDOFF = bool(REFLEX_HOST) or ("claude_code" in SOURCES and shutil.which("claude") is not None)
# How long the AskUserQuestion hook holds a question for an answer from thync before the session asks itself.
ASK_WAIT_SEC = int(CONFIG.get("ask_wait_sec", 300))
# Tailscale login names that may use the page without the access token when it comes through `tailscale serve`,
# which sets Tailscale-User-Login and removes one a client sent. Who else can forge it: docs/security.md.
_users = CONFIG.get("tailscale_users", [])
if not isinstance(_users, list):
    print("thync: tailscale_users must be a list of login names; ignored", file=sys.stderr, flush=True)
    _users = []
TAILSCALE_USERS = {str(u).strip().casefold() for u in _users if str(u).strip()}
HOST = "127.0.0.1"
PORT = int(CONFIG.get("port", 8765))

PROFILE_WORK, PROFILE_PERSONAL = "회사", "개인"


def parse_vaults(raw) -> list[dict]:
    """`obsidian_vaults` -> [{name, path, profile}]. `name` tells vaults apart inside thync and defaults to the
    folder name; an entry whose name is already taken is skipped, since its notes would share node ids."""
    vaults, names = [], set()
    for v in raw if isinstance(raw, list) else []:
        if not isinstance(v, dict) or not v.get("path"):
            continue
        try:
            path = str(Path(str(v["path"])).expanduser())
        except RuntimeError:  # ~someone with no such user
            print(f"thync: Obsidian vault {v['path']} skipped: no such user", file=sys.stderr, flush=True)
            continue
        if not os.path.isabs(path):  # it would depend on where thync was started, and so would the note links
            print(f"thync: Obsidian vault {path} skipped: write an absolute path or one that starts with ~",
                  file=sys.stderr, flush=True)
            continue
        name = str(v.get("name") or Path(path).name)
        if not name or ":" in name:  # the name is a field of the node id obsidian:<name>:<path>
            print(f"thync: Obsidian vault {path} skipped: set a \"name\" that is not empty and has no ':'",
                  file=sys.stderr, flush=True)
            continue
        if name in names:
            print(f"thync: Obsidian vault {path} skipped: the name '{name}' is taken; set a different \"name\"",
                  file=sys.stderr, flush=True)
            continue
        names.add(name)
        vaults.append({"name": name, "path": path, "profile": PROFILE_WORK if v.get("profile") == PROFILE_WORK else PROFILE_PERSONAL})
    return vaults


def parse_board(raw) -> dict | None:
    """`obsidian_board` -> {path, profile}: the one Obsidian Kanban board whose cards the kanban shows, read-only.
    A path, or {"path": ..., "profile": "회사"}."""
    v = {"path": raw} if isinstance(raw, str) else raw
    if not isinstance(v, dict) or not isinstance(v.get("path"), str) or not v["path"]:
        if raw:
            print("thync: obsidian_board ignored: give a path, or {\"path\": ..., \"profile\": ...}", file=sys.stderr, flush=True)
        return None
    try:
        path = str(Path(v["path"]).expanduser())
    except RuntimeError:  # ~someone with no such user
        path = v["path"]
    if not os.path.isabs(path):
        print(f"thync: obsidian_board {v['path']} ignored: write an absolute path or one under ~", file=sys.stderr, flush=True)
        return None
    try:
        if "\0" in path:
            raise ValueError
        path.encode("utf-8")
    except (ValueError, UnicodeEncodeError):  # lstat would raise on every request, or the card could not go out as JSON
        print("thync: obsidian_board ignored: the path has a NUL or a character that is not UTF-8", file=sys.stderr, flush=True)
        return None
    return {"path": path, "profile": PROFILE_WORK if v.get("profile") == PROFILE_WORK else PROFILE_PERSONAL}


# Obsidian vaults whose recently edited notes join the graph: [{"path": "~/vault", "profile": "회사", "name": "work"}].
OBSIDIAN_VAULTS = parse_vaults(CONFIG.get("obsidian_vaults", []))
OBSIDIAN_BOARD = parse_board(CONFIG.get("obsidian_board"))

# --- Time windows (seconds) ---
# Commits, ticket changes and page edits come hours apart, not minutes like prompts, so without session
# logs the focus window, its cooling and the feed widen (docs/brain-model.md).
FOCUS_SEC = 60 * 60 if SESSIONS else 8 * 3600  # activity within this window counts toward focus
BACKGROUND_SEC = 10 * 60         # Claude working but no prompt from me for this long -> background
RUNNING_STALE_SEC = 5 * 60       # Claude's turn but the log has been quiet this long -> treat as stopped
LIVE_FILE_SEC = 10 * 60          # files edited this recently glow
FILES_SEC = 60 * 60              # files edited this recently become nodes
LIVE_SESSION_SEC = 24 * 3600     # sessions talked to this recently are "in progress"
ACTIVITY_SEC = 30 * 60 if SESSIONS else 24 * 3600  # live activity feed window
REVIEW_TTL_SEC = 120             # how long GitHub results are reused
DEFAULT_DAYS, MAX_DAYS = 7, 30   # period selector

# --- Brain model (sources in docs/brain-model.md). Only WORKSPACE_SIZE comes from a paper. ---
WORKSPACE_SIZE = 4                                     # Cowan 2001: about 4 items (3-5)
TAU_FAST, TAU_SLOW, FAST_SHARE = 3600, 5 * 86400, 0.7  # two-exponential forgetting (Murre & Dros 2015)
SPREAD, SPREAD_HOPS = 0.5, 2                           # spreading activation (Collins & Loftus 1975)
TAU_FOCUS = 10 * 60 if SESSIONS else 2 * 3600         # how fast focus cools after the last activity

# --- Graph size limits ---
PRS_PER_SESSION = 3
FILES_PER_SESSION = 8

# Jira statuses (lowercased) that never go on the brain, even when linked to live work.
JIRA_TODO = {"backlog", "해야 할 일", "to do"}
JIRA_DONE = {"완료", "done", "종료", "closed", "resolved"}
