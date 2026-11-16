"""GitHub through the `gh` CLI: review requests to me, my open PRs waiting on review, and my recent commits.

With `mien_profiles` set, the GitHub account of every one of those mien profiles that has one is read through
`mien exec`, in parallel, and the lists are merged. Without it, gh's active account is the only one.
The calls take seconds, so refresh_forever() fetches in a background thread and requests only read what it left.
"""
import functools
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from ..config import MIEN_PROFILES, HOME, MAX_DAYS, REVIEW_TTL_SEC, SESSIONS, STATE_DIR

ACCOUNTS = MIEN_PROFILES or [""]  # "" = gh's active account
MIEN = shutil.which("mien") or "mien"
# gh settings folder without any login, for profile calls: gh then has only the token mien hands it.
NO_LOGIN = STATE_DIR / "gh-no-login"
MAX_BRANCHES = 20  # pushed branches whose head commit message is fetched
_seen_by: dict[str, str] = {}  # PR url -> account that listed it, so its reviews are read and posted as that account
_lists: dict[str, dict[str, list]] = {}  # account -> "reviews" | "sent" | "commits" -> what GitHub last returned
_logins: dict[str, str] = {}  # account -> my login there, lowercased
refreshed = 0.0  # when refresh() last finished
FAILED = (subprocess.SubprocessError, OSError, json.JSONDecodeError, KeyError, TypeError)

SENT_QUERY = """query { search(query:"is:open is:pr author:@me archived:false", type:ISSUE, first:50) { nodes { ... on PullRequest {
  title url number updatedAt isDraft repository { name } reviewDecision author { login }
  reviewRequests(first:5) { nodes { requestedReviewer { ... on User { login } ... on Team { name } } } }
  latestReviews(first:10) { nodes { author { login } state submittedAt body } } } } } }"""


