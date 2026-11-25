"""Sources other than session logs, with stubbed HTTP and gh responses."""
import io
import json
import os
import subprocess
import tempfile
import time
import unittest
import urllib.parse
from unittest import mock

os.environ["THYNC_HOME"] = tempfile.mkdtemp()

from thync import graph  # noqa: E402
from thync.sources import github, jira, notion  # noqa: E402


def reply(body) -> io.BytesIO:
    return io.BytesIO(json.dumps(body).encode())


class GitHub(unittest.TestCase):
    def test_search_items_become_default_branch_commits(self):
        items = [{"html_url": "https://github.com/o/r/commit/1", "repository": {"full_name": "o/r"},
                  "commit": {"message": "[API] Fix ABC-7\n\nbody", "author": {"date": "2026-10-29T14:04:33.000+09:00"}}}]
        self.assertEqual(github.parse_search(items), [{"slug": "o/r", "branch": "", "at": "2026-10-29T14:04:33.000+09:00",
                                                       "message": "[API] Fix ABC-7\n\nbody",
                                                       "url": "https://github.com/o/r/commit/1"}])

    def test_pushes_keep_the_newest_per_branch(self):
        events = [
            {"type": "PushEvent", "repo": {"name": "o/r"}, "created_at": "2026-10-29T02:00:00Z",
             "payload": {"ref": "refs/heads/abc-7-audit", "head": "b"}},
            {"type": "PushEvent", "repo": {"name": "o/r"}, "created_at": "2026-10-28T02:00:00Z",
             "payload": {"ref": "refs/heads/abc-7-audit", "head": "a"}},
            {"type": "PushEvent", "repo": {"name": "o/r"}, "created_at": "2026-10-28T01:00:00Z",
             "payload": {"ref": "refs/tags/v1", "head": "t"}},
            {"type": "WatchEvent", "repo": {"name": "o/x"}, "created_at": "2026-10-28T00:00:00Z", "payload": {}},
        ]
        got = github.parse_pushes(events)
        self.assertEqual([(p["branch"], p["head"], p["at"]) for p in got], [("abc-7-audit", "b", "2026-10-29T02:00:00Z")])
        self.assertEqual(got[0]["url"], "https://github.com/o/r/tree/abc-7-audit")

    def test_received_reviews_skip_mine_and_empty_comments(self):
        pr = {"reviews": {"nodes": [
                  {"author": {"login": "mina"}, "state": "CHANGES_REQUESTED", "body": "", "submittedAt": "t1", "url": "u1"},
                  {"author": {"login": "me"}, "state": "COMMENTED", "body": "my reply", "submittedAt": "t2", "url": "u2"},
                  {"author": {"login": "jun"}, "state": "COMMENTED", "body": "<!-- bot: approve -->", "submittedAt": "t3", "url": "u3"},
                  {"author": None, "state": "APPROVED", "body": "", "submittedAt": "t4", "url": "u4"}]},
              "reviewThreads": {"nodes": [
                  {"isResolved": True, "isOutdated": False, "path": "a.py", "line": 1, "comments": {"nodes": []}},
                  {"isResolved": False, "isOutdated": True, "path": "b.py", "line": None,
                   "comments": {"nodes": [{"author": {"login": "mina"}, "body": "why?", "createdAt": "t", "url": "u"}]}}]}}
        got = github.parse_reviews(pr, {"me"})
        self.assertEqual([(r["author"], r["state"]) for r in got["reviews"]], [("", "APPROVED"), ("mina", "CHANGES_REQUESTED")])
        self.assertEqual([t["path"] for t in got["threads"]], ["b.py", "a.py"])  # unresolved first

    def test_accounts_are_merged_and_a_failing_one_keeps_its_last_list(self):
        def whoami(cmd, **kw):  # c is a mien profile without GitHub
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"github": None if cmd[2] == "c" else cmd[2]}), "")

        def run(cmd, stdin, env, timeout):
            if cmd[:2] == ["mien", "exec"]:
                self.assertEqual(cmd[3:], ["--", "printenv", "GH_TOKEN"])
                self.assertNotEqual(cmd[2], "c")
                self.assertNotIn("GH_TOKEN", env)  # an inherited token never stands in for a profile's own
                return f"token-{cmd[2]}\n"
            self.assertEqual(env["GH_CONFIG_DIR"], str(github.NO_LOGIN))  # nor does gh's own login
            account = env["GH_TOKEN"].removeprefix("token-")
            if account == "b" and fail:
                raise subprocess.CalledProcessError(1, cmd, stderr="HTTP 502")
            if "search/issues" not in cmd:
                raise subprocess.CalledProcessError(1, cmd)  # only review requests matter here
            urls = {"a": ["u1", "u2"], "b": ["u2", "u3"]}[account]
            return json.dumps({"items": [{"html_url": u} for u in urls]})

        fail = False
        with mock.patch.multiple(github, ACCOUNTS=["a", "b", "c"], MIEN="mien", _seen_by={}, _lists={}, _logins={},
                                 _failing=set(), _tokens={}), \
                mock.patch.dict(os.environ, {"GH_TOKEN": "inherited"}), \
                mock.patch.object(github.subprocess, "run", side_effect=whoami), \
                mock.patch.object(github, "_run", side_effect=run), mock.patch("sys.stderr"):
            for a in ("c", "b", "a"):  # b answers before a; the configured order still decides
                github._refresh_account(a, 0)
            self.assertEqual([it["html_url"] for it in github.review_requests()], ["u1", "u2", "u3"])
            self.assertEqual([github.account_for(u) for u in ("u1", "u2", "u3", "elsewhere")], ["a", "a", "b", None])
            fail = True
            github._refresh_account("b", 0)
            self.assertEqual(len(github.review_requests()), 3)  # b's last list is kept

    def test_a_profile_token_is_asked_of_mien_once_and_kept_in_memory(self):
        calls, tokens = [], iter(["old\n", "new\n", "newer\n"])

        def run(cmd, stdin, env, timeout):
            calls.append(cmd)
            if cmd[0] == "mien":
                self.assertFalse(set(github.HARNESS_ENV) & set(env))  # else mien redacts the token it prints
                return next(tokens)
            if env["GH_TOKEN"] == "old" and "revoked" in cmd:
                raise subprocess.CalledProcessError(1, cmd, stderr="HTTP 401: Bad credentials")
            if env["GH_TOKEN"] == "newer" and "refused" in cmd:
                raise subprocess.CalledProcessError(1, cmd, stderr="HTTP 401: Bad credentials")
            return env["GH_TOKEN"]

        def mien_calls():
            return sum(c[0] == "mien" for c in calls)

        with mock.patch.multiple(github, MIEN="mien", _tokens={}), mock.patch.object(github, "_run", side_effect=run), \
                mock.patch.dict(os.environ, {"CLAUDECODE": "1", "MIEN_CAPTURED": ""}):
            self.assertEqual([github.gh("a", "api", "x") for _ in range(3)], ["old"] * 3)
            self.assertEqual(mien_calls(), 1)
            self.assertEqual(github.gh("a", "api", "revoked"), "new")  # GitHub refused the kept token: asked again
            self.assertEqual(mien_calls(), 2)
            github._tokens["a"] = (time.time() - github.TOKEN_TTL_SEC - 1, "new")
            with self.assertRaises(subprocess.CalledProcessError):
                github.gh("a", "api", "refused")  # newer is fresh, so its own 401 is not asked about again
            self.assertEqual(mien_calls(), 3)

    def test_a_profile_without_a_github_token_fails(self):
        def run(cmd, stdin, env, timeout):
            self.assertEqual(cmd[0], "mien")  # gh never runs without the profile's token
            raise subprocess.CalledProcessError(1, cmd, "", "")

        with mock.patch.multiple(github, MIEN="mien", _tokens={}), mock.patch.object(github, "_run", side_effect=run):
            with self.assertRaises(subprocess.CalledProcessError) as e:
                github.gh("nogh", "api", "user")
        self.assertIn("no GitHub token", e.exception.stderr)
        self.assertNotIn("nogh", github._tokens)

    def test_a_review_request_is_answered_as_the_reviewer_not_the_author(self):
        lists = {"author": {"sent": [{"url": "u"}]}, "reviewer": {"reviews": [{"html_url": "u"}]}}
        with mock.patch.multiple(github, ACCOUNTS=["author", "reviewer"], _lists=lists, _seen_by={}):
            github._index()
            self.assertEqual(github.account_for("u"), "reviewer")

    def test_graph_lists_reviews_others_left_on_my_prs(self):
        at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 600))
        pr = {"url": "https://github.com/o/r/pull/7", "number": 7, "title": "t", "updatedAt": at, "isDraft": False,
              "repository": {"name": "r"}, "reviewDecision": None, "author": {"login": "me"},
              "reviewRequests": {"nodes": []}, "latestReviews": {"nodes": [
                  {"author": {"login": "me"}, "state": "COMMENTED", "submittedAt": at, "body": "self note"},
                  {"author": {"login": "mina"}, "state": "CHANGES_REQUESTED", "submittedAt": at, "body": "<!-- x -->fix"}]}}
        with mock.patch.multiple(graph, SESSIONS=False, SOURCES={"github"}), \
                mock.patch.object(github, "my_commits", return_value=[]), \
                mock.patch.object(github, "review_requests", return_value=[]), \
                mock.patch.object(github, "sent_reviews", return_value=[pr]):
            got = graph.build_graph(7)["received"]
        self.assertEqual([(r["author"], r["state"], r["text"]) for r in got], [("mina", "CHANGES_REQUESTED", "fix")])


