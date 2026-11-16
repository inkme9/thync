// Hand a PR off to a Concord workflow (docs/design/review-handoff.md). Every entry point opens this confirmation: a
// review-list row's 맡기기 button or the row dropped on the spine, a PR node dropped on the spine, a card dropped on
// "맡긴 일". The machine that runs the work checks the clone, account and author and shows them here; starting
// checks again there and refuses if anything changed.
import { post } from "./api.ts";
import { claimModal, closeDetail, detailBox, modalTitle } from "./detail.ts";
import { apply } from "./main.ts";
import { renderer } from "./renderer.ts";
import { state } from "./state.ts";
import { ROLE_COLOR, cut } from "./style.ts";
import type { ApiNode } from "./types.ts";

interface Plan { url: string; slug: string; number: number; workflow: string; profile: string; login: string; author: string; cwd: string; host?: string }

const EFFECTS: Record<string, string> = {
  "concord:review-until-approve": "리뷰를 게시하고, 막는 지적이 없는 라운드에서 이 계정으로 승인해요. 작성자의 push 와 답글을 기다리며 라운드를 되풀이해요. 처음 한 번은 세션에 들어가(tmux attach) 이 PR 을 믿는다고 직접 써야 Claude 리뷰어가 돌아요.",
  "concord:pr-to-merge": "이 PR 에 달린 리뷰를 고쳐 커밋·push 하고 답글을 달아요. 자동 리뷰어 스레드를 resolve 하고, 리뷰어에게 다시 리뷰를 요청하고, 필요하면 PR 본문을 고쳐요. 기본 브랜치보다 뒤처지면 브랜치를 갱신하고, 실패한 필수 검사를 한 번 다시 돌리고, 병합 조건을 채우면 이 PR 하나만 병합해요. 병합 뒤에는 실패한 작업을 한 번 다시 돌리고 이 PR 이 가리키는 티켓의 상태를 바꾸고 댓글을 달아요. 시작을 누르면 이 PR 의 병합을 허락하는 거예요.",
};
const COMMON = "세션은 PR 저장소의 로컬 폴더에서 tmux 안의 Claude Code 로 auto 권한으로 돌아요. force push 와 기본 브랜치 직접 push 는 하지 않아요.";

const isPR = (n: ApiNode) => n.roles.includes("sent") || n.roles.includes("review");

/** PRs that can be handed off from a node or card: the PR itself, or the PRs inside its work unit. */
export function reflexTargets(id: string): ApiNode[] {
  const self = state.data.nodes.find((x) => x.id === id);
  if (self && isPR(self)) return [self];
  const unit = self && self.kind !== "workThread" ? id : String(self?.detail.unit ?? id);
  return state.data.nodes.filter((n) => isPR(n) && n.detail.unit === unit);
}

const p = (text: string) => Object.assign(document.createElement("p"), { textContent: text });
const button = (text: string, primary = false) =>
  Object.assign(document.createElement("button"), { textContent: text, className: primary ? "primary" : "" });
const radio = (name: string, value: string, label: string, checked: boolean) => {
  const l = document.createElement("label");
  l.append(Object.assign(document.createElement("input"), { type: "radio", name, value, checked }), ` ${label}`);
  return l;
};
const rows = (pairs: [string, string][]) => {
  const dl = Object.assign(document.createElement("dl"), { className: "plan" });
  for (const [k, v] of pairs) dl.append(Object.assign(document.createElement("dt"), { textContent: k }),
    Object.assign(document.createElement("dd"), { textContent: v }));
  return dl;
};

