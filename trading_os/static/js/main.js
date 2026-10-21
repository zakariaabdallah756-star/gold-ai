// App shell: navigation, hash router and the global discipline status bar.

import { api } from "./api.js";
import { clear, field, h, icon, modal, segmented, toast } from "./dom.js";
import { fmt, pnlClass, toInputDateTime } from "./format.js";
import { mountDisclaimer } from "./disclaimer.js";
import { N_, t, tn, useLanguage } from "./i18n.js";
import { accountNow, loadPlan, loadStatus, onStatus, state } from "./state.js";
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
import * as timeCalendar from "./views/calendar.js";
import * as vision from "./views/vision.js";

const ROUTES = [
  { path: "dashboard", label: "Dashboard", icon: "dashboard", view: dashboard, group: "Trade" },
  { path: "premarket", label: "Pre-Market", icon: "checklist", view: checklist, group: "Trade" },
  { path: "signals", label: "Signals", icon: "signal", view: signals, group: "Trade" },
  { path: "alerts", label: "Strategy Alerts", icon: "bell", view: alerts, group: "Trade" },
  { path: "vision", label: "Vision AI", icon: "brain", view: vision, group: "Trade" },
  { path: "calculator", label: "Risk Calculator", icon: "calculator", view: calculator, group: "Trade" },
  { path: "positions", label: "Positions", icon: "layers", view: positions, group: "Trade" },
  { path: "journal", label: "Journal", icon: "journal", view: journal, group: "Trade" },
  { path: "calendar", label: "Calendar", icon: "calendar", view: timeCalendar, group: "Improve" },
  { path: "review", label: "Review", icon: "review", view: review, group: "Improve" },
  { path: "incubator", label: "Incubator", icon: "flame", view: incubator, group: "Improve" },
  { path: "prop", label: "Prop Firm", icon: "shield", view: prop, group: "Improve" },
  { path: "plan", label: "Trading Plan", icon: "plan", view: plan, group: "Setup" },
  { path: "partners", label: "Partners", icon: "star", view: partners, group: "Setup" },
  { path: "backup", label: "Backup & Export", icon: "backup", view: backup, group: "Setup" },
];

const STATE_CHIPS = {
  ok: { cls: "ok", icon: "unlock", text: "Trading enabled" },
  warning: { cls: "warn", icon: "alert", text: "Near daily loss limit" },
  locked: { cls: "bad", icon: "lock", text: "Trading locked" },
  breached: { cls: "bad", icon: "alert", text: "Daily limit breached" },
};

