// Trading plan configuration: account & risk rules plus the plan's building blocks.

import { api } from "../api.js";
import { clear, confirmDialog, field, h, icon, modal, select, toast } from "../dom.js";
import { fmt } from "../format.js";
import { loadPlan, loadStatus, state } from "../state.js";
import { renderNotificationSettings } from "./notifications.js";
import { renderPartnerSettings } from "./partners.js";
import { renderAiSettings } from "./settings-ai.js";

const TABS = [
  ["rules", "Account & risk rules"],
  ["markets", "Markets"],
  ["sessions", "Sessions"],
  ["strategies", "Strategies"],
  ["confluences", "Confluences"],
  ["checklist", "Checklist"],
  ["mistakes", "Mistakes"],
  ["notifications", "Notifications"],
  ["ai", "AI assistant"],
  ["partners", "Partners"],
];
// Tabs rendered by other modules into their own container.
const MOUNTED_TABS = { notifications: renderNotificationSettings, ai: renderAiSettings, partners: renderPartnerSettings };
const STRATEGY_STATUS = {
  live: { cls: "good", icon: "play", label: "Live" },
  incubator: { cls: "warn", icon: "flame", label: "Incubator" },
  retired: { cls: "", icon: "x", label: "Retired" },
};
let activeTab = "rules";

// ------------------------------------------------------------- form builder

function buildForm(fields, values = {}) {
  const controls = new Map();
  const el = h(
    "div",
    { class: "form-grid" },
    fields.map((f) => {
      if (f.type === "heading") {
        return h("div", { class: "full form-section" }, h("h3", {}, f.label), f.help ? h("p", { class: "muted small" }, f.help) : null);
      }
      const value = values[f.name] ?? f.default ?? "";
      let control;
      if (f.type === "multi") {
        // Checkbox group; selections no longer offered (renamed / removed items) stay visible so they can be unticked.
        const selected = new Set(values[f.name] || []);
        const options = [...f.options, ...[...selected].filter((option) => !f.options.includes(option))];
        const boxes = options.map((option) => ({ option, box: h("input", { type: "checkbox", checked: selected.has(option) }) }));
        controls.set(f.name, { f, control: boxes });
        return h(
          "div",
          { class: "field full" },
          h("span", { class: "label" }, f.label),
          options.length
            ? h(
                "div",
                { class: "check-multi", role: "group", "aria-label": f.label },
                boxes.map(({ option, box }) => h("label", { class: "check" }, box, f.options.includes(option) ? option : `${option} (not in plan)`)),
              )
            : h("div", { class: "muted small" }, f.empty || "Nothing to choose from yet."),
          f.help ? h("div", { class: "help" }, f.help) : null,
        );
      }
      if (f.type === "checkbox") {
        control = h("input", { type: "checkbox", checked: Boolean(values[f.name] ?? f.default) });
        controls.set(f.name, { f, control });
        return h("div", { class: `field${f.full ? " full" : ""}`, style: { justifyContent: "flex-end" } }, h("label", { class: "check" }, control, f.label), f.help ? h("div", { class: "help" }, f.help) : null);
      }
      if (f.type === "select") control = select(f.options, value);
      else if (f.type === "textarea") control = h("textarea", { rows: f.rows || 3, value });
      else if (f.type === "number") control = h("input", { type: "number", step: f.step || "any", min: f.min, max: f.max, value, placeholder: f.placeholder || "" });
      else control = h("input", { type: f.type || "text", value, placeholder: f.placeholder || "", list: f.list, maxlength: f.maxlength });
      controls.set(f.name, { f, control });
      return field(f.label, control, { help: f.help, full: f.full });
    }),
  );
  function read() {
    const out = {};
    for (const [name, { f, control }] of controls) {
      if (f.type === "checkbox") out[name] = control.checked;
      else if (f.type === "multi") out[name] = control.filter(({ box }) => box.checked).map(({ option }) => option);
      else if (f.type === "number") out[name] = control.value === "" ? null : Number(control.value);
      else out[name] = control.value.trim();
    }
    return out;
  }
  return { el, read, controls };
}

function statusBadge(status) {
  const meta = STRATEGY_STATUS[status] || STRATEGY_STATUS.live;
  return h("span", { class: `badge ${meta.cls}` }, icon(meta.icon), meta.label);
}

