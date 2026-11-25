"""Handing a PR from the review lists to Concord: which workflow starts, for which PR, under which account.

Nothing here reaches GitHub, tmux or Claude: subprocess.run answers as a machine with one clone of o/r whose
`.mien` names the profile `cryptolab`, signed in to GitHub as `me`.
"""
import json
import os
import re
import shlex
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ["THYNC_HOME"] = tempfile.mkdtemp()

from thync import board, reflex  # noqa: E402

A = "https://github.com/o/r/pull/7"


def pr_node(role: str, url: str = A) -> dict:
    return {"id": f"pr:{url}", "name": "r #7", "roles": [role], "detail": {"url": url, "repo": "r", "text": "제목"}}


class FakeMachine:
    """subprocess.run for a machine with mien, gh, tmux and a clone of o/r."""

    def __init__(self, author: str, login: str | None = "me", profile: str | None = "cryptolab", tmux_rc: int = 0, state: str = "open",
                 draft: bool = False, alive: tuple = ()):
        self.author, self.login, self.profile, self.tmux_rc, self.state = author, login, profile, tmux_rc, state
        self.draft, self.alive = draft, alive
        self.calls: list[list[str]] = []
        self.envs: dict[str, dict | None] = {}

    def __call__(self, argv, **kw):
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        self.envs[" ".join(argv[:2])] = kw.get("env")
        rc, out, err = 0, "", ""
        if argv[:2] == ["tmux", "new-session"]:
            rc, err = self.tmux_rc, "duplicate session: x" if self.tmux_rc == 1 else "no server" if self.tmux_rc else ""
        elif argv[:2] == ["tmux", "has-session"]:
            rc = 0 if argv[-1].lstrip("=") in self.alive else 1
        elif argv[:2] == ["git", "-C"]:
            out = "git@github.com:o/r.git\n"
        elif Path(argv[0]).name == "mien" and argv[1] == "which":
            rc, out, err = (0, f"{self.profile}\n", "") if self.profile else \
                (1, "", "Error: no profile claims /x." if self.profile == "" else "Error: two profiles claim /x.")
        elif "gh" in argv and "user" in argv:
            rc, out, err = (0, f"{self.login}\n", "") if self.login is not None else (1, "", "HTTP 401")
        elif "gh" in argv and any(a.startswith("repos/o/r/pulls/") for a in argv):
            out = json.dumps({"author": self.author, "state": self.state, "draft": self.draft})
        return mock.Mock(returncode=rc, stdout=out, stderr=err)

    def gh(self, account: str, *args: str, **kw) -> str:
        """sources/github.gh on this machine: through `mien exec <profile> --` when a profile is given."""
        argv = [*(["mien", "exec", account, "--"] if account else []), "gh", *args]
        r = self(argv)
        if r.returncode:
            raise subprocess.CalledProcessError(r.returncode, argv, r.stdout, r.stderr)
        return r.stdout

    def launches(self) -> list[str]:
        """The shell script of every tmux session started (`tmux new-session ... sh -c <script>`)."""
        return [c[-1] for c in self.calls if c[:2] == ["tmux", "new-session"] and c[-3:-1] == ["sh", "-c"]]


ENV = ["env", "-u", "CLAUDECODE", "-u", "CLAUDE_CODE_ENTRYPOINT", "-u", "CODEX_THREAD_ID", "-u", "COPILOT_AGENT",
       "-u", "MIEN_CAPTURED"]


def argv_of(script: str) -> list[str]:
    """The command before the exit-code write, without the env prefix that keeps the session interactive."""
    argv = shlex.split(script.split("; echo $?")[0])
    assert argv[:len(ENV)] == ENV, argv[:6]
    return argv[len(ENV):]


