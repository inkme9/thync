import assert from "node:assert/strict";
import { test } from "node:test";
import { droppable, editable, opens } from "../src/card.ts";

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
