// Notification settings: channels (Web Push, ntfy, Telegram, ...), event types and history (track N).
// Placeholder until implemented.

import { clear, h } from "../dom.js";

export async function renderNotificationSettings(container) {
  clear(container, h("div", { class: "card empty" }, h("strong", {}, "Notifications"), "Coming soon."));
}
