// Which urls the detail panel turns into links, and how a click opens them.
//   "web": http(s). Opened in one browser tab per url (detail.ts, openOnce).
//   "app": obsidian://open?... of an Obsidian note. Left to the browser, which hands it to Obsidian; a tab
//          opened for it would stay blank, and clicking again would only bring that blank tab forward.
export type LinkKind = "web" | "app" | null;

export function linkKind(url: string): LinkKind {
  if (/^https?:\/\//.test(url)) return "web";
  if (/^obsidian:\/\/open\?/.test(url)) return "app";
  return null;
}
