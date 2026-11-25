// What a kanban card allows, decided without the DOM so it can be tested (web/test/card.test.ts).
// A card read from the Obsidian Kanban board is read-only: it is moved and edited in Obsidian, not here.
import { linkKind } from "./link.ts";

export interface CardLike { readonly?: boolean; url?: string }

/** Whether the card can be dragged, renamed or given a note here. */
export const editable = (c: CardLike): boolean => !c.readonly;

/** Whether dropping the card on a column may do anything. The id of a drop comes from the page, so it is
 *  checked again here and not trusted because the card was not draggable. */
export const droppable = (c: CardLike | undefined): boolean => !!c && editable(c);

/** The url a click on the card hands to the browser, or null when a click works on the brain as usual.
 *  Only a read-only card opens its url, and only one Obsidian opens (link.ts). */
export const opens = (c: CardLike): string | null =>
  c.readonly && c.url && linkKind(c.url) === "app" ? c.url : null;

/** The GitHub url of a PR card (a review request, my PR, a hand-off), or null for any other card. */
export function prLink(c: { id: string }): string | null {
  const url = c.id.replace(/^(pr|reflex):/, "");
  return url !== c.id && linkKind(url) === "web" ? url : null;
}

interface NodeLike { id: string; kind: string; detail: Record<string, unknown> }

/** The node whose app button the card shows: its newest Obsidian note when the note is newer than the card's focus,
 *  else the focus. A note never becomes the focus while session logs are read (graph.py touch), so it is looked up. */
export function appNodeOf<N extends NodeLike>(c: { id: string; focus: string }, nodes: N[]): N | undefined {
  const at = (n: N) => Number(n.detail.last_at ?? n.detail.at ?? 0);
  const lead = nodes.find((n) => n.id === c.focus);
  const note = nodes.filter((n) => n.kind === "dailyNote" && n.detail.unit === c.id && String(n.detail.url ?? "").startsWith("obsidian:"))
    .reduce<N | undefined>((a, n) => (!a || at(n) > at(a) ? n : a), undefined);
  return note && (!lead || at(note) > at(lead)) ? note : lead;
}
