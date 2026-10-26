// App shell top bar (v1.5): the account switcher, the getting-started guide button and the profile menu.
// main.js renderStatus() passes the page title, the status strip and the Kill switch on every status refresh;
// the switcher and the profile menu stay the same nodes, so an open menu or a focused button survives a refresh.

import { accountMoney, colorDot, initials, kindBadge, shownAccount } from "./accounts-ui.js";
import { api } from "./api.js";
import { clear, h, icon, toast } from "./dom.js";
import { fmt } from "./format.js";
import { N_, t, tn } from "./i18n.js";
import { openOnboarding } from "./onboarding.js";
import { accounts, activeAccount, onStatus, reloadAccounts, setActiveAccount, state } from "./state.js";

const FALLBACK_LANGUAGES = [
  { code: "en", name: "English" },
  { code: "it", name: "Italiano" },
];

let summary = null; // GET /api/accounts/summary?account=all: every account's balance, equity, floating P&L, margin
let summarySeq = 0;
let onSwitched = () => {};
const slots = {};

// ------------------------------------------------------------------ data

const SYNCED_FIELDS = ["name", "kind", "status", "color", "currency", "broker"];

/** True when accounts were added, removed or renamed elsewhere (another tab or device) since the plan loaded. */
function plansAccountsAreStale(listed) {
  const known = new Map(accounts().map((account) => [account.id, account]));
  return listed.length !== known.size || listed.some((account) => !known.has(account.id) || SYNCED_FIELDS.some((key) => known.get(account.id)[key] !== account[key]));
}

/** Refresh every account's figures (the switcher's equities). Only the latest answer is kept. */
export async function loadAccountsSummary() {
  const seq = ++summarySeq;
  const data = await api.get("/api/accounts/summary?account=all");
  if (seq !== summarySeq) return data;
  summary = data;
  if (plansAccountsAreStale(data.accounts)) await reloadAccounts().catch(() => {});
  slots.switcher?.update();
  slots.profile?.update();
  return data;
}

function metricsOf(accountId) {
  return summary?.accounts.find((account) => account.id === accountId)?.metrics || null;
}

function accountById(accountId) {
  return summary?.accounts.find((account) => account.id === accountId) || accounts().find((account) => account.id === accountId) || null;
}

/** "Leaves out 1 archived account · 1 account in another currency" for the "All accounts" entry. */
function excludedNote() {
  const excluded = (summary?.scope.excluded || []).map(accountById).filter(Boolean);
  const archived = excluded.filter((account) => account.status !== "active").length;
  const demo = excluded.filter((account) => account.status === "active" && account.kind === "demo").length;
  const otherCurrency = excluded.length - archived - demo;
  const parts = [];
  if (otherCurrency) parts.push(tn(otherCurrency, "{n} account in another currency left out", "{n} accounts in another currency left out"));
  if (demo) parts.push(tn(demo, "{n} demo account left out", "{n} demo accounts left out"));
  if (archived) parts.push(tn(archived, "{n} archived account left out", "{n} archived accounts left out"));
  return parts.join(" · ");
}

// ------------------------------------------------------------------ shared menu behaviour

/** Close ``widget`` on a click outside it or when focus leaves it. */
function dismissable(widget) {
  const onPointer = (event) => {
    if (!widget.el.contains(event.target)) widget.close(false);
  };
  widget.el.addEventListener("focusout", (event) => {
    if (widget.isOpen() && event.relatedTarget && !widget.el.contains(event.relatedTarget)) widget.close(false);
  });
  return {
    listen: () => document.addEventListener("mousedown", onPointer, true),
    unlisten: () => document.removeEventListener("mousedown", onPointer, true),
  };
}

/** Arrow keys, Home and End over ``items`` (wrapping); returns the new index or null when the key is not one. */
function moveIndex(event, index, count) {
  if (!count) return null;
  switch (event.key) {
    case "ArrowDown":
      return (index + 1) % count;
    case "ArrowUp":
      return (index - 1 + count) % count;
    case "Home":
      return 0;
    case "End":
      return count - 1;
    default:
      return null;
  }
}

// ------------------------------------------------------------------ account switcher

