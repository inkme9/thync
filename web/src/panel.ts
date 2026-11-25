// The side panel: what I focus on, background work, review requests, my PRs under review.
// Clicking an item highlights it on the brain; only its 상세 button opens the detail modal. A row of the three
// review lists is handed to Claude with its 맡기기 button or by dragging it onto the spine (reflex.ts).
import { openDetail } from "./detail.ts";
import { focus } from "./main.ts";
import { openReflex, runningHandoff } from "./reflex.ts";
import { isSource, shown, state } from "./state.ts";
import { REVIEW_STATE, ago, cut } from "./style.ts";
import type { ApiNode } from "./types.ts";

const focusList = document.querySelector<HTMLUListElement>("#focus")!;
const runningList = document.querySelector<HTMLUListElement>("#running")!;
const reviewList = document.querySelector<HTMLUListElement>("#reviews")!;
const sentList = document.querySelector<HTMLUListElement>("#sent")!;
const receivedList = document.querySelector<HTMLUListElement>("#received")!;
const statusLine = document.querySelector<HTMLSpanElement>("#status")!;

export function setStatus(text: string, title = ""): void {
  statusLine.textContent = text;
  statusLine.title = title;
}

export function infoButton(id: string): HTMLButtonElement {
  const btn = Object.assign(document.createElement("button"), { className: "info", textContent: "상세 →", title: "상세 보기" });
  btn.onclick = (e) => { e.stopPropagation(); focus(id); openDetail(id); };
  btn.ondblclick = (e) => e.stopPropagation();
  return btn;
}

/** The drag type a review-list row carries, so the board's own card drops ignore it. */
export const NODE_DRAG = "application/x-thync-node";
let dragging = false; // the 2 s refresh would replace the row being dragged

function item(n: ApiNode, main: string, sub: string, role: string, handoff = false, key = main): HTMLLIElement {
  const li = Object.assign(document.createElement("li"), { className: role });
  li.append(Object.assign(document.createElement("div"), { className: "main", textContent: main }),
    Object.assign(document.createElement("div"), { className: "sub", textContent: sub }), infoButton(n.id));
  li.onclick = () => focus(n.id);
  const running = handoff && state.data.handoff ? runningHandoff(n) : undefined;
  if (running) {
    // Handed off and still running: say so instead of offering it again.
    const tag = Object.assign(document.createElement("button"), { className: "info hand running", textContent: "맡김",
      title: `Claude 에게 맡긴 작업 · ${running.tags.join(" · ")}` });
    tag.setAttribute("aria-disabled", "true");
    tag.onclick = (e) => e.stopPropagation();
    tag.ondblclick = (e) => e.stopPropagation();
    li.append(tag);
  } else if (handoff && state.data.handoff) {
    const btn = Object.assign(document.createElement("button"), { className: "info hand", textContent: "맡기기",
      title: "Claude 에게 맡기기 (확인 창이 열려요)" });
    btn.onclick = (e) => { e.stopPropagation(); openReflex(n.id); };
    btn.ondblclick = (e) => e.stopPropagation();
    li.append(btn);
    li.draggable = true;
    li.ondragstart = (e) => { dragging = true; e.dataTransfer!.setData(NODE_DRAG, n.id); e.dataTransfer!.effectAllowed = "link"; };
    li.ondragend = () => { dragging = false; };
  }
  // The hand-off button keeps its key when 맡기기 turns into 맡김, so keyboard focus stays on it.
  li.querySelectorAll("button").forEach((b) => { b.dataset.key = `${role}|${n.id}|${key}|${b.classList.contains("hand") ? "hand" : b.textContent}`; });
  return li;
}

function list(ul: HTMLUListElement, items: HTMLLIElement[], empty: string): void {
  // Keep keyboard focus on the same button across the 2 s refresh (keys are set in item()).
  const had = ul.contains(document.activeElement) ? (document.activeElement as HTMLElement).dataset.key : undefined;
  ul.replaceChildren(...(items.length ? items : [Object.assign(document.createElement("li"), { className: "empty", textContent: empty })]));
  if (had) [...ul.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.dataset.key === had)?.focus();
}

const STATE_ORDER: Record<string, number> = { "수정 요청": 0, "승인됨": 1, "코멘트 있음": 2, "리뷰 대기": 3 };