export function openReflex(id: string): void {
  const targets = reflexTargets(id);
  state.signature = ""; // put a dragged node back in its place
  apply();
  if (!state.data.handoff || !targets.length) {
    claimModal(state.data.handoff ? "맡길 PR 이 없어요" : "PR 을 맡길 수 없어요");
    const close = button("닫기");
    close.onclick = closeDetail;
    const row = Object.assign(document.createElement("div"), { className: "actions" });
    row.append(close);
    detailBox.replaceChildren(...(state.data.handoff
      ? [p("'맡긴 일'은 리뷰 요청(남의 PR)이나 내 PR 을 Concord 작업으로 Claude 에게 맡기는 칸이에요."),
        p("이 작업에는 리뷰를 기다리는 내 PR 이나 나에게 온 리뷰 요청이 없어요.")]
      : [p("이 서버에는 claude CLI 가 없거나 sources 에 claude_code 가 없어 PR 을 맡길 수 없어요.")]), row);
    return;
  }
  claimModal("");
  // With several PRs, pick which one to hand off.
  const pick = Object.assign(document.createElement("fieldset"), { className: "choice" });
  pick.append(Object.assign(document.createElement("legend"), { textContent: "맡길 PR" }),
    ...targets.map((n, i) => radio("reflex-target", n.id,
      `${n.name} · ${n.roles.includes("review") ? "리뷰 요청" : String(n.detail.review_state ?? "보낸 리뷰")} — ${cut(String(n.detail.text ?? ""), 48)}`, i === 0)));
  const body = document.createElement("div");
  const ok = button("시작", true), cancel = button("취소");
  const row = Object.assign(document.createElement("div"), { className: "actions" });
  row.append(cancel, ok);
  const selected = () => targets.find((n) => n.id === pick.querySelector<HTMLInputElement>("input:checked")?.value) ?? targets[0];
  let plan: Plan | null = null;
  let asked = 0;
  // Ask the machine that will run it what it would start; nothing starts until 시작.
  const check = async () => {
    const n = selected(), mine = ++asked;
    plan = null;
    ok.disabled = true;
    modalTitle.textContent = `${n.name} 맡기기`;
    body.replaceChildren(p("실행할 머신에서 저장소 폴더, 계정, PR 작성자를 확인하고 있어요…"));
    const res = await post("/reflex", { id: n.id, check: true }).catch(() => null);
    const reply = res ? await res.json().catch(() => ({})) : {};
    if (mine !== asked || !detailBox.contains(body)) return; // another PR was picked, or this dialog was closed
    if (!res?.ok || !reply.plan) {
      body.replaceChildren(p(String(reply.message ?? `확인하지 못했어요 (${res?.status ?? "연결 안 됨"})`)), p("시작하지 않았어요."));
      return;
    }
    const pl: Plan = reply.plan;
    plan = pl;
    modalTitle.textContent = `${pl.slug}#${pl.number}에 ${pl.workflow} 를 시작할까요?`;
    body.replaceChildren(rows([
      ["저장소", pl.slug], ["PR", `#${pl.number} ${String(n.detail.text ?? "")} (작성자 ${pl.author})`], ["작업", pl.workflow],
      ["실행 계정", `${pl.login}${pl.profile ? ` · mien 프로필 ${pl.profile}` : " · mien 프로필 없음 (서버의 gh 계정)"}`],
      ["실행 위치", `${pl.host ? `${pl.host}:` : ""}${pl.cwd}`],
    ]), p(EFFECTS[pl.workflow] ?? ""), p(COMMON));
    ok.disabled = false;
  };
  pick.onchange = () => { void check(); };
  detailBox.replaceChildren(...(targets.length > 1 ? [pick] : []), body, row);
  void check();
  cancel.onclick = closeDetail;
  ok.onclick = async () => {
    if (!plan) return;
    ok.disabled = true;
    const n = selected();
    const res = await post("/reflex", { id: n.id, plan });
    const { message } = await res.json().catch(() => ({ message: `실패 (${res.status})` }));
    if (detailBox.contains(row)) detailBox.replaceChildren(p(message)); // only into this dialog, if still open
    if (res.ok) renderer.burst(n.id, ROLE_COLOR.running, 1500);
  };
}
