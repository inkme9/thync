// Entry point: build the graph, start the renderer, wire the controls, and poll the server every 2 s.
import { allLobesEnabled } from "../../vendor/brain-atlas/src/lobe-visibility.ts";
import { currentToken, fetchGraph, setToken } from "./api.ts";
import { renderBoard } from "./board.ts";
import { isModalOpen, openDetail } from "./detail.ts";
import { toGraph } from "./layout.ts";
import { reactToChanges, renderLive, resetLive } from "./live.ts";
import { NODE_DRAG, renderPanel, setStatus } from "./panel.ts";
import { openReflex } from "./reflex.ts";
import { renderer } from "./renderer.ts";
import { notableUnits, onBrain, recall, remember, shown, state } from "./state.ts";
import { colorOf, look } from "./style.ts";
import { checkWaiting } from "./waiting.ts";

/** Redraw everything from state.data. The graph is rebuilt only when its shape changed, so nodes keep their places. */
export function apply(): void {
  const d = state.data;
  // Which units carry a title is part of the shape: a unit gaining or losing focus re-lays the titles.
  const notable = notableUnits(shown());
  const sig = [state.profile, state.showAll, state.showLinked, state.days, d.units.map((u) => u.id).join(","), onBrain().map((n) => n.id).join(","), d.edges.map((e) => e.join(">")).join(","),
    [...notable].sort().join(",")].join("|");
  if (sig !== state.signature) {
    state.signature = sig;
    state.graph = toGraph();
  } else {
    for (const n of d.nodes) {
      const g = state.graph.idx[n.id];
      if (!g) continue;
      const l = look(n, notable);
      g.glow = l.glow;
      g.status = l.status;
      g.hub = l.hub;
      g.color = colorOf(n);
    }
  }
  renderPanel();
  checkWaiting();
  renderBoard();
  renderLive();
  reactToChanges();
}

const tokenForm = document.querySelector<HTMLFormElement>("#token-form")!;
tokenForm.addEventListener("submit", (e) => {
  e.preventDefault();
  setToken(tokenForm.token.value);
  tokenForm.reset();
  void poll();
});

export async function poll(): Promise<void> {
  try {
    const sent = currentToken();
    const data = await fetchGraph();
    if (sent !== currentToken()) return; // answered with a token replaced since; the newer request decides
    tokenForm.hidden = data !== null;
    if (!data) {
      setStatus(sent ? "토큰이 맞지 않아요 · 서버의 ~/.thync/token 내용을 넣으세요" : "토큰이 필요해요 · 서버의 ~/.thync/token 내용을 넣으세요");
      return;
    }
    state.data = data;
    apply();
  } catch {
    setStatus("서버에 연결할 수 없음");
  }
}

/** Rebuild from scratch after a change the server made (rename, card edit, ...). */
export async function refresh(): Promise<void> {
  state.signature = "";
  await poll();
}

let lastFocused: string | null = null;
/** Highlight a node on the brain. */
export function focus(id: string): void {
  renderer.setFocusForTest(id);
  lastFocused = id;
}