def gh(account: str, *args: str, stdin: str | None = None, timeout: int = 60) -> str:
    """Run gh as one account and return its output. A profile's call goes through `mien exec`, which hands gh the
    token in its environment; that adds about two seconds, and thync never holds the token."""
    cmd, env = ["gh", *args], None
    if account:
        cmd = [MIEN, "exec", account, "--", *cmd]
        # A profile without GitHub must fail, not borrow another account: drop the token this process inherited,
        # and point gh away from its own logins.
        NO_LOGIN.mkdir(parents=True, exist_ok=True)
        env = {**{k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN")}, "GH_CONFIG_DIR": str(NO_LOGIN)}
    # From home: mien refuses a profile in a folder another profile claims, such as this repository.
    return subprocess.run(cmd, input=stdin, cwd=HOME, env=env, capture_output=True, text=True, encoding="utf-8",
                          timeout=timeout, check=True).stdout


def account_for(url: str) -> str:
    return _seen_by.get(url, ACCOUNTS[0])


def _merged(kind: str, key: str) -> list:
    """One list across accounts; a PR two accounts see is kept once, as the first account's."""
    out = {}
    for a in ACCOUNTS:
        for it in _lists.get(a, {}).get(kind, []):
            if it[key] not in out:
                out[it[key]] = it
                _seen_by.setdefault(it[key], a)
    return list(out.values())


def review_requests() -> list[dict]:
    """Open PRs where my review is requested (search API items)."""
    return _merged("reviews", "html_url")


def sent_reviews() -> list[dict]:
    """My open, non-draft PRs that have a pending review request or any review."""
    return _merged("sent", "url")


def my_commits() -> list[dict]:
    """My commits and pushes of the last MAX_DAYS on every account: {slug, branch, at, message, url, personal}."""
    commits, mine = [c for a in ACCOUNTS for c in _lists.get(a, {}).get("commits", [])], set(_logins.values())
    for c in commits:
        c["personal"] = c["slug"].split("/")[0].lower() in mine  # my own repos are personal work
    return commits


@functools.lru_cache(maxsize=8)
def login(account: str) -> str:
    return gh(account, "api", "user", "--jq", ".login").strip()


def has_github(profile: str) -> bool:
    """Whether the mien profile carries a GitHub account. `mien whoami` reads only mien's settings, never a secret.
    When mien cannot tell, the gh call is tried and decides."""
    try:
        out = subprocess.run([MIEN, "whoami", profile, "--json"], cwd=HOME, capture_output=True, text=True, timeout=30,
                             check=True).stdout
        return bool(json.loads(out).get("github"))
    except FAILED:
        return True


@functools.lru_cache(maxsize=256)
def commit_message(account: str, slug: str, sha: str) -> str:
    """Message of one commit. Commits never change, so it is fetched once."""
    return gh(account, "api", f"repos/{slug}/commits/{sha}", "--jq", ".commit.message")


def parse_search(items: list) -> list[dict]:
    """search/commits items -> work items. The search only covers default branches, so branch is ''."""
    return [{"slug": it["repository"]["full_name"], "branch": "", "at": it["commit"]["author"]["date"],
             "message": it["commit"]["message"], "url": it["html_url"]} for it in items]


def parse_pushes(events: list) -> list[dict]:
    """PushEvents -> work items, newest push per branch. Events no longer list commits, only the head sha."""
    out: dict[tuple, dict] = {}
    for e in events:
        ref = (e.get("payload") or {}).get("ref") or ""
        if e.get("type") != "PushEvent" or not ref.startswith("refs/heads/"):
            continue
        slug, branch = e["repo"]["name"], ref.removeprefix("refs/heads/")
        if (slug, branch) not in out:  # events come newest first
            out[slug, branch] = {"slug": slug, "branch": branch, "at": e["created_at"], "message": "",
                                 "head": e["payload"].get("head", ""),
                                 "url": f"https://github.com/{slug}/tree/{branch}"}
    return list(out.values())


def _account_commits(account: str, now: float) -> list[dict]:
    since = time.strftime("%Y-%m-%d", time.gmtime(now - MAX_DAYS * 86400))
    items = json.loads(gh(account, "api", "-X", "GET", "search/commits", "-f", f"q=author:@me author-date:>={since}",
                          "-f", "sort=author-date", "-f", "per_page=100")).get("items", [])
    pushes = parse_pushes(json.loads(gh(account, "api", f"users/{login(account)}/events?per_page=100")))
    for p in pushes[:MAX_BRANCHES]:
        try:
            p["message"] = commit_message(account, p["slug"], p["head"]) if p["head"] else ""
        except subprocess.SubprocessError:
            pass  # a deleted branch or repository; the branch name still says enough
    return parse_search(items) + pushes


REVIEWS_QUERY = """query($owner: String!, $name: String!, $number: Int!) { repository(owner: $owner, name: $name) {
  pullRequest(number: $number) {
    reviews(last: 30) { nodes { author { login } state body submittedAt url } }
    reviewThreads(first: 100) { nodes { isResolved isOutdated path line
      comments(first: 20) { nodes { author { login } body createdAt url } } } } } } }"""
_pr_reviews: dict[str, tuple[float, dict]] = {}


def plain(body: str | None) -> str:
    """Comment body without HTML comments, which bots use for hidden markers."""
    return re.sub(r"<!--.*?-->", "", body or "", flags=re.S).strip()


def parse_reviews(pr: dict, me: set[str]) -> dict:
    """Reviews others left on a PR, and its comment threads with the unresolved ones first."""
    reviews = [{"author": (r.get("author") or {}).get("login", ""), "state": r["state"], "body": plain(r.get("body")),
                "at": r.get("submittedAt") or "", "url": r.get("url", "")}
               for r in pr["reviews"]["nodes"]
               if r["state"] != "PENDING" and (r.get("author") or {}).get("login", "").lower() not in me
               and (plain(r.get("body")) or r["state"] in ("APPROVED", "CHANGES_REQUESTED"))]
    threads = [{"path": t.get("path") or "", "line": t.get("line"), "resolved": t["isResolved"], "outdated": t["isOutdated"],
                "comments": [{"author": (c.get("author") or {}).get("login", ""), "body": plain(c.get("body")),
                              "at": c.get("createdAt") or "", "url": c.get("url", "")} for c in t["comments"]["nodes"]]}
               for t in pr["reviewThreads"]["nodes"]]
    threads.sort(key=lambda t: t["resolved"])
    return {"reviews": reviews[::-1], "threads": threads}


def pr_reviews(url: str, now: float) -> dict:
    """Reviews and threads of one PR, fetched when its details open and reused for a minute."""
    seen, data = _pr_reviews.get(url, (0.0, None))
    if data is None or now - seen > 60:
        owner, name, _, number = url.split("/")[3:7]
        out = gh(account_for(url), "api", "graphql", "-f", f"query={REVIEWS_QUERY}", "-f", f"owner={owner}",
                 "-f", f"name={name}", "-F", f"number={number}")
        data = parse_reviews(json.loads(out)["data"]["repository"]["pullRequest"], set(_logins.values()))
        _pr_reviews[url] = (now, data)
    return data


def review_state(pr: dict) -> str:
    if pr["reviewDecision"] == "APPROVED":
        return "승인됨"
    if pr["reviewDecision"] == "CHANGES_REQUESTED":
        return "수정 요청"
    if pr["latestReviews"]["nodes"]:
        return "코멘트 있음"
    return "리뷰 대기"


_canonical: dict[str, str] = {}


def canonical_slug(slug: str) -> str:
    """Current 'owner/name' of a repository, so a renamed repo still matches its old remote URL. Only answers
    GitHub gave are remembered; a failed lookup falls back to the slug and is tried again next time.
    Each account is asked in turn, since a private repository is visible to only one of them."""
    if slug not in _canonical:
        for a in ACCOUNTS:
            try:
                name = gh(a, "api", f"repos/{slug}", "--jq", ".full_name").strip()
            except FAILED:
                continue
            if name:
                _canonical[slug] = name.lower()
                break
        else:
            return slug.lower()
    return _canonical[slug]


FETCH = {
    "reviews": lambda a, now: json.loads(gh(a, "api", "-X", "GET", "search/issues", "-f",
                                            "q=is:open is:pr review-requested:@me archived:false", "-f", "per_page=30"))["items"],
    "sent": lambda a, now: [p for p in json.loads(gh(a, "api", "graphql", "-f", f"query={SENT_QUERY}"))["data"]["search"]["nodes"]
                            if p and not p["isDraft"] and (p["reviewRequests"]["nodes"] or p["latestReviews"]["nodes"])],
    # Commits stand in for session logs, so with session logs they are not fetched (graph.build_graph).
    **({} if SESSIONS else {"commits": _account_commits}),
}


def _refresh_account(account: str, now: float) -> None:
    """Each list is shown as soon as it arrives; one that fails keeps what it had."""
    if account and not has_github(account):
        _lists.pop(account, None)
        return
    for kind, fetch in FETCH.items():
        try:
            got = fetch(account, now)
        except FAILED:
            continue
        _lists[account] = {**_lists.get(account, {}), kind: got}
    try:
        _logins[account] = login(account).lower()
    except FAILED:
        pass


def refresh(now: float) -> None:
    """Fetch every account at once. Only this does; pages read what it left, so they never wait on GitHub."""
    global refreshed
    with ThreadPoolExecutor(len(ACCOUNTS)) as pool:
        list(pool.map(lambda a: _refresh_account(a, now), ACCOUNTS))
    refreshed = time.time()


def refresh_forever() -> None:
    if MIEN_PROFILES and not shutil.which("mien"):
        print("thync: mien_profiles needs mien on PATH; GitHub lists stay empty", file=sys.stderr, flush=True)
    while True:
        refresh(time.time())
        time.sleep(REVIEW_TTL_SEC)