function createSwitcher() {
  const listId = "acct-switch-list";
  const button = h("button", { type: "button", class: "acct-switch-btn", "aria-haspopup": "listbox", "aria-expanded": "false", "aria-controls": listId });
  const list = h("ul", { class: "acct-options", role: "listbox", id: listId, "aria-label": t("Account shown") });
  const menu = h(
    "div",
    { class: "acct-menu", hidden: true },
    h("div", { class: "acct-menu-head" }, t("Show on every page")),
    list,
    h("div", { class: "acct-menu-foot" }, h("a", { class: "acct-manage", href: "#/accounts", onclick: () => widget.close(false) }, icon("wallet"), t("Manage accounts"), icon("arrowRight"))),
  );
  const el = h("div", { class: "acct-switch" }, button, menu);
  let open = false;
  let options = []; // [{id, el}]
  let index = 0;

  const currentId = () => state.plan?.settings.active_account_id ?? null;

  function entries() {
    const active = currentId();
    const listed = accounts().filter((account) => account.status === "active" || account.id === active);
    return [{ id: null }, ...listed.map((account) => ({ id: account.id, account: accountById(account.id) || account }))];
  }

  function optionNode({ id, account }) {
    const selected = id === currentId();
    if (id === null) {
      const note = excludedNote();
      const count = summary?.scope.accounts.length ?? accounts().filter((a) => a.status === "active").length;
      return h(
        "li",
        { role: "option", id: "acct-opt-all", tabindex: "-1", "aria-selected": String(selected), class: "acct-option all" },
        h("span", { class: "acct-option-icon" }, icon("layers")),
        h(
          "span",
          { class: "acct-option-main" },
          h("span", { class: "acct-option-name" }, t("All accounts")),
          h("span", { class: "acct-option-sub" }, tn(count, "{n} account combined", "{n} accounts combined")),
          note ? h("span", { class: "acct-option-note" }, icon("info"), note) : null,
        ),
        h("span", { class: "acct-option-equity num" }, summary ? accountMoney(summary.totals.equity, summary.scope.currency) : ""),
        selected ? icon("check", "icon acct-check") : h("span", { class: "acct-check" }),
      );
    }
    const metrics = metricsOf(id);
    const details = [account.broker, account.status !== "active" ? t("Archived account") : null].filter(Boolean).join(" · ");
    return h(
      "li",
      { role: "option", id: `acct-opt-${id}`, tabindex: "-1", "aria-selected": String(selected), class: "acct-option" },
      h("span", { class: "acct-option-icon" }, colorDot(account.color)),
      h(
        "span",
        { class: "acct-option-main" },
        h("span", { class: "acct-option-name" }, h("span", { class: "acct-name-text", title: account.name }, account.name), kindBadge(account.kind)),
        details ? h("span", { class: "acct-option-sub" }, details) : null,
      ),
      h("span", { class: "acct-option-equity num" }, metrics ? accountMoney(metrics.equity, account.currency) : ""),
      selected ? icon("check", "icon acct-check") : h("span", { class: "acct-check" }),
    );
  }

  function renderList() {
    options = entries().map((entry) => ({ id: entry.id, el: optionNode(entry) }));
    options.forEach((option, i) => {
      option.el.addEventListener("click", () => choose(option.id));
      option.el.addEventListener("mousemove", () => (index = i));
    });
    list.replaceChildren(...options.map((option) => option.el));
  }

  function focusOption(next) {
    index = next;
    options[index]?.el.focus();
  }

  function renderButton() {
    const active = activeAccount();
    let content;
    if (active) {
      const metrics = metricsOf(active.id);
      content = [
        colorDot(active.color),
        h("span", { class: "acct-switch-name" }, active.name),
        kindBadge(active.kind),
        metrics ? h("span", { class: "acct-switch-equity num" }, accountMoney(metrics.equity, active.currency)) : null,
      ];
      button.title = t("Account shown: {name}", { name: active.name });
    } else {
      content = [
        h("span", { class: "acct-switch-all" }, icon("layers")),
        h("span", { class: "acct-switch-name" }, t("All accounts")),
        summary ? h("span", { class: "acct-switch-equity num" }, accountMoney(summary.totals.equity, summary.scope.currency)) : null,
      ];
      button.title = t("Account shown: {name}", { name: t("All accounts") });
    }
    button.replaceChildren(...content.filter(Boolean), icon("down", "icon acct-switch-chevron"));
  }

  const outside = dismissable({ el, isOpen: () => open, close: (...args) => close(...args) });

  function openMenu(focusLast = false) {
    if (open) return;
    open = true;
    renderList();
    menu.hidden = false;
    button.setAttribute("aria-expanded", "true");
    outside.listen();
    const selected = options.findIndex((option) => option.id === currentId());
    focusOption(focusLast ? options.length - 1 : Math.max(selected, 0));
    loadAccountsSummary().catch(() => {}); // fresh equities (and accounts added elsewhere) while it is open
  }

  function close(restoreFocus = true) {
    if (!open) return;
    open = false;
    menu.hidden = true;
    button.setAttribute("aria-expanded", "false");
    outside.unlisten();
    if (restoreFocus) button.focus();
    renderButton(); // figures that arrived while the menu was open
  }

  async function choose(accountId) {
    close(true);
    if (accountId === currentId()) return;
    button.setAttribute("aria-busy", "true");
    try {
      await setActiveAccount(accountId);
      const shown = accountId === null ? t("All accounts") : accounts().find((account) => account.id === accountId)?.name;
      toast(t("Now showing {name}", { name: shown }), "success", 2200);
      await onSwitched();
    } catch (error) {
      toast(error.message, "error");
    } finally {
      button.removeAttribute("aria-busy");
    }
  }

  button.addEventListener("click", () => (open ? close(true) : openMenu()));
  button.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      openMenu(event.key === "ArrowUp");
    }
  });
  list.addEventListener("keydown", (event) => {
    const next = moveIndex(event, index, options.length);
    if (next !== null) {
      event.preventDefault();
      focusOption(next);
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      choose(options[index].id);
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      close(true);
    }
  });
  menu.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && open) {
      event.preventDefault();
      event.stopPropagation();
      close(true);
    }
  });

  const widget = {
    el,
    close,
    update() {
      renderButton();
      if (open) {
        const hadFocus = list.contains(document.activeElement);
        renderList();
        if (hadFocus) focusOption(Math.min(index, options.length - 1));
      }
    },
  };
  renderButton();
  return widget;
}

