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

    def __init__(self, author: str, login: str = "me", profile: str = "cryptolab", tmux_rc: int = 0, state: str = "open",
                 draft: bool = False):
        self.author, self.login, self.profile, self.tmux_rc, self.state = author, login, profile, tmux_rc, state
        self.draft = draft
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kw):
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        rc, out, err = 0, "", ""
        if argv[:2] == ["tmux", "new-session"]:
            rc, err = self.tmux_rc, "duplicate session: x" if self.tmux_rc else ""
        elif argv[0] == "tmux":
            rc = 1
        elif argv[:2] == ["git", "-C"]:
            out = "git@github.com:o/r.git\n"
        elif Path(argv[0]).name == "mien" and argv[1] == "which":
            rc, out = (0, f"{self.profile}\n") if self.profile else (1, "")
        elif "gh" in argv and "user" in argv:
            out = f"{self.login}\n"
        elif "gh" in argv and any(a.startswith("repos/o/r/pulls/") for a in argv):
            out = json.dumps({"author": self.author, "state": self.state, "draft": self.draft})
        return mock.Mock(returncode=rc, stdout=out, stderr=err)

    def launches(self) -> list[str]:
        """The shell command of every tmux session started."""
        return [c[-1] for c in self.calls if c[:2] == ["tmux", "new-session"]]


class HandoffTest(unittest.TestCase):
    def setUp(self):
        self.clone = tempfile.mkdtemp()
        self.cache = Path(tempfile.mkdtemp())
        for skill in ("review-until-approve", "pr-to-merge"):
            path = self.cache / "market" / "concord" / "1.0.0" / "skills" / skill / "SKILL.md"
            path.parent.mkdir(parents=True)
            path.write_text("---\nname: x\n---\n")
        self.saved: dict = {}
        self.exits = Path(tempfile.mkdtemp())

    def run_handoff(self, node: dict, machine: FakeMachine, cache: Path | None = None, call=None, which=None):
        with mock.patch("subprocess.run", machine), \
                mock.patch.object(reflex, "find_clone", return_value=self.clone), \
                mock.patch.object(reflex, "save_reflex", lambda url, rec: self.saved.update({url: rec})), \
                mock.patch.object(reflex, "PLUGIN_CACHE", cache or self.cache, create=True), \
                mock.patch.object(reflex, "EXIT_DIR", self.exits, create=True), \
                mock.patch("shutil.which", which or (lambda name: f"/usr/bin/{name}")):
            reflex._tmux_cache.clear()
            return (call or reflex.start_reflex)(node)

    def test_review_request_starts_review_until_approve_for_that_pr_as_the_profile(self):
        machine = FakeMachine(author="alice")
        status, _ = self.run_handoff(pr_node("review"), machine)
        self.assertEqual(status, 202)
        [command] = machine.launches()
        self.assertIn(f"/concord:review-until-approve {A}", command)
        self.assertEqual(shlex.split(command)[:3], ["/usr/bin/mien", "exec", "cryptolab"])
        self.assertNotIn("pr-to-merge", command)
        self.assertNotIn("/pull/8", command)
        prompt = shlex.split(command.split("; echo $?")[0])[-1]
        self.assertEqual(re.findall(r"trust|vouch|신뢰|믿", prompt, re.I), [])  # the user declares trust, not thync

    def test_own_pr_starts_pr_to_merge_for_that_pr_only(self):
        machine = FakeMachine(author="me")
        status, _ = self.run_handoff(pr_node("sent"), machine)
        self.assertEqual(status, 202)
        [command] = machine.launches()
        self.assertIn("/concord:pr-to-merge", command)
        self.assertIn("o/r#7", command)
        self.assertNotIn("review-until-approve", command)
        self.assertEqual(shlex.split(command)[:3], ["/usr/bin/mien", "exec", "cryptolab"])

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
        prompt = shlex.split(command.split("; echo $?")[0])[-1]
        self.assertTrue(prompt.startswith(f"/concord:pr-to-merge {A} "))
        self.assertIn("Merging is authorized for o/r#7 only", prompt)
        self.assertEqual(re.findall(r"\bo/r(?![#=/])", prompt), [])  # never the whole repository
        self.assertIn(f"--checkout o/r={self.clone}", prompt)
        self.assertEqual(self.saved[A]["skill"], "concord:pr-to-merge")
        self.assertEqual((self.saved[A]["profile"], self.saved[A]["login"]), ("cryptolab", "me"))

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

    def test_start_refuses_when_the_check_differs_from_what_was_confirmed(self):
        shown = {"url": A, "workflow": "concord:review-until-approve", "profile": "cryptolab", "login": "someone-else"}
        machine = FakeMachine(author="alice")
        status, _ = self.run_handoff(pr_node("review"), machine, call=lambda n: reflex.start_reflex(n, shown))
        self.assertEqual(status, 409)
        self.assertEqual((machine.launches(), self.saved), ([], {}))

    def test_second_handoff_of_a_running_pr_is_refused(self):
        machine = FakeMachine(author="me", tmux_rc=1)  # tmux refuses the session name it already has
        status, message = self.run_handoff(pr_node("sent"), machine)
        self.assertEqual((status, self.saved), (409, {}))
        self.assertIn("tmux attach -t thync-o-r-7", message)

    def test_my_pr_under_another_account_or_closed_or_unclaimed_starts_nothing(self):
        for machine in (FakeMachine(author="alice"), FakeMachine(author="me", profile=""),
                        FakeMachine(author="me", state="closed"), FakeMachine(author="me", draft=True)):
            status, _ = self.run_handoff(pr_node("sent"), machine)
            self.assertEqual(status, 409)
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


class RunLabelTest(unittest.TestCase):
    def test_labels_follow_tmux_session_log_and_exit_code(self):
        alive = {"alive": True, "exit": None}
        self.assertEqual(board.run_label(alive, None), "시작함")
        self.assertEqual(board.run_label(alive, {"claude_working": True}), "작업 중")
        self.assertEqual(board.run_label(alive, {"waiting": {"id": "q"}}), "답을 기다림")
        self.assertEqual(board.run_label(alive, {"claude_working": False}), "대기 중 · 백그라운드 감시 중이거나 입력을 기다림")
        self.assertEqual(board.run_label({"alive": False, "exit": 0}, None), "끝남")
        self.assertEqual(board.run_label({"alive": False, "exit": None}, None), "끝남 (종료 코드 모름)")

    def test_one_shot_review_records_are_not_shown(self):
        old = {A: {"kind": "review", "skill": "thync 리뷰", "status": "running", "started": 1}}
        with mock.patch.object(board, "load_reflexes", return_value=old), mock.patch.object(board, "HANDOFF", True):
            cards = board.build_board({}, {}, {}, {})
        self.assertEqual(cards, [])


if __name__ == "__main__":
    unittest.main()
