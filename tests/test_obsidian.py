"""The Obsidian vault source: scanning a vault on disk and what the notes add to the graph."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ["THYNC_HOME"] = tempfile.mkdtemp()  # even when the shell sets it: tests delete card files

from thync import config, graph  # noqa: E402
from thync.sources import github, jira, obsidian  # noqa: E402

DAY = 86400


def write(path: Path, text: str = "", age: float = 600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    at = time.time() - age
    os.utime(path, (at, at))
    return path


class Vaults(unittest.TestCase):
    def test_profile_defaults_to_personal_and_name_to_the_folder(self):
        got = config.parse_vaults([{"path": "/v/work", "profile": "회사"}, {"path": "/v/notes"},
                                   {"path": "/v/odd", "profile": "team", "name": "mine"}, "junk", {"name": "no path"}])
        self.assertEqual(got, [{"name": "work", "path": "/v/work", "profile": "회사"},
                               {"name": "notes", "path": "/v/notes", "profile": "개인"},
                               {"name": "mine", "path": "/v/odd", "profile": "개인"}])

    def test_a_repeated_name_is_skipped_unless_named_apart(self):
        with mock.patch("sys.stderr"):
            same = config.parse_vaults([{"path": "/a/vault"}, {"path": "/b/vault"}])
        apart = config.parse_vaults([{"path": "/a/vault"}, {"path": "/b/vault", "name": "b"}])
        self.assertEqual([v["path"] for v in same], ["/a/vault"])
        self.assertEqual([v["name"] for v in apart], ["vault", "b"])

    def test_a_path_or_name_that_cannot_be_used_is_skipped(self):
        with mock.patch("sys.stderr"):
            got = config.parse_vaults([{"path": "~no-such-user-here/v"}, {"path": "/"}, {"path": "/v/a", "name": "a:b"},
                                       {"path": "/v/ok"}])
        self.assertEqual(got, [{"name": "ok", "path": "/v/ok", "profile": "개인"}])

    def test_a_relative_path_is_skipped_and_a_home_path_is_made_absolute(self):
        with mock.patch("sys.stderr"):
            got = config.parse_vaults([{"path": "notes/vault"}, {"path": "~/vault"}])
        self.assertEqual([v["path"] for v in got], [str(Path.home() / "vault")])


class Extract(unittest.TestCase):
    def test_links_drop_alias_heading_folder_and_extension(self):
        text = "[[회의록]] [[폴더/설계|보이는 이름]] ![[그림 노트#제목]] [[Notes.md]] [[블록^abc]]"
        self.assertEqual(obsidian.extract(text)["links"], ["회의록", "설계", "그림 노트", "notes", "블록"])

    def test_a_link_in_a_table_escapes_the_bar_before_its_alias(self):
        self.assertEqual(obsidian.extract("| [[회의록\\|표시]] | [[설계]] |")["links"], ["회의록", "설계"])

    def test_names_are_compared_composed(self):
        import unicodedata
        nfd = unicodedata.normalize("NFD", "회의록")
        self.assertNotEqual(nfd, "회의록")
        self.assertEqual(obsidian.extract(f"[[{nfd}]] #{nfd}"), {**obsidian.extract(""), "links": ["회의록"], "tags": ["회의록"]})
        self.assertEqual(obsidian.fold(nfd), obsidian.fold("회의록"))

    def test_tags_come_from_the_body_and_the_front_matter(self):
        text = "---\ntags: [Alpha, beta]\n---\n# 제목\n#설계 줄 끝 #team/web.\nhttps://example.com/#top #123 a#b ##x\n"
        self.assertEqual(obsidian.extract(text)["tags"], ["alpha", "beta", "설계", "team/web"])

    def test_front_matter_tags_as_a_block_list_or_a_plain_line(self):
        self.assertEqual(obsidian.extract("---\ntitle: t\ntags:\n  - one\n  - '#two'\nother: x\n---\n")["tags"], ["one", "two"])
        self.assertEqual(obsidian.extract("---\ntags: a, b\n---\n")["tags"], ["a", "b"])

    def test_front_matter_with_windows_line_endings(self):
        self.assertEqual(obsidian.extract("---\r\ntags: [crlf]\r\n---\r\n#body\r\n")["tags"], ["crlf", "body"])

    def test_a_tag_needs_a_letter(self):
        self.assertEqual(obsidian.extract("#- #_ #/ #2026-11 #a-1 #v2")["tags"], ["a-1", "v2"])

    def test_a_pull_request_url_must_stand_alone(self):
        text = "xhttps://github.com/o/r/pull/3 https://github.com/o/r/pull/12abc (https://github.com/o/r/pull/5)"
        self.assertEqual([n for _, _, n in obsidian.extract(text)["prs"]], [5])

    def test_a_pull_request_url_next_to_korean_or_markup_is_read(self):
        for text in ("https://github.com/o/r/pull/5와 비교", "참고https://github.com/o/r/pull/5", "_https://github.com/o/r/pull/5_",
                     "[글](https://github.com/o/r/pull/5)", "https://github.com/o/r/pull/5/files"):
            self.assertEqual([n for _, _, n in obsidian.extract(text)["prs"]], [5], text)

    def test_many_unclosed_links_on_one_line_stay_fast(self):
        for text in ("[[a " * 60000, "[[a|" * 60000, "[[a#" * 60000):  # 240KB, about one full read
            start = time.time()
            self.assertEqual(obsidian.extract(text)["links"], [])
            self.assertLess(time.time() - start, 2.0)  # the quadratic pattern needed minutes

    def test_tag_at_the_start_of_a_line_counts(self):
        self.assertEqual(obsidian.extract("#첫줄\n본문")["tags"], ["첫줄"])

    def test_keys_and_pull_requests_keep_their_order_once(self):
        text = "ABC-9 그리고 XY-12, 다시 ABC-9. https://github.com/o/r/pull/7 와 https://github.com/o/r/pull/7/files"
        got = obsidian.extract(text)
        self.assertEqual(got["keys"], ["ABC-9", "XY-12"])
        self.assertEqual(got["prs"], [("https://github.com/o/r/pull/7", "r", 7)])

    def test_code_is_ignored_and_an_open_fence_runs_to_the_end(self):
        text = "`#inline ABC-1` 앞 #keep\n```\n#fenced [[x]] ABC-2\n```\n뒤 [[y]]\n```py\n#open ABC-3 [[z]]"
        got = obsidian.extract(text)
        self.assertEqual((got["tags"], got["links"], got["keys"]), (["keep"], ["y"], []))

    def test_a_fence_line_with_an_info_string_does_not_close_the_fence(self):
        got = obsidian.extract("```python\ncode ABC-1\n```js\nABC-2 inside\n```~~\nABC-4 inside\n```\nABC-3 after\n")
        self.assertEqual(got["keys"], ["ABC-3"])
        self.assertEqual(obsidian.extract("~~~\nABC-1\n~~~~\nABC-2\n")["keys"], ["ABC-2"])

    def test_at_most_ten_pull_requests_per_note(self):
        text = " ".join(f"https://github.com/o/r/pull/{i}" for i in range(1, 15))
        self.assertEqual([n for _, _, n in obsidian.extract(text)["prs"]], list(range(1, 11)))


class Scan(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.vaults = [{"name": "vault", "path": str(self.vault), "profile": "회사"}]
        patches = [mock.patch.object(obsidian, "OBSIDIAN_VAULTS", self.vaults),
                   mock.patch.object(obsidian, "CARDS_DIR", self.vault / "cards"),
                   mock.patch.object(obsidian, "_notes", (0.0, [])), mock.patch.object(obsidian, "_threaded", False)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def rels(self, notes):
        return sorted(n["rel"] for n in notes)

    def listed(self, *paths):
        """_recent_files as if it had listed these paths a moment ago."""
        vault = self.vaults[0]
        return mock.patch.object(obsidian, "_recent_files", lambda *a: [(time.time() - 60, vault, str(p)) for p in paths])

    def test_a_file_swapped_for_a_fifo_after_the_listing_is_skipped_without_waiting(self):
        fifo = self.vault / "pipe.md"
        os.mkfifo(fifo)
        with self.listed(fifo, write(self.vault / "note.md", "x")):
            self.assertEqual(self.rels(obsidian.scan(time.time())), ["note.md"])

    def test_a_file_name_that_is_not_utf8_is_skipped_and_the_rest_can_be_sent_as_json(self):
        odd = str(self.vault) + "/\udcff.md"  # how os.walk returns the byte 0xff on Linux; macOS cannot store it
        real = str(write(self.vault / "note.md", "x"))
        open_ = os.open  # macOS cannot store such a name: open the real file in its place
        with self.listed(odd, real), mock.patch("os.open", lambda p, *a: open_(real if p == odd else p, *a)):
            notes = obsidian.scan(time.time())
        self.assertEqual(self.rels(notes), ["note.md"])
        json.dumps(notes, ensure_ascii=False).encode()  # what the server and the MCP server send

    def test_a_recent_note_carries_its_title_url_time_and_profile(self):
        path = write(self.vault / "일지" / "회의 메모.md", "#설계 [[다른]] ABC-9", age=600)
        (note,) = obsidian.scan(time.time())
        self.assertEqual((note["vault"], note["rel"], note["title"], note["profile"]), ("vault", "일지/회의 메모.md", "회의 메모", "회사"))
        self.assertAlmostEqual(note["at"], path.stat().st_mtime, places=3)
        self.assertEqual(note["url"], "obsidian://open?path=" + str(path).replace("/", "%2F").replace(" ", "%20")
                         .replace("일지", "%EC%9D%BC%EC%A7%80").replace("회의", "%ED%9A%8C%EC%9D%98").replace("메모", "%EB%A9%94%EB%AA%A8"))
        self.assertEqual((note["tags"], note["links"], note["keys"]), (["설계"], ["다른"], ["ABC-9"]))

    def test_old_hidden_card_non_markdown_and_linked_files_are_left_out(self):
        write(self.vault / "keep.md")
        write(self.vault / "old.md", age=(config.MAX_DAYS + 1) * DAY)
        write(self.vault / ".obsidian" / "plugin.md")
        write(self.vault / ".trash" / "gone.md")
        write(self.vault / "cards" / "card.md")
        write(self.vault / "board.canvas")
        outside = write(self.root / "outside" / "secret.md", "ABC-1")
        os.symlink(outside, self.vault / "link.md")
        os.symlink(outside.parent, self.vault / "linked-folder")
        self.assertEqual(self.rels(obsidian.scan(time.time())), ["keep.md"])

    def test_a_file_swapped_for_a_link_after_the_listing_is_not_opened(self):
        outside = write(self.root / "outside" / "secret.md", "ABC-1 #secret")
        target = write(self.vault / "note.md", "#mine")
        real = obsidian._recent_files

        def swap(*args):  # the listing saw a regular file; before it is read it becomes a link out of the vault
            found = real(*args)
            target.unlink()
            os.symlink(outside, target)
            return found

        with mock.patch.object(obsidian, "_recent_files", swap):
            self.assertEqual(obsidian.scan(time.time()), [])

    def test_only_the_newest_hundred_notes_across_all_vaults(self):
        second = self.root / "second"
        self.vaults.append({"name": "second", "path": str(second), "profile": "개인"})
        for i in range(60):
            write(self.vault / f"a{i:03}.md", age=100 + i)       # newest in this vault: a000
            write(second / f"b{i:03}.md", age=1000 + i)          # all older than every a*
        notes = obsidian.scan(time.time())
        self.assertEqual(len(notes), obsidian.MAX_NOTES)
        self.assertEqual(sum(n["vault"] == "vault" for n in notes), 60)
        self.assertEqual(self.rels(n for n in notes if n["vault"] == "second"), [f"b{i:03}.md" for i in range(40)])

    def test_only_the_head_of_a_big_note_is_read(self):
        write(self.vault / "big.md", "#앞 " + "x" * obsidian.READ_BYTES + " #뒤 ABC-9")
        (note,) = obsidian.scan(time.time())
        self.assertEqual((note["tags"], note["keys"]), (["앞"], []))

    def test_a_word_the_cut_splits_is_dropped_whole(self):
        head = "#앞 "
        fill = "x" * (obsidian.READ_BYTES - len(head.encode()) - len(" ABC-12"))
        write(self.vault / "cut.md", f"{head}{fill} ABC-123 뒤")           # the cut falls between ABC-12 and 3
        write(self.vault / "fits.md", f"{head}{fill[:-4]} ABC-123")         # ends before the cut: nothing is dropped
        notes = {n["rel"]: n for n in obsidian.scan(time.time())}
        self.assertEqual((notes["cut.md"]["tags"], notes["cut.md"]["keys"]), (["앞"], []))
        self.assertEqual(notes["fits.md"]["keys"], ["ABC-123"])

    def test_a_link_the_cut_leaves_open_is_not_a_link_to_a_shorter_name(self):
        fill = "x" * (obsidian.READ_BYTES - len("[[회의 ".encode()) - 1)
        write(self.vault / "cut.md", f"{fill} [[회의 메모]] 뒤")               # the cut falls after "[[회의 "
        (note,) = obsidian.scan(time.time())
        self.assertEqual(note["links"], [])
        self.assertEqual(obsidian.extract("[[열린 링크\n[[닫힌 링크]]")["links"], ["닫힌 링크"])

    def test_a_file_dated_in_the_future_counts_as_edited_now(self):
        write(self.vault / "future.md", age=-3600)
        now = time.time()
        (note,) = obsidian.scan(now)
        self.assertEqual(note["at"], now)

    def test_a_missing_vault_is_skipped(self):
        self.vaults.insert(0, {"name": "gone", "path": str(self.root / "gone"), "profile": "개인"})
        write(self.vault / "keep.md")
        self.assertEqual(self.rels(obsidian.scan(time.time())), ["keep.md"])

    def test_without_a_refresh_thread_the_caller_scans_when_the_result_is_stale(self):
        write(self.vault / "one.md")
        now = time.time()
        self.assertEqual(self.rels(obsidian.recent_notes(now)), ["one.md"])
        write(self.vault / "two.md")
        self.assertEqual(self.rels(obsidian.recent_notes(now + obsidian.SCAN_SEC - 1)), ["one.md"])
        self.assertEqual(self.rels(obsidian.recent_notes(now + obsidian.SCAN_SEC + 1)), ["one.md", "two.md"])

    def test_with_a_refresh_thread_the_caller_never_scans(self):
        write(self.vault / "one.md")
        with mock.patch.object(obsidian, "_threaded", True), mock.patch.object(obsidian, "scan") as scan:
            self.assertEqual(obsidian.recent_notes(time.time()), [])
        scan.assert_not_called()


class Graph(unittest.TestCase):
    """build_graph() with a vault on disk. Sessions are off, as in GraphWithoutSessions (test_sources.py)."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.vaults = [{"name": "vault", "path": str(self.vault), "profile": "개인"}]
        self.tickets = {"ABC-9": {"summary": "감사 로그", "status": "진행 중", "updated": self.iso(3600), "mine": True}}
        self.sent = []
        self.requests = []

    def iso(self, ago: float) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - ago))

    def build(self, days: int = 7, sources=("jira", "obsidian", "github")) -> dict:
        with mock.patch.multiple(graph, SESSIONS=False, SOURCES=set(sources)), \
                mock.patch.multiple(obsidian, OBSIDIAN_VAULTS=self.vaults, CARDS_DIR=self.vault / "cards",
                                    _notes=(0.0, []), _threaded=False), \
                mock.patch.object(github, "my_commits", return_value=[]), \
                mock.patch.object(github, "review_requests", return_value=self.requests), \
                mock.patch.object(github, "sent_reviews", return_value=self.sent), \
                mock.patch.object(jira, "read_json", return_value=self.tickets):
            g = graph.build_graph(days)
        self.nodes = {n["id"]: n for n in g["nodes"]}
        self.edges = {tuple(e) for e in g["edges"]}
        self.units = {u["id"]: u for u in g["units"]}
        return g

    def linked(self, a: str, b: str) -> bool:
        return (min(a, b), max(a, b)) in self.edges

    def test_no_vault_leaves_the_graph_as_it_was(self):
        self.vaults = []
        with mock.patch("time.time", return_value=time.time()):  # brightness decays with time; hold it still
            with_source = self.build()
            without = self.build(sources=("jira", "github"))
        self.assertEqual(with_source, without)

    def test_a_note_edited_in_the_period_is_a_node_and_an_older_one_is_not(self):
        write(self.vault / "오늘.md", age=600)
        write(self.vault / "지난달.md", age=10 * DAY)
        g = self.build(7)
        note = self.nodes["obsidian:vault:오늘.md"]
        self.assertEqual((note["name"], note["kind"], note["profile"], note["detail"]["kind_label"]),
                         ("오늘", "dailyNote", "개인", "Obsidian 노트"))
        self.assertTrue(note["detail"]["url"].startswith("obsidian://open?path="))
        self.assertNotIn("obsidian:vault:지난달.md", self.nodes)
        self.assertEqual(note["detail"]["unit"], "obsidian:vault:오늘.md")
        self.assertIn(("obsidian", "오늘"), {(a["kind"], a["text"]) for a in g["activity"]})
        self.assertIn("obsidian:vault:지난달.md", {n["id"] for n in self.build(30)["nodes"]})

    def test_a_key_in_the_title_puts_the_note_in_the_tickets_unit(self):
        write(self.vault / "ABC-9 설계.md", "XY-1 은 모르는 접두어")
        self.build()
        self.assertEqual(self.nodes["obsidian:vault:ABC-9 설계.md"]["detail"]["unit"], "jira:ABC-9")
        self.assertTrue(self.linked("obsidian:vault:ABC-9 설계.md", "jira:ABC-9"))
        self.assertNotIn("jira:XY-1", self.nodes)

    def test_a_ticket_a_review_request_made_joins_the_unit_its_title_key_names(self):
        self.requests = [{"html_url": "https://github.com/o/r/pull/3", "number": 3, "title": "ABC-44 fix login",
                          "repository_url": "https://api.github.com/repos/o/r", "updated_at": self.iso(600),
                          "user": {"login": "peer"}}]
        self.build()
        self.assertEqual(self.nodes["jira:ABC-44"]["detail"]["unit"], "common")   # without a note: no unit of its own
        write(self.vault / "ABC-44 조사.md")
        self.build()
        self.assertEqual(self.nodes["jira:ABC-44"]["detail"]["unit"], "jira:ABC-44")
        self.assertEqual(self.nodes["obsidian:vault:ABC-44 조사.md"]["detail"]["unit"], "jira:ABC-44")

    def test_a_future_dated_note_does_not_outshine_the_scale(self):
        write(self.vault / "future.md", age=-3600)
        g = self.build()
        self.assertLessEqual(self.nodes["obsidian:vault:future.md"]["detail"]["act"], 1.0)
        self.assertLessEqual(g["focus_level"], 1.0)

    def test_a_link_finds_a_note_whose_file_name_is_stored_decomposed(self):
        import unicodedata
        write(self.vault / (unicodedata.normalize("NFD", "회의록") + ".md"))
        write(self.vault / "가.md", "[[회의록]]")
        self.build()
        other = next(i for i in self.nodes if i.startswith("obsidian:vault:") and i != "obsidian:vault:가.md")
        self.assertTrue(self.linked("obsidian:vault:가.md", other))

    def test_a_key_in_the_body_only_links(self):
        self.tickets["ABC-5"] = {"summary": "키 회전", "status": "진행 중", "updated": self.iso(7200), "mine": True}
        write(self.vault / "회의록.md", "ABC-5 논의, ABC-77 은 동료 티켓")
        self.build()
        note = "obsidian:vault:회의록.md"
        self.assertEqual(self.nodes[note]["detail"]["unit"], note)                # the body does not decide the unit
        self.assertTrue(self.linked(note, "jira:ABC-5") and self.linked(note, "jira:ABC-77"))
        self.assertEqual(self.nodes["jira:ABC-5"]["detail"]["unit"], "jira:ABC-5")  # an existing ticket keeps its unit
        # A ticket the note made sits in the note's unit: a node in "common" is never drawn (web/src/state.ts).
        self.assertEqual(self.nodes["jira:ABC-77"]["detail"]["unit"], note)
        self.assertTrue(self.nodes["jira:ABC-77"]["detail"]["mention"])
        self.assertEqual(self.nodes["jira:ABC-77"]["profile"], "회사")
        self.assertNotIn("jira:ABC-77", self.units)                                 # so a colleague's ticket is no card of mine

    def test_a_title_key_claims_a_ticket_another_note_only_mentioned(self):
        for mentioning in ("A.md", "Z.md"):  # processed before and after the note that carries the key in its title
            with self.subTest(mentioning=mentioning):
                for old in self.vault.glob("*.md"):
                    old.unlink()
                write(self.vault / mentioning, "ABC-77 언급")
                write(self.vault / "ABC-77 계획.md")
                g = self.build()
                ticket = self.nodes["jira:ABC-77"]["detail"]
                self.assertEqual((ticket["unit"], ticket.get("mention")), ("jira:ABC-77", None))
                self.assertEqual(next(c for c in g["board"] if c["id"] == "jira:ABC-77")["tags"], ["ABC-77"])
                self.assertTrue(self.linked(f"obsidian:vault:{mentioning}", "jira:ABC-77"))

    def test_a_colleagues_ticket_in_the_body_does_not_move_the_notes_card(self):
        write(self.vault / "회의록.md", age=2 * DAY)
        g = self.build()
        before = next(c for c in g["board"] if c["id"] == "obsidian:vault:회의록.md")
        self.tickets["ABC-77"] = {"summary": "동료 작업", "status": "진행 중", "updated": self.iso(600), "mine": False}
        write(self.vault / "회의록.md", "ABC-77 은 동료 티켓", age=2 * DAY)
        g = self.build()
        after = next(c for c in g["board"] if c["id"] == "obsidian:vault:회의록.md")
        self.assertTrue(self.linked("obsidian:vault:회의록.md", "jira:ABC-77"))
        self.assertEqual(self.nodes["jira:ABC-77"]["detail"]["unit"], "obsidian:vault:회의록.md")  # drawn with the note
        self.assertEqual((before["column"], before["tags"]), (after["column"], after["tags"]))

    def test_at_most_ten_body_keys_per_note(self):
        write(self.vault / "목록.md", " ".join(f"ABC-{i}" for i in range(100, 115)))
        self.build()
        made = sorted(int(i.split("-")[1]) for i in self.nodes if i.startswith("jira:ABC-1"))
        self.assertEqual(made, list(range(100, 110)))

    def test_a_pull_request_in_the_body_is_linked_and_made_when_missing(self):
        url = "https://github.com/o/tool/pull/7"
        write(self.vault / "리뷰 메모.md", f"{url} 확인")
        self.build()
        pr = self.nodes[f"pr:{url}"]
        self.assertEqual((pr["name"], pr["kind"], pr["profile"], pr["detail"]["kind_label"], pr["detail"]["unit"]),
                         ("tool #7", "source", "개인", "PR", "obsidian:vault:리뷰 메모.md"))
        self.assertTrue(self.linked("obsidian:vault:리뷰 메모.md", f"pr:{url}"))

    def test_an_existing_pull_request_node_is_left_as_it_is(self):
        url = "https://github.com/o/tool/pull/7"
        self.sent = [{"url": url, "number": 7, "title": "Add audit log", "updatedAt": self.iso(600),
                      "repository": {"name": "tool"}, "author": {"login": "me"}, "isDraft": False, "reviewDecision": "",
                      "latestReviews": {"nodes": []}, "reviewRequests": {"nodes": []}}]
        def facts():  # everything but what model.think() computes from the node's neighbours
            n = self.nodes[f"pr:{url}"]
            kept = ("kind_label", "text", "url", "unit", "review_state", "reviewers", "reviewed", "updated", "repo")
            return (n["name"], n["kind"], n["profile"], "sent" in n["roles"], {k: n["detail"][k] for k in kept})

        self.build()
        before = facts()
        write(self.vault / "리뷰 메모.md", url)
        self.build()
        self.assertEqual(before, facts())
        self.assertEqual(self.nodes[f"pr:{url}"]["detail"]["unit"], "sent")
        self.assertTrue(self.linked("obsidian:vault:리뷰 메모.md", f"pr:{url}"))

    def test_notes_that_link_each_other_are_joined_when_both_are_in_the_period(self):
        write(self.vault / "가.md", "[[나|표시]] [[폴더/다#제목]] [[옛날]]")
        write(self.vault / "나.md")
        write(self.vault / "폴더" / "다.md")
        write(self.vault / "옛날.md", age=10 * DAY)
        self.build(7)
        self.assertTrue(self.linked("obsidian:vault:가.md", "obsidian:vault:나.md"))
        self.assertTrue(self.linked("obsidian:vault:가.md", "obsidian:vault:폴더/다.md"))
        self.assertNotIn("obsidian:vault:옛날.md", self.nodes)

    def test_a_tag_two_notes_share_becomes_one_node_and_a_lone_tag_does_not(self):
        write(self.vault / "가.md", "#설계 #혼자")
        write(self.vault / "나.md", "---\ntags: [설계]\n---\n")
        self.build()
        tag = self.nodes["obsidian-tag:vault:설계"]
        self.assertEqual((tag["name"], tag["kind"], tag["profile"], tag["detail"]["kind_label"], tag["detail"]["unit"]),
                         ("#설계", "concept", "개인", "Obsidian 태그", "obsidian:vault:가.md"))
        self.assertTrue(self.linked("obsidian:vault:가.md", tag["id"]) and self.linked("obsidian:vault:나.md", tag["id"]))
        self.assertNotIn("obsidian-tag:vault:혼자", self.nodes)

    def test_each_vault_gives_its_profile_and_same_folder_names_stay_apart(self):
        other = self.root / "work" / "vault"
        self.vaults.append({"name": "work", "path": str(other), "profile": "회사"})
        write(self.vault / "메모.md", "#공통")
        write(other / "메모.md", "#공통 [[메모]]")
        self.build()
        self.assertEqual((self.nodes["obsidian:vault:메모.md"]["profile"], self.nodes["obsidian:work:메모.md"]["profile"]),
                         ("개인", "회사"))
        self.assertFalse(self.linked("obsidian:vault:메모.md", "obsidian:work:메모.md"))  # links stay inside a vault
        self.assertFalse(any(i.startswith("obsidian-tag:") for i in self.nodes))          # and so do tags

    def test_hidden_folders_and_the_card_folder_make_no_nodes(self):
        write(self.vault / ".obsidian" / "a.md")
        write(self.vault / ".trash" / "b.md")
        write(self.vault / "cards" / "c.md")
        self.build()
        self.assertFalse(any(i.startswith("obsidian:") for i in self.nodes))

    def test_no_sentence_of_the_body_reaches_the_graph(self):
        write(self.vault / "비밀.md", "이 문장은 밖으로 나가면 안 됩니다 #설계 ABC-9 [[다른]]")
        dumped = json.dumps(self.build(), ensure_ascii=False)
        self.assertNotIn("나가면 안 됩니다", dumped)
        self.assertNotIn("이 문장은", dumped)


if __name__ == "__main__":
    unittest.main()