// ------------------------------------------------------------------ profile menu

function avatar(account, size = "") {
  const text = account ? initials(account.name) : "";
  return h(
    "span",
    { class: `avatar ${size}`, style: account?.color ? { "--acct": account.color } : {}, "aria-hidden": "true" },
    text || icon("user"),
  );
}

async function switchLanguage(code) {
  if (code === state.plan.settings.language) return;
  try {
    await api.put("/api/settings", { language: code });
    location.reload(); // every page and the app shell switch language at once
  } catch (error) {
    toast(error.message, "error");
  }
}

function createProfile() {
  const menuId = "profile-menu";
  const button = h("button", { type: "button", class: "avatar-btn", "aria-haspopup": "menu", "aria-expanded": "false", "aria-controls": menuId });
  const menu = h("div", { class: "profile-menu", role: "menu", id: menuId, hidden: true, "aria-label": t("Profile and settings") });
  const el = h("div", { class: "profile" }, button, menu);
  let open = false;
  let items = [];
  let index = 0;

  function renderMenu() {
    const account = shownAccount();
    const go = (hash) => () => {
      close(false);
      location.hash = hash;
    };
    const item = (iconName, text, onclick, extra = {}) =>
      h("button", { type: "button", role: "menuitem", tabindex: "-1", class: "profile-item", onclick, ...extra }, icon(iconName), h("span", {}, text));
    const languages = state.plan?.enums.languages?.length ? state.plan.enums.languages : FALLBACK_LANGUAGES;
    const current = state.plan?.settings.language || "en";
    const languageItems = languages.map((lang) =>
      h(
        "button",
        {
          type: "button",
          role: "menuitemradio",
          tabindex: "-1",
          class: "profile-item lang",
          lang: lang.code,
          "aria-checked": String(lang.code === current),
          onclick: () => {
            close(false);
            switchLanguage(lang.code);
          },
        },
        lang.code === current ? icon("check") : h("span", { class: "icon-space" }),
        h("span", {}, lang.name),
      ),
    );
    const head = h(
      "div",
      { class: "profile-head", role: "presentation" },
      avatar(account, "lg"),
      h(
        "div",
        { class: "profile-head-text" },
        h("strong", {}, account ? account.name : t("All accounts")),
        h("span", { class: "muted small" }, account ? [kindBadge(account.kind), " ", account.broker || ""] : t("Every active account together")),
      ),
    );
    const entries = [
      item("wallet", t("Trading accounts"), go("#/accounts")),
      item("plan", t("Trading plan"), go("#/plan?tab=rules")),
      item("globe", t("Network & access"), go("#/plan?tab=network")),
    ];
    const guideItem = item("playCircle", t("Getting started guide"), () => {
      close(false);
      openOnboarding();
    });
    menu.replaceChildren(
      head,
      h("div", { class: "profile-sep", role: "separator" }),
      ...entries,
      h("div", { class: "profile-sep", role: "separator" }),
      h("div", { class: "profile-group-label", role: "presentation", id: "profile-lang-label" }, "Language / Lingua"),
      h("div", { role: "group", "aria-labelledby": "profile-lang-label" }, languageItems),
      h("div", { class: "profile-sep", role: "separator" }),
      guideItem,
    );
    items = [...entries, ...languageItems, guideItem];
    items.forEach((node, i) => node.addEventListener("mousemove", () => (index = i)));
  }

  function renderButton() {
    const account = shownAccount();
    button.replaceChildren(avatar(account));
    button.setAttribute("aria-label", account ? t("Profile and settings · {name}", { name: account.name }) : t("Profile and settings"));
    button.title = button.getAttribute("aria-label");
  }

  const outside = dismissable({ el, isOpen: () => open, close: (...args) => close(...args) });

  function focusItem(next) {
    index = next;
    items[index]?.focus();
  }

  function openMenu(focusLast = false) {
    if (open) return;
    open = true;
    renderMenu();
    menu.hidden = false;
    button.setAttribute("aria-expanded", "true");
    outside.listen();
    focusItem(focusLast ? items.length - 1 : 0);
  }

  function close(restoreFocus = true) {
    if (!open) return;
    open = false;
    menu.hidden = true;
    button.setAttribute("aria-expanded", "false");
    outside.unlisten();
    if (restoreFocus) button.focus();
  }

  button.addEventListener("click", () => (open ? close(true) : openMenu()));
  button.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      openMenu(event.key === "ArrowUp");
    }
  });
  menu.addEventListener("keydown", (event) => {
    const next = moveIndex(event, index, items.length);
    if (next !== null) {
      event.preventDefault();
      focusItem(next);
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      close(true);
    } else if (event.key === "Tab") {
      close(false);
    }
  });

  renderButton();
  return { el, close, update: renderButton };
}

