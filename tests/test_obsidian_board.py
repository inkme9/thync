"""One Obsidian Kanban board shown as read-only cards (docs/design/obsidian-board.md, issue #9)."""
import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

os.environ.setdefault("THYNC_HOME", tempfile.mkdtemp())

from thync import board, config, graph, mcp, server, store  # noqa: E402
from thync.sources import github, jira, obsidian  # noqa: E402
from thync.util import BOARD_CARD, is_kanban  # noqa: E402

TEMPLATE = """---
kanban-plugin: board
tags:
  - 칸반
---

## 1. 수집함

- [ ] 모으기

## 2. 다음 할 일

- [ ] **공구 구매** — 핀셋과 니퍼 → [[구매 가이드|가이드]]
  둘째 줄 [[참고 노트]]

## 3. 진행 중

- [ ] 조립

## 4. 대기 / 막힘

- [ ] 배송 대기

## 5. 확인 / 검수

- [ ] 검수

## 6. 완료

- [ ] 끝낸 일

%% kanban:settings
```
{"kanban-plugin":"board"}
```
%%
"""


def cards_of(text: str) -> list[dict]:
    return obsidian.parse_board(text, "개인", "/v/board.md", 1700000000)


def by_title(cards: list[dict]) -> dict[str, dict]:
    return {c["title"]: c for c in cards}


class Setting(unittest.TestCase):
    def test_a_path_or_an_object_with_a_profile(self):
        home = str(Path.home())
        self.assertEqual(config.parse_board("~/v/board.md"), {"path": f"{home}/v/board.md", "profile": "개인"})
        self.assertEqual(config.parse_board({"path": "/v/board.md", "profile": "회사"}), {"path": "/v/board.md", "profile": "회사"})
        self.assertEqual(config.parse_board({"path": "/v/board.md", "profile": "team"})["profile"], "개인")

    def test_nothing_or_an_unusable_value_is_no_board(self):
        with mock.patch("sys.stderr"):
            for raw in (None, "", [], 3, {"profile": "회사"}, "notes/board.md", {"path": "board.md"}, "~no-such-user-here/b.md"):
                self.assertIsNone(config.parse_board(raw), raw)


