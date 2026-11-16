// Where nodes sit in the brain. Work units are laid out front to back by how focused I am on them,
// following which areas are active during focused work (prefrontal, parietal) and at rest (posterior midline):
//   tier 0  forehead           top workspace session's unit
//   tier 1  upper front, sides other workspace units
//   tier 2  temples, rear top   in progress but less focused, review requests
//   tier 3  back of the head    background (Claude working, I am not)
//   tier 4  cerebellum          units holding only my PRs waiting on review
//   spine                       sessions handed off to Claude
import { CHAOS, KIND_LABEL } from "../../vendor/brain-atlas/src/palette.ts";
import { LOBE_CENTERS, spinePoint } from "../../vendor/brain-atlas/src/shape.ts";
import type { BrainEdge, BrainGraph, BrainNode, LobeName, Vec3 } from "../../vendor/brain-atlas/src/types.ts";
import { isSource, notableUnits, onBrain, shown, state } from "./state.ts";
import { ROLE_COLOR, ROLE_ORDER, colorOf, cut, look, palette, profileColor } from "./style.ts";
import type { ApiNode, ApiUnit } from "./types.ts";

const TIER_SPOTS: Vec3[][] = [
  [{ x: 0, y: 0.28, z: 0.82 }],
  [{ x: -0.46, y: 0.5, z: 0.36 }, { x: 0.46, y: 0.5, z: 0.36 }, { x: 0, y: 0.76, z: 0.05 }],
  [{ x: -0.66, y: -0.06, z: 0.12 }, { x: 0.66, y: -0.06, z: 0.12 }, { x: -0.46, y: 0.46, z: -0.42 }, { x: 0.46, y: 0.46, z: -0.42 },
    { x: -0.62, y: -0.22, z: -0.32 }, { x: 0.62, y: -0.22, z: -0.32 }],
  [{ x: 0, y: 0.12, z: -0.96 }, { x: -0.36, y: 0.16, z: -0.86 }, { x: 0.36, y: 0.16, z: -0.86 }, { x: 0, y: 0.46, z: -0.76 },
    { x: -0.3, y: -0.14, z: -0.86 }, { x: 0.3, y: -0.14, z: -0.86 }],
  [{ x: 0, y: -0.52, z: -0.8 }, { x: -0.3, y: -0.5, z: -0.72 }, { x: 0.3, y: -0.5, z: -0.72 },
    { x: -0.4, y: -0.32, z: -0.62 }, { x: 0.4, y: -0.32, z: -0.62 }, { x: 0, y: -0.3, z: -0.98 }]
];
const TIER_SPREAD = [0.2, 0.15, 0.12, 0.11, 0.1];          // front tiers spread wider
const TIER_NAME = ["", "", "", "백그라운드", "리뷰 기다리는 내 PR"];
const COLLECTION_UNITS = new Set(["sent", "review"]);      // collections of PRs: each PR gets its own spot

function hash(v: string): number {
  let h = 2166136261 >>> 0;
  for (let i = 0; i < v.length; i += 1) { h ^= v.charCodeAt(i); h = Math.imul(h, 16777619) >>> 0; }
  return h;
}

// Scatter around a spot deterministically by id, so a node keeps its place across refreshes.
function placeNear(id: string, at: Vec3, spread: number): Vec3 {
  const h = hash(id);
  const u = ((h & 0xffff) / 0xffff) * Math.PI * 2;
  const v = Math.acos(2 * (((h >>> 16) & 0xffff) / 0xffff) - 1);
  const r = spread * (0.35 + 0.65 * ((Math.imul(h, 31) >>> 0) % 1000) / 1000);
  return { x: at.x + r * Math.sin(v) * Math.cos(u), y: at.y + r * Math.sin(v) * Math.sin(u), z: at.z + r * Math.cos(v) };
}

function nearestLobe(p: Vec3): LobeName {
  let best: LobeName = "parietal", d = Infinity;
  for (const [name, c] of Object.entries(LOBE_CENTERS) as [LobeName, typeof LOBE_CENTERS[LobeName]][]) {
    const dd = (Math.abs(p.x) - Math.abs(c.c.x)) ** 2 + (p.y - c.c.y) ** 2 + (p.z - c.c.z) ** 2;
    if (dd < d) { d = dd; best = name; }
  }
  return best;
}

