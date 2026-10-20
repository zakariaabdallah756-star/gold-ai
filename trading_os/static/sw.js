// Service worker for Web Push notifications (track N). Placeholder until implemented.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
