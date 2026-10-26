// App shell: navigation, hash router and the global discipline status bar.

import { ensureAccess } from "./access.js";
import { api } from "./api.js";
import { clear, field, h, icon, modal, segmented, toast, withNodes } from "./dom.js";
import { fmt, pnlClass, toInputDateTime } from "./format.js";
import { mountDisclaimer } from "./disclaimer.js";
import { N_, t, tn, useLanguage } from "./i18n.js";
import { autoOpenOnboarding, openOnboarding } from "./onboarding.js";
import { accountNow, accounts, activeAccount, loadPlan, loadStatus, onStatus, state } from "./state.js";
import { renderTopbar } from "./topbar.js";
import * as academy from "./views/academy.js";
import * as tradingAccounts from "./views/accounts.js";
import * as alerts from "./views/alerts.js";
import * as backup from "./views/backup.js";
import * as calculator from "./views/calculator.js";
import * as checklist from "./views/checklist.js";
import * as dashboard from "./views/dashboard.js";
import * as incubator from "./views/incubator.js";
import * as journal from "./views/journal.js";
import * as partners from "./views/partners.js";
import * as plan from "./views/plan.js";
import * as positions from "./views/positions.js";
import * as prop from "./views/prop.js";
import * as review from "./views/review.js";
import * as signals from "./views/signals.js";
import * as telegram from "./views/telegram.js";
import * as timeCalendar from "./views/calendar.js";
import * as vision from "./views/vision.js";

const ROUTES = [
  { path: "dashboard", label: N_("Dashboard"), icon: "dashboard", view: dashboard, group: N_("Trade") },
  { path: "premarket", label: N_("Pre-Market"), icon: "checklist", view: checklist, group: N_("Trade") },
  { path: "signals", label: N_("Signals"), icon: "signal", view: signals, group: N_("Trade") },
  { path: "alerts", label: N_("Strategy Alerts"), icon: "bell", view: alerts, group: N_("Trade") },
  { path: "telegram", label: N_("Telegram"), icon: "send", view: telegram, group: N_("Trade") },
  { path: "vision", label: N_("Vision AI"), icon: "brain", view: vision, group: N_("Trade") },
  { path: "calculator", label: N_("Risk Calculator"), icon: "calculator", view: calculator, group: N_("Trade") },
  { path: "positions", label: N_("Positions"), icon: "layers", view: positions, group: N_("Trade") },
  { path: "journal", label: N_("Journal"), icon: "journal", view: journal, group: N_("Trade") },
  { path: "calendar", label: N_("Calendar"), icon: "calendar", view: timeCalendar, group: N_("Improve") },
  { path: "review", label: N_("Review"), icon: "review", view: review, group: N_("Improve") },
  { path: "incubator", label: N_("Incubator"), icon: "flame", view: incubator, group: N_("Improve") },
  { path: "prop", label: N_("Prop Firm"), icon: "shield", view: prop, group: N_("Improve") },
  { path: "academy", label: N_("Academy"), icon: "graduation", view: academy, group: N_("Learning & Growth") },
  { path: "accounts", label: N_("Trading Accounts"), icon: "wallet", view: tradingAccounts, group: N_("Setup") },
  { path: "plan", label: N_("Trading Plan"), icon: "plan", view: plan, group: N_("Setup") },
  { path: "partners", label: N_("Partners"), icon: "star", view: partners, group: N_("Setup") },
  { path: "backup", label: N_("Backup & Export"), icon: "backup", view: backup, group: N_("Setup") },
];

const STATE_CHIPS = {
  ok: { cls: "ok", icon: "unlock", text: N_("Trading enabled") },
  warning: { cls: "warn", icon: "alert", text: N_("Near daily loss limit") },
  locked: { cls: "bad", icon: "lock", text: N_("Trading locked") },
  breached: { cls: "bad", icon: "alert", text: N_("Daily limit breached") },
};

const COOLOFF_HOURS = [4, 12, 24, 48];
const MAX_COOLOFF_HOURS = 72;
const COOLOFF_REASONS = [N_("Tilt"), N_("Emotional fatigue"), N_("Loss streak"), N_("Revenge urge"), N_("Personal")];
const UNLOCK_PHRASE = "UNLOCK"; // typed as is in every language
const HOUR_MS = 3_600_000;
const IMPACT_LABEL = { high: N_("high impact"), medium: N_("medium impact"), low: N_("low impact") };

let currentRoute = ROUTES[0];
let cleanup = null;
let renderToken = 0;
let expiryTimer = null;