class JiraMine(unittest.TestCase):
    def test_search_mine_reads_assigned_tickets(self):
        body = {"issues": [{"key": "ABC-9", "fields": {
            "summary": "감사 로그", "status": {"name": "진행 중"}, "updated": "2026-10-29T14:04:33.000+0900",
            "parent": {"key": "ABC-1", "fields": {"summary": "콘솔"}}}}]}
        with mock.patch.object(jira, "_open", return_value=reply(body)) as op:
            got = jira.search_mine("https://x.atlassian.net", "Basic abc")
        self.assertEqual(got, {"ABC-9": {"summary": "감사 로그", "status": "진행 중", "epic": "ABC-1",
                                         "epic_summary": "콘솔", "updated": "2026-10-29T14:04:33.000+0900"}})
        req = op.call_args[0][0]
        url = urllib.parse.urlsplit(req.full_url)
        self.assertEqual(url.path, "/rest/api/3/search/jql")
        self.assertIn("assignee = currentUser()", urllib.parse.parse_qs(url.query)["jql"][0])
        self.assertEqual(req.get_method(), "GET")


class Notion(unittest.TestCase):
    PAGE = {"object": "page", "id": "p1", "url": "https://www.notion.so/p1", "last_edited_time": "2026-10-29T05:00:00.000Z",
            "last_edited_by": {"id": "u1"},
            "properties": {"Tags": {"type": "multi_select"}, "Name": {"type": "title", "title": [
                {"plain_text": "ABC-9 "}, {"plain_text": "설계"}]}}}

    def test_parse_takes_the_title_property(self):
        self.assertEqual(notion.parse([self.PAGE, {"object": "database", "id": "d"}]),
                         [{"id": "p1", "title": "ABC-9 설계", "url": "https://www.notion.so/p1",
                           "at": "2026-10-29T05:00:00.000Z"}])

    def test_parse_filters_by_last_editor(self):
        self.assertEqual(notion.parse([self.PAGE], "someone-else"), [])

    def test_search_only_posts_to_search(self):
        with mock.patch.object(notion, "_open", return_value=reply({"results": [self.PAGE]})) as op:
            got = notion.search("secret")
        req = op.call_args[0][0]
        self.assertEqual((req.full_url, req.get_method()), ("https://api.notion.com/v1/search", "POST"))
        self.assertEqual(req.get_header("Notion-version"), notion.VERSION)
        self.assertNotIn("secret", req.full_url)
        self.assertEqual(got, [self.PAGE])


