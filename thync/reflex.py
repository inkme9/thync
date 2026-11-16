"""Hand a PR off to a Concord workflow in Claude Code (docs/design/review-handoff.md).

Someone else's PR -> /concord:review-until-approve, my PR -> /concord:pr-to-merge for that PR only. Both run in an
interactive Claude Code session in tmux, in a local clone of the PR's repository, as the mien profile that claims the
clone. plan_reflex() checks everything and starts nothing; start_reflex() checks again and starts the session.
"""
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
from pathlib import Path

from .config import COMPANY_ROOTS, HOME, STATE_DIR
from .sources.claude_code import known_sessions, repo_of
from .sources.github import canonical_slug
from .store import load_reflexes, save_reflex

WORKFLOW = {"review": "concord:review-until-approve", "sent": "concord:pr-to-merge"}
PLUGIN_CACHE = HOME / ".claude" / "plugins" / "cache"
EXIT_DIR = STATE_DIR / "handoff"
PR_URL = re.compile(r"https://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)")
# mien pipes a child's output instead of giving it the terminal when one of these says an agent harness runs it (mien's
# CAPTURE_MARKER_VARS); the session must stay interactive even when the tmux server was started from an agent's shell.
HARNESS_ENV = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CODEX_THREAD_ID", "COPILOT_AGENT", "MIEN_CAPTURED")
# The session edits, commits and pushes (my PR) or runs Concord's scripts (someone else's), so it keeps auto mode;
# it still may not read secrets.
PERMISSIONS = ["--permission-mode", "auto", "--disallowedTools", "Read(~/.thync/**)", "Read(~/.ssh/**)",
               "Read(~/.claude/settings.json)"]

_tmux_cache: dict[str, tuple[float, bool]] = {}
# One start at a time: the same-clone check reads reflex.json, and the record is saved only once tmux runs.
_start_lock = threading.Lock()


def tmux_alive(name: str) -> bool:
    """Whether a tmux session exists, remembered for 10 s so the 2 s refresh does not spawn tmux each time."""
    seen, alive = _tmux_cache.get(name, (0.0, False))
    if time.time() - seen > 10:
        try:
            alive = subprocess.run(["tmux", "has-session", "-t", f"={name}"], capture_output=True, timeout=5).returncode == 0
        except (subprocess.SubprocessError, OSError):  # no tmux on this machine
            alive = False
        _tmux_cache[name] = (time.time(), alive)
    return alive


def session_name(slug: str, number: str) -> str:
    """One tmux session per PR. The hash keeps foo/bar-baz#1 and foo-bar/baz#1 apart after the cleanup."""
    owner, repo = slug.split("/")
    digest = hashlib.sha1(slug.lower().encode()).hexdigest()[:6]
    return re.sub(r"[^A-Za-z0-9_-]", "-", f"thync-{owner}-{repo}-{number}-{digest}")


def exit_file(record: dict) -> Path:
    """Where the session's shell writes Claude Code's exit code. One file per start, so a failed restart leaves the
    previous run's code in place."""
    return EXIT_DIR / f"{record['tmux']}.{record['started']}.exit"


def run_state(record: dict) -> dict:
    """{"alive": bool, "exit": int | None} of a handed-off session on this machine; exit is None until it ends."""
    alive = tmux_alive(record["tmux"])
    code = None
    if not alive:
        try:
            code = int(exit_file(record).read_text().strip())
        except (OSError, ValueError, KeyError):
            pass
    return {"alive": alive, "exit": code}


def origin_matches(path: str, slug: str) -> bool:
    """Whether `path` is a git clone whose origin is the GitHub repository `owner/name` (renames included)."""
    if not Path(path).is_dir():
        return False
    out = subprocess.run(["git", "-C", path, "remote", "get-url", "origin"], capture_output=True, text=True, timeout=10)
    remote, github_remote = re.subn(r"^.*github\.com[:/]", "", out.stdout.strip())
    remote = remote.removesuffix(".git").lower()
    if out.returncode != 0 or not remote or not github_remote:  # only a GitHub remote can be looked up there
        return False
    return remote == slug.lower() or canonical_slug(remote) == canonical_slug(slug)