class Format(unittest.TestCase):
    def test_a_file_is_a_board_by_its_front_matter_key(self):
        self.assertTrue(is_kanban("---\nkanban-plugin: board\n---\n"))
        self.assertTrue(is_kanban("--- \r\ntags: [a]\r\nkanban-plugin: list\r\n---\r\n## 칸\r\n"))
        self.assertTrue(is_kanban("\ufeff---\nkanban-plugin: board\n---\n"))        # a byte order mark before it
        self.assertFalse(is_kanban("---\ntitle: kanban-plugin: board\n---\n"))
        self.assertFalse(is_kanban("kanban-plugin: board\n"))
        self.assertFalse(is_kanban("---\nid: x\n---\n\nkanban-plugin: board\n"))
        self.assertEqual(cards_of("## 칸\n\n- [ ] 카드\n"), [])

    def test_the_template_lanes_map_to_three_columns(self):
        got = {c["title"]: (c["column"], c["tags"][0]) for c in cards_of(TEMPLATE)}
        self.assertEqual(got, {"모으기": ("todo", "1. 수집함"), "공구 구매": ("todo", "2. 다음 할 일"),
                               "조립": ("doing", "3. 진행 중"), "배송 대기": ("doing", "4. 대기 / 막힘"),
                               "검수": ("doing", "5. 확인 / 검수"), "끝낸 일": ("done", "6. 완료")})

    def test_a_card_carries_its_text_and_says_it_is_read_only(self):
        card = by_title(cards_of(TEMPLATE))["공구 구매"]
        self.assertTrue(card["id"].startswith(BOARD_CARD))
        self.assertEqual(card["note"], "핀셋과 니퍼 → 가이드\n둘째 줄 참고 노트")
        self.assertEqual((card["readonly"], card["profile"], card["updated"], card["file"], card["tags"]),
                         (True, "개인", 1700000000, "/v/board.md", ["2. 다음 할 일", "Obsidian"]))
        self.assertEqual(card["url"], "obsidian://open?path=%2Fv%2Fboard.md")
        self.assertEqual((card["sub"], card["focus"], card["auto_column"], card["moved"]), ("", "", "todo", False))

    def test_cards_keep_the_order_of_the_board(self):
        self.assertEqual([c["title"] for c in cards_of(TEMPLATE)], ["모으기", "공구 구매", "조립", "배송 대기", "검수", "끝낸 일"])

    def test_lane_names_are_matched_by_whole_words(self):
        for lane, column in (("Ideas", "todo"), ("미완료", "todo"), ("Incomplete", "todo"), ("미확인", "todo"),
                             ("Done", "done"), ("In progress", "doing"), ("Waiting for review", "doing"),
                             ("완료됨", "done"), ("리뷰 (Review)", "doing"), ("Closed / 종료", "done")):
            (card,) = cards_of(f"---\nkanban-plugin: board\n---\n\n## {lane}\n\n- [ ] 카드\n")
            self.assertEqual(card["column"], column, lane)

    def test_a_lane_the_plugin_marks_complete_is_done_whatever_its_name(self):
        for marker in ("**Complete**", "**완료됨**"):
            text = f"---\nkanban-plugin: board\n---\n\n## Shipped\n\n{marker}\n\n- [ ] 카드\n\n## Later\n\n- [ ] **굵은 카드**\n"
            got = {c["title"]: c["column"] for c in cards_of(text)}
            self.assertEqual(got, {"카드": "done", "굵은 카드": "todo"}, marker)

    def test_a_checked_card_is_done_and_a_plain_bullet_is_a_card(self):
        text = "---\nkanban-plugin: board\n---\n\n## Ideas\n\n- [x] 체크함\n- [X] 대문자\n- 기호 없음\n- [/] 다른 표시\n- [ ] \n"
        self.assertEqual([(c["title"], c["column"]) for c in cards_of(text)],
                         [("체크함", "done"), ("대문자", "done"), ("기호 없음", "todo"), ("다른 표시", "todo")])

    def test_the_id_survives_a_check_and_a_move_and_tells_twins_apart(self):
        head = "---\nkanban-plugin: board\n---\n\n"
        (before,) = cards_of(head + "## Ideas\n\n- [ ] 같은 글 ^abc123\n")
        (after,) = cards_of(head + "## Done\n\n- [x] 같은   글\n")
        self.assertEqual(before["id"], after["id"])
        twins = cards_of(head + "## Ideas\n\n- [ ] 같은 글\n- [ ] 같은 글\n- [ ] 다른 글\n")
        self.assertEqual(len({c["id"] for c in twins}), 3)
        self.assertEqual(twins[1]["id"], twins[0]["id"] + "-2")
        self.assertEqual(before["title"], "같은 글")  # the block id is not shown

    def test_the_settings_block_and_the_archive_are_not_cards(self):
        text = ("---\nkanban-plugin: board\n---\n\n## Ideas\n\n- [ ] 남는 카드\n\n***\n\n## Archive\n\n- [ ] 보관한 카드\n\n"
                "%% kanban:settings\n```\n- [ ] 설정\n```\n%%\n")
        self.assertEqual([c["title"] for c in cards_of(text)], ["남는 카드"])
        self.assertEqual([c["title"] for c in cards_of(TEMPLATE.replace("%% kanban:settings", "%% kanban:settings\n- [ ] 설정"))][-1], "끝낸 일")

    def test_three_stars_inside_a_card_do_not_end_the_board(self):
        text = "---\nkanban-plugin: board\n---\n\n## Ideas\n\n- [ ] 첫 카드\n    위\n    ***\n    아래\n- [ ] 둘째 카드\n\n## Done\n\n- [ ] 셋째\n"
        cards = cards_of(text)
        self.assertEqual([c["title"] for c in cards], ["첫 카드", "둘째 카드", "셋째"])
        self.assertEqual(cards[0]["note"], "위\n***\n아래")

    def test_markup_is_taken_out_of_the_title_and_the_note(self):
        head = "---\nkanban-plugin: board\n---\n\n## Ideas\n\n"
        (card,) = cards_of(head + "- [ ] *기울임* ~~지움~~ ==강조== ![[그림.png]] 2*3\n  **굵게** [[노트]]\n")
        self.assertEqual((card["title"], card["note"]), ("기울임 지움 강조 그림.png 2*3", "굵게 노트"))
        for marks in ("** **", "****", "[[]]", "~~ ~~"):
            (blank,) = cards_of(head + f"- [ ] {marks}\n")
            self.assertEqual(blank["title"], "(제목 없음)", marks)

    def test_operators_are_not_taken_for_markup(self):
        head = "---\nkanban-plugin: board\n---\n\n## Ideas\n\n"
        for text in ("2*3*4 계산", "a==b==c", "if a==1 and b==2", "5 * 3 * 2", "_밑줄_ 그대로"):
            (card,) = cards_of(head + f"- [ ] {text}\n")
            self.assertEqual(card["title"], text)
        (card,) = cards_of(head + "- [ ] **굵은 글**을 고치고 *기울임* 과 ==강조== 확인\n")
        self.assertEqual((card["title"], card["note"]), ("굵은 글", "을 고치고 기울임 과 강조 확인"))

    def test_long_text_and_many_cards_are_cut(self):
        text = "---\nkanban-plugin: board\n---\n\n## Ideas\n\n" + "".join(f"- [ ] 카드 {i}\n" for i in range(250))
        self.assertEqual([c["title"] for c in cards_of(text)], [f"카드 {i}" for i in range(obsidian.MAX_BOARD_CARDS)])
        (long,) = cards_of("---\nkanban-plugin: board\n---\n\n## Ideas\n\n- [ ] " + "가" * 300 + "\n  " + "나" * 5000 + "\n")
        self.assertEqual((len(long["title"]), len(long["note"])), (200, 4000))