// The brain's outline as two ellipsoids from vendor shape.ts: the cerebrum (brainPoint; its lower half is squashed)
// and the cerebellum (cerebellumPoint). A node may sit in either; one outside both is pulled toward the center of
// the nearer one until it is within EDGE of it, which leaves room for the folds of the drawn surface.
export const CEREBRUM = { c: { x: 0, y: 0, z: 0 }, x: 0.95, up: 0.92, down: 0.69, z: 1.25 };
export const CEREBELLUM = { c: { x: 0, y: -0.55, z: -0.78 }, x: 0.40, up: 0.26, down: 0.26, z: 0.32 };
export const EDGE = 0.92; // the deepest fold dip of the drawn surface is about 0.93 of the outline
export function outlineRatio(p: Vec3, o: typeof CEREBRUM): number {
  const dy = p.y - o.c.y;
  return Math.hypot((p.x - o.c.x) / o.x, dy / (dy < 0 ? o.down : o.up), (p.z - o.c.z) / o.z);
}
export function insideBrain(p: Vec3): Vec3 {
  const [big, small] = [outlineRatio(p, CEREBRUM), outlineRatio(p, CEREBELLUM)];
  if (Math.min(big, small) <= EDGE) return p;
  const [o, e] = big <= small ? [CEREBRUM, big] : [CEREBELLUM, small];
  const k = EDGE / e;
  return { x: o.c.x + (p.x - o.c.x) * k, y: o.c.y + (p.y - o.c.y) * k, z: o.c.z + (p.z - o.c.z) * k };
}
export const inBrain = (p: Vec3): boolean => Math.min(outlineRatio(p, CEREBRUM), outlineRatio(p, CEREBELLUM)) <= EDGE + 1e-9;

// When a tier runs out of spots, later units fill the space around the same spots: each lap takes the next
// direction of a golden-angle spiral, a little farther out (cube root, so the volume fills evenly), up to
// OVERFLOW_MAX scaled to the tier's shape (the cerebellum for tier 4, else the cerebrum, by its smallest semi-axis).
// Past that, units share the same shell, so a tier with hundreds of units grows denser rather than spreading
// into the areas of other tiers.
const OVERFLOW_MAX = 0.3;
function tierSpot(tier: number, i: number): Vec3 {
  const spots = TIER_SPOTS[tier];
  const base = spots[i % spots.length];
  const lap = Math.floor(i / spots.length);
  if (!lap) return base;
  const shape = tier === 4 ? CEREBELLUM : CEREBRUM; // tier 4 is the cerebellum, though some of its spots sit just above it
  const r = Math.min(OVERFLOW_MAX * Math.min(shape.x, shape.up, shape.down, shape.z) / 0.69, 0.12 * Math.cbrt(lap));
  const theta = lap * 2.399963;
  const phi = Math.acos(1 - 2 * ((lap * 0.618034) % 1));
  let d = { x: Math.sin(phi) * Math.cos(theta), y: Math.cos(phi), z: Math.sin(phi) * Math.sin(theta) };
  // Spots sit near the surface, so a direction pointing away from the shape's center is mirrored to point into it.
  const n = { x: base.x - shape.c.x, y: base.y - shape.c.y, z: base.z - shape.c.z };
  const len = Math.hypot(n.x, n.y, n.z) || 1;
  const out = (d.x * n.x + d.y * n.y + d.z * n.z) / len;
  if (out > 0) d = { x: d.x - (2 * out * n.x) / len, y: d.y - (2 * out * n.y) / len, z: d.z - (2 * out * n.z) / len };
  return insideBrain({ x: base.x + r * d.x, y: base.y + r * d.y, z: base.z + r * d.z });
}

// Title color of a unit: its strongest role, else its profile.
export function unitColor(unit: string, ns: ApiNode[]): string {
  const inUnit = ns.filter((n) => n.detail.unit === unit);
  const roles = new Set(inUnit.flatMap((n) => n.roles));
  const role = ROLE_ORDER.find((r) => roles.has(r));
  return role ? ROLE_COLOR[role] : profileColor(state.data.units.find((u) => u.id === unit)?.profile ?? inUnit[0]?.profile ?? "");
}

