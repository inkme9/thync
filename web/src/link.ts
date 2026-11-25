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
  const here = ["127.0.0.1", "localhost", "[::1]"].includes(host);
  return typeof url === "string" && SESSION_LINK.test(url) && here !== elsewhere ? url : null;
}

export function linkKind(url: string): LinkKind {
  if (/^https?:\/\//.test(url)) return "web";
  if (/^obsidian:\/\/open\?/.test(url) || SESSION_LINK.test(url)) return "app";
  return null;
}
