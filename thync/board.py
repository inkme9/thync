"""Kanban cards. One card per work unit; review requests and my PRs under review get one card per PR.

Columns: waiting (a session waits on my answer), todo, doing (including work Claude runs on its own), reflex (work
handed off to Claude), done. A column set by hand in the
card's Markdown file wins over the automatic one, which stays in `auto_column` so it can be restored.
"""
from .config import HANDOFF, JIRA_DONE, JIRA_TODO, PROFILE_PERSONAL, PROFILE_WORK
from .reflex import run_state
from .sources import remote
from .store import BOARD_COLUMNS, load_reflexes



def _auto_column(members: list[dict]) -> str:
    if any(n["detail"].get("waiting") for n in members):
        return "waiting"
    sources = [n for n in members if "last_at" in n["detail"]]
    statuses = {n["detail"].get("jira_status", "").lower() for n in members if n["kind"] == "project"} - {""}
    if any("focus" in n["roles"] or "running" in n["roles"] or n["detail"]["live"] for n in sources):
        return "doing"
    if any("review" in n["roles"] or "sent" in n["roles"] for n in members):
        return "todo" if any("review" in n["roles"] for n in members) else "doing"
    if statuses - JIRA_TODO - JIRA_DONE:
        return "doing"  # a ticket is still in progress or in review
    if statuses and statuses <= JIRA_TODO:
        return "todo"
    return "done"


def run_label(run: dict, session: dict | None) -> str:
    """What a handed-off session is doing, as far as tmux, its exit code and its session log tell."""
    if run.get("alive"):
        if session is None:
            return "시작함"
        if session.get("waiting"):
            return "답을 기다림"
        if session.get("claude_working"):
            return "작업 중"
        return "대기 중 · 백그라운드 감시 중이거나 입력을 기다림"
    code = run.get("exit")
    return "끝남 (종료 코드 모름)" if code is None else "끝남" if code == 0 else f"실패 (종료 코드 {code})"


def build_board(units: dict, nodes: dict, names: dict, card_files: dict) -> list[dict]:
    by_unit: dict[str, list[dict]] = {}
    for n in nodes.values():
        by_unit.setdefault(n["detail"].get("unit", "common"), []).append(n)

    cards = []
    for uid, u in units.items():
        members = by_unit.get(uid, [])
        if uid in ("review", "sent"):
            for n in members:
                if n["kind"] == "repo":
                    continue
                review = "review" in n["roles"]
                cards.append({"id": n["id"], "title": names.get(n["id"], n["detail"].get("text", n["name"])),
                              "column": "todo" if review else "doing", "profile": n["profile"],
                              "updated": n["detail"].get("updated", ""), "focus": n["id"],
                              "tags": [n["name"], "리뷰 요청" if review else n["detail"].get("review_state", "")]})
            continue
        sessions = [n for n in members if n["kind"] == "workThread"]
        tickets = [n for n in members if n["kind"] == "project"]
        lead = max((n for n in members if "last_at" in n["detail"]), key=lambda n: n["detail"]["last_at"], default=None)
        tags = [t["detail"].get("key", "") for t in tickets][:3]
        tags += [f"세션 {len(sessions)}"] if sessions else []
        reviews = sum("review" in n["roles"] for n in members)
        tags += [f"리뷰 요청 {reviews}"] if reviews else []
        cards.append({"id": uid, "title": u["name"], "sub": u["title"] if u["title"] != u["name"] else "",
                      "column": _auto_column(members), "profile": u["profile"], "updated": u["updated"],
                      "focus": lead["id"] if lead else (members[0]["id"] if members else ""),
                      "tags": [t for t in tags if t]})

    for c in cards:
        c["auto_column"] = c["column"]
        f = card_files.get(c["id"])
        meta = f["meta"] if f else {}
        if c["column"] != "waiting":  # a pending question shows up even on a card I placed by hand
            c["column"] = meta.get("column") if meta.get("column") in BOARD_COLUMNS else c["column"]
        c["title"] = meta.get("title") or c["title"]
        c["note"] = f["note"] if f else ""
        c["moved"] = c["column"] != c["auto_column"]
        c["file"] = str(f["path"]) if f else ""

    # Work handed off to Claude: one card per PR.
    handed = {n["detail"]["reflex"]: n for n in nodes.values() if n["kind"] == "workThread" and n["detail"].get("reflex")}
    # Hand-offs a remote runs come with their run state; there `tmux attach` needs an ssh first.
    for url, r in {**(load_reflexes() if HANDOFF else {}), **remote.reflexes()}.items():
        if "tmux" not in r:
            continue  # a record of the one-shot reviewer thync no longer has
        pr = nodes.get(f"pr:{url}")
        session = handed.get(url)
        attach = f"tmux attach -t {r['tmux']}"
        run = r if "host" in r else run_state(r)
        tags = [r["skill"], run_label(run, session["detail"] if session else None),
                (f"ssh -t {r['ssh']} {attach}" if "host" in r else attach) if run.get("alive") else ""]
        cards.append({"id": f"reflex:{url}", "title": pr["detail"].get("text", pr["name"]) if pr else url,
                      "sub": pr["name"] if pr else "", "column": "reflex", "auto_column": "reflex", "moved": False,
                      "note": "", "profile": pr["profile"] if pr else PROFILE_WORK, "updated": r["started"],
                      "focus": (session or pr or {}).get("id", ""), "file": "", "tags": [t for t in tags if t]})

    # Card files with no automatic card are cards made by hand.
    auto_ids = {c["id"] for c in cards}
    for cid, f in card_files.items():
        if cid in auto_ids:
            continue
        meta = f["meta"]
        column = meta.get("column") if meta.get("column") in BOARD_COLUMNS else "todo"
        cards.append({"id": cid, "title": meta.get("title") or f["path"].stem, "sub": "", "column": column,
                      "auto_column": column, "moved": False, "note": f["note"],
                      "profile": meta.get("profile", PROFILE_PERSONAL), "updated": int(f["path"].stat().st_mtime),
                      "focus": "", "tags": ["직접 추가"], "file": str(f["path"])})
    return cards