// ------------------------------------------------------------------ the bar

function guideButton() {
  return h(
    "button",
    { type: "button", class: "guide-btn", "aria-label": t("Getting started guide"), title: t("Getting started guide"), onclick: () => openOnboarding() },
    icon("play"),
  );
}

/**
 * Fill the top bar. ``title``, ``strip`` and ``killswitch`` are rebuilt by the caller on each status refresh;
 * ``onSwitch`` re-renders the current page after another account was chosen.
 */
// ------------------------------------------------------------ connectivity indicator

const LEVEL_LABEL = { ok: N_("Online"), warn: N_("Check connections"), down: N_("Offline") };
const STATE_DOT = { live: "ok", idle: "warn", stale: "warn", error: "bad", off: "", down: "bad", warn: "warn", ok: "ok" };

function createConnectivity() {
  let data = null;
  let open = false;
  const dot = h("span", { class: "conn-dot" });
  const panel = h("div", { class: "conn-panel", role: "dialog", "aria-label": t("Connections"), hidden: true });
  const button = h(
    "button",
    {
      type: "button",
      class: "conn-btn",
      "aria-haspopup": "dialog",
      "aria-expanded": "false",
      title: t("Connections"),
      onclick: () => toggle(!open),
    },
    dot,
    icon("wifi", "icon conn-glyph"),
  );
  const el = h("div", { class: "conn" }, button, panel);

  function toggle(next) {
    open = next;
    panel.hidden = !open;
    button.setAttribute("aria-expanded", String(open));
    if (open) {
      draw();
      refresh(true);
      document.addEventListener("pointerdown", onOutside, true);
      document.addEventListener("keydown", onEsc, true);
    } else {
      document.removeEventListener("pointerdown", onOutside, true);
      document.removeEventListener("keydown", onEsc, true);
    }
  }
  function onOutside(event) {
    if (!el.contains(event.target)) toggle(false);
  }
  function onEsc(event) {
    if (event.key === "Escape") {
      toggle(false);
      button.focus();
    }
  }

  async function refresh(force = false) {
    try {
      data = await api.get(`/api/status/connectivity${force ? "?refresh=true" : ""}`);
    } catch {
      data = null;
    }
    paint();
    if (open) draw();
  }

  function paint() {
    const level = data?.level || "down";
    dot.className = `conn-dot ${STATE_DOT[level] || "bad"}`;
    button.setAttribute("aria-label", t("Connections: {state}", { state: t(LEVEL_LABEL[level] || "Offline") }));
    button.title = button.getAttribute("aria-label");
  }

  function line(dotClass, label, detail) {
    return h("div", { class: "conn-line" }, h("span", { class: `conn-dot ${dotClass}` }), h("span", { class: "conn-label" }, label), detail ? h("span", { class: "conn-detail muted small" }, detail) : null);
  }

  function draw() {
    if (!data) {
      clear(panel, h("div", { class: "conn-head" }, t("Connections")), h("div", { class: "muted small" }, t("Status unavailable")));
      return;
    }
    const net = data.internet.online;
    const bridges = data.bridges || [];
    clear(
      panel,
      h("div", { class: "conn-head" }, t("Connections")),
      line(net ? "ok" : "bad", t("Internet"), net ? t("Online") : t("No connection")),
      data.telegram.configured ? line(STATE_DOT[data.telegram.state] || "", t("Telegram listener"), telegramDetail(data.telegram)) : null,
      bridges.length ? h("div", { class: "conn-sub muted small" }, t("Trading accounts")) : null,
      ...bridges.map((b) => line(STATE_DOT[b.state] || "", b.name, bridgeDetail(b))),
      h("button", { class: "btn ghost sm conn-refresh", type: "button", onclick: () => refresh(true) }, icon("refresh"), t("Refresh")),
    );
  }

  function bridgeDetail(b) {
    if (!b.sending) return t("sending off");
    if (b.state === "error") return b.last_error || t("Error");
    if (b.last_seen_at) return t("seen {when}", { when: fmt.datetime(b.last_seen_at) });
    return t("no data yet");
  }
  function telegramDetail(tg) {
    if (!tg.enabled) return t("paused");
    if (tg.state === "error") return tg.last_error || t("Error");
    return tg.last_ok_at ? t("ok {when}", { when: fmt.datetime(tg.last_ok_at) }) : t("waiting");
  }

  paint();
  return { el, update: () => refresh(false) };
}