class GraphWithoutSessions(unittest.TestCase):
    def test_commits_tickets_and_pages_make_a_graph(self):
        now = time.time()
        iso = lambda ago: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - ago))  # noqa: E731
        commits = [{"slug": "o/r", "branch": "abc-9-audit", "at": iso(600), "message": "Add audit log",
                    "url": "https://github.com/o/r/tree/abc-9-audit", "personal": False},
                   {"slug": "me/tool", "branch": "main", "at": iso(7200), "message": "Fix typo",
                    "url": "https://github.com/me/tool/commit/1", "personal": True}]
        tickets = {"ABC-9": {"summary": "감사 로그", "status": "진행 중", "updated": iso(3600), "mine": True},
                   "ABC-5": {"summary": "키 회전", "status": "In Progress", "updated": iso(5 * 86400), "mine": True}}
        pages = [{"id": "p1", "title": "ABC-9 설계", "url": "https://www.notion.so/p1", "at": iso(1800)}]
        with mock.patch.multiple(graph, SESSIONS=False, SOURCES={"github", "jira", "notion"}), \
                mock.patch.object(github, "my_commits", return_value=commits), \
                mock.patch.object(github, "review_requests", return_value=[]), \
                mock.patch.object(github, "sent_reviews", return_value=[]), \
                mock.patch.object(notion, "recent_pages", return_value=pages), \
                mock.patch.object(jira, "read_json", return_value=tickets):
            g = graph.build_graph(7)
        nodes = {n["id"]: n for n in g["nodes"]}
        self.assertEqual(nodes["branch:r:abc-9-audit"]["detail"]["unit"], "jira:ABC-9")
        self.assertEqual(nodes["notion:p1"]["detail"]["unit"], "jira:ABC-9")
        self.assertIn("focus", nodes["branch:r:abc-9-audit"]["roles"])
        self.assertEqual(nodes["repo:tool"]["profile"], "개인")
        self.assertIn("running", nodes["jira:ABC-5"]["roles"])  # in progress, outside the workspace
        cards = {c["id"]: c["column"] for c in g["board"]}
        self.assertEqual((cards["jira:ABC-9"], cards["jira:ABC-5"]), ("doing", "doing"))
        self.assertTrue(g["activity"])

    def test_with_session_logs_commits_add_nothing(self):
        commits = [{"slug": "o/r", "branch": "feat", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "message": "x", "url": "https://github.com/o/r/tree/feat", "personal": False}]
        with mock.patch.multiple(graph, SESSIONS=True, SOURCES={"claude_code", "github"}), \
                mock.patch.object(graph, "load_sessions", return_value=[]), \
                mock.patch.object(github, "my_commits", return_value=commits), \
                mock.patch.object(github, "review_requests", return_value=[]), \
                mock.patch.object(github, "sent_reviews", return_value=[]):
            g = graph.build_graph(7)
        # Sessions describe the work; commits would only add a unit per branch and merge commit.
        self.assertFalse(any(n["id"] == "branch:r:feat" for n in g["nodes"]))
        self.assertFalse(any(u["id"] == "branch:r:feat" for u in g["units"]))


class EpochTest(unittest.TestCase):
    def test_offsets_without_colon_parse_on_every_supported_python(self):
        from thync.util import epoch
        self.assertEqual(epoch("2026-10-29T10:04:48.123+0900"), epoch("2026-10-29T01:04:48.123Z"))
        self.assertEqual(epoch("2026-10-29T10:04:48+09:00"), epoch("2026-10-29T01:04:48Z"))


if __name__ == "__main__":
    unittest.main()