class BoardFile(unittest.TestCase):
    """Reading the configured file: what is read, what is kept, what is refused."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.path = self.root / "board.md"
        self.path.write_text(TEMPLATE, encoding="utf-8")
        for p in (mock.patch.object(obsidian, "OBSIDIAN_BOARD", {"path": str(self.path), "profile": "회사"}),
                  mock.patch.object(obsidian, "_board", (None, []))):
            p.start()
            self.addCleanup(p.stop)

    def titles(self):
        return [c["title"] for c in obsidian.board_cards()]

    def test_a_card_on_the_board_is_a_readonly_card(self):
        card = by_title(obsidian.board_cards())["공구 구매"]
        self.assertEqual((card["readonly"], card["profile"], card["file"]), (True, "회사", str(self.path)))
        self.assertEqual(card["updated"], int(self.path.stat().st_mtime))

    def test_no_setting_no_cards(self):
        with mock.patch.object(obsidian, "OBSIDIAN_BOARD", None):
            self.assertEqual(obsidian.board_cards(), [])

    def test_a_board_that_starts_with_a_byte_order_mark_is_read(self):
        self.path.write_text("\ufeff" + TEMPLATE, encoding="utf-8")
        self.assertEqual(len(self.titles()), 6)
        self.assertEqual(len(cards_of("\ufeff" + TEMPLATE)), 6)

    def test_an_unchanged_board_is_not_read_again(self):
        self.assertEqual(len(self.titles()), 6)
        with mock.patch.object(obsidian.os, "open", side_effect=AssertionError("opened again")):
            self.assertEqual(len(self.titles()), 6)

    def test_an_edited_board_shows_on_the_next_request(self):
        self.titles()
        self.path.write_text(TEMPLATE.replace("- [ ] 조립", "- [ ] 조립\n- [ ] 도색"), encoding="utf-8")
        self.assertIn("도색", self.titles())

    def test_the_kept_cards_are_not_shared_with_the_caller(self):
        first = obsidian.board_cards()
        first[0]["title"] = "바꿈"
        first[0]["tags"].append("바꿈")
        again = obsidian.board_cards()[0]
        self.assertEqual((again["title"], again["tags"]), ("모으기", ["1. 수집함", "Obsidian"]))

    def test_a_board_that_vanished_or_is_not_a_regular_file_gives_no_cards(self):
        self.assertEqual(len(self.titles()), 6)
        real = self.root / "real.md"
        self.path.rename(real)
        self.assertEqual(self.titles(), [])          # vanished
        os.symlink(real, self.path)
        self.assertEqual(self.titles(), [])          # a symlink is not followed
        self.path.unlink()
        self.path.mkdir()
        self.assertEqual(self.titles(), [])          # a folder
        self.path.rmdir()
        os.mkfifo(self.path)
        done = []
        t = threading.Thread(target=lambda: done.append(self.titles()), daemon=True)
        t.start()
        t.join(3)
        self.assertEqual(done, [[]])                  # a FIFO does not hold the request
        self.path.unlink()
        real.rename(self.path)
        self.assertEqual(len(self.titles()), 6)      # and the board comes back

    def test_a_file_swapped_for_a_fifo_after_the_listing_does_not_block(self):
        st = os.lstat(self.path)
        self.path.unlink()
        os.mkfifo(self.path)
        done = []
        t = threading.Thread(target=lambda: done.append(obsidian._read_board(obsidian.OBSIDIAN_BOARD, st)), daemon=True)
        t.start()
        t.join(3)
        self.assertEqual(done, [None])

    def test_a_file_swapped_for_a_link_or_a_folder_after_the_listing_is_not_read(self):
        st = os.lstat(self.path)
        real = self.root / "real.md"
        self.path.rename(real)
        os.symlink(real, self.path)                   # O_NOFOLLOW: the link is not followed to a real board
        self.assertIsNone(obsidian._read_board(obsidian.OBSIDIAN_BOARD, st))
        self.path.unlink()
        self.path.mkdir()                             # opens, but is no regular file
        self.assertIsNone(obsidian._read_board(obsidian.OBSIDIAN_BOARD, st))

    def test_a_read_that_failed_is_tried_again_on_the_next_request(self):
        real_open = os.open
        with mock.patch.object(obsidian.os, "open", side_effect=OSError(24, "Too many open files")):
            self.assertEqual(self.titles(), [])
        self.assertEqual(len(self.titles()), 6)       # the file did not change; the failure was not kept
        with mock.patch.object(obsidian.os, "fdopen", side_effect=OSError(5, "Input/output error")), \
                mock.patch.object(obsidian, "_board", (None, [])):
            self.assertEqual(self.titles(), [])       # an error while reading gives no cards and does not escape
        self.assertEqual(real_open, os.open)

    def test_a_file_that_is_no_board_gives_no_cards(self):
        self.titles()
        self.path.write_text(TEMPLATE.replace("kanban-plugin: board\n", ""), encoding="utf-8")
        self.assertEqual(self.titles(), [])
        self.path.write_bytes(TEMPLATE.encode("utf-8") + b"\n- [ ] \xff\xfe\n")
        self.assertEqual(self.titles(), [])
        with mock.patch.object(obsidian.os, "open", side_effect=AssertionError("read again")):
            self.assertEqual(self.titles(), [])      # "not a board" is kept too

    def test_only_whole_lines_of_the_first_256kb_are_read(self):
        head = "---\nkanban-plugin: board\n---\n\n## Ideas\n\n- [ ] 앞 카드\n"
        fill = "- [ ] " + "x" * 90 + "\n"
        body = fill * ((obsidian.READ_BYTES - len(head.encode())) // len(fill))
        tail = "- [ ] 잘리는 카드 " + "y" * 200 + "\n- [ ] 뒤 카드\n"
        self.path.write_text(head + body + tail, encoding="utf-8")
        with mock.patch.object(obsidian, "MAX_BOARD_CARDS", 10 ** 6):
            titles = self.titles()
        self.assertEqual(titles[0], "앞 카드")
        self.assertFalse(any(t.startswith("잘리는") or t == "뒤 카드" for t in titles))
        self.assertTrue(all(t == "x" * 90 for t in titles[1:]))

    def test_the_board_file_is_not_scanned_as_a_note(self):
        vault = self.root / "vault"
        (vault / "area").mkdir(parents=True)
        inside = vault / "area" / "칸반.md"
        inside.write_text(TEMPLATE, encoding="utf-8")
        (vault / "note.md").write_text("#tag", encoding="utf-8")
        link = self.root / "vault-link"
        os.symlink(vault, link)                       # the vault is configured through a symlink
        vaults = [{"name": "v", "path": str(link), "profile": "개인"}]
        with mock.patch.multiple(obsidian, OBSIDIAN_VAULTS=vaults, CARDS_DIR=self.root / "cards", _notes=(0.0, []), _threaded=False):
            with mock.patch.object(obsidian, "OBSIDIAN_BOARD", None):
                self.assertEqual(sorted(n["rel"] for n in obsidian.scan(time.time())), ["area/칸반.md", "note.md"])
            with mock.patch.object(obsidian, "OBSIDIAN_BOARD", {"path": str(inside), "profile": "개인"}):
                self.assertEqual([n["rel"] for n in obsidian.scan(time.time())], ["note.md"])


class CardFolder(unittest.TestCase):
    """The card files in cards_dir next to a Kanban board (thync/store.py)."""

    def setUp(self):
        self.cards = Path(tempfile.mkdtemp()).resolve()
        for p in (mock.patch.object(store, "CARDS_DIR", self.cards), mock.patch("sys.stderr")):
            p.start()
            self.addCleanup(p.stop)

    def test_a_board_inside_the_card_folder_is_not_a_card_and_is_left_alone(self):
        board_file = self.cards / "board.md"
        board_file.write_text(TEMPLATE, encoding="utf-8")
        (self.cards / "mine.md").write_text("---\nid: manual:mine\ncolumn: doing\n---\n\n메모\n", encoding="utf-8")
        self.assertEqual(sorted(store.read_cards()), ["manual:mine"])
        store.save_card("manual:board", {"column": "doing"})   # what POST /board did to the board before
        self.assertEqual(board_file.read_text(encoding="utf-8"), TEMPLATE)
        self.assertEqual(sorted(store.read_cards()), ["manual:board", "manual:mine"])  # a card file of its own

    def test_a_board_that_starts_with_a_byte_order_mark_is_left_alone_too(self):
        board_file = store.card_path("jira:ABC-9")
        board_file.write_text("\ufeff" + TEMPLATE, encoding="utf-8")
        self.assertEqual(store.read_cards(), {})
        with self.assertRaises(ValueError):
            store.save_card("jira:ABC-9", {"column": "doing"})
        self.assertEqual(board_file.read_text(encoding="utf-8"), "\ufeff" + TEMPLATE)

    def test_each_card_file_is_read_once_a_request(self):
        for i in range(3):
            (self.cards / f"c{i}.md").write_text(f"---\nid: manual:c{i}\n---\n", encoding="utf-8")
        with mock.patch.object(Path, "read_text", autospec=True, side_effect=Path.read_text) as read:
            self.assertEqual(len(store.read_cards()), 3)
        self.assertEqual(read.call_count, 3)

    def test_saving_a_card_never_overwrites_a_kanban_file(self):
        target = store.card_path("jira:ABC-1")
        target.write_text(TEMPLATE, encoding="utf-8")
        for change in ({"column": "done"}, {"note": "메모"}, {"reset": True}, {"title": ""}):
            with self.assertRaises(ValueError):
                store.save_card("jira:ABC-1", change)
        self.assertEqual(target.read_text(encoding="utf-8"), TEMPLATE)

    def test_a_card_file_that_is_not_utf8_is_skipped(self):
        (self.cards / "bad.md").write_bytes(b"---\nid: manual:bad\n---\n\xff\xfe\n")
        (self.cards / "good.md").write_text("---\nid: manual:good\n---\n", encoding="utf-8")
        self.assertEqual(sorted(store.read_cards()), ["manual:good"])

    def test_saving_a_card_never_overwrites_a_file_it_could_not_read(self):
        # A card file thync wrote, saved again by an editor in another encoding: it is skipped, not replaced.
        target = store.card_path("jira:ABC-1")
        raw = "---\nid: jira:ABC-1\n---\n\n메모\n".encode("cp949")
        target.write_bytes(raw)
        self.assertEqual(store.read_cards(), {})
        for change in ({"column": "done"}, {"note": "새 메모"}, {"reset": True}):
            with self.assertRaises(ValueError):
                store.save_card("jira:ABC-1", change)
        self.assertEqual(target.read_bytes(), raw)


class Kanban(unittest.TestCase):
    """build_graph() and the write requests, with a board on disk."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.path = self.root / "board.md"
        self.path.write_text(TEMPLATE, encoding="utf-8")
        self.cards = self.root / "cards"
        self.cards.mkdir()
        self.board = {"path": str(self.path), "profile": "개인"}

    def build(self, sources=("obsidian",)) -> dict:
        with mock.patch.multiple(graph, SESSIONS=False, SOURCES=set(sources)), \
                mock.patch.object(board, "SOURCES", set(sources)), \
                mock.patch.multiple(obsidian, OBSIDIAN_BOARD=self.board, OBSIDIAN_VAULTS=[], _board=(None, []), _notes=(0.0, [])), \
                mock.patch.object(store, "CARDS_DIR", self.cards), mock.patch("sys.stderr"), \
                mock.patch.object(github, "my_commits", return_value=[]), \
                mock.patch.object(github, "review_requests", return_value=[]), \
                mock.patch.object(github, "sent_reviews", return_value=[]), \
                mock.patch.object(jira, "read_json", return_value={}):
            return graph.build_graph(7)

    def test_the_board_cards_are_on_the_kanban(self):
        cards = by_title(self.build()["board"])
        self.assertEqual({t: c["column"] for t, c in cards.items()},
                         {"모으기": "todo", "공구 구매": "todo", "조립": "doing", "배송 대기": "doing", "검수": "doing", "끝낸 일": "done"})
        self.assertTrue(all(c["readonly"] and c["id"].startswith(BOARD_CARD) for c in cards.values()))
        self.assertIn("핀셋", cards["공구 구매"]["note"])

    def test_without_the_setting_the_source_or_a_usable_file_the_kanban_is_as_before(self):
        self.board = None
        plain = self.build()["board"]
        self.board = {"path": str(self.path), "profile": "개인"}
        self.assertEqual(self.build(sources=("github",))["board"], plain)       # "obsidian" is not a source
        for unusable in (self.root / "missing.md", self.root):
            self.board = {"path": str(unusable), "profile": "개인"}
            self.assertEqual(self.build()["board"], plain)
        self.path.write_text("## 칸\n\n- [ ] 카드\n", encoding="utf-8")            # no kanban-plugin key
        self.board = {"path": str(self.path), "profile": "개인"}
        self.assertEqual(self.build()["board"], plain)

    def test_a_card_file_cannot_change_a_board_card(self):
        before = by_title(self.build()["board"])["조립"]
        (self.cards / "x.md").write_text(f"---\nid: {before['id']}\ntitle: 바꾼 제목\ncolumn: done\n---\n\n바꾼 메모\n", encoding="utf-8")
        cards = self.build()["board"]
        after = by_title(cards)["조립"]
        self.assertEqual((after["column"], after["note"], after["moved"]), (before["column"], before["note"], False))
        self.assertEqual(sum(c["id"] == before["id"] for c in cards), 1)
        self.assertNotIn("바꾼 제목", by_title(cards))

    def test_a_board_inside_the_card_folder_shows_its_cards_once(self):
        inside = self.cards / "board.md"
        inside.write_text(TEMPLATE, encoding="utf-8")
        self.board = {"path": str(inside), "profile": "개인"}
        cards = self.build()["board"]
        self.assertEqual(len(cards), 6)
        self.assertFalse(any(c["id"].startswith("manual:") for c in cards))

    def test_the_summary_for_agents_leaves_the_card_text_out(self):
        text = mcp.summarize(self.build())
        for words in ("공구 구매", "핀셋", "조립", "끝낸 일"):
            self.assertNotIn(words, text)