def find_clone(slug: str, repo: str) -> str | None:
    # Standalone clones under the configured roots first; session folders may be submodules.
    candidates = [str(p) for r in COMPANY_ROOTS for p in sorted(Path(r).glob("*/")) if (p / ".git").is_dir()]
    candidates += [s.cwd.split("/.claude/worktrees/")[0] for s in known_sessions() if s.cwd and repo_of(s.cwd) == repo]
    return next((c for c in dict.fromkeys(candidates) if origin_matches(c, slug)), None)


def skill_installed(skill: str) -> bool:
    """Whether some installed version of the Concord plugin has this skill; the version is never assumed."""
    return any(PLUGIN_CACHE.glob(f"*/concord/*/skills/{skill.split(':')[1]}/SKILL.md"))


def _run(argv: list[str], cwd: str) -> str:
    out = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=30)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[-200:] or f"exit {out.returncode}")
    return out.stdout.strip()


def plan_reflex(node: dict, listed: str | None = "") -> tuple[int, dict]:
    """Everything start_reflex needs, checked: (200, plan) or (error status, {"message": ...}). Starts nothing.
    `listed` is the mien profile whose GitHub account lists the PR (sources/github.account_for): "" when thync reads
    gh's active account only, None when no configured account lists it."""
    role = next((r for r in ("sent", "review") if r in node.get("roles", [])), None)
    m = PR_URL.fullmatch(str(node.get("detail", {}).get("url", "")))
    if not role or not m:
        return 400, {"message": "리뷰 요청이나 내 PR 만 맡길 수 있어요"}
    owner, repo, number = m.groups()
    slug, url, skill = f"{owner}/{repo}", m.group(0), WORKFLOW[role]
    name = session_name(slug, number)
    _tmux_cache.pop(name, None)  # a session that ended a moment ago must not answer "still running"
    if tmux_alive(name):  # before anything else, so a running session always answers with where to find it
        return 409, {"message": f"이미 돌고 있어요 (tmux attach -t {name})"}
    missing = [b for b in ("claude", "tmux", "gh") if not shutil.which(b)]
    if missing:
        return 503, {"message": f"이 머신에 {', '.join(missing)} 가 없어요"}
    if not skill_installed(skill):
        return 503, {"message": f"이 머신의 Claude Code 에 {skill} 스킬이 설치되어 있지 않아요"}
    clone = find_clone(slug, repo)
    if not clone:
        return 404, {"message": f"이 머신에서 {slug} 저장소 폴더를 찾지 못했어요"}
    if role == "sent":
        # pr-to-merge fixes my PR in the clone; two of them would share one working tree.
        busy = next((r["tmux"] for u, r in load_reflexes().items()
                     if u != url and os.path.realpath(r.get("cwd", "")) == os.path.realpath(clone)
                     and r.get("skill") == skill and "tmux" in r and tmux_alive(r["tmux"])), None)
        if busy:
            return 409, {"message": f"{clone} 에서 다른 PR 의 pr-to-merge 가 돌고 있어요 (tmux attach -t {busy})"}
    profile, prefix = "", []
    if shutil.which("mien"):
        # Without MIEN_PROFILE, which mien would report ahead of the clone's own claim.
        env = {k: v for k, v in os.environ.items() if k != "MIEN_PROFILE"}
        out = subprocess.run(["mien", "which"], cwd=clone, env=env, capture_output=True, text=True, timeout=30)
        profile = out.stdout.strip().splitlines()[-1] if out.returncode == 0 and out.stdout.strip() else ""
        if not profile:
            return 409, {"message": f"{clone} 를 맡은 mien 프로필이 없어요 (mien claim <프로필>)"}
        prefix = ["mien", "exec", profile, "--"]
    if listed is None:
        return 409, {"message": f"{slug}#{number} 를 목록에 올린 계정이 없어 맡길 수 없어요"}
    if listed and listed != profile:
        # The review was requested of, or the PR written by, the listing profile's account; run as that one only.
        return 409, {"message": f"{slug}#{number} 를 목록에 올린 프로필({listed})이 {clone} 를 맡은 프로필"
                                f"({profile or '없음'})과 달라요"}
    try:
        login = _run([*prefix, "gh", "api", "user", "--jq", ".login"], clone)
        pr = json.loads(_run([*prefix, "gh", "api", f"repos/{slug}/pulls/{number}", "--jq",
                              "{author: .user.login, state: .state, draft: .draft}"], clone))
    except (RuntimeError, subprocess.SubprocessError, OSError, ValueError) as e:
        return 502, {"message": f"GitHub 계정이나 PR 을 확인하지 못했어요 ({e})"}
    if pr.get("state") != "open":
        return 409, {"message": f"{slug}#{number} 는 열려 있지 않아요"}
    mine = pr.get("author", "").lower() == login.lower()
    if role == "sent" and not mine:
        return 409, {"message": f"{slug}#{number} 의 작성자({pr.get('author')})가 실행 계정({login})이 아니에요"}
    if role == "sent" and pr.get("draft"):
        return 409, {"message": f"{slug}#{number} 는 초안이에요. pr-to-merge 는 초안을 건너뛰니 준비 완료로 바꾼 뒤 맡겨 주세요"}
    if role == "review" and mine:
        return 409, {"message": f"{slug}#{number} 는 실행 계정({login})이 쓴 PR 이라 리뷰를 맡길 수 없어요"}
    return 200, {"url": url, "slug": slug, "number": int(number), "workflow": skill, "profile": profile,
                 "login": login, "author": pr.get("author", ""), "cwd": clone, "tmux": name}


