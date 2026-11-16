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

test("with showLinked, nodes directly linked to in-progress work join the brain; others stay off", () => {
  const past = (id: string): ApiNode => ({ ...node(id, "u2"), detail: { unit: "u2", live: false } });
  state.data = { ...state.data, units: [unit("u1"), unit("u2")],
    nodes: [node("a", "u1"), past("b"), past("c")], edges: [["a", "b"], ["b", "c"]] };
  Object.assign(state, { showAll: false, showLinked: false });
  assert.deepEqual(Object.keys(toGraph().idx), ["a"]);
  state.showLinked = true;
  assert.deepEqual(Object.keys(toGraph().idx).sort(), ["a", "b"]);
  state.showAll = true;                                   // past work wins: everything in the period
  assert.deepEqual(Object.keys(toGraph().idx).sort(), ["a", "b", "c"]);
  Object.assign(state, { showAll: false, profile: "개인" }); // in-progress work of another tab links nothing in
  state.data.nodes[1] = { ...state.data.nodes[1], profile: "개인" };
  assert.deepEqual(Object.keys(toGraph().idx), []);
  state.profile = "전체";
});

test("hundreds of units in one tier all stay inside the brain", () => {
  const units = Array.from({ length: 300 }, (_, i) => unit(`u${i}`));
  const nodes = units.flatMap((u) => [node(`${u.id}:a`, u.id), node(`${u.id}:b`, u.id, "source")]);
  state.data = { ...state.data, units, nodes, edges: [] };
  Object.assign(state, { showAll: false, showLinked: false, profile: "전체" });
  const g = toGraph();
  assert.equal(g.nodes.length, 600);
  for (const n of g.nodes) {
    const p = n._3dLobe!;
    const e = Math.hypot(p.x / 0.95, p.y / (p.y < 0 ? 0.69 : 0.92), p.z / 1.25);
    assert.ok(e <= 0.97 + 1e-9, `${n.id} at ${e.toFixed(2)}`);
  }
  // units still get distinct places, not one pile on the edge
  const spots = new Set(g.nodes.map((n) => `${n._3dLobe!.x.toFixed(1)},${n._3dLobe!.y.toFixed(1)},${n._3dLobe!.z.toFixed(1)}`));
  assert.ok(spots.size > 150, `only ${spots.size} distinct places`);
});