// ------------------------------------------------------- kill switch & news

/** Milliseconds from the account's "now" until a naive account-local timestamp. */
function msUntil(iso) {
  return new Date(iso).getTime() - accountNow().getTime();
}

function span(ms) {
  const minutes = Math.max(1, Math.ceil(ms / 60_000));
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  return minutes % 60 && hours < 10 ? `${hours}h ${minutes % 60}m` : `${hours}h`;
}

function remaining(ms) {
  const minutes = Math.max(0, Math.floor(ms / 60_000));
  if (minutes < 1) return t("less than a minute");
  return minutes < 60 ? `${minutes}m` : `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

/** "Non-Farm Payrolls (NFP)" -> "NFP"; other titles are kept (the chip truncates them). */
function shortTitle(title) {
  return title.match(/\(([^()]{2,12})\)\s*$/)?.[1] || title;
}

function inHours(hours) {
  return fmt.datetime(toInputDateTime(new Date(accountNow().getTime() + hours * HOUR_MS)));
}

function newsChip(status) {
  const next = status.upcoming_news?.[0];
  if (!next) return null;
  const guarded = state.plan.settings.news_mode !== "off";
  const ms = msUntil(next.time);
  const when = ms > 0 ? t("in {span}", { span: span(ms) }) : t("{span} ago", { span: span(-ms) });
  const restricted = t("restricted {start}–{end}", { start: fmt.time(next.window_start), end: fmt.time(next.window_end) });
  const impact = IMPACT_LABEL[next.impact] ? t(IMPACT_LABEL[next.impact]) : t("{impact} impact", { impact: next.impact });
  return h(
    "a",
    {
      class: `chip news-chip${next.active && guarded ? " warn" : ""}`,
      href: "#/premarket",
      title: [`${next.title} (${next.currency}, ${impact})`, restricted, next.active ? t("window open now") : null].filter(Boolean).join(" · "),
    },
    icon("bell"),
    h("span", { class: "chip-label" }, shortTitle(next.title)),
    h("strong", { class: "num" }, fmt.time(next.time)),
    ` · ${when}`,
  );
}

// Prop mode: the active challenge's remaining room (the tighter of daily loss and max drawdown).
function propChip(status) {
  const prop = status.prop;
  if (!prop) return null;
  const room = Math.min(prop.daily_remaining, prop.total_remaining);
  const ended = prop.status !== "active";
  const cls = ended || room <= 0 ? "bad" : room < prop.daily_limit * 0.25 ? "warn" : "";
  const title = [
    `${prop.name}${prop.firm ? ` (${prop.firm})` : ""} · ${prop.enforce ? t("rules enforced") : t("warnings only")}`,
    t("Daily loss {used} of {limit}", { used: fmt.money(prop.daily_loss_used), limit: fmt.money(prop.daily_limit) }),
    t("Balance {balance} · floor {floor}", { balance: fmt.money(prop.balance), floor: fmt.money(prop.drawdown_floor) }),
  ].join("\n");
  return h(
    "a",
    { class: `chip prop-chip ${cls}`, href: "#/prop", title },
    icon("target"),
    h("span", { class: "chip-label" }, prop.firm || "Prop"),
    ended
      ? h("strong", {}, prop.status === "passed" ? t("Passed") : prop.status === "failed" ? t("Failed") : t("Ended"))
      : [" ", withNodes(t("room {amount}"), { amount: h("strong", { class: "num" }, fmt.money(Math.max(room, 0))) })],
  );
}

async function startCooloff(hours, reason, button) {
  button.disabled = true;
  try {
    await api.post("/api/killswitch", { hours, reason });
    await loadStatus();
    return true;
  } catch (error) {
    toast(error.message, "error");
    button.disabled = false;
    return false;
  }
}

function openActivate() {
  let hours = 24;
  const picked = new Set();
  const label = h("span");
  const until = h("strong", { class: "num" });
  const update = () => {
    label.textContent = t("Lock trading for {hours}h", { hours });
    until.textContent = inHours(hours);
  };
  const duration = segmented(
    COOLOFF_HOURS.map((value) => [String(value), `${value}h`]),
    String(hours),
    (value) => {
      hours = Number(value);
      update();
    },
  );
  const reasons = COOLOFF_REASONS.map((reason) => t(reason)); // saved as the user reads them
  const chips = reasons.map((reason) => {
    const chip = h("button", { type: "button", class: "chip-toggle", "aria-pressed": "false" }, reason);
    chip.addEventListener("click", () => {
      if (picked.has(reason)) picked.delete(reason);
      else picked.add(reason);
      chip.classList.toggle("on", picked.has(reason));
      chip.setAttribute("aria-pressed", String(picked.has(reason)));
    });
    return chip;
  });
  const note = h("input", { type: "text", maxlength: "120", placeholder: t("What's going on? (optional)") });
  const submit = h("button", { class: "btn danger-solid", type: "button" }, icon("power"), label);
  submit.addEventListener("click", async () => {
    const reason = [...reasons.filter((r) => picked.has(r)), note.value.trim()].filter(Boolean).join(" · ").slice(0, 200);
    if (await startCooloff(hours, reason, submit)) {
      ref.close();
      toast(t("Kill switch on. No new trades until {until}.", { until: fmt.datetime(state.status.cooloff_until) }), "success");
    }
  });
  update();

  const ref = modal({
    title: t("Kill switch"),
    body: h(
      "div",
      { class: "stack ks-body" },
      h(
        "div",
        { class: "ks-intro" },
        h("p", {}, t("Stop all new trade entries for a cool-off period. Use it the moment you notice tilt, revenge urges or fatigue - before the next trade, not after it.")),
        h(
          "ul",
          {},
          h("li", {}, t("Live entries are blocked until the cool-off ends. Past trades can still be logged (they are recorded with a warning).")),
          h("li", {}, t("You can extend a running cool-off, but never shorten it.")),
          h("li", {}, t("Ending it early requires typing {phrase}.", { phrase: UNLOCK_PHRASE })),
        ),
      ),
      field(t("Cool-off duration"), duration.el),
      h("div", { class: "ks-until" }, icon("clock"), h("span", {}, withNodes(t("Trading resumes {when}"), { when: until }))),
      field(t("Why are you stopping?"), h("div", { class: "stack", style: { gap: "8px" } }, h("div", { class: "chips" }, chips), note)),
    ),
    footer: [h("button", { class: "btn ghost", type: "button", onclick: () => ref.close() }, t("Cancel")), submit],
  });
}

function openCooloff(status) {
  const ms = msUntil(status.cooloff_until);
  const extendable = [...COOLOFF_HOURS, MAX_COOLOFF_HOURS].filter((hours) => hours * HOUR_MS > ms + 60_000);
  const phrase = h("input", {
    type: "text",
    placeholder: t("Type {phrase}", { phrase: UNLOCK_PHRASE }),
    autocomplete: "off",
    spellcheck: false,
    "aria-label": t("Type {phrase} to confirm", { phrase: UNLOCK_PHRASE }),
  });
  const release = h("button", { class: "btn danger", type: "button", disabled: true }, icon("unlock"), t("End cool-off now"));
  phrase.addEventListener("input", () => (release.disabled = phrase.value.trim() !== UNLOCK_PHRASE));
  release.addEventListener("click", async () => {
    release.disabled = true;
    try {
      await api.post("/api/killswitch/release", { confirm: phrase.value.trim() });
      await loadStatus();
      ref.close();
      toast(t("Cool-off ended. Trade your plan, not your emotions."), "success");
    } catch (error) {
      toast(error.message, "error");
      release.disabled = false;
    }
  });
  const extendButtons = extendable.map((hours) => {
    const button = h("button", { class: "btn sm", type: "button", title: t("Trading resumes {when}", { when: inHours(hours) }) }, t("{hours}h from now", { hours }));
    button.addEventListener("click", async () => {
      if (await startCooloff(hours, "", button)) {
        ref.close();
        toast(t("Cool-off extended until {until}.", { until: fmt.datetime(state.status.cooloff_until) }), "success");
      }
    });
    return button;
  });

  const ref = modal({
    title: t("Cool-off active"),
    body: h(
      "div",
      { class: "stack ks-body" },
      h(
        "div",
        { class: "cooloff-summary" },
        icon("lock"),
        h(
          "div",
          {},
          h("strong", {}, t("{time} left", { time: remaining(ms) })),
          h(
            "div",
            { class: "muted small" },
            withNodes(t("New trade entries are locked until {until}."), { until: h("span", { class: "num" }, fmt.datetime(status.cooloff_until)) }),
          ),
          status.cooloff_reason ? h("div", { class: "small" }, t("Reason: {reason}", { reason: status.cooloff_reason })) : null,
        ),
      ),
      h(
        "section",
        { class: "ks-section" },
        h("h3", {}, t("Extend")),
        h("p", { class: "muted small" }, t("Still not ready? Add time. A cool-off can be extended but not shortened.")),
        extendButtons.length
          ? h("div", { class: "row" }, extendButtons)
          : h("p", { class: "small" }, t("Already at the {hours}-hour maximum.", { hours: MAX_COOLOFF_HOURS })),
      ),
      h(
        "section",
        { class: "ks-section" },
        h("h3", {}, t("End early")),
        h("p", { class: "muted small" }, t("Ending a cool-off early is exactly the impulse it protects you from. Only do it if the reason no longer applies.")),
        h("div", { class: "row ks-release" }, phrase, release),
      ),
    ),
    footer: h("button", { class: "btn ghost", type: "button", onclick: () => ref.close() }, t("Close")),
  });
}

async function openKillSwitch() {
  // Fresh status: the cool-off may have ended, or been changed from another tab, since the last poll.
  const status = await loadStatus().catch(() => state.status);
  if (status?.cooloff_until && msUntil(status.cooloff_until) > 0) openCooloff(status);
  else openActivate();
}

/** Reload the status right after a cool-off ends so the lock lifts without waiting for the next poll. */
function scheduleExpiryRefresh(ms) {
  clearTimeout(expiryTimer);
  expiryTimer = ms !== null && ms < 2 * 60_000 ? setTimeout(() => loadStatus().catch(() => {}), Math.max(ms, 0) + 1500) : null;
}

function buildNav() {
  const nav = document.getElementById("nav");
  clear(
    nav,
    ROUTES.map((route, index) => [
      index === 0 || ROUTES[index - 1].group !== route.group ? h("div", { class: "nav-group" }, t(route.group)) : null,
      h("a", { href: `#/${route.path}`, dataset: { path: route.path } }, icon(route.icon), h("span", {}, t(route.label))),
    ]),
  );
}

