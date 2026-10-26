// Trading OS service worker: Web Push notifications, and an offline-capable app shell (v1.7).
//
// Caching is UI-only. The app shell (HTML, JS, CSS, icons) is cached so Trading OS opens offline or over a flaky
// connection; it never caches /api/ or /uploads/ (always the live server), so trade data is never stale. Static
// assets use stale-while-revalidate (fresh copy fetched in the background); navigations are network-first with
// the cached shell as the offline fallback.

const ICON = "/static/icons/icon-192.png";
const BADGE = "/static/icons/badge-72.png";
const API = "/api/notifications/webpush";

const CACHE = "trading-os-shell-v1";
const PRECACHE = ["/", "/static/js/main.js", "/static/css/app.css", "/static/manifest.webmanifest"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE);
      await cache.addAll(PRECACHE).catch(() => {}); // a missing asset must never block install
      self.skipWaiting();
    })(),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      for (const name of await caches.keys()) {
        if (name !== CACHE) await caches.delete(name); // drop older shell versions
      }
      await self.clients.claim();
    })(),
  );
});

function cacheable(url) {
  return url.origin === self.location.origin && (url.pathname === "/" || url.pathname.startsWith("/static/"));
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/uploads/") || url.pathname === "/sw.js") return;

  if (request.mode === "navigate") {
    // Network-first so an upgraded app is served fresh; the cached shell is the offline fallback.
    event.respondWith(
      (async () => {
        try {
          const response = await fetch(request);
          if (response.ok) (await caches.open(CACHE)).put("/", response.clone());
          return response;
        } catch {
          return (await caches.match("/")) || Response.error();
        }
      })(),
    );
    return;
  }

  if (!cacheable(url)) return;
  // Stale-while-revalidate: serve the cached asset at once, refresh it in the background.
  event.respondWith(
    (async () => {
      const cache = await caches.open(CACHE);
      const cached = await cache.match(request);
      const network = fetch(request)
        .then((response) => {
          if (response.ok) cache.put(request, response.clone());
          return response;
        })
        .catch(() => cached);
      return cached || network;
    })(),
  );
});

function readPayload(event) {
  if (!event.data) return {};
  try {
    return event.data.json();
  } catch {
    return { body: event.data.text() };
  }
}

self.addEventListener("push", (event) => {
  const data = readPayload(event);
  const high = data.priority === "high";
  const options = {
    body: data.body || "",
    icon: ICON,
    badge: BADGE,
    data: { url: data.url || "/" },
    requireInteraction: high,
    renotify: Boolean(data.tag),
  };
  if (data.tag) options.tag = data.tag;
  event.waitUntil(self.registration.showNotification(data.title || "Trading OS", options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL(event.notification.data?.url || "/", self.location.origin).href;
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      const open = windows.find((client) => new URL(client.url).origin === self.location.origin);
      if (open) {
        await open.focus();
        if (open.url === target) return;
        try {
          await open.navigate(target); // only works for windows this worker controls
          return;
        } catch {
          // fall through: open the page in a new window instead
        }
      }
      await self.clients.openWindow(target);
    })(),
  );
});

function base64UrlToBytes(text) {
  const padded = text.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (text.length % 4)) % 4);
  return Uint8Array.from(atob(padded), (char) => char.charCodeAt(0));
}

async function serverKey() {
  const response = await fetch(`${API}/key`, { headers: { Accept: "application/json" } });
  const { public_key: key, available } = await response.json();
  if (!available || !key) throw new Error("Web Push is not available on the server");
  return base64UrlToBytes(key);
}

// The push service rotated or dropped the subscription: subscribe again and tell the server,
// replacing the old endpoint so the channel keeps its name and event choices.
self.addEventListener("pushsubscriptionchange", (event) => {
  event.waitUntil(
    (async () => {
      const old = event.oldSubscription;
      const subscription =
        event.newSubscription ||
        (await self.registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: old?.options?.applicationServerKey || (await serverKey()),
        }));
      await fetch(`${API}/subscribe`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ subscription: subscription.toJSON(), old_endpoint: old?.endpoint || "" }),
      });
    })(),
  );
});