class HandoffTest(unittest.TestCase):
    def setUp(self):
        self.clone = str(Path(tempfile.mkdtemp(), "o", "r"))  # a path that itself ends in OWNER/REPO
        Path(self.clone).mkdir(parents=True)
        self.cache = Path(tempfile.mkdtemp())
        for skill in ("review-until-approve", "pr-to-merge"):
            path = self.cache / "market" / "concord" / "1.0.0" / "skills" / skill / "SKILL.md"
            path.parent.mkdir(parents=True)
            path.write_text("---\nname: x\n---\n")
        self.saved: dict = {}
        self.exits = Path(tempfile.mkdtemp())

    def run_handoff(self, node: dict, machine: FakeMachine, cache: Path | None = None, call=None, which=None,
                    clone: str | None = "", gh=None):
        with mock.patch("subprocess.run", machine), mock.patch.object(reflex.github, "gh", gh or machine.gh), \
                mock.patch.object(reflex, "find_clone", return_value=self.clone if clone == "" else clone), \
                mock.patch.object(reflex, "save_reflex", lambda url, rec: self.saved.update({url: rec})), \
                mock.patch.object(reflex, "PLUGIN_CACHE", cache or self.cache, create=True), \
                mock.patch.object(reflex, "EXIT_DIR", self.exits, create=True), \
                mock.patch("shutil.which", which or (lambda name: f"/usr/bin/{name}")):
            reflex._tmux_cache.clear()
            reflex.github.login.cache_clear()
            return (call or reflex.start_reflex)(node)

    def test_review_request_starts_review_until_approve_for_that_pr_as_the_profile(self):
        machine = FakeMachine(author="alice")
        status, _ = self.run_handoff(pr_node("review"), machine)
        self.assertEqual(status, 202)
        [command] = machine.launches()
        self.assertIn(f"/concord:review-until-approve {A}", command)
        argv = argv_of(command)
        # mien exec drops the first `--`; the one left must still sit right before the prompt for claude.
        self.assertEqual(argv[:4], ["/usr/bin/mien", "exec", "cryptolab", "--"])
        self.assertEqual((argv[4], argv[-2]), ("/usr/bin/claude", "--"))
        self.assertNotIn("pr-to-merge", command)
        self.assertNotIn("/pull/8", command)
        prompt = argv_of(command)[-1]
        self.assertEqual(re.findall(r"trust|vouch|신뢰|믿", prompt, re.I), [])  # the user declares trust, not thync

    def test_own_pr_starts_pr_to_merge_for_that_pr_only(self):
        machine = FakeMachine(author="me")
        status, _ = self.run_handoff(pr_node("sent"), machine)
        self.assertEqual(status, 202)
        [command] = machine.launches()
        self.assertIn("/concord:pr-to-merge", command)
        self.assertIn("o/r#7", command)
        self.assertNotIn("review-until-approve", command)
        self.assertEqual(argv_of(command)[:4], ["/usr/bin/mien", "exec", "cryptolab", "--"])

    def test_review_request_on_my_own_pr_starts_nothing(self):
        machine = FakeMachine(author="me")
        status, _ = self.run_handoff(pr_node("review"), machine)
        self.assertGreaterEqual(status, 400)
        self.assertEqual((machine.launches(), self.saved), ([], {}))

    def test_without_the_concord_skill_nothing_starts_or_shows_running(self):
        machine = FakeMachine(author="me")
        status, _ = self.run_handoff(pr_node("sent"), machine, cache=Path(tempfile.mkdtemp()))
        self.assertGreaterEqual(status, 400)
        self.assertEqual((machine.launches(), self.saved), ([], {}))

    def test_own_pr_prompt_names_only_that_pr_as_target_and_merge_scope(self):
        machine = FakeMachine(author="me")
        self.run_handoff(pr_node("sent"), machine)
        [command] = machine.launches()
        prompt = argv_of(command)[-1]
        self.assertTrue(prompt.startswith(f"/concord:pr-to-merge {A} "))
        self.assertIn("Merging is authorized for o/r#7 only", prompt)
        # Never the whole repository; the clone path is a path, not a target.
        self.assertEqual(re.findall(r"\bo/r(?!#7\b|/pull/7\b|=)", prompt.replace(self.clone, "<clone>")), [])
        self.assertIn(f"--checkout o/r={self.clone}", prompt)
        self.assertEqual(self.saved[A]["skill"], "concord:pr-to-merge")
        self.assertEqual((self.saved[A]["profile"], self.saved[A]["login"]), ("cryptolab", "me"))

    def test_profile_comes_from_the_clone_not_the_servers_active_profile(self):
        machine = FakeMachine(author="alice")
        with mock.patch.dict(os.environ, {"MIEN_PROFILE": "someone-else"}):
            self.run_handoff(pr_node("review"), machine, call=reflex.plan_reflex)
        env = machine.envs["mien which"]
        self.assertIsNotNone(env)
        self.assertNotIn("MIEN_PROFILE", env)

    def test_runs_only_as_the_profile_that_lists_the_pr(self):
        # mien_profiles set: the listing profile must be the one that claims the clone; an unlisted PR is refused.
        for listed, status in (("cryptolab", 200), ("other", 409), (None, 409), ("", 200)):
            machine = FakeMachine(author="alice")
            got, reply = self.run_handoff(pr_node("review"), machine, call=lambda n: reflex.plan_reflex(n, listed))
            self.assertEqual(got, status, listed)
            if status == 409:
                self.assertEqual((machine.launches(), [c for c in machine.calls if "gh" in c]), ([], []))
        status, _ = self.run_handoff(pr_node("review"), FakeMachine(author="alice"),
                                     call=lambda n: reflex.start_reflex(n, None, "other"))
        self.assertEqual((status, self.saved), (409, {}))

    def test_a_mien_claim_that_cannot_be_read_is_not_taken_as_no_claim(self):
        machine = FakeMachine(author="alice", profile=None)  # e.g. two profiles claim the clone
        status, reply = self.run_handoff(pr_node("review"), machine, call=lambda n: reflex.plan_reflex(n, "cryptolab"))
        self.assertEqual(status, 409)
        self.assertIn("two profiles claim", reply["message"])
        self.assertEqual([c for c in machine.calls if "gh" in c], [])

    def test_my_pr_another_of_my_accounts_is_asked_to_review_is_a_review_request(self):
        node = {**pr_node("sent"), "roles": ["review", "sent"]}  # graph.py gives such a node both roles
        machine = FakeMachine(author="me-at-work", login="me")
        status, plan = self.run_handoff(node, machine, call=reflex.plan_reflex)
        self.assertEqual((status, plan["workflow"]), (200, "concord:review-until-approve"))

    def test_unclaimed_clone_runs_as_the_profile_that_lists_the_pr(self):
        machine = FakeMachine(author="alice", profile="")
        status, plan = self.run_handoff(pr_node("review"), machine, call=lambda n: reflex.plan_reflex(n, "cryptolab"))
        self.assertEqual((status, plan["profile"]), (200, "cryptolab"))
        gh_calls = [c for c in machine.calls if "gh" in c]
        self.assertTrue(gh_calls and all(c[:4] == ["mien", "exec", "cryptolab", "--"] for c in gh_calls))
        status, _ = self.run_handoff(pr_node("review"), FakeMachine(author="alice", profile=""),
                                     call=lambda n: reflex.plan_reflex(n, ""))
        self.assertEqual(status, 409)  # no claim and no listing profile: nothing to run as

    def test_check_shows_the_plan_and_starts_nothing(self):
        machine = FakeMachine(author="alice")
        status, plan = self.run_handoff(pr_node("review"), machine, call=reflex.plan_reflex)
        self.assertEqual(status, 200)
        self.assertEqual({k: plan[k] for k in ("url", "slug", "number", "workflow", "profile", "login", "author")},
                         {"url": A, "slug": "o/r", "number": 7, "workflow": "concord:review-until-approve",
                          "profile": "cryptolab", "login": "me", "author": "alice"})
        self.assertEqual((machine.launches(), self.saved), ([], {}))
        # The account is read as the profile, never as the server's own gh account.
        self.assertTrue(all(c[:3] == ["mien", "exec", "cryptolab"] for c in machine.calls if "gh" in c))

    def test_a_second_check_reuses_the_login(self):
        machine = FakeMachine(author="alice")
        both = self.run_handoff(pr_node("review"), machine, call=lambda n: [reflex.plan_reflex(n) for _ in range(2)])
        self.assertEqual([status for status, _ in both], [200, 200])
        self.assertEqual(sum("user" in c for c in machine.calls if "gh" in c), 1)  # each call is a mien call, ~2 s

    def test_start_refuses_when_the_check_differs_from_what_was_confirmed(self):
        same = {"url": A, "workflow": "concord:review-until-approve", "profile": "cryptolab", "login": "me", "cwd": self.clone}
        for key, value in (("login", "someone-else"), ("profile", "other"), ("cwd", "/elsewhere"),
                           ("workflow", "concord:pr-to-merge")):
            machine = FakeMachine(author="alice")
            status, _ = self.run_handoff(pr_node("review"), machine,
                                         call=lambda n: reflex.start_reflex(n, {**same, key: value}))
            self.assertEqual(status, 409, key)
            self.assertEqual((machine.launches(), self.saved), ([], {}))
        status, _ = self.run_handoff(pr_node("review"), FakeMachine(author="alice"),
                                     call=lambda n: reflex.start_reflex(n, same))
        self.assertEqual(status, 202)

    def test_second_handoff_of_a_running_pr_is_refused(self):
        machine = FakeMachine(author="me", tmux_rc=1)  # tmux refuses the session name it already has
        status, message = self.run_handoff(pr_node("sent"), machine)
        self.assertEqual((status, self.saved), (409, {}))
        self.assertIn(f"tmux attach -t {reflex.session_name('o/r', '7')}", message)

    def test_check_of_a_running_pr_answers_with_its_session_even_when_merged(self):
        name = reflex.session_name("o/r", "7")
        machine = FakeMachine(author="me", state="closed", alive=(name,))
        status, reply = self.run_handoff(pr_node("sent"), machine, call=reflex.plan_reflex)
        self.assertEqual((status, reply["message"]), (409, f"이미 돌고 있어요 (tmux attach -t {name})"))

    def test_second_pr_to_merge_in_the_same_clone_is_refused(self):
        other = reflex.session_name("o/r", "9")
        machine = FakeMachine(author="me", alive=(other,))
        records = {"https://github.com/o/r/pull/9": {"tmux": other, "skill": "concord:pr-to-merge", "cwd": self.clone,
                                                     "started": 1}}
        with mock.patch.object(reflex, "load_reflexes", return_value=records):
            status, message = self.run_handoff(pr_node("sent"), machine)
        self.assertEqual((status, machine.launches(), self.saved), (409, [], {}))
        self.assertIn(other, message)
        with mock.patch.object(reflex, "load_reflexes", return_value=records):  # a review there is fine
            self.assertEqual(self.run_handoff(pr_node("review"), FakeMachine(author="alice", alive=(other,)))[0], 202)

    def test_session_names_do_not_collide_after_cleanup(self):
        self.assertNotEqual(reflex.session_name("foo/bar-baz", "1"), reflex.session_name("foo-bar/baz", "1"))
        self.assertNotEqual(reflex.session_name("a/b.c", "1"), reflex.session_name("a/b-c", "1"))

    def test_failed_restart_keeps_the_previous_runs_outcome(self):
        old = {"tmux": reflex.session_name("o/r", "7"), "started": 100}
        self.exits.joinpath(f"{old['tmux']}.100.exit").write_text("0\n")
        machine = FakeMachine(author="me", tmux_rc=2)
        status, _ = self.run_handoff(pr_node("sent"), machine)
        self.assertEqual((status, self.saved), (500, {}))
        with mock.patch.object(reflex, "EXIT_DIR", self.exits), mock.patch.object(reflex, "tmux_alive", return_value=False):
            self.assertEqual(reflex.run_state(old), {"alive": False, "exit": 0})

    def test_my_pr_under_another_account_or_closed_or_unclaimed_starts_nothing(self):
        for machine in (FakeMachine(author="alice"), FakeMachine(author="me", profile=""),
                        FakeMachine(author="me", state="closed"), FakeMachine(author="me", draft=True)):
            status, _ = self.run_handoff(pr_node("sent"), machine)
            self.assertEqual(status, 409)
            self.assertEqual((machine.launches(), self.saved), ([], {}))

    def test_no_clone_or_unreadable_account_starts_nothing(self):
        machine = FakeMachine(author="me")
        status, _ = self.run_handoff(pr_node("sent"), machine, clone=None)
        self.assertEqual((status, machine.launches(), self.saved), (404, [], {}))
        machine = FakeMachine(author="me", login=None)  # gh refuses: signed out, or the token expired
        status, message = self.run_handoff(pr_node("sent"), machine)
        self.assertEqual((status, machine.launches(), self.saved), (502, [], {}))
        self.assertIn("HTTP 401", message)

    def test_timeouts_answer_with_a_reason_and_start_nothing(self):
        machine = FakeMachine(author="me")
        def slow_gh(account, *args, **kw):
            raise subprocess.TimeoutExpired(["gh", *args], 30, stderr=b"partial")
        status, reply = self.run_handoff(pr_node("sent"), machine, call=reflex.plan_reflex, gh=slow_gh)
        self.assertEqual((status, reply["message"]), (502, "GitHub 계정이나 PR 을 확인하지 못했어요 (TimeoutExpired)"))

        def slow_mien(argv, **kw):
            if argv[:2] == ["mien", "which"]:
                raise subprocess.TimeoutExpired(argv, 30)
            return machine(argv, **kw)
        slow_mien.gh, slow_mien.launches = machine.gh, machine.launches
        status, reply = self.run_handoff(pr_node("sent"), slow_mien, call=reflex.plan_reflex)
        self.assertEqual(status, 502)
        self.assertIn("TimeoutExpired", reply["message"])
        self.assertEqual((machine.launches(), self.saved), ([], {}))

    def test_missing_cli_starts_nothing(self):
        machine = FakeMachine(author="me")
        status, message = self.run_handoff(pr_node("sent"), machine,
                                           which=lambda name: None if name == "tmux" else f"/usr/bin/{name}")
        self.assertEqual((status, machine.launches(), self.saved), (503, [], {}))
        self.assertIn("tmux", message)

    def test_exit_code_of_the_session_reaches_the_card(self):
        machine = FakeMachine(author="me")
        self.run_handoff(pr_node("sent"), machine)
        [command] = machine.launches()
        # The real shell runs the command tmux would; /usr/bin/mien does not exist, so it fails with 127.
        subprocess.run(["sh", "-c", command.replace("/usr/bin/mien", "/nonexistent/mien")], capture_output=True)
        with mock.patch.object(reflex, "EXIT_DIR", self.exits), mock.patch.object(reflex, "tmux_alive", return_value=False):
            run = reflex.run_state(self.saved[A])
        self.assertEqual(run, {"alive": False, "exit": 127})
        self.assertEqual(board.run_label(run, None), "실패 (종료 코드 127)")
        with mock.patch.object(board, "load_reflexes", return_value=self.saved), mock.patch.object(board, "HANDOFF", True), \
                mock.patch.object(board, "run_state", return_value=run):
            [card] = board.build_board({}, {}, {}, {})
        self.assertFalse(card["running"])  # an ended run can be handed off again


