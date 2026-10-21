// Trading plan -> Notifications: what to send, Web Push on this device, phone channels and history.
// Notifications are information only - Trading OS never places or logs a trade on its own.

import { api } from "../api.js";
import { clear, confirmDialog, field, h, icon, modal, toast } from "../dom.js";
import { fmt } from "../format.js";
import { loadPlan } from "../state.js";

const BASE = "/api/notifications";
const HISTORY_LIMIT = 20;
const EVENT_LABELS = { strategy_alert: "Strategy alerts", risk: "Risk events", news: "News reminders", test: "Test" };
const USER_EVENTS = ["strategy_alert", "risk", "news"];
const STATUS = {
  sent: { cls: "good", icon: "check", label: "Sent" },
  partial: { cls: "warn", icon: "alert", label: "Partly sent" },
  failed: { cls: "bad", icon: "x", label: "Failed" },
  skipped: { cls: "", icon: "info", label: "No channel" },
};
const SECRET_HINT = "Leave the masked value as it is to keep the saved one.";

const KINDS = {
  ntfy: {
    label: "ntfy",
    icon: "bell",
    recommended: true,
    description: "Free push app for Android and iOS - the quickest way to get alerts on your phone.",
    steps: [
      "Install the free ntfy app from Google Play or the App Store.",
      "In the app, tap + and subscribe to a topic only you know, e.g. tradingos-7f3k9q. Anyone who knows a topic on ntfy.sh can read it, so make it hard to guess.",
      "Enter the same topic below, save, then send a test.",
    ],
    fields: [
      { key: "topic", label: "Topic", placeholder: "tradingos-7f3k9q", required: true, help: "Letters, digits, - and _ only." },
      { key: "server", label: "Server", placeholder: "https://ntfy.sh", help: "Change it only if you run your own ntfy server." },
      { key: "token", label: "Access token (optional)", placeholder: "tk_…", secret: true, help: "For protected topics or a self-hosted server." },
    ],
  },
  telegram: {
    label: "Telegram",
    icon: "signal",
    description: "Messages from your own Telegram bot.",
    steps: [
      "In Telegram, open @BotFather, send /newbot and follow the prompts. Copy the bot token it gives you.",
      "Open a chat with your new bot and send it any message, for example /start.",
      "Find your chat id: message @userinfobot, or open api.telegram.org/bot<token>/getUpdates in a browser and copy message.chat.id.",
    ],
    fields: [
      { key: "bot_token", label: "Bot token", placeholder: "123456789:AA…", secret: true, required: true },
      { key: "chat_id", label: "Chat id", placeholder: "123456789", required: true, help: "Your user id, a group id (starts with -100) or @channelname." },
    ],
  },
  pushover: {
    label: "Pushover",
    icon: "zap",
    description: "Dependable push app for iOS and Android (one-time purchase after a free trial).",
    steps: [
      "Install Pushover and sign in at pushover.net - your user key is shown on the dashboard.",
      "Under Your Applications, create an application called Trading OS and copy its API token.",
    ],
    fields: [
      { key: "user_key", label: "User key", placeholder: "30 letters and digits", secret: true, required: true },
      { key: "app_token", label: "API token", placeholder: "30 letters and digits", secret: true, required: true },
    ],
  },
  discord: {
    label: "Discord",
    icon: "layers",
    description: "Posts to a channel on your Discord server.",
    steps: ["Open the channel settings → Integrations → Webhooks → New Webhook.", "Copy the webhook URL and paste it below."],
    fields: [{ key: "webhook_url", label: "Webhook URL", placeholder: "https://discord.com/api/webhooks/…", secret: true, required: true }],
  },
  slack: {
    label: "Slack",
    icon: "list",
    description: "Posts to a Slack channel through an incoming webhook.",
    steps: [
      "Create a Slack app at api.slack.com/apps, turn on Incoming Webhooks and add a webhook for your channel.",
      "Copy the webhook URL (https://hooks.slack.com/…) and paste it below.",
    ],
    fields: [{ key: "webhook_url", label: "Webhook URL", placeholder: "https://hooks.slack.com/services/…", secret: true, required: true }],
  },
  webhook: {
    label: "Webhook",
    icon: "link",
    description: "Sends JSON to your own URL - Home Assistant, IFTTT, Zapier, n8n or a script.",
    steps: [
      "Trading OS sends a POST request with a JSON body: event, title, body, url, priority, data and sent_at.",
      "With a signing secret, every request carries the header X-TradingOS-Signature: sha256=<hex HMAC-SHA256 of the raw body, keyed with the secret>, so your endpoint can check it came from Trading OS.",
    ],
    fields: [
      { key: "url", label: "URL", placeholder: "https://example.com/hooks/trading", secret: true, required: true },
      { key: "secret", label: "Signing secret (optional)", placeholder: "Any long random text", secret: true },
    ],
  },
  webpush: { label: "Web Push", icon: "grid", description: "This browser, or Trading OS added to a phone's Home Screen.", steps: [], fields: [] },
};
const ADDABLE = ["ntfy", "telegram", "pushover", "discord", "slack", "webhook"];

