import assert from "node:assert/strict";
import { test } from "node:test";
import { linkKind, noteLink, sessionLink, webSessionLink } from "../src/link.ts";

test("http and https urls open as web links", () => {
  assert.equal(linkKind("https://github.com/o/r/pull/7"), "web");
  assert.equal(linkKind("http://localhost:8765/"), "web");
});

test("the url of an Obsidian note is a link the app opens", () => {
  assert.equal(linkKind("obsidian://open?path=%2FUsers%2Fme%2Fvault%2Fnote.md"), "app");
});

test("anything else stays plain text", () => {
  for (const url of ["", "javascript:alert(1)", "file:///etc/passwd", "obsidian://new?name=x", "notes/obsidian://open?path=x"]) {
    assert.equal(linkKind(url), null, url);
  }
});

test("a session link opens its app only on the machine that runs it", () => {
  const claude = "claude://claude.ai/epitaxy/local_5e3ac12e-73cb-42ab-95a3-46b66077e992";
  const codex = "codex://threads/01a1a896-b8b6-7a73-a3b8-0cc8e38e7278";
  assert.equal(linkKind(claude), "app");
  assert.equal(sessionLink(claude, false, "127.0.0.1"), claude);
  assert.equal(sessionLink(codex, false, "localhost"), codex);
  assert.equal(sessionLink(claude, false, "mini.tail1234.ts.net"), null);
  // The app of the computer that runs the session over SSH is next to a browser on another computer.
  assert.equal(sessionLink(claude, true, "mini.tail1234.ts.net"), claude);
  assert.equal(sessionLink(claude, true, "127.0.0.1"), null);
  for (const url of ["", undefined, "claude://code/new?q=x", "claude://resume?session=52d04f3e-af3f-4277-b3ee-366ce2e30eeb",
    "claude://claude.ai/epitaxy/local_x?q=y", "codex://threads/new?prompt=x"]) {
    assert.equal(sessionLink(url, false, "127.0.0.1"), null, String(url));
    assert.equal(sessionLink(url, true, "mini.tail1234.ts.net"), null, String(url));
  }
});

test("an Obsidian note opens in Obsidian only from a page on the thync computer", () => {
  const note = "obsidian://open?path=%2FUsers%2Fme%2Fvault%2Fnote.md";
  assert.equal(noteLink(note, "127.0.0.1"), note);
  assert.equal(noteLink(note, "mini.tail1234.ts.net"), null);
  for (const url of ["", undefined, "obsidian://new?name=x", "obsidian://open?vault=v&file=x", "obsidian://open?path=a b"]) {
    assert.equal(noteLink(url, "127.0.0.1"), null, String(url));
  }
});

test("the claude.ai page of a Remote Control session opens from any device", () => {
  const url = "https://claude.ai/code/session_01MDtL5zEM1z7iYeaDfEx7vE";
  assert.equal(webSessionLink(url), url);
  for (const bad of ["", undefined, "https://claude.ai/code/session_x/../../logout", "https://evil.example/code/session_01",
    "http://claude.ai/code/session_01", "https://claude.ai/code/cse_01"]) {
    assert.equal(webSessionLink(bad), null, String(bad));
  }
});