class RemoteUrlTest(unittest.TestCase):
    def test_github_remotes_are_read_through_ssh_aliases(self):
        with mock.patch.object(reflex, "ssh_host", lambda alias: {"gh-cl": "github.com"}.get(alias, alias)):
            for url in ("git@github.com:O/R.git", "https://github.com/o/r", "https://github.com/o/r.git/",
                        "ssh://git@github.com:22/o/r.git", "git@gh-cl:O/R.git\n", "ssh://git@gh-cl/o/r"):
                self.assertEqual(reflex.github_slug(url), "o/r", url)
            for url in ("git@gitlab.com:o/r.git", "git@other-alias:o/r.git", "https://gh-cl/o/r", "/srv/git/r.git",
                        "git@github.com:o/r/extra.git"):
                self.assertEqual(reflex.github_slug(url), "", url)

    def test_ssh_alias_resolves_from_ssh_config(self):
        reflex._ssh_hosts.clear()
        out = mock.Mock(stdout="user git\nhostname github.com\nport 22\n")
        with mock.patch("subprocess.run", return_value=out) as run:
            self.assertEqual(reflex.ssh_host("gh-cl"), "github.com")
        self.assertEqual(run.call_args.args[0], ["ssh", "-G", "--", "gh-cl"])  # never read as an ssh option
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ssh", 10)):
            self.assertEqual(reflex.ssh_host("gh-slow"), "gh-slow")
        self.assertNotIn("gh-slow", reflex._ssh_hosts)  # a failed lookup is tried again next time
        reflex._ssh_hosts.clear()

    def test_github_ssh_over_port_443_counts_as_github(self):
        with mock.patch.object(reflex, "ssh_host", lambda alias: "ssh.github.com"):
            self.assertEqual(reflex.github_slug("git@gh-443:o/r.git"), "o/r")


