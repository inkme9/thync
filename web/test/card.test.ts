import assert from "node:assert/strict";
import { test } from "node:test";
import { appNodeOf, droppable, editable, opens, prLink } from "../src/card.ts";

const board = { readonly: true, url: "obsidian://open?path=%2Fv%2Fboard.md" };
const mine = {};

test("a card from the Obsidian board cannot be dragged, edited or dropped", () => {
  assert.equal(editable(board), false);
  assert.equal(droppable(board), false);
});

test("thync's own cards still can", () => {
  assert.equal(editable(mine), true);
  assert.equal(droppable(mine), true);
});

test("a drop that names no card does nothing", () => {
  assert.equal(droppable(undefined), false);
});

test("a click on a board card opens the board in Obsidian", () => {
  assert.equal(opens(board), "obsidian://open?path=%2Fv%2Fboard.md");
});

test("a click opens nothing else", () => {
  assert.equal(opens(mine), null);
  assert.equal(opens({ url: "obsidian://open?path=x" }), null);                 // not read-only: it works on the brain
  assert.equal(opens({ readonly: true }), null);
  assert.equal(opens({ readonly: true, url: "https://example.com/" }), null);    // a board card only opens Obsidian
  assert.equal(opens({ readonly: true, url: "javascript:alert(1)" }), null);
});

test("a PR card links to its PR on GitHub; other cards do not", () => {
  assert.equal(prLink({ id: "pr:https://github.com/o/r/pull/7" }), "https://github.com/o/r/pull/7");
  assert.equal(prLink({ id: "reflex:https://github.com/o/r/pull/7" }), "https://github.com/o/r/pull/7");
  for (const id of ["unit:x", "pr:javascript:alert(1)", "https://github.com/o/r/pull/7", "reflex:"]) assert.equal(prLink({ id }), null, id);
});

test("a card shows the app button of its focus, or of its newest Obsidian note when that is newer", () => {
  const session = { id: "session:s", kind: "workThread", detail: { unit: "u", last_at: 200 } };
  const note = (id: string, at: number) => ({ id, kind: "dailyNote", detail: { unit: "u", at, url: "obsidian://open?path=%2Fv%2Fn.md" } });
  const page = { id: "notion:p", kind: "dailyNote", detail: { unit: "u", at: 900, url: "https://notion.so/p" } };
  const card = { id: "u", focus: "session:s" };
  assert.equal(appNodeOf(card, [session, note("obsidian:a", 100)])?.id, "session:s");
  assert.equal(appNodeOf(card, [session, note("obsidian:a", 300), note("obsidian:b", 250), page])?.id, "obsidian:a");
  assert.equal(appNodeOf({ id: "u", focus: "jira:K-1" }, [{ id: "jira:K-1", kind: "project", detail: { unit: "u" } }, note("obsidian:a", 5)])?.id, "obsidian:a");
  assert.equal(appNodeOf({ id: "other", focus: "" }, [note("obsidian:a", 5)]), undefined);
});
