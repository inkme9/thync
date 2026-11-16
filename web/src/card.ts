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