class FindCloneTest(unittest.TestCase):
    def test_exact_origin_wins_without_asking_github_about_other_clones(self):
        root = Path(tempfile.mkdtemp())
        for name in ("a-other", "b-target"):
            (root / name / ".git").mkdir(parents=True)
        origins = {str(root / "a-other"): "x/other", str(root / "b-target"): "o/r"}
        with mock.patch.object(reflex, "COMPANY_ROOTS", [str(root)]), mock.patch.object(reflex, "known_sessions", list), \
                mock.patch.object(reflex, "origin_of", lambda path: origins[path]), \
                mock.patch.object(reflex, "canonical_slug") as canonical:
            self.assertEqual(reflex.find_clone("O/R", "r"), str(root / "b-target"))
        canonical.assert_not_called()  # a rename lookup is a GitHub call per clone


class FindRenamedCloneTest(unittest.TestCase):
    def test_a_clone_under_the_repositorys_old_name_is_found_asking_github_about_all_at_once(self):
        root = Path(tempfile.mkdtemp())
        names = ["a-other", "b-old-name", "c-third"]
        for name in names:
            (root / name / ".git").mkdir(parents=True)
        origins = {str(root / n): f"o/{n}" for n in names}
        current = {"o/b-old-name": "o/r", "o/r": "o/r"}
        running, most = 0, 0
        lock = threading.Lock()

        def canonical(slug):
            nonlocal running, most
            with lock:
                running += 1
                most = max(most, running)
            time.sleep(0.2)
            with lock:
                running -= 1
            return current.get(slug, slug)

        with mock.patch.object(reflex, "COMPANY_ROOTS", [str(root)]), mock.patch.object(reflex, "known_sessions", list), \
                mock.patch.object(reflex, "origin_of", lambda path: origins[path]), \
                mock.patch.object(reflex, "canonical_slug", canonical):
            self.assertEqual(reflex.find_clone("o/r", "r"), str(root / "b-old-name"))
        self.assertGreater(most, 1)  # the lookups overlapped instead of running one after another


