// Trading accounts in the app shell (v1.5): labels, badges, the logo and money in an account's own currency.
// Shared by the top bar (account switcher, profile menu), the dashboard hero and the Trading Accounts page.

import { h, icon } from "./dom.js";
import { currentLocale } from "./format.js";
import { N_, t } from "./i18n.js";
import { accountNow, accounts, activeAccount } from "./state.js";

export const KIND_LABEL = { live: N_("Live"), demo: N_("Demo"), prop: N_("Prop"), funded: N_("Funded") };
// Product names stay as they are; "other" is translated where shown.
export const PLATFORM_LABEL = { mt4: "MT4", mt5: "MT5", ctrader: "cTrader", tradingview: "TradingView", api: "API", other: N_("Other platform") };
export const CONNECTION_LABEL = {
  paper: N_("Paper trading"),
  webhook: N_("Webhook bridge"),
  mt_bridge: N_("MT4/MT5 bridge"),
  mt5_terminal: N_("MT5 terminal"),
  oanda: "OANDA",
};
const ONLINE_MS = 5 * 60_000; // a bridge seen within this window counts as online (services.accounts.SNAPSHOT_FRESH)
const SVG_NS = "http://www.w3.org/2000/svg";

export const label = (table, key) => (table[key] ? t(table[key]) : key || "");

export function kindBadge(kind) {
  return h("span", { class: `badge kind-badge kind-${kind}` }, label(KIND_LABEL, kind));
}

export function colorDot(color) {
  return h("span", { class: "acct-dot", style: { background: color || "var(--muted)" }, "aria-hidden": "true" });
}

/** The Trading OS mark (the sidebar logo), at any size. */
export function logoMark(className = "logo-mark") {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 32 32");
  svg.setAttribute("class", className);
  svg.setAttribute("aria-hidden", "true");
  const rect = document.createElementNS(SVG_NS, "rect");
  for (const [key, value] of Object.entries({ width: "32", height: "32", rx: "8", fill: "var(--surface-2)" })) rect.setAttribute(key, value);
  const path = document.createElementNS(SVG_NS, "path");
  const stroke = { d: "M7 21l6-6 4 4 8-9", stroke: "var(--profit)", "stroke-width": "3", fill: "none", "stroke-linecap": "round", "stroke-linejoin": "round" };
  for (const [key, value] of Object.entries(stroke)) path.setAttribute(key, value);
  svg.append(rect, path);
  return svg;
}

const moneyFormats = new Map();

function moneyFormat(currency) {
  const key = `${currentLocale() || ""}|${currency}`;
  if (!moneyFormats.has(key)) {
    let format;
    try {
      format = new Intl.NumberFormat(currentLocale(), { style: "currency", currency, minimumFractionDigits: 2, maximumFractionDigits: 2 });
    } catch {
      format = new Intl.NumberFormat(currentLocale(), { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    moneyFormats.set(key, format);
  }
  return moneyFormats.get(key);
}

/** An amount in ``currency`` (accounts can differ from the app's currency); ``signed`` adds + / −. */
export function accountMoney(value, currency, { signed = false } = {}) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  const text = moneyFormat(currency || "USD").format(signed ? Math.abs(value) : value).replace("-", "−");
  if (!signed || Math.abs(value) < 0.005) return text;
  return (value > 0 ? "+" : "−") + text;
}

/** "FTMO Challenge 1" -> "FC", "Personal" -> "PE". */
export function initials(name) {
  const words = String(name || "")
    .trim()
    .split(/[\s\-_.·/]+/)
    .filter(Boolean);
  if (!words.length) return "";
  const letters = words.length === 1 ? [...words[0]].slice(0, 2) : [[...words[0]][0], [...words[1]][0]];
  return letters.join("").toUpperCase();
}

/** The account the app shows; with "all accounts" and a single active account, that account. */
export function shownAccount() {
  const active = activeAccount();
  if (active) return active;
  const open = accounts().filter((account) => account.status === "active");
  return open.length === 1 ? open[0] : null;
}

/** Milliseconds since a naive account-local timestamp (as the server sends them). */
export function msSince(iso) {
  return iso ? accountNow().getTime() - new Date(iso).getTime() : null;
}

/** "just now", "12s ago", "3m ago", "2h ago", "4d ago". */
export function ago(iso) {
  const ms = msSince(iso);
  if (ms === null || Number.isNaN(ms)) return "";
  const seconds = Math.max(0, Math.round(ms / 1000));
  if (seconds < 5) return t("just now");
  let span;
  if (seconds < 60) span = `${seconds}s`;
  else if (seconds < 3600) span = `${Math.floor(seconds / 60)}m`;
  else if (seconds < 86_400) span = `${Math.floor(seconds / 3600)}h`;
  else span = `${Math.floor(seconds / 86_400)}d`;
  return t("{span} ago", { span });
}

/** Connection status of an account: how it is linked and when its bridge was last heard from. */
export function connectionChip(account) {
  if (!account.connection || account.connection === "none") {
    return h("span", { class: "chip conn-chip", title: t("Figures come from your journal") }, icon("plug"), t("Not connected · journal only"));
  }
  const name = label(CONNECTION_LABEL, account.connection);
  if (account.last_error) {
    return h("span", { class: "chip conn-chip bad", title: account.last_error }, icon("alert"), t("{connection} · error", { connection: name }));
  }
  if (account.connection === "paper") {
    return h("span", { class: "chip conn-chip ok" }, icon("checkCircle"), t("{connection} · simulated fills", { connection: name }));
  }
  const since = msSince(account.last_seen_at);
  if (since === null) {
    return h("span", { class: "chip conn-chip" }, icon("clock"), t("{connection} · waiting for first contact", { connection: name }));
  }
  if (since < ONLINE_MS) {
    return h("span", { class: "chip conn-chip ok" }, h("span", { class: "live-dot", "aria-hidden": "true" }), t("{connection} · online, seen {when}", { connection: name, when: ago(account.last_seen_at) }));
  }
  return h("span", { class: "chip conn-chip warn" }, icon("alert"), t("{connection} · last seen {when}", { connection: name, when: ago(account.last_seen_at) }));
}