// ------------------------------------------------------------------ helpers

function base64UrlToBytes(text) {
  const padded = text.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (text.length % 4)) % 4);
  return Uint8Array.from(atob(padded), (char) => char.charCodeAt(0));
}

function sameBytes(buffer, bytes) {
  if (!buffer) return false;
  const view = new Uint8Array(buffer);
  return view.length === bytes.length && view.every((value, index) => value === bytes[index]);
}

async function endpointId(endpoint) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(endpoint));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 16);
}

function pushEnvironment() {
  const ua = navigator.userAgent;
  return {
    secure: window.isSecureContext,
    supported: "serviceWorker" in navigator && "PushManager" in window && "Notification" in window,
    ios: /iPad|iPhone|iPod/.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1),
    standalone: window.matchMedia?.("(display-mode: standalone)").matches || navigator.standalone === true,
  };
}

async function currentSubscription() {
  const registration = await navigator.serviceWorker.getRegistration("/");
  return (await registration?.pushManager.getSubscription()) || null;
}

function switchControl(checked, label, onchange, { title } = {}) {
  const input = h("input", { type: "checkbox", role: "switch", checked, onchange: (event) => onchange(event.target.checked, event.target) });
  return { input, el: h("label", { class: "nt-switch", title }, input, h("span", { class: "nt-switch-track", "aria-hidden": "true" }), label ? h("span", {}, label) : null) };
}

function statusBadge(status) {
  const meta = STATUS[status] || STATUS.skipped;
  return h("span", { class: `badge ${meta.cls}` }, icon(meta.icon, "icon nt-badge-icon"), meta.label);
}

function eventBadge(event) {
  return h("span", { class: `badge nt-event-badge nt-ev-${event}` }, EVENT_LABELS[event] || event);
}

function kindIcon(kind) {
  return h("span", { class: `nt-kind-icon nt-k-${kind}`, "aria-hidden": "true" }, icon(KINDS[kind]?.icon || "bell"));
}

function resultMessage(result) {
  if (result.status === "skipped") return ["No active channel yet - add one under Channels first.", "warn"];
  if (result.status === "sent") return ["Test notification sent", "success"];
  const failed = result.results.filter((item) => !item.ok);
  const first = failed[0] ? `${failed[0].name}: ${failed[0].error}` : "";
  if (result.status === "partial") return [`Sent, but ${failed.length} channel(s) failed - ${first}`, "warn"];
  return [`Not delivered - ${first}`, "error"];
}

// --------------------------------------------------------------------- view