class CanonicalNameTest(unittest.TestCase):
    """sources/github.canonical_slug as the clone search uses it."""

    def ask(self, answers: dict):
        """canonical_slug with gh answering per account: a name, or the stderr of a failure."""
        self.calls = []

        def gh(account, *args, **kw):
            self.calls.append(account)
            answer = answers[account]
            if answer.startswith("gh:"):
                raise subprocess.CalledProcessError(1, ["gh"], "", answer)
            return answer + "\n"
        return mock.patch.object(reflex.github, "gh", gh), mock.patch.object(reflex.github, "readers", lambda: list(answers))

    def setUp(self):
        reflex.github._canonical.clear()
        reflex.github._not_found.clear()

    def test_a_repository_every_account_cannot_find_is_not_asked_again_for_an_hour(self):
        a, b = self.ask({"work": "gh: Not Found (HTTP 404)", "home": "gh: Not Found (HTTP 404)"})
        with a, b:
            self.assertEqual(reflex.github.canonical_slug("O/Gone"), "o/gone")
            self.assertEqual(reflex.github.canonical_slug("O/Gone"), "o/gone")  # same case either way
        self.assertEqual(self.calls, ["work", "home"])  # both accounts once, then nothing for an hour

    def test_an_account_that_starts_reading_later_is_asked_despite_the_404s(self):
        a, b = self.ask({"work": "gh: Not Found (HTTP 404)"})
        with a, b:
            self.assertEqual(reflex.github.canonical_slug("O/Old"), "o/old")
        a, b = self.ask({"work": "gh: Not Found (HTTP 404)", "home": "O/New"})
        with a, b:
            self.assertEqual(reflex.github.canonical_slug("O/Old"), "o/new")  # home had not been asked

    def test_a_failure_that_is_not_a_404_is_asked_again_next_time(self):
        a, b = self.ask({"work": "gh: HTTP 504", "home": "gh: Not Found (HTTP 404)"})
        with a, b:
            self.assertEqual(reflex.github.canonical_slug("O/Old"), "o/old")
        self.assertNotIn("O/Old", reflex.github._not_found)
        a, b = self.ask({"work": "O/New", "home": "gh: Not Found (HTTP 404)"})
        with a, b:
            self.assertEqual(reflex.github.canonical_slug("O/Old"), "o/new")


