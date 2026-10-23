// Onboarding video links -> what the getting-started guide plays. No DOM and no imports, so tests can run it in node.
//
//   YouTube (watch, youtu.be, shorts, live, embed)  -> https://www.youtube-nocookie.com/embed/ID
//   Vimeo (vimeo.com/ID, unlisted hashes, player)   -> https://player.vimeo.com/video/ID
//   .mp4 / .webm / .ogg / .mov / .m4v, /static/...   -> a <video controls> file
//   any other https:// page                         -> a link that opens in a new tab

const YOUTUBE_HOSTS = new Set(["youtube.com", "youtu.be", "youtube-nocookie.com", "music.youtube.com"]);
const VIMEO_HOSTS = new Set(["vimeo.com", "player.vimeo.com"]);
const YOUTUBE_ID = /^[A-Za-z0-9_-]{11}$/;
const VIDEO_FILE = /\.(mp4|webm|ogg|ogv|mov|m4v)$/i;

/** "90", "90s", "1m30s", "1h2m3s" -> seconds (0 when unreadable). */
function seconds(value) {
  if (!value) return 0;
  if (/^\d+$/.test(value)) return Number(value);
  const match = /^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$/.exec(value);
  return match ? Number(match[1] || 0) * 3600 + Number(match[2] || 0) * 60 + Number(match[3] || 0) : 0;
}

function youtube(url, host, parts) {
  let id = null;
  if (host === "youtu.be") id = parts[0];
  else if (["embed", "shorts", "live", "v"].includes(parts[0])) id = parts[1];
  else id = url.searchParams.get("v");
  if (!id || !YOUTUBE_ID.test(id)) return null;
  const query = new URLSearchParams({ rel: "0" });
  const start = seconds(url.searchParams.get("t") || url.searchParams.get("start"));
  if (start) query.set("start", String(start));
  return { kind: "youtube", src: `https://www.youtube-nocookie.com/embed/${id}?${query}` };
}

function vimeo(url, parts) {
  const index = parts.findIndex((part) => /^\d+$/.test(part));
  if (index < 0) return null;
  const next = parts[index + 1];
  const hash = url.searchParams.get("h") || (next && /^[0-9a-f]{6,}$/i.test(next) ? next : "");
  return { kind: "vimeo", src: `https://player.vimeo.com/video/${parts[index]}${hash ? `?h=${encodeURIComponent(hash)}` : ""}` };
}

/** {kind: "youtube" | "vimeo" | "file" | "link" | "none", src}. */
export function videoEmbed(raw) {
  const text = String(raw || "").trim();
  if (!text) return { kind: "none", src: "" };
  if (text.startsWith("/static/")) return { kind: "file", src: text };
  let url;
  try {
    url = new URL(text);
  } catch {
    return { kind: "none", src: "" };
  }
  if (url.protocol !== "https:") return { kind: "none", src: "" };
  const host = url.hostname.toLowerCase().replace(/^(www|m)\./, "");
  const parts = url.pathname.split("/").filter(Boolean);
  const embed = YOUTUBE_HOSTS.has(host) ? youtube(url, host, parts) : VIMEO_HOSTS.has(host) ? vimeo(url, parts) : null;
  if (embed) return embed;
  if (VIDEO_FILE.test(url.pathname)) return { kind: "file", src: url.href };
  return { kind: "link", src: url.href };
}
