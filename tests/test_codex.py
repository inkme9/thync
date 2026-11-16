"""Codex session logs, current format (response_item role: user) and the older event_msg/user_message.

Shapes follow real logs from Codex 0.144.6 to 0.169.0-alpha.11.1 (issue #5). The decisions are in
docs/design/codex-prompts.md.
"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("THYNC_HOME", tempfile.mkdtemp())

from thync.sources.claude_code import _load_one  # noqa: E402
from thync.sources.codex import CodexSession  # noqa: E402

NOW = time.time()


def ts(seconds_ago: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(NOW - seconds_ago))


def meta(source="cli"):
    return {"type": "session_meta", "timestamp": ts(100), "payload": {"id": "c1", "cwd": "/w/repo", "source": source}}


def user(at, *blocks):
    """A response_item role: user message; a str block is input_text, None is an input_image."""
    content = [{"type": "input_text", "text": b} if isinstance(b, str) else {"type": "input_image", "image_url": "x"}
               for b in blocks]
    return {"type": "response_item", "timestamp": at, "payload": {"type": "message", "role": "user", "content": content}}


def legacy(at, text):
    return {"type": "event_msg", "timestamp": at, "payload": {"type": "user_message", "message": text}}


class CodexPrompts(unittest.TestCase):
    def write(self, lines) -> Path:
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("".join((x if isinstance(x, str) else json.dumps(x)) + "\n" for x in lines))
        self.addCleanup(os.unlink, f.name)
        return Path(f.name)

    def read(self, lines) -> CodexSession:
        s = CodexSession(self.write(lines))
        s.update()
        return s

    def test_typed_text_is_a_prompt_and_the_session_shows(self):  # criterion 1
        path = self.write([meta(), user(ts(50), "fix the  login bug")])
        item = _load_one(path, NOW, 7 * 86400, CodexSession)
        self.assertIsNotNone(item)
        s = item[0]
        self.assertEqual(s.prompts, [(ts(50), "fix the login bug")])
        self.assertEqual(list(s.events), [(ts(50), "prompt", "fix the login bug")])
        self.assertEqual(s.last_at, ts(50))

    def test_codex_context_is_not_a_prompt(self):  # criterion 2
        s = self.read([
            meta(),
            user(ts(60), "# AGENTS.md instructions for /w/repo\n\n<INSTRUCTIONS>\nbe nice\n</INSTRUCTIONS>",
                 "<environment_context>\n  <cwd>/w/repo</cwd>\n</environment_context>"),
            user(ts(55), '<codex_internal_context source="goal">\n<objective>x</objective>\n</codex_internal_context>'),
            user(ts(50), "fix it"),
            user(ts(45), "<send_user_message_question_reply>\nyes\n</send_user_message_question_reply>"),
        ])
        self.assertEqual(s.prompts, [(ts(50), "fix it")])
        self.assertEqual([e for e in s.events if e[1] == "prompt"], [(ts(50), "prompt", "fix it")])
        self.assertEqual((s.last_at, s.last_prompt), (ts(50), ("response_item", "fix it")))

    def test_text_after_my_request_with_an_image(self):  # criterion 3
        s = self.read([meta(), user(
            ts(50),
            "# Files mentioned by the user:\n\n## shot.png: /tmp/shot.png\n\n## My request:\nwhy is this  red?\n",
            "<image name=[Image #1] path=/tmp/shot.png>", None, "</image>")])
        self.assertEqual(s.prompts, [(ts(50), "why is this red?")])

    def test_only_input_text_blocks_are_read(self):
        line = user(ts(50), "fix")
        line["payload"]["content"].append({"type": "output_text", "text": "a reply"})
        self.assertEqual(self.read([meta(), line]).prompts, [(ts(50), "fix")])

    def test_in_app_browser_context_keeps_the_request(self):  # criterion 3, the other heading
        s = self.read([meta(), user(
            ts(50), "<in-app-browser-context>\n# In app browser:\n- tab\n## My request for Codex:\nclick it\n")])
        self.assertEqual(s.prompts, [(ts(50), "click it")])

    def test_legacy_user_message_only(self):  # criterion 4
        s = self.read([meta("vscode"), legacy(ts(50), "# Context from my IDE setup:\n## My request for Codex:\nfix\n")])
        self.assertEqual(s.prompts, [(ts(50), "fix")])

    def test_one_input_in_both_formats_counts_once(self):  # criterion 5, both orders
        for order in ("response_item first", "event_msg first"):
            pair = [user(ts(50), "<environment_context>x</environment_context>"), user(ts(50), "fix\n  it"),
                    legacy(ts(50), "fix\n  it")]
            if order == "event_msg first":
                pair = [pair[2], pair[0], pair[1]]
            s = self.read([meta(), *pair, user(ts(40), "next"), legacy(ts(40), "next")])
            self.assertEqual([p for _, p in s.prompts], ["fix it", "next"], order)

    def test_the_same_input_twice_in_both_formats_counts_twice(self):  # criterion 5 with criterion 6
        s = self.read([meta(), user(ts(50), "ok"), legacy(ts(50), "ok"), user(ts(40), "ok"), legacy(ts(40), "ok")])
        self.assertEqual(s.prompts, [(ts(50), "ok"), (ts(40), "ok")])

    def test_legacy_text_starting_with_a_tag_counts_once(self):  # only the event_msg copy is read
        s = self.read([meta(), user(ts(50), "<b>bold</b> it"), legacy(ts(50), "<b>bold</b> it")])
        self.assertEqual(s.prompts, [(ts(50), "<b>bold</b> it")])

    def test_both_formats_split_across_reads(self):  # criterion 5, the two lines arrive in different update() calls
        path = self.write([meta(), user(ts(50), "fix")])
        s = CodexSession(path)
        s.update()
        with path.open("a") as f:
            f.write(json.dumps(legacy(ts(50), "fix")) + "\n")
        s.update()
        self.assertEqual([p for _, p in s.prompts], ["fix"])

    def test_same_text_typed_twice_counts_twice(self):  # criterion 6
        s = self.read([meta(), user(ts(50), "ok"), user(ts(40), "ok")])
        self.assertEqual([p for _, p in s.prompts], ["ok", "ok"])
        s = self.read([meta(), legacy(ts(50), "ok"), legacy(ts(40), "ok")])
        self.assertEqual([p for _, p in s.prompts], ["ok", "ok"])

    def test_subagent_and_exec_are_not_sessions(self):  # criterion 7
        sub = self.write([meta({"subagent": {"thread_spawn": {"parent_thread_id": "p"}}}), user(ts(50), "do x")])
        self.assertIsNone(_load_one(sub, NOW, 7 * 86400, CodexSession))
        run = self.write([meta("exec"), user(ts(50), "do x")])
        self.assertIsNone(_load_one(run, NOW, 7 * 86400, CodexSession))
        handed = {"type": "session_meta", "timestamp": ts(100), "payload": {
            "id": "h", "cwd": "/w", "source": "vscode", "originator": "Claude Code"}}
        self.assertIsNone(_load_one(self.write([handed, user(ts(50), "do x")]), NOW, 7 * 86400, CodexSession))
        for source in ("cli", "vscode"):  # typed in the CLI, Codex Desktop or the IDE extension
            self.assertIsNotNone(_load_one(self.write([meta(source), user(ts(50), "do x")]), NOW, 7 * 86400,
                                           CodexSession), source)

    def test_a_fork_keeps_its_own_session_meta(self):  # criterion 7: a subagent log copies its parent's meta
        parent = {"type": "session_meta", "timestamp": ts(90), "payload": {
            "id": "parent", "cwd": "/w/other", "source": "vscode", "thread_source": "user"}}
        child = {"type": "session_meta", "timestamp": ts(90), "payload": {
            "id": "child", "cwd": "/w/repo", "source": {"subagent": {}}, "thread_source": "subagent",
            "forked_from_id": "parent"}}
        s = self.read([child, parent, user(ts(50), "copied parent prompt")])
        self.assertEqual((s.id, s.cwd, s.spawned, s.prompts), ("child", "/w/repo", True, []))
        s = self.read([meta(), parent, user(ts(50), "x")])  # a later meta does not replace the first one either
        self.assertEqual((s.id, s.cwd), ("c1", "/w/repo"))

    def test_a_malformed_first_meta_does_not_adopt_the_copied_parent(self):
        parent = {"type": "session_meta", "timestamp": ts(90), "payload": {"id": "parent", "source": "vscode"}}
        for payload in (["not", "a", "dict"], [], None, "text"):
            broken = {"type": "session_meta", "timestamp": ts(90), "payload": payload}
            s = self.read([broken, parent, user(ts(50), "copied parent prompt")])
            self.assertEqual((s.spawned, s.prompts), (True, []), payload)

    def test_thread_source_subagent_with_a_string_source(self):  # criterion 7
        path = self.write([{"type": "session_meta", "timestamp": ts(90), "payload": {
            "id": "c", "source": "vscode", "thread_source": "subagent"}}, user(ts(50), "do x")])
        self.assertIsNone(_load_one(path, NOW, 7 * 86400, CodexSession))

    def test_half_written_last_line_waits(self):  # criterion 8
        path = self.write([meta()])
        line = json.dumps(user(ts(50), "fix"))
        with path.open("a") as f:
            f.write(line[:20])
        s = CodexSession(path)
        s.update()
        self.assertEqual(s.prompts, [])
        with path.open("a") as f:
            f.write(line[20:] + "\n")
        s.update()
        self.assertEqual(s.prompts, [(ts(50), "fix")])

    def test_malformed_lines_are_skipped(self):  # criterion 8
        bad = {"type": "response_item", "timestamp": ts(55), "payload": {"type": "message", "role": "user"}}
        s = self.read([
            meta(),
            {**bad, "payload": {**bad["payload"], "content": "a string"}},
            {**bad, "payload": {**bad["payload"], "content": [{"type": "input_text"}]}},
            {**bad, "payload": {**bad["payload"], "content": ["not a dict", 3]}},
            {**bad, "payload": {**bad["payload"], "content": [{"type": "input_text", "text": None}]}},
            '{"type": "response_item", "pay',
            user(ts(50), "still read"),
        ])
        self.assertEqual([p for _, p in s.prompts], ["still read"])


if __name__ == "__main__":
    unittest.main()