class LearnCloneNamesTest(unittest.TestCase):
    def test_every_clone_under_the_roots_has_its_current_name_asked_once(self):
        root = Path(tempfile.mkdtemp())
        for name in ("a", "b", "plain"):
            (root / name / ".git" if name != "plain" else root / name).mkdir(parents=True)
        origins = {str(root / "a"): "o/a", str(root / "b"): ""}  # b is not a GitHub clone, plain is not a clone
        with mock.patch.object(reflex, "COMPANY_ROOTS", [str(root)]), \
                mock.patch.object(reflex, "origin_of", lambda path: origins[path]), \
                mock.patch.object(reflex.github, "refreshed", 1.0), \
                mock.patch.object(reflex, "canonical_slug") as canonical:
            reflex.learn_clone_names()
        self.assertEqual([c.args for c in canonical.call_args_list], [("o/a",)])

    def test_learning_waits_for_githubs_first_read(self):
        slept = []
        def sleep(sec):
            slept.append(sec)
            reflex.github.refreshed = 1.0  # the first read finishes meanwhile
        with mock.patch.object(reflex.github, "refreshed", 0.0), mock.patch.object(reflex, "SOURCES", {"github"}), \
                mock.patch.object(reflex.time, "sleep", sleep), mock.patch.object(reflex, "clones_under_roots", list):
            reflex.learn_clone_names()
        self.assertEqual(slept, [5])