export function toGraph(): BrainGraph {
  const keep = onBrain();
  const notable = notableUnits(shown()); // same set main.ts restyles with
  const present = new Set(keep.map((n) => String(n.detail.unit)));
  const units = state.data.units.filter((u) => present.has(u.id));
  const sessionsOf = (u: ApiUnit) => keep.filter((n) => isSource(n) && n.detail.unit === u.id && !n.detail.reflex);
  const lastPrompt = (u: ApiUnit) => Math.max(0, ...sessionsOf(u).map((n) => Number(n.detail.last_at) || 0));
  const tierOf = (u: ApiUnit): number => {
    const ss = sessionsOf(u);
    if (ss.some((n) => n.roles.includes("focus"))) return 1; // split into tiers 0 and 1 by recency below
    if (ss.some((n) => n.roles.includes("running"))) return 3;
    if (u.id === "sent" || (!ss.length && keep.filter((n) => n.detail.unit === u.id).every((n) => n.roles.includes("sent") || n.kind === "repo"))) return 4;
    return 2;
  };
  const tiers = new Map<string, number>();
  units.filter((u) => tierOf(u) === 1).sort((a, b) => lastPrompt(b) - lastPrompt(a))
    .forEach((u, i) => tiers.set(u.id, i === 0 ? 0 : 1));
  for (const u of units) if (!tiers.has(u.id)) tiers.set(u.id, tierOf(u));
  const anchorOf = new Map<string, Vec3>();
  const counter = [0, 0, 0, 0, 0];
  for (const u of units) { const t = tiers.get(u.id)!; anchorOf.set(u.id, tierSpot(t, counter[t]++)); }

  // Handed-off sessions line up along the spine, top to bottom.
  const reflexes = keep.filter((n) => n.kind === "workThread" && n.detail.reflex);
  const spineAt = new Map(reflexes.map((n, i) => [n.id, spinePoint(0.15 + (0.8 * i) / Math.max(1, reflexes.length))]));

  const at = new Map<string, Vec3>();
  for (const n of keep) {
    const unit = String(n.detail.unit);
    if (unit === "common") continue;
    const tier = tiers.get(unit) ?? 2;
    const hubLike = n.id === unit || isSource(n);
    const spread = TIER_SPREAD[tier] * (hubLike ? 0.45 : 1);
    const own = COLLECTION_UNITS.has(unit) ? tierSpot(tier, hash(n.id) % (TIER_SPOTS[tier].length * 2)) : undefined;
    at.set(n.id, spineAt.get(n.id) ?? insideBrain(placeNear(n.id, own ?? anchorOf.get(unit) ?? TIER_SPOTS[2][0], own ? 0.05 : spread)));
  }
  // A repository in "common" sits at the center of the work it joins, so a repository several units use
  // draws lines between them. One with nothing on the brain to join is left off.
  const near = new Map<string, Vec3[]>();
  for (const [a, b] of state.data.edges) {
    for (const [x, y] of [[a, b], [b, a]]) {
      const p = at.get(y);
      if (p) near.set(x, [...(near.get(x) ?? []), p]);
    }
  }
  for (const n of keep) {
    const ps = near.get(n.id);
    if (n.detail.unit !== "common" || !ps) continue;
    const mid = (k: "x" | "y" | "z") => ps.reduce((sum, p) => sum + p[k], 0) / ps.length;
    at.set(n.id, insideBrain(placeNear(n.id, { x: mid("x"), y: mid("y"), z: mid("z") }, 0.05)));
  }

  const nodes: BrainNode[] = keep.filter((n) => at.has(n.id)).map((n) => {
    const unit = String(n.detail.unit);
    const p = at.get(n.id)!;
    const l = look(n, notable);
    return {
      id: n.id, name: n.name, title: n.name, kind: n.kind,
      kindLabel: String(n.detail.kind_label ?? KIND_LABEL[n.kind]),
      status: l.status, hub: l.hub, degree: 0, color: colorOf(n),
      path: `${unit}/${n.name}`, classificationSource: "default", glow: l.glow,
      _3dLobe: p, _lobeName: nearestLobe(p)
    };
  });
  const idx: Record<string, BrainNode> = {};
  const adj: Record<string, string[]> = {};
  for (const n of nodes) { idx[n.id] = n; adj[n.id] = []; }
  // Every relation between nodes on the brain is a line; the renderer brightens lines near active work.
  const edges: BrainEdge[] = state.data.edges.filter(([a, b]) => idx[a] && idx[b]).map(([a, b]) => ({ a, b }));
  for (const e of edges) { adj[e.a].push(e.b); adj[e.b].push(e.a); }
  for (const n of nodes) n.degree = adj[n.id].length;

  // Only notable units get a title, front tiers first so a crowded overlay keeps the ones I am focused on.
  const titled = units.filter((u) => notable.has(u.id)).sort((a, b) => tiers.get(a.id)! - tiers.get(b.id)!);
  const clusterTitles = [
    ...titled.map((u) => {
      const t = tiers.get(u.id)!;
      const spot = anchorOf.get(u.id)!;
      // A unit named after its repository or branch is titled by what I asked last; the folder goes below.
      const byFolder = u.id.startsWith("repo:") || u.id.startsWith("branch:");
      const [main, rest] = byFolder && u.title ? [u.title, u.name] : [u.name, u.name !== u.title ? u.title : ""];
      return {
        title: cut(main, 24),
        sub: cut([TIER_NAME[t], rest || (u.sessions > 1 ? `세션 ${u.sessions}개` : "")].filter(Boolean).join(" · ")),
        at: { x: spot.x, y: spot.y + 0.2, z: spot.z },
        color: unitColor(u.id, keep)
      };
    }),
    ...(reflexes.length ? [{ title: `맡긴 일 ${reflexes.length}`, sub: cut(reflexes.map((n) => n.name).join(", ")),
      at: { ...spinePoint(0.5), x: 0.12 }, color: ROLE_COLOR.running }] : [])
  ];
  return { nodes, edges, idx, adj, KIND_LABEL, activePalette: palette, activePaletteName: "graphite", CHAOS, clusterTitles };
}
