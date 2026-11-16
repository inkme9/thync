import assert from "node:assert/strict";
import { test } from "node:test";
import { toGraph } from "../src/layout.ts";
import { state } from "../src/state.ts";
import type { ApiNode } from "../src/types.ts";

const node = (id: string, unit: string, kind: ApiNode["kind"] = "workThread"): ApiNode =>
  ({ id, name: id, kind, profile: "회사", glow: 0, roles: [], status: "active", detail: { unit, live: true } });
const unit = (id: string) => ({ id, name: id, title: id, profile: "회사", sessions: 1, working: false, count: 1 });

test("a repository in common sits between the units it joins; other common nodes stay off", () => {
  state.data = { ...state.data, units: [unit("u1"), unit("u2")],
    nodes: [node("a", "u1"), node("b", "u2"), node("repo:shared", "common", "repo"), node("repo:alone", "common", "repo"),
      node("jira:X-1", "common", "project")],
    edges: [["a", "repo:shared"], ["b", "repo:shared"], ["a", "jira:X-1"]] };
  const g = toGraph();
  const [a, b, r] = ["a", "b", "repo:shared"].map((id) => g.idx[id]._3dLobe!);
  for (const k of ["x", "y", "z"] as const) assert.ok(Math.abs(r[k] - (a[k] + b[k]) / 2) <= 0.05 + 1e-9, k);
  assert.equal(g.idx["repo:alone"], undefined);  // nothing on the brain to join
  assert.equal(g.idx["jira:X-1"], undefined);    // not a repository
  assert.deepEqual(g.adj["repo:shared"].sort(), ["a", "b"]);
});
