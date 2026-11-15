import assert from "node:assert/strict";
import { test } from "node:test";
import { linkKind } from "../src/link.ts";

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