export function renderTopbar(bar, { title, strip, killswitch, onSwitch }) {
  onSwitched = onSwitch || (() => {});
  if (!slots.title || !bar.contains(slots.title)) {
    const first = !slots.switcher;
    slots.title = h("div", { class: "title" });
    slots.switcher = createSwitcher();
    slots.profile = createProfile();
    slots.kill = h("span");
    slots.strip = h("div");
    slots.tools = h("div", { class: "topbar-tools" }, slots.switcher.el, slots.kill);
    slots.connectivity = createConnectivity();
    slots.user = h("div", { class: "topbar-user" }, slots.connectivity.el, guideButton(), slots.profile.el);
    bar.replaceChildren(slots.title, slots.tools, slots.user, slots.strip);
    if (first) {
      // Every status refresh (the 60 s poll, a trade, an account switch) also refreshes the accounts' figures.
      onStatus(() => loadAccountsSummary().catch(() => {}));
      onStatus(() => slots.connectivity?.update());
      loadAccountsSummary().catch(() => {});
      slots.connectivity.update();
    }
  }
  slots.title.textContent = title;
  slots.strip.replaceWith(strip);
  slots.strip = strip;
  slots.kill.replaceWith(killswitch);
  slots.kill = killswitch;
  slots.switcher.update();
  slots.profile.update();
}
