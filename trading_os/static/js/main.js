// App shell: navigation, hash router and the global discipline status bar.

import { clear, h, icon } from "./dom.js";
import { fmt, pnlClass } from "./format.js";
import { mountDisclaimer } from "./disclaimer.js";
import { loadPlan, loadStatus, onStatus, state } from "./state.js";
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

let currentRoute = ROUTES[0];
let cleanup = null;
let renderToken = 0;

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
    ),
  );

  const banner = document.getElementById("banner");
  const shown = status.reasons.filter((r) => r.code !== "CHECKLIST_INCOMPLETE" || currentRoute.path !== "premarket");
  if (!shown.length && status.state !== "warning") {
    clear(banner);
  } else {
    const severe = blocked || status.state === "locked" || status.state === "breached";
    const heading = blocked ? "New trade entries are locked" : severe ? "Daily risk limit breached" : "Approaching your daily loss limit";
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
            ? h("ul", {}, shown.map((r) => h("li", {}, r.message, r.code === "CHECKLIST_INCOMPLETE" ? [" ", h("a", { href: "#/premarket" }, "Open checklist →")] : null)))
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
  buildNav();
  try {
    await loadPlan();
    await loadStatus();
  } catch (error) {
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