function renderStatus(status) {
  const blocked = status.reasons.some((r) => r.severity === "block");
  const chip = STATE_CHIPS[status.state === "ok" && blocked ? "locked" : status.state];
  const ratio = Math.min(status.loss_used_ratio, 1);
  const meterClass = ratio >= 1 ? "bad" : ratio >= 0.75 ? "warn" : "";
  const cooloffMs = status.cooloff_until ? msUntil(status.cooloff_until) : null;
  const cooling = cooloffMs !== null && cooloffMs > 0;
  scheduleExpiryRefresh(cooloffMs);

  renderTopbar(document.getElementById("topbar"), {
    title: t(currentRoute.label),
    onSwitch: route,
    strip: h(
      "div",
      { class: "status-strip" },
      status.checklist_required
        ? h(
            "a",
            { class: `chip ${status.checklist_complete ? "ok" : "bad"}`, href: "#/premarket" },
            icon(status.checklist_complete ? "checkCircle" : "xCircle"),
            status.checklist_complete ? t("Checklist done") : t("Checklist pending"),
          )
        : null,
      h(
        "span",
        { class: "chip" },
        withNodes(t("Today {pnl}"), { pnl: h("strong", { class: pnlClass(status.realized_pnl_today) }, fmt.signedMoney(status.realized_pnl_today)) }),
      ),
      h(
        "span",
        { class: "chip", title: t("Daily loss used: {used} of {limit}", { used: fmt.money(status.loss_used), limit: fmt.money(status.daily_loss_limit) }) },
        t("Daily loss"),
        h("span", { class: `meter ${meterClass}` }, h("span", { style: { width: `${ratio * 100}%` } })),
        h("strong", { class: "num" }, `${fmt.money(status.loss_used)} / ${fmt.money(status.daily_loss_limit)}`),
      ),
      h(
        "span",
        { class: "chip" },
        withNodes(t("Trades {count}"), {
          count: h("strong", {}, status.max_trades_per_day ? `${status.trades_today}/${status.max_trades_per_day}` : String(status.trades_today)),
        }),
      ),
      h("span", { class: `chip ${chip.cls}` }, icon(chip.icon), t(chip.text)),
      cooling
        ? h(
            "button",
            { class: "chip bad", type: "button", title: t("Cool-off until {until}", { until: fmt.datetime(status.cooloff_until) }), onclick: openKillSwitch },
            icon("lock"),
            t("Cool-off · {time} left", { time: remaining(cooloffMs) }),
          )
        : null,
      newsChip(status),
      propChip(status),
    ),
    killswitch: h(
      "button",
      { class: `btn sm killswitch${cooling ? " on" : ""}`, type: "button", onclick: openKillSwitch },
      icon("power"),
      cooling ? t("Cool-off active") : t("Kill switch"),
    ),
  });

  const banner = document.getElementById("banner");
  const shown = status.reasons.filter((r) => r.code !== "CHECKLIST_INCOMPLETE" || currentRoute.path !== "premarket");
  if (!shown.length && status.state !== "warning") {
    clear(banner);
  } else {
    const severe = blocked || status.state === "locked" || status.state === "breached";
    const heading = blocked
      ? t("New trade entries are locked")
      : severe
        ? t("Daily risk limit breached")
        : status.state === "warning"
          ? t("Approaching your daily loss limit")
          : t("Trade with caution");
    const link = (reason) => {
      if (reason.code === "CHECKLIST_INCOMPLETE") return h("a", { href: "#/premarket" }, t("Open checklist →"));
      if (reason.code === "NEWS_WINDOW" && currentRoute.path !== "premarket") return h("a", { href: "#/premarket" }, t("Open calendar →"));
      if (reason.code.startsWith("PROP_") && currentRoute.path !== "prop") return h("a", { href: "#/prop" }, t("Open Prop Firm →"));
      return null;
    };
    clear(
      banner,
      h(
        "div",
        { class: `banner ${severe ? "bad" : "warn"}`, role: "status" },
        icon(blocked ? "lock" : "alert"),
        h(
          "div",
          {},
          h("strong", {}, heading),
          shown.length
            ? h(
                "ul",
                {},
                shown.map((r) => {
                  const action = link(r);
                  return h("li", {}, r.message, action ? [" ", action] : null);
                }),
              )
            : h("p", { class: "muted" }, t("{amount} of today's loss budget remains.", { amount: fmt.money(status.remaining_loss_budget) })),
        ),
      ),
    );
  }

  clear(
    document.getElementById("sidebar-foot"),
    h("div", {}, activeAccount()?.name || (accounts().filter((a) => a.status === "active").length > 1 ? t("All accounts") : state.plan.settings.account_name)),
    h("div", { class: "equity num" }, fmt.money(status.current_equity)),
    h(
      "div",
      {},
      withNodes(t("Equity · {change} all-time"), {
        change: h("span", { class: pnlClass(status.current_equity - status.starting_balance) }, fmt.signedMoney(status.current_equity - status.starting_balance)),
      }),
    ),
  );
}

