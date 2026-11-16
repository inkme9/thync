import assert from "node:assert/strict";
import { test } from "node:test";
import { CEREBRUM, EDGE, inBrain, insideBrain, outlineRatio, toGraph } from "../src/layout.ts";
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
  for (const n of g.nodes) assert.ok(inBrain(n._3dLobe!), n.id);
  const onEdge = g.nodes.filter((n) => Math.abs(outlineRatio(n._3dLobe!, CEREBRUM) - EDGE) < 1e-6);
  assert.ok(onEdge.length < 60, `${onEdge.length} nodes pressed onto the edge`);
  const spots = new Set(g.nodes.map((n) => ["x", "y", "z"].map((k) => n._3dLobe![k as "x"].toFixed(2)).join()));
  assert.ok(spots.size > 500, `only ${spots.size} distinct places`);
});

test("my PRs waiting on review stay in the cerebellum, and the brain's first spots do not move", () => {
  const sent = (i: number): ApiNode => ({ ...node(`pr:${i}`, `s${i}`, "source"), roles: ["sent"] });
  const units = [...Array.from({ length: 6 }, (_, i) => unit(`s${i}`)), unit("f")];
  state.data = { ...state.data, units, nodes: [...Array.from({ length: 6 }, (_, i) => sent(i)),
    { ...node("f", "f"), roles: ["focus"], detail: { unit: "f", live: true, last_at: 1 } }], edges: [] };
  const g = toGraph();
  for (let i = 0; i < 6; i += 1) {
    const n = g.idx[`pr:${i}`];
    assert.equal(n._lobeName, "cerebellum", `pr:${i}`);
    assert.ok(inBrain(n._3dLobe!));
  }
  assert.deepEqual(insideBrain({ x: 0, y: 0.28, z: 0.82 }), { x: 0, y: 0.28, z: 0.82 });  // tier 0
  assert.deepEqual(insideBrain({ x: 0, y: -0.52, z: -0.8 }), { x: 0, y: -0.52, z: -0.8 }); // tier 4, lower cerebellum
});

test("a crowded cerebellum stays in the cerebellum", () => {
  const units = Array.from({ length: 60 }, (_, i) => unit(`w${i}`));
  state.data = { ...state.data, units, edges: [],
    nodes: units.map((u, i) => ({ ...node(`pr:w${i}`, u.id, "source"), roles: ["sent"] })) };
  const g = toGraph();
  const inCerebellum = g.nodes.filter((n) => n._lobeName === "cerebellum").length;
  assert.ok(inCerebellum >= 57, `${inCerebellum} of 60 in the cerebellum`);
  for (const n of g.nodes) assert.ok(inBrain(n._3dLobe!), n.id);
});