// --- Brain: a click highlights a node; clicking the highlighted node again opens its details. ---
const canvas = document.querySelector<HTMLCanvasElement>("#graph")!;
let pointerUpAt = -Infinity;
canvas.addEventListener("pointerup", () => { pointerUpAt = performance.now(); });
state.graph = toGraph();
// A node dropped on the spine goes back to its place before the dialog opens; other entry points move nothing.
renderer.onSpineDrop = (id) => { state.signature = ""; apply(); openReflex(id); };
// A review-list row dragged from the side panel onto the spine hands its PR off. While a row is dragged the brain
// stops turning and the spine lights up; near it (wider than a node drop, which also moves the node) it lights up
// more and takes the drop. Anywhere else the browser shows that nothing can be dropped.
const SPINE_DROP_PX = 70;
const brainBox = document.querySelector<HTMLDivElement>("#brain")!;
const spineMark = document.querySelector<SVGSVGElement>("#spine-target")!;
const drawSpine = () => {
  const line = renderer.spineLine();
  Object.assign(spineMark.style, { left: `${canvas.offsetLeft}px`, top: `${canvas.offsetTop}px`,
    width: `${canvas.clientWidth}px`, height: `${canvas.clientHeight}px` });
  spineMark.querySelector("polyline")!.setAttribute("points", line.map((p) => `${p.x},${p.y}`).join(" "));
  const mid = line[Math.floor(line.length / 2)], label = spineMark.querySelector("text")!; // beside it, inside the brain
  label.setAttribute("x", String(mid.x + 24));
  label.setAttribute("y", String(mid.y + 4));
};
const showSpine = (on: boolean) => {
  spineMark.classList.toggle("on", on);
  spineMark.classList.remove("over");
  renderer.setOptions({ idleAutoRotate: !on && recall("autoRotate") !== "0" });
  if (on) drawSpine();
};
document.addEventListener("dragstart", (e) => { if (e.dataTransfer?.types.includes(NODE_DRAG)) showSpine(true); });
document.addEventListener("dragend", () => { if (spineMark.classList.contains("on")) showSpine(false); });
brainBox.addEventListener("dragover", (e) => {
  if (!e.dataTransfer?.types.includes(NODE_DRAG)) return;
  drawSpine(); // the brain may still be easing to a stop
  const near = renderer.nearSpineAt(e, SPINE_DROP_PX);
  spineMark.classList.toggle("over", near);
  if (near) { e.preventDefault(); e.dataTransfer.dropEffect = "link"; }
});
brainBox.addEventListener("drop", (e) => {
  const id = e.dataTransfer?.getData(NODE_DRAG);
  if (!id || !renderer.nearSpineAt(e, SPINE_DROP_PX)) return;
  e.preventDefault();
  showSpine(false);
  openReflex(id);
});
renderer.start(canvas, () => state.graph, {
  idleAutoRotate: recall("autoRotate") !== "0",
  showLobeLabels: true,
  enabledLobes: allLobesEnabled(),
  onChange: () => {
    if (performance.now() - pointerUpAt > 50) return; // onChange also fires on hover; only react to a click
    const id = renderer.getFocusedNode()?.id ?? null;
    if (id !== lastFocused) { lastFocused = id; return; }
    if (id && !isModalOpen()) openDetail(id);
  }
});
renderer.setView({ zoom: 1.25 });

// --- Controls: profile tab, period, show past work, show what in-progress work links to. ---
for (const btn of document.querySelectorAll<HTMLButtonElement>("[data-profile]")) {
  btn.classList.toggle("on", btn.dataset.profile === state.profile);
  btn.onclick = () => {
    state.profile = btn.dataset.profile!;
    remember("profile", state.profile);
    document.querySelectorAll("[data-profile]").forEach((b) => b.classList.toggle("on", b === btn));
    resetLive();
    apply();
  };
}
for (const btn of document.querySelectorAll<HTMLButtonElement>("[data-days]")) {
  btn.classList.toggle("on", Number(btn.dataset.days) === state.days);
  btn.onclick = () => {
    state.days = Number(btn.dataset.days);
    remember("days", String(state.days));
    document.querySelectorAll("[data-days]").forEach((b) => b.classList.toggle("on", b === btn));
    resetLive();
    void poll();
  };
}
// Past work already includes everything linked, so the second box is greyed out while the first is on.
const showLinked = document.querySelector<HTMLInputElement>("#show-linked")!;
for (const key of ["showAll", "showLinked"] as const) {
  const box = document.querySelector<HTMLInputElement>(`#${key === "showAll" ? "show-all" : "show-linked"}`)!;
  box.checked = state[key];
  box.onchange = () => {
    state[key] = box.checked;
    remember(key, box.checked ? "1" : "0");
    showLinked.disabled = state.showAll;
    apply();
  };
}
showLinked.disabled = state.showAll;

// The brain turns slowly when left alone; this button stops or restarts that, and the browser remembers it.
const rotate = document.querySelector<HTMLButtonElement>("#rotate")!;
const showRotate = (on: boolean) => { rotate.textContent = on ? "⏸ 회전 멈춤" : "▶ 회전"; };
showRotate(recall("autoRotate") !== "0");
rotate.onclick = () => {
  const on = recall("autoRotate") === "0";
  remember("autoRotate", on ? "1" : "0");
  renderer.setOptions({ idleAutoRotate: on });
  showRotate(on);
};

void poll();
setInterval(poll, 2000);