export async function renderNotificationSettings(container) {
  const cards = {
    settings: h("form", { class: "card nt-settings", autocomplete: "off" }),
    device: h("section", { class: "card nt-device", "aria-live": "polite" }),
    channels: h("section", { class: "card nt-channels" }),
    history: h("section", { class: "card nt-history" }),
  };
  const data = { settings: null, channels: [], log: [], jobs: [] };
  clear(container, h("div", { class: "stack nt" }, cards.settings, cards.device, cards.channels, cards.history));

  try {
    [data.settings, data.channels, data.log, data.jobs] = await Promise.all([
      api.get(`${BASE}/settings`),
      api.get(`${BASE}/channels`),
      api.get(`${BASE}/log?limit=${HISTORY_LIMIT}`),
      api.get(`${BASE}/jobs`),
    ]);
  } catch (error) {
    clear(container, h("div", { class: "card empty" }, h("strong", {}, "Notification settings could not be loaded"), error.message));
    return;
  }

  async function refresh({ channels = true, log = true } = {}) {
    const [nextChannels, nextLog] = await Promise.all([
      channels ? api.get(`${BASE}/channels`) : data.channels,
      log ? api.get(`${BASE}/log?limit=${HISTORY_LIMIT}`) : data.log,
    ]);
    data.channels = nextChannels;
    data.log = nextLog;
    if (channels) {
      drawChannels();
      await drawDevice();
    }
    if (log) drawHistory();
  }

  // ------------------------------------------------------------- settings

  function drawSettings() {
    const s = data.settings;
    const master = switchControl(s.enabled, "Send notifications", (on) => eventsBox.classList.toggle("nt-off", !on));
    const eventBoxes = {};
    const minutes = h("input", { type: "number", min: 0, max: 120, step: 1, value: s.news_reminder_min, class: "nt-minutes", "aria-label": "Minutes before the release" });
    const eventsBox = h(
      "div",
      { class: `nt-events${s.enabled ? "" : " nt-off"}` },
      s.events_available.map((event) => {
        eventBoxes[event.id] = h("input", { type: "checkbox", checked: s.events.includes(event.id) });
        return h(
          "div",
          { class: "nt-event" },
          h("label", { class: "nt-event-main" }, eventBoxes[event.id], h("span", {}, h("strong", {}, event.label), h("span", { class: "muted small" }, event.description))),
          event.id === "news"
            ? h("label", { class: "nt-event-extra small" }, "Remind me", minutes, "min before (0 = off)")
            : null,
        );
      }),
    );
    const appUrl = h("input", { type: "url", value: s.app_url, placeholder: "http://192.168.1.20:8765", spellcheck: false, inputmode: "url" });
    const contact = h("input", { value: s.vapid_contact || "", placeholder: "mailto:you@example.com", spellcheck: false, maxlength: 120 });
    const saveBtn = h("button", { class: "btn primary", type: "submit" }, icon("check"), "Save");
    const background = data.jobs.length && !data.jobs[0].running;

    cards.settings.onsubmit = async (event) => {
      event.preventDefault();
      const value = Number(minutes.value);
      if (!Number.isInteger(value) || value < 0 || value > 120) {
        toast("News reminder minutes must be a whole number from 0 to 120.", "error");
        minutes.focus();
        return;
      }
      saveBtn.disabled = true;
      try {
        data.settings = await api.put(`${BASE}/settings`, {
          enabled: master.input.checked,
          events: USER_EVENTS.filter((id) => eventBoxes[id]?.checked),
          news_reminder_min: value,
          app_url: appUrl.value.trim(),
          ...(contact.value.trim() ? { vapid_contact: contact.value.trim() } : {}), // empty keeps the current contact
        });
        toast("Notification settings saved", "success");
        drawSettings();
        loadPlan().catch(() => {});
      } catch (error) {
        toast(error.message, "error");
        saveBtn.disabled = false;
      }
    };

    clear(
      cards.settings,
      h(
        "div",
        { class: "card-head" },
        h(
          "div",
          {},
          h("h2", {}, "Notifications"),
          h("p", {}, "Get strategy alerts, risk events and news reminders on your phone or in this browser. Alerts are information only - you always decide whether to trade."),
        ),
        h("span", { class: `chip ${s.enabled ? "ok" : ""}` }, icon(s.enabled ? "bell" : "power"), s.enabled ? "On" : "Off"),
      ),
      h("div", { class: "nt-master" }, master.el),
      h("div", { class: "nt-label" }, "Send me"),
      eventsBox,
      h(
        "div",
        { class: "form-grid nt-link" },
        field("App link", appUrl, {
          full: true,
          help: "The address your phone uses to open Trading OS, e.g. http://192.168.1.20:8765. Notifications then link to the right page. Leave empty to omit links.",
        }),
        field("Web Push contact", contact, {
          full: true,
          help: "Sent to browser push services (Apple, Google, Mozilla) as the sender's contact: a mailto: address or an https:// page. Apple may reject placeholder addresses.",
        }),
      ),
      background
        ? h("div", { class: "banner warn nt-banner" }, icon("alert"), h("div", {}, h("strong", {}, "Background checks are paused"), "Risk events, news reminders and strategy alerts are only checked while background jobs run. Restart Trading OS without TRADING_OS_BACKGROUND_JOBS=0."))
        : h("p", { class: "muted small nt-footnote" }, icon("clock"), "Risk events and news reminders are checked every minute while Trading OS is running."),
      h("div", { class: "row nt-actions" }, saveBtn),
    );
  }

  // ------------------------------------------------------------- Web Push

  async function enablePush(button, { renew = false } = {}) {
    button.disabled = true;
    try {
      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        toast(permission === "denied" ? "Notifications are blocked for this site - allow them in the browser's site settings." : "Notification permission was not granted.", "warn");
        return;
      }
      const { public_key: key, available } = await api.get(`${BASE}/webpush/key`);
      if (!available || !key) throw new Error("Web Push is not available on the server.");
      const registration = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
      await navigator.serviceWorker.ready;
      const keyBytes = base64UrlToBytes(key);
      let subscription = await registration.pushManager.getSubscription();
      let oldEndpoint = "";
      // A paused channel usually means the push service dropped the subscription: get a fresh one.
      if (subscription && (renew || !sameBytes(subscription.options?.applicationServerKey, keyBytes))) {
        oldEndpoint = subscription.endpoint;
        await subscription.unsubscribe();
        subscription = null;
      }
      subscription ||= await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes });
      await api.post(`${BASE}/webpush/subscribe`, { subscription: subscription.toJSON(), old_endpoint: oldEndpoint });
      toast("Notifications are on for this device", "success");
    } catch (error) {
      toast(`Web Push could not be turned on: ${error.message}`, "error");
    } finally {
      await refresh({ log: false }).catch((error) => toast(error.message, "error"));
    }
  }

  async function disablePush(button) {
    button.disabled = true;
    try {
      const subscription = await currentSubscription();
      if (subscription) {
        await api.post(`${BASE}/webpush/unsubscribe`, { endpoint: subscription.endpoint });
        await subscription.unsubscribe();
      }
      toast("Notifications are off for this device", "success");
    } catch (error) {
      toast(error.message, "error");
    } finally {
      await refresh({ log: false }).catch((error) => toast(error.message, "error"));
    }
  }

  async function drawDevice() {
    const env = pushEnvironment();
    const head = h(
      "div",
      { class: "card-head" },
      h("div", {}, h("h2", {}, "This device (Web Push)"), h("p", {}, "Notifications straight from the browser - no extra app needed.")),
    );
    const iosNote = h(
      "p",
      { class: "muted small nt-footnote" },
      icon("info"),
      "iPhone and iPad (iOS 16.4 or later): tap Share → Add to Home Screen, open Trading OS from the Home Screen, then enable notifications here.",
    );
    const notice = (kind, title, text) => h("div", { class: `banner ${kind} nt-banner` }, icon(kind === "bad" ? "xCircle" : "alert"), h("div", {}, h("strong", {}, title), text));

    if (!data.settings.webpush_available) {
      clear(cards.device, head, notice("warn", "Web Push is unavailable on the server", "Install the Python package cryptography (pip install -r requirements.txt) and restart Trading OS."));
      return;
    }
    if (!env.secure) {
      clear(
        cards.device,
        head,
        notice(
          "warn",
          "Web Push needs a secure address",
          `This page is open at ${location.origin}. Browsers only allow Web Push on https:// addresses or on http://localhost. On a phone, add an ntfy, Telegram or Pushover channel below instead - or serve Trading OS over HTTPS.`,
        ),
        iosNote,
      );
      return;
    }
    if (!env.supported) {
      const text = env.ios && !env.standalone
        ? "On iPhone and iPad, Web Push only works for Trading OS added to the Home Screen (iOS 16.4 or later): tap Share → Add to Home Screen and open it from there."
        : "This browser does not offer Web Push. Use an ntfy, Telegram or Pushover channel below instead.";
      clear(cards.device, head, notice("warn", "Web Push is not supported here", text));
      return;
    }

    let subscription = null;
    try {
      subscription = await currentSubscription();
    } catch {
      subscription = null;
    }
    const id = subscription ? await endpointId(subscription.endpoint) : null;
    const channel = id ? data.channels.find((item) => item.kind === "webpush" && item.endpoint_id === id) : null;
    const permission = Notification.permission;
    let chip;
    let actions;
    let text;
    if (permission === "denied") {
      chip = h("span", { class: "chip bad" }, icon("xCircle"), "Blocked");
      text = "Notifications are blocked for this site. Allow them in the browser's site settings, then reload this page.";
      actions = [];
    } else if (channel && channel.enabled) {
      chip = h("span", { class: "chip ok" }, icon("checkCircle"), `On for ${channel.name}`);
      text = "This device receives the notification types you switched on above.";
      const testBtn = h("button", { class: "btn sm", type: "button", onclick: () => testChannel(channel, testBtn) }, icon("play"), "Send test");
      const offBtn = h("button", { class: "btn sm ghost", type: "button", onclick: () => disablePush(offBtn) }, icon("power"), "Turn off");
      actions = [testBtn, offBtn];
    } else {
      const paused = channel && !channel.enabled;
      chip = h("span", { class: `chip ${paused ? "warn" : ""}` }, icon(paused ? "alert" : "power"), paused ? "Paused" : "Off on this device");
      text = paused
        ? `This device's subscription is paused${channel.last_error ? ` (${channel.last_error})` : ""}. Turn it on again to renew it.`
        : "Your browser will ask for permission. Notifications then arrive even when this tab is closed (the browser must be running).";
      const onBtn = h("button", { class: "btn primary sm", type: "button", onclick: () => enablePush(onBtn, { renew: paused }) }, icon("bell"), paused ? "Turn on again" : "Enable on this device");
      actions = [onBtn];
    }
    clear(cards.device, head, h("div", { class: "nt-device-state" }, h("div", { class: "row" }, chip), h("p", { class: "small nt-device-text" }, text), h("div", { class: "row" }, actions)), env.ios ? null : iosNote);
  }

  // ------------------------------------------------------------- channels

  async function testChannel(channel, button) {
    button.disabled = true;
    try {
      const result = await api.post(`${BASE}/channels/${channel.id}/test`);
      const [message, kind] = resultMessage(result);
      toast(kind === "success" ? `Test sent to ${channel.name}` : message, kind);
      await refresh();
    } catch (error) {
      toast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  async function toggleChannel(channel, enabled, input) {
    input.disabled = true;
    try {
      await api.put(`${BASE}/channels/${channel.id}`, { enabled });
      toast(`${channel.name} ${enabled ? "turned on" : "paused"}`, "success");
      await refresh({ log: false });
    } catch (error) {
      input.checked = !enabled;
      toast(error.message, "error");
    } finally {
      input.disabled = false;
    }
  }

  async function deleteChannel(channel) {
    const ok = await confirmDialog(`Remove the channel “${channel.name}”? Notifications will no longer be sent there.`, {
      title: "Remove channel",
      confirmText: "Remove",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.del(`${BASE}/channels/${channel.id}`);
      toast("Channel removed", "success");
      await refresh({ log: false });
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function channelCard(channel) {
    const toggle = switchControl(channel.enabled, null, (on, input) => toggleChannel(channel, on, input), { title: channel.enabled ? "Pause this channel" : "Turn this channel on" });
    toggle.input.setAttribute("aria-label", `${channel.name} enabled`);
    const testBtn = h("button", { class: "btn sm", type: "button", onclick: () => testChannel(channel, testBtn) }, icon("play"), "Test");
    const state = channel.last_error
      ? h("div", { class: "nt-ch-state bad" }, icon("alert"), h("span", {}, `Last error: ${channel.last_error}`))
      : channel.last_sent_at
        ? h("div", { class: "nt-ch-state ok" }, icon("check"), h("span", {}, `Last sent ${fmt.datetime(channel.last_sent_at)}`))
        : h("div", { class: "nt-ch-state" }, icon("clock"), h("span", {}, "Nothing sent yet"));
    return h(
      "article",
      { class: `nt-channel${channel.enabled ? "" : " paused"}` },
      h(
        "div",
        { class: "nt-ch-head" },
        kindIcon(channel.kind),
        h("div", { class: "nt-ch-title" }, h("strong", {}, channel.name), h("span", { class: "muted small" }, channel.kind_label, channel.enabled ? "" : " · paused")),
        toggle.el,
      ),
      h("div", { class: "muted small nt-ch-summary" }, channel.summary),
      h(
        "div",
        { class: "chips" },
        channel.events.length
          ? channel.events.map((event) => h("span", { class: "chip nt-chip" }, EVENT_LABELS[event] || event))
          : h("span", { class: "chip nt-chip" }, "All switched-on events"),
      ),
      state,
      h(
        "div",
        { class: "row nt-ch-actions" },
        testBtn,
        h("button", { class: "btn sm ghost", type: "button", onclick: () => openChannelForm(channel.kind, channel) }, icon("edit"), "Edit"),
        h("button", { class: "btn sm ghost danger", type: "button", onclick: () => deleteChannel(channel) }, icon("trash"), "Remove"),
      ),
    );
  }

  function drawChannels() {
    clear(
      cards.channels,
      h(
        "div",
        { class: "card-head" },
        h("div", {}, h("h2", {}, "Channels"), h("p", {}, "Where notifications are delivered. For phones, ntfy is the quickest free option.")),
        h("button", { class: "btn primary sm", type: "button", onclick: openKindPicker }, icon("plus"), "Add channel"),
      ),
      data.channels.length
        ? h("div", { class: "nt-channel-grid" }, data.channels.map(channelCard))
        : h("div", { class: "empty" }, h("strong", {}, "No channels yet"), "Add ntfy to get notifications on your phone in a couple of minutes."),
    );
  }

  function openKindPicker() {
    const ref = modal({
      title: "Add a channel",
      body: h(
        "div",
        { class: "nt-kinds" },
        ADDABLE.map((kind) =>
          h(
            "button",
            {
              type: "button",
              class: "nt-kind",
              onclick: () => {
                ref.close();
                openChannelForm(kind);
              },
            },
            kindIcon(kind),
            h(
              "span",
              { class: "nt-kind-text" },
              h("strong", {}, KINDS[kind].label, KINDS[kind].recommended ? h("span", { class: "badge good nt-rec" }, "Recommended for phones") : null),
              h("span", { class: "muted small" }, KINDS[kind].description),
            ),
            icon("chevronRight", "icon nt-kind-go"),
          ),
        ),
      ),
      footer: [h("button", { class: "btn ghost", type: "button", onclick: () => ref.close() }, "Cancel")],
    });
  }

  function openChannelForm(kind, channel = null) {
    const spec = KINDS[kind];
    const inputs = {};
    const fields = spec.fields.map((def) => {
      inputs[def.key] = h("input", {
        type: "text",
        value: channel ? channel.config[def.key] ?? "" : "",
        placeholder: def.placeholder || "",
        required: def.required && !channel,
        spellcheck: false,
        autocomplete: "off",
        autocapitalize: "off",
      });
      const help = [def.help, channel && def.secret && channel.config[def.key] ? SECRET_HINT : null].filter(Boolean).join(" ");
      return field(def.label, inputs[def.key], { full: true, help: help || undefined });
    });
    const name = h("input", { type: "text", maxlength: 80, value: channel?.name || "", placeholder: kind === "webpush" ? "This phone" : spec.label });
    const chosen = new Set(channel?.events || []);
    const chips = h(
      "div",
      { class: "chips" },
      USER_EVENTS.map((event) => {
        const chip = h("button", { type: "button", class: `chip-toggle${chosen.has(event) ? " on" : ""}`, "aria-pressed": String(chosen.has(event)) }, EVENT_LABELS[event]);
        chip.addEventListener("click", () => {
          if (chosen.has(event)) chosen.delete(event);
          else chosen.add(event);
          chip.classList.toggle("on", chosen.has(event));
          chip.setAttribute("aria-pressed", String(chosen.has(event)));
        });
        return chip;
      }),
    );
    const saveBtn = h("button", { class: "btn primary", type: "submit", form: "nt-channel-form" }, icon("check"), channel ? "Save" : "Add channel");
    const form = h(
      "form",
      { id: "nt-channel-form", class: "nt-form", autocomplete: "off", onsubmit: submit },
      spec.steps.length ? h("ol", { class: "nt-steps" }, spec.steps.map((step) => h("li", {}, step))) : null,
      h(
        "div",
        { class: "form-grid" },
        fields,
        field("Name", name, { full: true, help: "Shown in the channel list and the history." }),
        h("div", { class: "field full" }, h("span", { class: "label" }, "Send"), chips, h("div", { class: "help" }, "Leave all off to receive every type you switched on under Notifications.")),
      ),
    );
    const ref = modal({
      title: channel ? `Edit ${channel.name}` : `Add ${spec.label}`,
      body: form,
      footer: [
        channel ? null : h("button", { class: "btn ghost", type: "button", onclick: () => { ref.close(); openKindPicker(); } }, icon("chevronLeft"), "Back"),
        h("span", { class: "spacer" }),
        h("button", { class: "btn ghost", type: "button", onclick: () => ref.close() }, "Cancel"),
        saveBtn,
      ],
    });

    async function submit(event) {
      event.preventDefault();
      const config = Object.fromEntries(Object.entries(inputs).map(([key, input]) => [key, input.value.trim()]));
      const body = { name: name.value.trim(), events: USER_EVENTS.filter((id) => chosen.has(id)) };
      if (kind !== "webpush") body.config = config;
      saveBtn.disabled = true;
      try {
        if (channel) {
          await api.put(`${BASE}/channels/${channel.id}`, body);
          toast("Channel saved", "success");
        } else {
          const created = await api.post(`${BASE}/channels`, { kind, ...body });
          toast(`${created.name} added - send a test to check it arrives`, "success");
        }
        ref.close();
        await refresh({ log: false });
      } catch (error) {
        toast(error.message, "error");
        saveBtn.disabled = false;
      }
    }
  }

  // -------------------------------------------------------------- history

  async function sendTest(button) {
    button.disabled = true;
    try {
      const result = await api.post(`${BASE}/test`);
      const [message, kind] = resultMessage(result);
      toast(message, kind);
      await refresh();
    } catch (error) {
      toast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  async function clearHistory() {
    const ok = await confirmDialog("Clear the notification history? Channels and settings are kept, and so are the last day's risk and news notices (so they aren't sent again).", { title: "Clear history", confirmText: "Clear", danger: true });
    if (!ok) return;
    try {
      await api.del(`${BASE}/log`);
      toast("History cleared", "success");
      await refresh({ channels: false });
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function logItem(row) {
    return h(
      "li",
      {},
      h(
        "details",
        { class: "nt-log" },
        h(
          "summary",
          {},
          h("span", { class: "nt-log-time num small" }, fmt.datetime(row.created_at)),
          eventBadge(row.event),
          h("span", { class: "nt-log-title" }, row.title),
          statusBadge(row.status),
        ),
        h(
          "div",
          { class: "nt-log-body" },
          row.body ? h("p", { class: "nt-log-text" }, row.body) : null,
          row.results.length
            ? h(
                "ul",
                { class: "nt-results" },
                row.results.map((result) =>
                  h(
                    "li",
                    { class: result.ok ? "ok" : "bad" },
                    icon(result.ok ? "check" : "x"),
                    h("span", {}, h("strong", {}, result.name), ` (${KINDS[result.kind]?.label || result.kind}) - `, result.ok ? "Delivered" : result.error),
                  ),
                ),
              )
            : h("p", { class: "muted small" }, "No channel was set up for this notification type."),
        ),
      ),
    );
  }

  function drawHistory() {
    const testBtn = h("button", { class: "btn sm", type: "button", onclick: () => sendTest(testBtn) }, icon("bell"), "Send test notification");
    clear(
      cards.history,
      h(
        "div",
        { class: "card-head" },
        h("div", {}, h("h2", {}, "History"), h("p", {}, `The last ${HISTORY_LIMIT} notifications and how each channel answered.`)),
        h(
          "div",
          { class: "row nt-history-actions" },
          testBtn,
          data.log.length ? h("button", { class: "btn sm ghost", type: "button", onclick: clearHistory }, icon("trash"), "Clear") : null,
        ),
      ),
      data.log.length ? h("ul", { class: "nt-log-list" }, data.log.map(logItem)) : h("div", { class: "empty" }, h("strong", {}, "Nothing sent yet"), "Send a test notification to check your channels."),
    );
  }

  drawSettings();
  drawChannels();
  drawHistory();
  await drawDevice();
}