const COOLOFF_HOURS = [4, 12, 24, 48];
const MAX_COOLOFF_HOURS = 72;
const COOLOFF_REASONS = ["Tilt", "Emotional fatigue", "Loss streak", "Revenge urge", "Personal"];
const UNLOCK_PHRASE = "UNLOCK";
const HOUR_MS = 3_600_000;

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
  if (minutes < 1) return "less than a minute";
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
  const when = ms > 0 ? `in ${span(ms)}` : `${span(-ms)} ago`;
  const restricted = `restricted ${fmt.time(next.window_start)}–${fmt.time(next.window_end)}`;
  return h(
    "a",
    {
      class: `chip news-chip${next.active && guarded ? " warn" : ""}`,
      href: "#/premarket",
      title: `${next.title} (${next.currency}, ${next.impact} impact) · ${restricted}${next.active ? " · window open now" : ""}`,
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
    `${prop.name}${prop.firm ? ` (${prop.firm})` : ""} · ${prop.enforce ? "rules enforced" : "warnings only"}`,
    `Daily loss ${fmt.money(prop.daily_loss_used)} of ${fmt.money(prop.daily_limit)}`,
    `Balance ${fmt.money(prop.balance)} · floor ${fmt.money(prop.drawdown_floor)}`,
  ].join("\n");
  return h(
    "a",
    { class: `chip prop-chip ${cls}`, href: "#/prop", title },
    icon("target"),
    h("span", { class: "chip-label" }, prop.firm || "Prop"),
    ended ? h("strong", {}, prop.status === "passed" ? "Passed" : prop.status === "failed" ? "Failed" : "Ended") : [" room ", h("strong", { class: "num" }, fmt.money(Math.max(room, 0)))],
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
    label.textContent = `Lock trading for ${hours}h`;
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
  const chips = COOLOFF_REASONS.map((reason) => {
    const chip = h("button", { type: "button", class: "chip-toggle", "aria-pressed": "false" }, reason);
    chip.addEventListener("click", () => {
      if (picked.has(reason)) picked.delete(reason);
      else picked.add(reason);
      chip.classList.toggle("on", picked.has(reason));
      chip.setAttribute("aria-pressed", String(picked.has(reason)));
    });
    return chip;
  });
  const note = h("input", { type: "text", maxlength: "120", placeholder: "What's going on? (optional)" });
  const submit = h("button", { class: "btn danger-solid", type: "button" }, icon("power"), label);
  submit.addEventListener("click", async () => {
    const reason = [...COOLOFF_REASONS.filter((r) => picked.has(r)), note.value.trim()].filter(Boolean).join(" · ").slice(0, 200);
    if (await startCooloff(hours, reason, submit)) {
      ref.close();
      toast(`Kill switch on. No new trades until ${fmt.datetime(state.status.cooloff_until)}.`, "success");
    }
  });
  update();

  const ref = modal({
    title: "Kill switch",
    body: h(
      "div",
      { class: "stack ks-body" },
      h(
        "div",
        { class: "ks-intro" },
        h("p", {}, "Stop all new trade entries for a cool-off period. Use it the moment you notice tilt, revenge urges or fatigue - before the next trade, not after it."),
        h(
          "ul",
          {},
          h("li", {}, "Live entries are blocked until the cool-off ends. Past trades can still be logged (they are recorded with a warning)."),
          h("li", {}, "You can extend a running cool-off, but never shorten it."),
          h("li", {}, `Ending it early requires typing ${UNLOCK_PHRASE}.`),
        ),
      ),
      field("Cool-off duration", duration.el),
      h("div", { class: "ks-until" }, icon("clock"), h("span", {}, "Trading resumes ", until)),
      field("Why are you stopping?", h("div", { class: "stack", style: { gap: "8px" } }, h("div", { class: "chips" }, chips), note)),
    ),
    footer: [h("button", { class: "btn ghost", type: "button", onclick: () => ref.close() }, "Cancel"), submit],
  });
}

function openCooloff(status) {
  const ms = msUntil(status.cooloff_until);
  const extendable = [...COOLOFF_HOURS, MAX_COOLOFF_HOURS].filter((hours) => hours * HOUR_MS > ms + 60_000);
  const phrase = h("input", { type: "text", placeholder: `Type ${UNLOCK_PHRASE}`, autocomplete: "off", spellcheck: false, "aria-label": `Type ${UNLOCK_PHRASE} to confirm` });
  const release = h("button", { class: "btn danger", type: "button", disabled: true }, icon("unlock"), "End cool-off now");
  phrase.addEventListener("input", () => (release.disabled = phrase.value.trim() !== UNLOCK_PHRASE));
  release.addEventListener("click", async () => {
    release.disabled = true;
    try {
      await api.post("/api/killswitch/release", { confirm: phrase.value.trim() });
      await loadStatus();
      ref.close();
      toast("Cool-off ended. Trade your plan, not your emotions.", "success");
    } catch (error) {
      toast(error.message, "error");
      release.disabled = false;
    }
  });
  const extendButtons = extendable.map((hours) => {
    const button = h("button", { class: "btn sm", type: "button", title: `Trading resumes ${inHours(hours)}` }, `${hours}h from now`);
    button.addEventListener("click", async () => {
      if (await startCooloff(hours, "", button)) {
        ref.close();
        toast(`Cool-off extended until ${fmt.datetime(state.status.cooloff_until)}.`, "success");
      }
    });
    return button;
  });

  const ref = modal({
    title: "Cool-off active",
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
          h("strong", {}, `${remaining(ms)} left`),
          h("div", { class: "muted small" }, "New trade entries are locked until ", h("span", { class: "num" }, fmt.datetime(status.cooloff_until)), "."),
          status.cooloff_reason ? h("div", { class: "small" }, "Reason: ", status.cooloff_reason) : null,
        ),
      ),
      h(
        "section",
        { class: "ks-section" },
        h("h3", {}, "Extend"),
        h("p", { class: "muted small" }, "Still not ready? Add time. A cool-off can be extended but not shortened."),
        extendButtons.length ? h("div", { class: "row" }, extendButtons) : h("p", { class: "small" }, `Already at the ${MAX_COOLOFF_HOURS}-hour maximum.`),
      ),
      h(
        "section",
        { class: "ks-section" },
        h("h3", {}, "End early"),
        h("p", { class: "muted small" }, "Ending a cool-off early is exactly the impulse it protects you from. Only do it if the reason no longer applies."),
        h("div", { class: "row ks-release" }, phrase, release),
      ),
    ),
    footer: h("button", { class: "btn ghost", type: "button", onclick: () => ref.close() }, "Close"),
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
      index === 0 || ROUTES[index - 1].group !== route.group ? h("div", { class: "nav-group" }, route.group) : null,
      h("a", { href: `#/${route.path}`, dataset: { path: route.path } }, icon(route.icon), h("span", {}, route.label)),
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

  clear(
    document.getElementById("topbar"),
    h("div", { class: "title" }, currentRoute.label),
    h(
      "div",
      { class: "status-strip" },
      status.checklist_required
        ? h(
            "a",
            { class: `chip ${status.checklist_complete ? "ok" : "bad"}`, href: "#/premarket" },
            icon(status.checklist_complete ? "checkCircle" : "xCircle"),
            status.checklist_complete ? "Checklist done" : "Checklist pending",
          )
        : null,
      h("span", { class: "chip" }, "Today ", h("strong", { class: pnlClass(status.realized_pnl_today) }, fmt.signedMoney(status.realized_pnl_today))),
      h(
        "span",
        { class: "chip", title: `Daily loss used: ${fmt.money(status.loss_used)} of ${fmt.money(status.daily_loss_limit)}` },
        "Daily loss",
        h("span", { class: `meter ${meterClass}` }, h("span", { style: { width: `${ratio * 100}%` } })),
        h("strong", { class: "num" }, `${fmt.money(status.loss_used)} / ${fmt.money(status.daily_loss_limit)}`),
      ),
      h(
        "span",
        { class: "chip" },
        "Trades ",
        h("strong", {}, status.max_trades_per_day ? `${status.trades_today}/${status.max_trades_per_day}` : String(status.trades_today)),
      ),
      h("span", { class: `chip ${chip.cls}` }, icon(chip.icon), chip.text),
      cooling
        ? h(
            "button",
            { class: "chip bad", type: "button", title: `Cool-off until ${fmt.datetime(status.cooloff_until)}`, onclick: openKillSwitch },
            icon("lock"),
            `Cool-off · ${remaining(cooloffMs)} left`,
          )
        : null,
      newsChip(status),
      propChip(status),
    ),
    h(
      "button",
      { class: `btn sm killswitch${cooling ? " on" : ""}`, type: "button", onclick: openKillSwitch },
      icon("power"),
      cooling ? "Cool-off active" : "Kill switch",
    ),
  );

  const banner = document.getElementById("banner");
  const shown = status.reasons.filter((r) => r.code !== "CHECKLIST_INCOMPLETE" || currentRoute.path !== "premarket");
  if (!shown.length && status.state !== "warning") {
    clear(banner);
  } else {
    const severe = blocked || status.state === "locked" || status.state === "breached";
    const heading = blocked
      ? "New trade entries are locked"
      : severe
        ? "Daily risk limit breached"
        : status.state === "warning"
          ? "Approaching your daily loss limit"
          : "Trade with caution";
    const link = (reason) => {
      if (reason.code === "CHECKLIST_INCOMPLETE") return h("a", { href: "#/premarket" }, "Open checklist →");
      if (reason.code === "NEWS_WINDOW" && currentRoute.path !== "premarket") return h("a", { href: "#/premarket" }, "Open calendar →");
      if (reason.code.startsWith("PROP_") && currentRoute.path !== "prop") return h("a", { href: "#/prop" }, "Open Prop Firm →");
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
            : h("p", { class: "muted" }, `${fmt.money(status.remaining_loss_budget)} of today's loss budget remains.`),
        ),
      ),
    );
  }

  clear(
    document.getElementById("sidebar-foot"),
    h("div", {}, state.plan.settings.account_name),
    h("div", { class: "equity num" }, fmt.money(status.current_equity)),
    h("div", {}, "Equity · ", h("span", { class: pnlClass(status.current_equity - status.starting_balance) }, fmt.signedMoney(status.current_equity - status.starting_balance)), " all-time"),
  );
}

function errorBox(error) {
  return h("div", { class: "card empty" }, h("strong", {}, "Something went wrong"), error.message || String(error));
}

async function route() {
  const [path, search] = location.hash.replace(/^#\/?/, "").split("?");
  currentRoute = ROUTES.find((r) => r.path === path) || ROUTES[0];
  for (const link of document.querySelectorAll("#nav a")) {
    link.classList.toggle("active", link.dataset.path === currentRoute.path);
    link.toggleAttribute("aria-current", link.dataset.path === currentRoute.path);
  }
  document.title = `${currentRoute.label} · Trading OS`;
  if (state.status) renderStatus(state.status);

  cleanup?.();
  cleanup = null;
  const token = ++renderToken;
  // Each render gets its own container: a slow, superseded render writes into
  // a detached node instead of clobbering the current page.
  const container = h("div", {}, h("div", { class: "loading" }, "Loading…"));
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
  await mountDisclaimer();
  window.addEventListener("hashchange", route);
  route();
  setInterval(() => loadStatus().catch(() => {}), 60_000);
}

boot();