function errorBox(error) {
  return h("div", { class: "card empty" }, h("strong", {}, t("Something went wrong")), error.message || String(error));
}

async function route() {
  const [path, search] = location.hash.replace(/^#\/?/, "").split("?");
  if (path === "onboarding") {
    // A link to the getting-started guide (e.g. from a promo card): open it over the dashboard.
    history.replaceState(null, "", "#/dashboard");
    openOnboarding();
    return route();
  }
  currentRoute = ROUTES.find((r) => r.path === path) || ROUTES[0];
  for (const link of document.querySelectorAll("#nav a")) {
    link.classList.toggle("active", link.dataset.path === currentRoute.path);
    link.toggleAttribute("aria-current", link.dataset.path === currentRoute.path);
  }
  document.title = `${t(currentRoute.label)} · Trading OS`;
  if (state.status) renderStatus(state.status);

  cleanup?.();
  cleanup = null;
  const token = ++renderToken;
  // Each render gets its own container: a slow, superseded render writes into
  // a detached node instead of clobbering the current page.
  const container = h("div", {}, h("div", { class: "loading" }, t("Loading…")));
  clear(document.getElementById("view"), container);
  try {
    const result = await currentRoute.view.render(container, { params: new URLSearchParams(search || "") });
    if (token !== renderToken) {
      if (typeof result === "function") result();
      return;
    }
    cleanup = typeof result === "function" ? result : null;
  } catch (error) {
    if (token === renderToken) clear(container, errorBox(error));
  }
}

async function boot() {
  if (!(await ensureAccess())) return; // another device, remote-access password set: sign in first
  try {
    await loadPlan();
    await useLanguage(state.plan.settings.language);
    buildNav();
    await loadStatus();
  } catch (error) {
    if (!document.querySelector("#nav a")) buildNav();
    clear(document.getElementById("view"), errorBox(error));
    return;
  }
  onStatus(renderStatus);
  renderStatus(state.status);
  const disclaimer = await mountDisclaimer();
  window.addEventListener("hashchange", route);
  window.addEventListener("tos:account-changed", () => route()); // switched on another tab or device
  route();
  disclaimer.accepted.then(autoOpenOnboarding); // first launch: the getting-started guide, once the risks were accepted
  setInterval(() => loadStatus().catch(() => {}), 60_000);
}

boot();