function copilotSummary(strategy) {
  const parts = [];
  if (strategy.required_confluences.length) parts.push(`Needs ${strategy.required_confluences.join(", ")}`);
  if (strategy.allowed_sessions.length) parts.push(`Sessions ${strategy.allowed_sessions.join(", ")}`);
  if (strategy.allowed_timeframes.length) parts.push(`TF ${strategy.allowed_timeframes.join(", ")}`);
  if (strategy.allowed_directions !== "both") parts.push(strategy.allowed_directions === "long" ? "Long only" : "Short only");
  if (strategy.min_rr !== null && strategy.min_rr !== undefined) parts.push(`R:R ≥ 1:${strategy.min_rr}`);
  if (strategy.require_bias_alignment) parts.push("With daily bias");
  return parts.length ? h("span", { class: "small" }, parts.join(" · ")) : h("span", { class: "muted small" }, "No parameters - any setup");
}

const yesNo = (value) => (value ? h("span", { class: "pos", "aria-label": "Yes" }, icon("check")) : h("span", { class: "muted", "aria-label": "No" }, icon("x")));

async function refreshPlan() {
  await loadPlan();
  await loadStatus();
}

// --------------------------------------------------------- generic entities

function entitySection({ title, description, endpoint, fields, columns, defaults = {}, label, rowActions }) {
  const box = h("div");
  let rows = [];

  async function load() {
    rows = await api.get(endpoint);
    draw();
  }

  function openEditor(row = null) {
    const form = buildForm(fields, row || (typeof defaults === "function" ? defaults(rows) : defaults));
    const save = h("button", {
      class: "btn primary",
      type: "button",
      onclick: async () => {
        save.disabled = true;
        try {
          const data = form.read();
          if (row) await api.put(`${endpoint}/${row.id}`, data);
          else await api.post(endpoint, data);
          toast(`${label} saved`, "success");
          ref.close();
          await refreshPlan();
          await load();
        } catch (error) {
          toast(error.message, "error");
        } finally {
          save.disabled = false;
        }
      },
    }, "Save");
    const ref = modal({ title: row ? `Edit ${label.toLowerCase()}` : `Add ${label.toLowerCase()}`, body: form.el, footer: [h("button", { class: "btn ghost", type: "button", onclick: () => ref.close() }, "Cancel"), save] });
  }

  async function remove(row) {
    const name = row.name || row.symbol || row.label;
    if (!(await confirmDialog(`Delete "${name}"? Past trades keep their recorded values.`, { title: `Delete ${label.toLowerCase()}`, confirmText: "Delete", danger: true }))) return;
    try {
      try {
        await api.del(`${endpoint}/${row.id}`);
      } catch (error) {
        // Still in use (e.g. a strategy with backtests or alert rules): say what goes with it, then confirm.
        if (error.status !== 409 || !(await confirmDialog(error.message, { title: `Delete ${label.toLowerCase()} and its data`, confirmText: "Delete everything", danger: true }))) throw error;
        await api.del(`${endpoint}/${row.id}?force=true`);
      }
      toast(`${label} deleted`, "success");
      await refreshPlan();
      await load();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function draw() {
    clear(
      box,
      rows.length
        ? h(
            "div",
            { class: "table-wrap" },
            h(
              "table",
              {},
              h("thead", {}, h("tr", {}, columns.map((c) => h("th", { class: c.num ? "num" : "" }, c.label)), h("th", {}))),
              h(
                "tbody",
                {},
                rows.map((row, index) =>
                  h(
                    "tr",
                    {},
                    columns.map((c) => h("td", { class: c.num ? "num" : "", style: c.wrap ? { whiteSpace: "normal", minWidth: "220px" } : {} }, c.value(row))),
                    h(
                      "td",
                      { class: "num" },
                      rowActions ? rowActions(row, index, rows, load) : null,
                      h("button", { class: "btn ghost sm icon-only", type: "button", "aria-label": "Edit", onclick: () => openEditor(row) }, icon("edit")),
                      h("button", { class: "btn ghost sm icon-only", type: "button", "aria-label": "Delete", onclick: () => remove(row) }, icon("trash")),
                    ),
                  ),
                ),
              ),
            ),
          )
        : h("div", { class: "empty" }, `No ${title.toLowerCase()} yet.`),
    );
  }

  const card = h(
    "div",
    { class: "card" },
    h("div", { class: "card-head" }, h("div", {}, h("h2", {}, title), h("p", {}, description)), h("button", { class: "btn primary", type: "button", onclick: () => openEditor() }, icon("plus"), `Add ${label.toLowerCase()}`)),
    box,
  );
  return { card, load };
}

// ---------------------------------------------------------------- sections

function rulesSection() {
  const s = state.plan.settings;
  const { currencies } = state.plan.enums;
  const zones = typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : [];
  const tzList = h("datalist", { id: "tz-options" }, zones.map((zone) => h("option", { value: zone })));

  const account = buildForm(
    [
      { name: "account_name", label: "Account name", maxlength: "80" },
      { name: "currency", label: "Account currency", type: "select", options: currencies.includes(s.currency) ? currencies : [s.currency, ...currencies] },
      { name: "starting_balance", label: "Starting balance", type: "number", min: "0", help: "Equity = starting balance + net P&L of closed trades" },
      { name: "timezone", label: "Time zone", list: "tz-options", help: "Time zone of your journal timestamps and 'today'" },
      {
        name: "language",
        label: "Language / Lingua",
        type: "select",
        options: (state.plan.enums.languages || []).map((l) => [l.code, l.name]),
        help: "Interface, guardrail messages, notifications and Vision AI answers",
      },
    ],
    s,
  );
  const tradeRules = buildForm(
    [
      { name: "default_risk_pct", label: "Default risk per trade (%)", type: "number", min: "0", step: "0.05" },
      { name: "max_risk_pct", label: "Max risk per trade (%)", type: "number", min: "0", step: "0.05" },
      { name: "min_rr", label: "Minimum reward : risk (1 : x)", type: "number", min: "0", step: "0.1", help: "0 disables the R:R rule" },
      { name: "trade_rule_mode", label: "When a trade rule is broken", type: "select", options: [["block", "Block the entry"], ["warn", "Warn only"]] },
    ],
    s,
  );
  const dailyRules = buildForm(
    [
      { name: "max_daily_loss_pct", label: "Max daily loss (%)", type: "number", min: "0", step: "0.1", help: "Of equity at the start of the day" },
      { name: "max_trades_per_day", label: "Max trades per day", type: "number", min: "0", step: "1", help: "0 = unlimited" },
      { name: "daily_limit_mode", label: "When a daily limit is hit", type: "select", options: [["lock", "Lock new entries"], ["warn", "Warn only"]] },
    ],
    s,
  );
  const processRules = buildForm(
    [
      { name: "require_checklist", label: "Require the pre-market checklist before entries", type: "checkbox", full: true },
      { name: "warn_outside_sessions", label: "Warn on entries outside my active sessions", type: "checkbox", full: true },
    ],
    s,
  );

  const newsRules = buildForm(
    [
      { name: "news_mode", label: "During a news window", type: "select", options: [["off", "Off (calendar is informational)"], ["warn", "Warn on entries"], ["block", "Block live entries"]] },
      { name: "news_min_impact", label: "Minimum impact", type: "select", options: [["high", "High only"], ["medium", "Medium and high"], ["low", "All releases"]] },
      { name: "news_before_min", label: "Minutes before the release", type: "number", min: "0", max: "240", step: "1" },
      { name: "news_after_min", label: "Minutes after the release", type: "number", min: "0", max: "240", step: "1" },
      {
        name: "news_match_currency",
        label: "Only events in the traded market's currencies",
        type: "checkbox",
        full: true,
        help: 'USD news restricts XAUUSD and EURUSD but not GER40. Events with currency "ALL" always apply. Events can override the minutes.',
      },
    ],
    s,
  );
  const positionDefaults = buildForm(
    [
      { name: "default_auto_be", label: "Move stop to break-even at TP1 by default", type: "checkbox", full: true },
      { name: "default_trail_pips", label: "Default trailing distance (pips/points, 0 = off)", type: "number", min: "0", step: "0.1", full: true },
    ],
    s,
  );
  const cooloffNote = h(
    "div",
    { class: "news-note" },
    icon("power"),
    h(
      "span",
      {},
      state.status?.cooloff_until
        ? `Cool-off active until ${fmt.datetime(state.status.cooloff_until)}${state.status.cooloff_reason ? ` (${state.status.cooloff_reason})` : ""}. `
        : "",
      "The Kill switch in the top bar stops all new entries for 4–48 hours. A cool-off can be extended but not shortened, and ending it early requires typing UNLOCK.",
    ),
  );

  const browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const useBrowserZone = h("button", { class: "btn sm", type: "button", onclick: () => (account.controls.get("timezone").control.value = browserZone) }, `Use ${browserZone}`);

  const save = h("button", {
    class: "btn primary",
    type: "button",
    onclick: async () => {
      save.disabled = true;
      try {
        await api.put("/api/settings", {
          ...account.read(),
          ...tradeRules.read(),
          ...dailyRules.read(),
          ...processRules.read(),
          ...newsRules.read(),
          ...positionDefaults.read(),
        });
        const previousLanguage = state.plan.settings.language;
        await refreshPlan();
        if (state.plan.settings.language !== previousLanguage) {
          location.reload(); // every page and the app shell switch language at once
          return;
        }
        toast("Trading plan saved", "success");
      } catch (error) {
        toast(error.message, "error");
      } finally {
        save.disabled = false;
      }
    },
  }, "Save rules");

  const block = (title, description, form, extra, footer) =>
    h("div", { class: "card" }, h("div", { class: "card-head" }, h("div", {}, h("h2", {}, title), h("p", {}, description)), extra), form.el, footer);

  return h(
    "div",
    { class: "stack" },
    tzList,
    block("Account", "Equity and time settings", account, useBrowserZone),
    h(
      "div",
      { class: "grid cols-2" },
      block("Per-trade rules", "Checked on every entry and in the calculator", tradeRules),
      block("Daily limits", "Protect the account from a bad day", dailyRules),
    ),
    h(
      "div",
      { class: "grid cols-2" },
      block("Process rules", "Discipline before and during the session", processRules),
      block("Position management defaults", "Pre-filled on new trades and signals", positionDefaults),
    ),
    block("News & cool-off", "Economic-calendar guardrail and the emergency kill switch", newsRules, h("a", { class: "btn sm", href: "#/premarket" }, icon("calendar"), "Calendar"), cooloffNote),
    h("div", { class: "row" }, h("span", { class: "muted small" }, "Blocking rules stop new open positions dated today. Closed trades logged after the fact are always recorded, with any violations attached."), h("span", { class: "spacer" }), save),
  );
}

function utcToZone(hhmm, tz) {
  const [hours, minutes] = hhmm.split(":").map(Number);
  const date = new Date();
  date.setUTCHours(hours, minutes, 0, 0);
  try {
    return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", timeZone: tz }).format(date);
  } catch {
    return hhmm;
  }
}

function sections() {
  const { settings, enums } = state.plan;
  const currency = settings.currency;
  const confluenceNames = state.plan.confluences.filter((c) => c.active).map((c) => c.name);
  const sessionNames = state.plan.sessions.map((s) => s.name);
  return {
    markets: entitySection({
      title: "Markets",
      label: "Market",
      description: "Instruments you trade and their contract specification (used for sizing and auto P&L)",
      endpoint: "/api/markets",
      defaults: { asset_class: "forex", quote_currency: currency, contract_size: 100000, pip_size: 0.0001, min_lot: 0.01, lot_step: 0.01, max_lot: 100, active: true },
      fields: [
        { name: "symbol", label: "Symbol", placeholder: "XAUUSD" },
        { name: "name", label: "Name" },
        { name: "asset_class", label: "Asset class", type: "select", options: enums.asset_classes.map((c) => [c, c[0].toUpperCase() + c.slice(1)]) },
        { name: "base_currency", label: "Base currency", placeholder: "e.g. EUR (blank for indices)", maxlength: "3" },
        { name: "quote_currency", label: "Quote currency", maxlength: "3" },
        { name: "contract_size", label: "Contract size (units per lot)", type: "number", min: "0", help: "FX 100000 · Gold 100 · Indices often 1" },
        { name: "pip_size", label: "Pip / point size", type: "number", min: "0", help: "FX 0.0001 (JPY 0.01) · Gold 0.1 · Indices 1" },
        { name: "min_lot", label: "Min lot", type: "number", min: "0" },
        { name: "lot_step", label: "Lot step", type: "number", min: "0" },
        { name: "max_lot", label: "Max lot", type: "number", min: "0" },
        { name: "active", label: "Active", type: "checkbox" },
        { name: "notes", label: "Notes", type: "textarea", full: true, rows: 2 },
      ],
      columns: [
        { label: "Symbol", value: (m) => h("strong", {}, m.symbol) },
        { label: "Name", value: (m) => m.name },
        { label: "Class", value: (m) => m.asset_class },
        { label: "Quote", value: (m) => m.quote_currency },
        { label: "Contract", value: (m) => m.contract_size.toLocaleString(), num: true },
        { label: "Pip size", value: (m) => String(m.pip_size), num: true },
        { label: "Pip value / lot", value: (m) => (m.quote_currency === currency ? fmt.money(m.contract_size * m.pip_size) : `${(m.contract_size * m.pip_size).toLocaleString()} ${m.quote_currency}`), num: true },
        { label: "Lots", value: (m) => `${m.min_lot}–${m.max_lot} / ${m.lot_step}`, num: true },
        { label: "Active", value: (m) => yesNo(m.active) },
      ],
    }),
    sessions: entitySection({
      title: "Sessions",
      label: "Session",
      description: `Session windows in UTC. "Active" sessions form your trading window. Times shown in ${settings.timezone}.`,
      endpoint: "/api/sessions",
      defaults: { start_utc: "07:00", end_utc: "16:00", active: true },
      fields: [
        { name: "name", label: "Name" },
        { name: "start_utc", label: "Start (UTC)", type: "time" },
        { name: "end_utc", label: "End (UTC)", type: "time", help: "May be earlier than start (wraps midnight)" },
        { name: "active", label: "Part of my trading window", type: "checkbox" },
      ],
      columns: [
        { label: "Session", value: (s) => h("strong", {}, s.name) },
        { label: "UTC", value: (s) => `${s.start_utc} – ${s.end_utc}`, num: true },
        { label: `Your time (${settings.timezone})`, value: (s) => `${utcToZone(s.start_utc, settings.timezone)} – ${utcToZone(s.end_utc, settings.timezone)}`, num: true },
        { label: "Trading window", value: (s) => yesNo(s.active) },
      ],
    }),
    strategies: entitySection({
      title: "Strategies",
      label: "Strategy",
      description: "Your playbook. The Strategy Co-Pilot checks every entry against the chosen strategy's parameters.",
      endpoint: "/api/strategies",
      defaults: { min_confluences: 2, active: true, status: "live", allowed_directions: "both", required_confluences: [], allowed_sessions: [], allowed_timeframes: [] },
      fields: [
        { name: "name", label: "Name" },
        { name: "status", label: "Status", type: "select", options: [["live", "Live"], ["incubator", "Incubator (testing)"], ["retired", "Retired"]] },
        { name: "timeframes", label: "Timeframes (notes)", placeholder: "H4 bias, M15 entry" },
        { name: "min_confluences", label: "Min confluences", type: "number", min: "0", step: "1", help: "Never below the required confluences" },
        { name: "active", label: "Active", type: "checkbox" },
        { name: "description", label: "Description", type: "textarea", full: true, rows: 2 },
        { name: "rules", label: "Entry / exit rules", type: "textarea", full: true, rows: 5 },
        { type: "heading", label: "Strategy Co-Pilot", help: "Entries with this strategy must meet every parameter below (blocked or warned per your trade-rule mode). Empty groups allow anything." },
        { name: "required_confluences", label: "Required confluences", type: "multi", options: confluenceNames, empty: "Add confluences first (Confluences tab)." },
        { name: "allowed_sessions", label: "Allowed sessions", type: "multi", options: sessionNames, empty: "Add sessions first (Sessions tab)." },
        { name: "allowed_timeframes", label: "Allowed entry timeframes", type: "multi", options: enums.timeframes },
        { name: "allowed_directions", label: "Allowed directions", type: "select", options: [["both", "Long and short"], ["long", "Long only"], ["short", "Short only"]] },
        { name: "min_rr", label: "Minimum R:R (1 : x)", type: "number", min: "0", step: "0.1", placeholder: `Account 1:${settings.min_rr}`, help: "Blank = account minimum" },
        { name: "backtest_risk_per_trade", label: `Backtest risk per trade (${currency})`, type: "number", min: "0", step: "any", placeholder: "Account default", help: "Money per 1R in backtests. Blank = account default risk" },
        { name: "require_bias_alignment", label: "Only trade in the direction of the daily bias", type: "checkbox", full: true },
      ],
      columns: [
        { label: "Strategy", value: (s) => h("strong", {}, s.name) },
        { label: "Status", value: (s) => statusBadge(s.status) },
        { label: "Co-Pilot", value: (s) => copilotSummary(s), wrap: true },
        { label: "Min confluences", value: (s) => s.min_confluences, num: true },
        { label: "Rules", value: (s) => s.rules || s.description || "—", wrap: true },
        { label: "Active", value: (s) => yesNo(s.active) },
      ],
    }),
    confluences: entitySection({
      title: "Confluences",
      label: "Confluence",
      description: "Factors you tick when logging a trade. Each one gets its own row in the dashboard's edge breakdown.",
      endpoint: "/api/confluences",
      defaults: { active: true },
      fields: [
        { name: "name", label: "Name", full: true },
        { name: "description", label: "Description", type: "textarea", full: true, rows: 2 },
        { name: "active", label: "Active", type: "checkbox" },
      ],
      columns: [
        { label: "Confluence", value: (c) => h("strong", {}, c.name) },
        { label: "Description", value: (c) => c.description || "—", wrap: true },
        { label: "Active", value: (c) => yesNo(c.active) },
      ],
    }),
    checklist: entitySection({
      title: "Pre-market checklist",
      label: "Checklist item",
      description: "Required items must be ticked before new entries are enabled (when the gate is on)",
      endpoint: "/api/checklist-items",
      defaults: (rows) => ({ category: "General", required: true, active: true, sort_order: rows.length ? Math.max(...rows.map((r) => r.sort_order)) + 1 : 0 }),
      fields: [
        { name: "label", label: "Item", full: true },
        { name: "category", label: "Category", placeholder: "Macro, HTF Analysis, Key Levels…" },
        { name: "sort_order", label: "Order", type: "number", step: "1" },
        { name: "required", label: "Required", type: "checkbox" },
        { name: "active", label: "Active", type: "checkbox" },
      ],
      columns: [
        { label: "Category", value: (i) => i.category },
        { label: "Item", value: (i) => i.label, wrap: true },
        { label: "Required", value: (i) => yesNo(i.required) },
        { label: "Active", value: (i) => yesNo(i.active) },
      ],
      rowActions: (row, index, rows, reload) => {
        const move = async (delta) => {
          const ordered = [...rows];
          const [item] = ordered.splice(index, 1);
          ordered.splice(index + delta, 0, item);
          try {
            for (const [position, entry] of ordered.entries()) {
              if (entry.sort_order !== position) await api.put(`/api/checklist-items/${entry.id}`, { ...entry, sort_order: position });
            }
            await refreshPlan();
            await reload();
          } catch (error) {
            toast(error.message, "error");
          }
        };
        return [
          h("button", { class: "btn ghost sm icon-only", type: "button", "aria-label": "Move up", disabled: index === 0, onclick: () => move(-1) }, icon("up")),
          h("button", { class: "btn ghost sm icon-only", type: "button", "aria-label": "Move down", disabled: index === rows.length - 1, onclick: () => move(1) }, icon("down")),
        ];
      },
    }),
    mistakes: entitySection({
      title: "Mistakes",
      label: "Mistake",
      description: "Your mistake library. Tag trades and daily reviews with these to see what each error costs you.",
      endpoint: "/api/mistakes",
      defaults: { active: true },
      fields: [
        { name: "name", label: "Name", full: true, maxlength: "60" },
        { name: "description", label: "Description", type: "textarea", full: true, rows: 2 },
        { name: "active", label: "Active", type: "checkbox" },
      ],
      columns: [
        { label: "Mistake", value: (m) => h("strong", {}, m.name) },
        { label: "Description", value: (m) => m.description || "—", wrap: true },
        { label: "Active", value: (m) => yesNo(m.active) },
      ],
    }),
  };
}

export async function render(root, { params }) {
  if (params.get("tab")) activeTab = params.get("tab");
  if (!TABS.some(([key]) => key === activeTab)) activeTab = "rules";
  const content = h("div");
  const tabs = h("div", { class: "tabs", role: "tablist" });

  async function show() {
    clear(tabs, TABS.map(([key, label]) => h("button", { type: "button", role: "tab", class: key === activeTab ? "on" : "", "aria-selected": String(key === activeTab), onclick: () => ((activeTab = key), show()) }, label)));
    if (activeTab === "rules") {
      clear(content, rulesSection());
      return;
    }
    if (MOUNTED_TABS[activeTab]) {
      const container = h("div");
      clear(content, container);
      await MOUNTED_TABS[activeTab](container);
      return;
    }
    const section = sections()[activeTab];
    clear(content, section.card);
    await section.load();
  }

  clear(root, h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Trading plan"), h("p", {}, "Define how you trade. The OS enforces it."))), tabs, content);
  await show();
}
