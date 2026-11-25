import assert from "node:assert/strict";
import { test } from "node:test";
import { linkKind, sessionLink } from "../src/link.ts";

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
  assert.equal(sessionLink(claude, "127.0.0.1"), claude);
  assert.equal(sessionLink(codex, "localhost"), codex);
  assert.equal(sessionLink(claude, "mini.tail1234.ts.net"), null);
  for (const url of ["", undefined, "claude://code/new?q=x", "claude://resume?session=52d04f3e-af3f-4277-b3ee-366ce2e30eeb",
    "claude://claude.ai/epitaxy/local_x?q=y", "codex://threads/new?prompt=x"]) {
    assert.equal(sessionLink(url, "127.0.0.1"), null, String(url));
  }
});