def prompt_of(plan: dict) -> str:
    """The first prompt: `/<skill> <PR url>`, then the scope, account and authorization the user confirmed."""
    target, url = f"{plan['slug']}#{plan['number']}", plan["url"]
    account = f"the mien profile `{plan['profile']}` (GitHub account `{plan['login']}`)" if plan["profile"] \
        else f"the GitHub account `{plan['login']}`"
    if plan["workflow"] == WORKFLOW["review"]:
        return f"/{plan['workflow']} {url} Review only {target}, as {account}."
    return (f"/{plan['workflow']} {url} Carry only {target}: pass `{target}` as the only target, never the whole "
            f"repository. Run as {account}; `--self` and `--mine` are `{plan['login']}`. Pass "
            f"`--checkout {plan['slug']}={plan['cwd']}`. Merging is authorized for {target} only: the user confirmed "
            f"it in thync, which started this session.")


def start_reflex(node: dict, shown: dict | None = None, listed: str | None = "") -> tuple[int, str]:
    """Check again, and start the session for a PR node unless the check differs from what the user confirmed."""
    with _start_lock:
        return _start(node, shown, listed)


def _start(node: dict, shown: dict | None, listed: str | None) -> tuple[int, str]:
    status, plan = plan_reflex(node, listed)
    if status != 200:
        return status, plan["message"]
    if shown is not None and any(shown.get(k) != plan[k] for k in ("url", "workflow", "profile", "login", "cwd")):
        return 409, "확인 창을 연 뒤 실행 계정, 작업이나 폴더가 바뀌었어요. 다시 맡겨 주세요"
    name = plan["tmux"]
    record = {"tmux": name, "skill": plan["workflow"], "cwd": plan["cwd"], "profile": plan["profile"],
              "login": plan["login"], "started": int(time.time())}
    # Absolute paths: the tmux server may have been started with another PATH. mien exec drops the first `--`,
    # so its own `--` keeps the one before the prompt for claude.
    prefix = [shutil.which("mien") or "mien", "exec", plan["profile"], "--"] if plan["profile"] else []
    unset = [a for v in HARNESS_ENV for a in ("-u", v)]
    argv = ["env", *unset, *prefix, shutil.which("claude") or "claude", *PERMISSIONS, "--", prompt_of(plan)]
    # sh, not tmux's default-shell: `$?` is the exit status only in a POSIX shell.
    script = f"{shlex.join(argv)}; echo $? > {shlex.quote(str(exit_file(record)))}"
    EXIT_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    # new-session itself refuses a duplicate name, so there is no check-then-create race.
    try:
        out = subprocess.run(["tmux", "new-session", "-d", "-s", name, "-c", plan["cwd"], "sh", "-c", script],
                             capture_output=True, text=True, timeout=10)
    except (subprocess.SubprocessError, OSError) as e:
        return 500, f"tmux 를 실행하지 못했어요 ({e.__class__.__name__})"
    if out.returncode != 0:
        if "duplicate session" in out.stderr:
            return 409, f"이미 돌고 있어요 (tmux attach -t {name})"
        return 500, f"tmux 를 실행하지 못했어요 ({out.stderr.strip()[:200]})"
    _tmux_cache.pop(name, None)
    save_reflex(plan["url"], record)
    return 202, f"{plan['workflow']} 를 시작했어요 (tmux attach -t {name})"