export function renderPanel(): void {
  if (dragging) return;
  const visible = shown();
  const sources = visible.filter(isSource);
  // A session on another machine names it first.
  const sub = (n: ApiNode): string => `${n.detail.host ? `${String(n.detail.host)} · ` : ""}${n.profile} · ${String(n.detail.branch || n.detail.kind_label)} · ${ago(Number(n.detail.updated))}`;
  // A session says how much I prompted; a branch, ticket or page says what its last activity was.
  const what = (n: ApiNode): string => n.kind === "workThread"
    ? `${n.detail.waiting ? "선택 대기 · " : ""}${n.detail.claude_working ? `${n.detail.source === "codex" ? "Codex" : "Claude"} 작업 중 · ` : ""}최근 1시간 프롬프트 ${n.detail.focus_prompts}개 · `
    : n.detail.text && n.detail.text !== n.name ? `${cut(String(n.detail.text), 60)} · ` : "";

  const focused = sources.filter((n) => n.roles.includes("focus"))
    .sort((x, y) => Number(y.detail.last_at) - Number(x.detail.last_at));
  list(focusList, focused.map((n) => item(n, n.name, `${what(n)}${sub(n)}`, "focus")), "최근에 손댄 일 없음");

  const running = sources.filter((n) => n.roles.includes("running"))
    .sort((x, y) => Number(y.detail.updated) - Number(x.detail.updated));
  list(runningList, running.map((n) => item(n, n.name,
    `${Number(n.detail.subagents) ? `subagent ${n.detail.subagents}개 · ` : ""}${n.kind === "workThread" ? "" : `${String(n.detail.jira_status)} · `}${sub(n)}`, "running")),
    "백그라운드 작업 없음");

  const reviews = visible.filter((n) => n.roles.includes("review"))
    .sort((x, y) => String(y.detail.updated).localeCompare(String(x.detail.updated)));
  list(reviewList, reviews.map((n) => item(n, `${n.name} ${String(n.detail.text)}`,
    `${String(n.detail.author)} · ${ago(Date.parse(String(n.detail.updated)) / 1000)}`, "review", true)),
    "리뷰 요청 없음");

  const sent = visible.filter((n) => n.roles.includes("sent"))
    .sort((x, y) => (STATE_ORDER[String(x.detail.review_state)] ?? 9) - (STATE_ORDER[String(y.detail.review_state)] ?? 9)
      || String(y.detail.updated).localeCompare(String(x.detail.updated)));
  list(sentList, sent.map((n) => item(n, `${n.name} ${String(n.detail.text)}`,
    `${String(n.detail.review_state)}${n.detail.reviewers ? ` · 대기: ${n.detail.reviewers}` : ""} · ${ago(Date.parse(String(n.detail.updated)) / 1000)}`, "sent", true)),
    "리뷰를 기다리는 내 PR 없음");

  // Reviews others left on my PRs, newest first. Clicking shows the PR; its 상세 button opens the full reviews.
  const shownById = new Map(visible.map((n) => [n.id, n]));
  const received = (state.data.received ?? []).filter((r) => shownById.has(r.pr));
  list(receivedList, received.map((r) => {
    const n = shownById.get(r.pr)!;
    const li = item(n, `${r.author} · ${REVIEW_STATE[r.state] ?? r.state} — ${n.name}`,
      `${r.text ? cut(r.text.replace(/[#*`>_]+/g, "").replace(/\s+/g, " ").trim(), 90) : "줄 코멘트"} · ${ago(Date.parse(r.at) / 1000)}`, "received", true,
      `${r.author}|${r.at}`);
    if (r.state === "CHANGES_REQUESTED") li.classList.add("changes");
    return li;
  }), "받은 리뷰 없음");

  // Each remote: ✓ while its sessions come in, ✕ 접속 끊김 when it cannot be reached; the reason is in the tooltip.
  const hosts = state.data.hosts ?? [];
  const marks = hosts.map((h) => `${h.name} ${h.ok ? "✓" : "✕ 접속 끊김"}`);
  const reasons = hosts.filter((h) => !h.ok).map((h) => `${h.name}: ${h.error}${h.seen ? ` (마지막 수신 ${ago(h.seen)})` : ""}`);
  setStatus(`몰입 ${Math.round(state.data.focus_level * 100)}% · 작업공간 ${focused.length}/4 · 백그라운드 ${running.length} · 리뷰 ${reviews.length} · ${new Date(state.data.generated * 1000).toLocaleTimeString()}${marks.length ? ` · ${marks.join(" · ")}` : ""}`,
    reasons.join("\n"));
}
