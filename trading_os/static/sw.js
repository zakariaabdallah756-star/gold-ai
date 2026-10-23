// Trading OS service worker: shows Web Push notifications and opens the app when one is clicked.
// It does not cache or intercept requests - the app always talks to the live local server.

const ICON = "/static/icons/icon-192.png";
const BADGE = "/static/icons/badge-72.png";
const API = "/api/notifications/webpush";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

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