class RunLabelTest(unittest.TestCase):
    def test_labels_follow_tmux_session_log_and_exit_code(self):
        alive = {"alive": True, "exit": None}
        self.assertEqual(board.run_label(alive, None), "시작함")
        self.assertEqual(board.run_label(alive, {"claude_working": True}), "작업 중")
        self.assertEqual(board.run_label(alive, {"waiting": {"id": "q"}}), "답을 기다림")
        self.assertEqual(board.run_label(alive, {"claude_working": False}), "대기 중 · 백그라운드 감시 중이거나 입력을 기다림")
        self.assertEqual(board.run_label({"alive": False, "exit": 0}, None), "끝남")
        self.assertEqual(board.run_label({"alive": False, "exit": None}, None), "끝남 (종료 코드 모름)")

    def test_card_reads_the_session_of_this_run_not_an_earlier_one(self):
        record = {A: {"tmux": "t", "skill": "concord:pr-to-merge", "started": 1000}}
        old = {"id": "session:old", "kind": "workThread", "roles": [], "detail": {
            "reflex": A, "first_at": 500, "claude_working": False}}
        nodes = {old["id"]: old}
        with mock.patch.object(board, "load_reflexes", return_value=record), mock.patch.object(board, "HANDOFF", True), \
                mock.patch.object(board, "run_state", return_value={"alive": True, "exit": None}):
            [card] = board.build_board({}, nodes, {}, {})
            self.assertIn("시작함", card["tags"])  # the earlier run's idle log does not count
            self.assertTrue(card["running"])  # the review list shows the PR as handed off, not 맡기기
            new = {"id": "session:new", "kind": "workThread", "roles": [], "detail": {
                "reflex": A, "first_at": 1003, "claude_working": True}}
            [card] = board.build_board({}, {**nodes, new["id"]: new}, {}, {})
        self.assertIn("작업 중", card["tags"])
        self.assertEqual(card["focus"], "session:new")

    def test_one_shot_review_records_are_not_shown(self):
        old = {A: {"kind": "review", "skill": "thync 리뷰", "status": "running", "started": 1}}
        with mock.patch.object(board, "load_reflexes", return_value=old), mock.patch.object(board, "HANDOFF", True):
            cards = board.build_board({}, {}, {}, {})
        self.assertEqual(cards, [])


if __name__ == "__main__":
    unittest.main()