class WriteRequests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp()).resolve()
        self.cards = self.home / "cards"
        self.names = self.home / "names.json"
        for p in (mock.patch.object(store, "CARDS_DIR", self.cards), mock.patch.object(store, "NAMES_FILE", self.names),
                  mock.patch.object(server, "TOKEN", "t")):
            p.start()
            self.addCleanup(p.stop)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def post(self, path: str, body: dict) -> int:
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
        conn.request("POST", path, json.dumps(body), {"X-Thync-Token": "t", "Content-Type": "application/json"})
        status = conn.getresponse().status
        conn.close()
        return status

    def test_the_board_api_refuses_a_board_card(self):
        cid = BOARD_CARD + "0123456789ab"
        for body in ({"column": "done"}, {"title": "새 제목"}, {"note": "메모"}, {"reset": True}):
            self.assertEqual(self.post("/board", {"id": cid, **body}), 400, body)
        self.assertFalse(self.cards.exists() and any(self.cards.iterdir()))
        self.assertEqual(self.post("/board", {"id": "jira:ABC-1", "column": "done"}), 204)   # other cards still save
        self.assertEqual(self.post("/board", {"new": True, "id": cid, "title": "직접 추가"}), 204)  # the id is not used
        self.assertFalse(any(c.startswith(BOARD_CARD) for c in store.read_cards()))

    def test_the_rename_api_refuses_a_board_card(self):
        self.assertEqual(self.post("/rename", {"id": BOARD_CARD + "0123456789ab", "name": "새 이름"}), 400)
        self.assertFalse(self.names.exists())
        self.assertEqual(self.post("/rename", {"id": "jira:ABC-1", "name": "새 이름"}), 204)


if __name__ == "__main__":
    unittest.main()
