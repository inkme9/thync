"""OpenAI Codex CLI session logs (~/.codex/sessions/YYYY/MM/DD/*.jsonl), read like Claude Code's.

Each line is {timestamp, type, payload}. The first line (session_meta) holds the id, cwd and git branch.
"""
import json
import re
from pathlib import Path

from .claude_code import ASK_TOOLS, Session

PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update) File: (.+)$", re.M)


MY_REQUEST = re.compile(r"^## My request[^\n]*\n", re.M)


def request(text: str, context: bool = False) -> str:
    """What the user typed in one block: after the last '## My request' heading, which Codex puts after the context
    it adds (IDE tabs, mentioned files, browser page). Without that heading, a block starting with '<' or '#' is
    context Codex added (<environment_context>, # AGENTS.md instructions, ...) when context=True."""
    parts = MY_REQUEST.split(text)
    if context and len(parts) == 1 and text.lstrip().startswith(("<", "#")):
        return ""
    return " ".join(parts[-1].split())


class CodexSession(Session):
    source = "codex"

    def __init__(self, path: Path):
        super().__init__(path)
        self.spawned: bool | None = None  # started by another Codex thread or program; set by the first session_meta
        self.last_prompt = ("", "")  # (line type, text) of the last prompt, to count one input logged both ways once

    def prompt(self, ts: str, kind: str, text: str) -> None:
        if not text:
            return
        # Older Codex wrote each input twice, as a response_item and as an event_msg. A second copy follows its twin
        # from the other line type; the same text from the same line type is a new input ('ok' typed twice).
        twin = self.last_prompt[0] not in ("", kind) and self.last_prompt[1] == text
        self.last_prompt = ("", "") if twin else (kind, text)
        if twin:
            return
        self.prompts.append((ts, text))
        self.events.append((ts, "prompt", text[:120]))
        self.last_at = max(self.last_at, ts)

    def _line(self, line: str) -> None:
        d = json.loads(line)
        ts, kind, p = d.get("timestamp", ""), d.get("type"), d.get("payload") or {}
        if kind == "session_meta":
            if self.spawned is None:  # a forked log copies its parent's session_meta after its own; keep the first
                self.spawned = True  # until this line proves otherwise: a malformed first meta hides the log
                if not isinstance(d.get("payload"), dict):
                    return
                # A subagent of another Codex thread (like a sidechain), or a run another program started and prompted:
                # `codex exec`, or a task Claude Code handed to Codex. The program's own session shows on its own.
                self.spawned = (isinstance(p.get("source"), dict) or p.get("thread_source") == "subagent"
                                or p.get("source") == "exec" or p.get("originator") == "Claude Code")
                self.id = p.get("id") or self.id
                self.cwd = p.get("cwd") or ""
                self.branch = (p.get("git") or {}).get("branch") or ""
                # The Codex app opens a thread it started; a thread from the CLI is left alone.
                self.app_url = f"codex://threads/{self.id}" if p.get("originator") == "Codex Desktop" else ""
            return
        if self.spawned:
            return
        t = p.get("type")
        if kind == "event_msg" and t == "user_message":
            self.prompt(ts, kind, request(p.get("message") or ""))
        elif kind == "response_item" and t == "message" and p.get("role") == "user":
            texts = (request(c.get("text") or "", context=True) for c in p.get("content") or []
                     if c.get("type") == "input_text")
            self.prompt(ts, kind, " ".join(filter(None, texts)))
        elif kind == "event_msg" and t == "agent_message":
            self.last_at = max(self.last_at, ts)
        elif kind == "event_msg" and t in ("task_started", "task_complete", "turn_aborted"):
            self.claude_turn = t == "task_started"
        elif kind == "event_msg" and t == "thread_name_updated":
            self.title = p.get("thread_name") or self.title
        elif kind == "response_item" and t in ("function_call_output", "custom_tool_call_output"):
            self.answered(p.get("call_id", ""))
        elif kind == "response_item" and t in ("function_call", "custom_tool_call"):
            self.last_at = max(self.last_at, ts)
            name, raw = p.get("name", ""), p.get("arguments") or p.get("input") or ""
            if name == "apply_patch":
                paths = PATCH_FILE.findall(raw)
                for fp in paths:
                    self.files[str(Path(self.cwd, fp.strip()))] = ts
                self.events.append((ts, "tool", f"apply_patch {', '.join(Path(f).name for f in paths)}"[:80]))
                return
            try:
                args = json.loads(raw) if t == "function_call" else {}
            except json.JSONDecodeError:
                args = {}
            if name in ASK_TOOLS:
                self.ask(p.get("call_id", ""), ts, name, args if isinstance(args, dict) else {})
            desc = next((str(args[k]) for k in ("cmd", "query", "path") if args.get(k)), "")
            self.events.append((ts, "tool", f"{name} {' '.join(desc.split())[:60]}".strip()))
