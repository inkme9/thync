import assert from "node:assert/strict";
import { test } from "node:test";
import { nearLine } from "../src/spine.ts";

const spine = [{ x: 100, y: 300 }, { x: 102, y: 320 }, { x: 104, y: 340 }];

test("a point within the radius of the spine is near it", () => {
  assert.equal(nearLine(spine, 160, 320, 70), true);
  assert.equal(nearLine(spine, 130, 320, 34), true);
});

test("a point outside the radius is not, and the node radius is the narrower one", () => {
  assert.equal(nearLine(spine, 180, 320, 70), false);
  assert.equal(nearLine(spine, 160, 320, 34), false);
  assert.equal(nearLine([], 100, 300, 70), false);
});
