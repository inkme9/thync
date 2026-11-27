// Which urls the detail panel turns into links, and how a click opens them.
//   "web": http(s). Opened in one browser tab per url (detail.ts, openOnce).
//   "app": obsidian://open?... of an Obsidian note. Left to the browser, which hands it to Obsidian; a tab
//          opened for it would stay blank, and clicking again would only bring that blank tab forward.
//          The same goes for the link that opens a session in the Claude or Codex desktop app.
export type LinkKind = "web" | "app" | null;

const SESSION_LINK = /^(claude:\/\/claude\.ai\/epitaxy\/local_[A-Za-z0-9-]{1,64}|codex:\/\/threads\/[0-9a-f-]{1,64})$/;

/** The link that opens a session in its desktop app, when that app is next to the browser, which is where the link
 *  opens it. The app of the thync computer: on a page opened there (127.0.0.1). The app of another computer that runs
 *  the session here over SSH (`elsewhere`): on a page opened from another computer (over Tailscale). */
export function sessionLink(url: unknown, elsewhere = false, host = location.hostname): string | null {
  return typeof url === "string" && SESSION_LINK.test(url) && isHere(host) !== elsewhere ? url : null;
}

const WEB_SESSION_LINK = /^https:\/\/claude\.ai\/code\/session_[A-Za-z0-9]{1,64}$/;

/** The claude.ai page of a session Remote Control connects. It opens on any device: in the Claude app where one is
 *  installed, else in the browser. */
export const webSessionLink = (url: unknown): string | null =>
  typeof url === "string" && WEB_SESSION_LINK.test(url) ? url : null;

interface LinkNode { kind: string; detail: Record<string, unknown> }

/** Where a session or note opens: the desktop app it runs in or Obsidian, when that app is next to this browser; else
 *  the claude.ai page of a session Remote Control connects, which opens on a phone or tablet too. Null when nothing
 *  here can open it. `web`: the link is a claude.ai page, not an app's own link. */
export function appLink(n: LinkNode | undefined, host = location.hostname): { url: string; app: string; note: boolean; web: boolean } | null {
  const note = n?.kind === "dailyNote" ? noteLink(n.detail.url, host) : null;
  const app = note ?? sessionLink(n?.detail.app_url, Boolean(n?.detail.app_elsewhere), host);
  const url = app ?? webSessionLink(n?.detail.web_url);
  if (!url) return null;
  return { url, app: note ? "Obsidian" : url.startsWith("codex:") ? "Codex" : "Claude", note: !!note, web: !app };
}

const NOTE_LINK = /^obsidian:\/\/open\?path=[^\s&#]+$/;

/** The link that opens an Obsidian note in the Obsidian app. The note is a file on the thync computer, so only a page
 *  opened there gets it. */
export function noteLink(url: unknown, host = location.hostname): string | null {
  return typeof url === "string" && NOTE_LINK.test(url) && isHere(host) ? url : null;
}

const isHere = (host: string) => ["127.0.0.1", "localhost", "[::1]"].includes(host);

export function linkKind(url: string): LinkKind {
  if (/^https?:\/\//.test(url)) return "web";
  if (/^obsidian:\/\/open\?/.test(url) || SESSION_LINK.test(url)) return "app";
  return null;
}
