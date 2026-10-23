// Trading plan configuration: account & risk rules plus the plan's building blocks.

import { api } from "../api.js";
import { clear, confirmDialog, field, h, icon, modal, select, toast } from "../dom.js";
import { currentLocale, fmt } from "../format.js";
import { N_, t } from "../i18n.js";
import { loadPlan, loadStatus, state } from "../state.js";
import { renderNotificationSettings } from "./notifications.js";
import { renderPartnerSettings } from "./partners.js";
import { renderAiSettings } from "./settings-ai.js";
import { renderNetworkSettings } from "./settings-network.js";
import { renderPromoSettings } from "./settings-promos.js";

const TABS = [
  ["rules", N_("Account & risk rules")],
  ["markets", N_("Markets")],
  ["sessions", N_("Sessions")],
  ["strategies", N_("Strategies")],
  ["confluences", N_("Confluences")],
  ["checklist", N_("Checklist")],
  ["mistakes", N_("Mistakes")],
  ["notifications", N_("Notifications")],
  ["ai", N_("AI assistant")],
  ["partners", N_("Partners")],
  ["promos", N_("Promotions")],
  ["network", N_("Network & access")],
];
// Tabs rendered by other modules into their own container.
const MOUNTED_TABS = { notifications: renderNotificationSettings, ai: renderAiSettings, partners: renderPartnerSettings, promos: renderPromoSettings, network: renderNetworkSettings };
const STRATEGY_STATUS = {
  live: { cls: "good", icon: "play", label: N_("Live") },
  incubator: { cls: "warn", icon: "flame", label: N_("Incubator") },
  retired: { cls: "", icon: "x", label: N_("Retired") },
};
// Labels of the asset-class codes (the codes themselves are sent to the API).
const ASSET_CLASS_LABEL = {
  forex: N_("Forex"),
  metal: N_("Metal"),
  index: N_("Index"),
  commodity: N_("Commodity"),
  crypto: N_("Crypto"),
  stock: N_("Stock"),
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
                boxes.map(({ option, box }) => h("label", { class: "check" }, box, f.options.includes(option) ? option : t("{option} (not in plan)", { option }))),
              )
            : h("div", { class: "muted small" }, f.empty || t("Nothing to choose from yet.")),
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
  return h("span", { class: `badge ${meta.cls}` }, icon(meta.icon), t(meta.label));
}

function copilotSummary(strategy) {
  const parts = [];
  if (strategy.required_confluences.length) parts.push(t("Needs {confluences}", { confluences: strategy.required_confluences.join(", ") }));
  if (strategy.allowed_sessions.length) parts.push(t("Sessions {sessions}", { sessions: strategy.allowed_sessions.join(", ") }));
  if (strategy.allowed_timeframes.length) parts.push(t("TF {timeframes}", { timeframes: strategy.allowed_timeframes.join(", ") }));
  if (strategy.allowed_directions !== "both") parts.push(strategy.allowed_directions === "long" ? t("Long only") : t("Short only"));
  if (strategy.min_rr !== null && strategy.min_rr !== undefined) parts.push(`R:R ≥ 1:${strategy.min_rr}`);
  if (strategy.require_bias_alignment) parts.push(t("With daily bias"));
  return parts.length ? h("span", { class: "small" }, parts.join(" · ")) : h("span", { class: "muted small" }, t("No parameters - any setup"));
}

const yesNo = (value) => (value ? h("span", { class: "pos", "aria-label": t("Yes") }, icon("check")) : h("span", { class: "muted", "aria-label": t("No") }, icon("x")));

async function refreshPlan() {
  await loadPlan();
  await loadStatus();
}

// --------------------------------------------------------- generic entities

/**
 * ``text`` holds the entity's own translated sentences - add, edit, saved, deleted, remove (dialog title),
 * removeAll (dialog title when related data goes too) and empty - written out per entity because gender and
 * word order differ between languages.
 */
function entitySection({ title, description, endpoint, fields, columns, defaults = {}, text, rowActions }) {
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
          toast(text.saved, "success");
          ref.close();
          await refreshPlan();
          await load();
        } catch (error) {
          toast(error.message, "error");
        } finally {
          save.disabled = false;
        }
      },
    }, t("Save"));
    const ref = modal({ title: row ? text.edit : text.add, body: form.el, footer: [h("button", { class: "btn ghost", type: "button", onclick: () => ref.close() }, t("Cancel")), save] });
  }

  async function remove(row) {
    const name = row.name || row.symbol || row.label;
    if (!(await confirmDialog(t('Delete "{name}"? Past trades keep their recorded values.', { name }), { title: text.remove, confirmText: t("Delete"), danger: true }))) return;
    try {
      try {
        await api.del(`${endpoint}/${row.id}`);
      } catch (error) {
        // Still in use (e.g. a strategy with backtests or alert rules): say what goes with it, then confirm.
        if (error.status !== 409 || !(await confirmDialog(error.message, { title: text.removeAll, confirmText: t("Delete everything"), danger: true }))) throw error;
        await api.del(`${endpoint}/${row.id}?force=true`);
      }
      toast(text.deleted, "success");
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
                      h("button", { class: "btn ghost sm icon-only", type: "button", "aria-label": t("Edit"), onclick: () => openEditor(row) }, icon("edit")),
                      h("button", { class: "btn ghost sm icon-only", type: "button", "aria-label": t("Delete"), onclick: () => remove(row) }, icon("trash")),
                    ),
                  ),
                ),
              ),
            ),
          )
        : h("div", { class: "empty" }, text.empty),
    );
  }

  const card = h(
    "div",
    { class: "card" },
    h("div", { class: "card-head" }, h("div", {}, h("h2", {}, title), h("p", {}, description)), h("button", { class: "btn primary", type: "button", onclick: () => openEditor() }, icon("plus"), text.add)),
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
      { name: "account_name", label: t("Account name"), maxlength: "80" },
      { name: "currency", label: t("Account currency"), type: "select", options: currencies.includes(s.currency) ? currencies : [s.currency, ...currencies] },
      { name: "starting_balance", label: t("Starting balance"), type: "number", min: "0", help: t("Equity = starting balance + net P&L of closed trades") },
      { name: "timezone", label: t("Time zone"), list: "tz-options", help: t("Time zone of your journal timestamps and 'today'") },
      {
        name: "language",
        label: "Language / Lingua",
        type: "select",
        options: (state.plan.enums.languages || []).map((l) => [l.code, l.name]),
        help: t("Interface, guardrail messages, notifications and Vision AI answers"),
      },
      {
        name: "onboarding_video_url",
        label: t("Onboarding video link"),
        type: "url",
        maxlength: "300",
        placeholder: "https://www.youtube.com/watch?v=…",
        help: t("YouTube, Vimeo or an .mp4 / .webm link, played in the Getting started guide (play button in the top bar)"),
        full: true,
      },
    ],
    s,
  );
  const tradeRules = buildForm(
    [
      { name: "default_risk_pct", label: t("Default risk per trade (%)"), type: "number", min: "0", step: "0.05" },
      { name: "max_risk_pct", label: t("Max risk per trade (%)"), type: "number", min: "0", step: "0.05" },
      { name: "min_rr", label: t("Minimum reward : risk (1 : x)"), type: "number", min: "0", step: "0.1", help: t("0 disables the R:R rule") },
      {
        name: "trade_rule_mode",
        label: t("When a trade rule is broken"),
        type: "select",
        options: [
          ["block", t("Block the entry")],
          ["warn", t("Warn only")],
        ],
      },
    ],
    s,
  );
  const dailyRules = buildForm(
    [
      { name: "max_daily_loss_pct", label: t("Max daily loss (%)"), type: "number", min: "0", step: "0.1", help: t("Of equity at the start of the day") },
      { name: "max_trades_per_day", label: t("Max trades per day"), type: "number", min: "0", step: "1", help: t("0 = unlimited") },
      {
        name: "daily_limit_mode",
        label: t("When a daily limit is hit"),
        type: "select",
        options: [
          ["lock", t("Lock new entries")],
          ["warn", t("Warn only")],
        ],
      },
    ],
    s,
  );
  const processRules = buildForm(
    [
      { name: "require_checklist", label: t("Require the pre-market checklist before entries"), type: "checkbox", full: true },
      { name: "warn_outside_sessions", label: t("Check entries against my active sessions"), type: "checkbox", full: true },
      { name: "fomo_guard", label: t("Anti-FOMO: warn when a Vision AI idea chases an extended move far from its stop"), type: "checkbox", full: true },
      {
        name: "session_rule_mode",
        label: t("Entries outside my active sessions"),
        type: "select",
        options: [
          ["warn", t("Warn only")],
          ["block", t("Block live entries")],
        ],
        help: t("Your active sessions are set under Sessions. Trades logged after the fact are always saved, with the warning attached."),
      },
    ],
    s,
  );

  const newsRules = buildForm(
    [
      {
        name: "news_mode",
        label: t("During a news window"),
        type: "select",
        options: [
          ["off", t("Off (calendar is informational)")],
          ["warn", t("Warn on entries")],
          ["block", t("Block live entries")],
        ],
      },
      {
        name: "news_min_impact",
        label: t("Minimum impact"),
        type: "select",
        options: [
          ["high", t("High only")],
          ["medium", t("Medium and high")],
          ["low", t("All releases")],
        ],
      },
      { name: "news_before_min", label: t("Minutes before the release"), type: "number", min: "0", max: "240", step: "1" },
      { name: "news_after_min", label: t("Minutes after the release"), type: "number", min: "0", max: "240", step: "1" },
      {
        name: "news_match_currency",
        label: t("Only events in the traded market's currencies"),
        type: "checkbox",
        full: true,
        help: t('USD news restricts XAUUSD and EURUSD but not GER40. Events with currency "ALL" always apply. Events can override the minutes.'),
      },
    ],
    s,
  );
  const positionDefaults = buildForm(
    [
      { name: "default_auto_be", label: t("Move stop to break-even at TP1 by default"), type: "checkbox", full: true },
      { name: "default_trail_pips", label: t("Default trailing distance (pips/points, 0 = off)"), type: "number", min: "0", step: "0.1", full: true },
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
        ? `${
            state.status.cooloff_reason
              ? t("Cool-off active until {until} ({reason}).", { until: fmt.datetime(state.status.cooloff_until), reason: state.status.cooloff_reason })
              : t("Cool-off active until {until}.", { until: fmt.datetime(state.status.cooloff_until) })
          } `
        : "",
      t("The Kill switch in the top bar stops all new entries for 4–48 hours. A cool-off can be extended but not shortened, and ending it early requires typing {phrase}.", { phrase: "UNLOCK" }),
    ),
  );

  const browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const useBrowserZone = h("button", { class: "btn sm", type: "button", onclick: () => (account.controls.get("timezone").control.value = browserZone) }, t("Use {zone}", { zone: browserZone }));

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
        toast(t("Trading plan saved"), "success");
      } catch (error) {
        toast(error.message, "error");
      } finally {
        save.disabled = false;
      }
    },
  }, t("Save rules"));

  const block = (title, description, form, extra, footer) =>
    h("div", { class: "card" }, h("div", { class: "card-head" }, h("div", {}, h("h2", {}, title), h("p", {}, description)), extra), form.el, footer);

  return h(
    "div",
    { class: "stack" },
    tzList,
    block(t("Account"), t("Equity and time settings"), account, useBrowserZone),
    h(
      "div",
      { class: "grid cols-2" },
      block(t("Per-trade rules"), t("Checked on every entry and in the calculator"), tradeRules),
      block(t("Daily limits"), t("Protect the account from a bad day"), dailyRules),
    ),
    h(
      "div",
      { class: "grid cols-2" },
      block(t("Process rules"), t("Discipline before and during the session"), processRules),
      block(t("Position management defaults"), t("Pre-filled on new trades and signals"), positionDefaults),
    ),
    block(t("News & cool-off"), t("Economic-calendar guardrail and the emergency kill switch"), newsRules, h("a", { class: "btn sm", href: "#/premarket" }, icon("calendar"), t("Calendar")), cooloffNote),
    h(
      "div",
      { class: "row" },
      h("span", { class: "muted small" }, t("Blocking rules stop new open positions dated today. Closed trades logged after the fact are always recorded, with any violations attached.")),
      h("span", { class: "spacer" }),
      save,
    ),
  );
}

function utcToZone(hhmm, tz) {
  const [hours, minutes] = hhmm.split(":").map(Number);
  const date = new Date();
  date.setUTCHours(hours, minutes, 0, 0);
  try {
    return new Intl.DateTimeFormat(currentLocale(), { hour: "2-digit", minute: "2-digit", timeZone: tz }).format(date);
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
      title: t("Markets"),
      text: {
        add: t("Add market"),
        edit: t("Edit market"),
        saved: t("Market saved"),
        deleted: t("Market deleted"),
        remove: t("Delete market"),
        removeAll: t("Delete market and its data"),
        empty: t("No markets yet."),
      },
      description: t("Instruments you trade and their contract specification (used for sizing and auto P&L)"),
      endpoint: "/api/markets",
      defaults: { asset_class: "forex", quote_currency: currency, contract_size: 100000, pip_size: 0.0001, min_lot: 0.01, lot_step: 0.01, max_lot: 100, active: true },
      fields: [
        { name: "symbol", label: t("Symbol"), placeholder: "XAUUSD" },
        { name: "name", label: t("Name") },
        {
          name: "asset_class",
          label: t("Asset class"),
          type: "select",
          options: enums.asset_classes.map((c) => [c, ASSET_CLASS_LABEL[c] ? t(ASSET_CLASS_LABEL[c]) : c[0].toUpperCase() + c.slice(1)]),
        },
        { name: "base_currency", label: t("Base currency"), placeholder: t("e.g. EUR (blank for indices)"), maxlength: "3" },
        { name: "quote_currency", label: t("Quote currency"), maxlength: "3" },
        { name: "contract_size", label: t("Contract size (units per lot)"), type: "number", min: "0", help: t("FX 100000 · Gold 100 · Indices often 1") },
        { name: "pip_size", label: t("Pip / point size"), type: "number", min: "0", help: t("FX 0.0001 (JPY 0.01) · Gold 0.1 · Indices 1") },
        { name: "min_lot", label: t("Min lot"), type: "number", min: "0" },
        { name: "lot_step", label: t("Lot step"), type: "number", min: "0" },
        { name: "max_lot", label: t("Max lot"), type: "number", min: "0" },
        { name: "active", label: t("Active"), type: "checkbox" },
        { name: "notes", label: t("Notes"), type: "textarea", full: true, rows: 2 },
      ],
      columns: [
        { label: t("Symbol"), value: (m) => h("strong", {}, m.symbol) },
        { label: t("Name"), value: (m) => m.name },
        { label: t("Class"), value: (m) => m.asset_class },
        { label: t("Quote"), value: (m) => m.quote_currency },
        { label: t("Contract"), value: (m) => m.contract_size.toLocaleString(currentLocale()), num: true },
        { label: t("Pip size"), value: (m) => String(m.pip_size), num: true },
        {
          label: t("Pip value / lot"),
          value: (m) => (m.quote_currency === currency ? fmt.money(m.contract_size * m.pip_size) : `${(m.contract_size * m.pip_size).toLocaleString(currentLocale())} ${m.quote_currency}`),
          num: true,
        },
        { label: t("Lots"), value: (m) => `${m.min_lot}–${m.max_lot} / ${m.lot_step}`, num: true },
        { label: t("Active"), value: (m) => yesNo(m.active) },
      ],
    }),
    sessions: entitySection({
      title: t("Sessions"),
      text: {
        add: t("Add session"),
        edit: t("Edit session"),
        saved: t("Session saved"),
        deleted: t("Session deleted"),
        remove: t("Delete session"),
        removeAll: t("Delete session and its data"),
        empty: t("No sessions yet."),
      },
      description: t('Session windows in UTC. "Active" sessions form your trading window. Times shown in {timezone}.', { timezone: settings.timezone }),
      endpoint: "/api/sessions",
      defaults: { start_utc: "07:00", end_utc: "16:00", active: true },
      fields: [
        { name: "name", label: t("Name") },
        { name: "start_utc", label: t("Start (UTC)"), type: "time" },
        { name: "end_utc", label: t("End (UTC)"), type: "time", help: t("May be earlier than start (wraps midnight)") },
        { name: "active", label: t("Part of my trading window"), type: "checkbox" },
      ],
      columns: [
        { label: t("Session"), value: (s) => h("strong", {}, s.name) },
        { label: "UTC", value: (s) => `${s.start_utc} – ${s.end_utc}`, num: true },
        {
          label: t("Your time ({timezone})", { timezone: settings.timezone }),
          value: (s) => `${utcToZone(s.start_utc, settings.timezone)} – ${utcToZone(s.end_utc, settings.timezone)}`,
          num: true,
        },
        { label: t("Trading window"), value: (s) => yesNo(s.active) },
      ],
    }),
    strategies: entitySection({
      title: t("Strategies"),
      text: {
        add: t("Add strategy"),
        edit: t("Edit strategy"),
        saved: t("Strategy saved"),
        deleted: t("Strategy deleted"),
        remove: t("Delete strategy"),
        removeAll: t("Delete strategy and its data"),
        empty: t("No strategies yet."),
      },
      description: t("Your playbook. The Strategy Co-Pilot checks every entry against the chosen strategy's parameters."),
      endpoint: "/api/strategies",
      defaults: { min_confluences: 2, active: true, status: "live", allowed_directions: "both", required_confluences: [], allowed_sessions: [], allowed_timeframes: [] },
      fields: [
        { name: "name", label: t("Name") },
        {
          name: "status",
          label: t("Status"),
          type: "select",
          options: [
            ["live", t("Live")],
            ["incubator", t("In test (incubator)")],
            ["retired", t("Retired")],
          ],
        },
        { name: "timeframes", label: t("Timeframes (notes)"), placeholder: t("H4 bias, M15 entry") },
        { name: "min_confluences", label: t("Min confluences"), type: "number", min: "0", step: "1", help: t("Never below the required confluences") },
        { name: "active", label: t("Active"), type: "checkbox" },
        { name: "description", label: t("Description"), type: "textarea", full: true, rows: 2 },
        { name: "rules", label: t("Entry / exit rules"), type: "textarea", full: true, rows: 5 },
        {
          type: "heading",
          label: t("Strategy Co-Pilot"),
          help: t("Entries with this strategy must meet every parameter below (blocked or warned per your trade-rule mode). Empty groups allow anything."),
        },
        { name: "required_confluences", label: t("Required confluences"), type: "multi", options: confluenceNames, empty: t("Add confluences first (Confluences tab).") },
        { name: "allowed_sessions", label: t("Allowed sessions"), type: "multi", options: sessionNames, empty: t("Add sessions first (Sessions tab).") },
        { name: "allowed_timeframes", label: t("Allowed entry timeframes"), type: "multi", options: enums.timeframes },
        {
          name: "allowed_directions",
          label: t("Allowed directions"),
          type: "select",
          options: [
            ["both", t("Long and short")],
            ["long", t("Long only")],
            ["short", t("Short only")],
          ],
        },
        {
          name: "min_rr",
          label: t("Minimum R:R (1 : x)"),
          type: "number",
          min: "0",
          step: "0.1",
          placeholder: t("Account 1:{rr}", { rr: settings.min_rr }),
          help: t("Blank = account minimum"),
        },
        {
          name: "backtest_risk_per_trade",
          label: t("Backtest risk per trade ({currency})", { currency }),
          type: "number",
          min: "0",
          step: "any",
          placeholder: t("Account default"),
          help: t("Money per 1R in backtests. Blank = account default risk"),
        },
        { name: "require_bias_alignment", label: t("Only trade in the direction of the daily bias"), type: "checkbox", full: true },
      ],
      columns: [
        { label: t("Strategy"), value: (s) => h("strong", {}, s.name) },
        { label: t("Status"), value: (s) => statusBadge(s.status) },
        { label: t("Co-Pilot"), value: (s) => copilotSummary(s), wrap: true },
        { label: t("Min confluences"), value: (s) => s.min_confluences, num: true },
        { label: t("Rules"), value: (s) => s.rules || s.description || "—", wrap: true },
        { label: t("Active"), value: (s) => yesNo(s.active) },
      ],
    }),
    confluences: entitySection({
      title: t("Confluences"),
      text: {
        add: t("Add confluence"),
        edit: t("Edit confluence"),
        saved: t("Confluence saved"),
        deleted: t("Confluence deleted"),
        remove: t("Delete confluence"),
        removeAll: t("Delete confluence and its data"),
        empty: t("No confluences yet."),
      },
      description: t("Factors you tick when logging a trade. Each one gets its own row in the dashboard's edge breakdown."),
      endpoint: "/api/confluences",
      defaults: { active: true },
      fields: [
        { name: "name", label: t("Name"), full: true },
        { name: "description", label: t("Description"), type: "textarea", full: true, rows: 2 },
        { name: "active", label: t("Active"), type: "checkbox" },
      ],
      columns: [
        { label: t("Confluence"), value: (c) => h("strong", {}, c.name) },
        { label: t("Description"), value: (c) => c.description || "—", wrap: true },
        { label: t("Active"), value: (c) => yesNo(c.active) },
      ],
    }),
    checklist: entitySection({
      title: t("Pre-market checklist"),
      text: {
        add: t("Add checklist item"),
        edit: t("Edit checklist item"),
        saved: t("Checklist item saved"),
        deleted: t("Checklist item deleted"),
        remove: t("Delete checklist item"),
        removeAll: t("Delete checklist item and its data"),
        empty: t("No pre-market checklist yet."),
      },
      description: t("Required items must be ticked before new entries are enabled (when the gate is on)"),
      endpoint: "/api/checklist-items",
      defaults: (rows) => ({ category: "General", required: true, active: true, sort_order: rows.length ? Math.max(...rows.map((r) => r.sort_order)) + 1 : 0 }),
      fields: [
        { name: "label", label: t("Item"), full: true },
        { name: "category", label: t("Category"), placeholder: t("Macro, HTF Analysis, Key Levels…") },
        { name: "sort_order", label: t("Order"), type: "number", step: "1" },
        { name: "required", label: t("Required"), type: "checkbox" },
        { name: "active", label: t("Active"), type: "checkbox" },
      ],
      columns: [
        { label: t("Category"), value: (i) => i.category },
        { label: t("Item"), value: (i) => i.label, wrap: true },
        { label: t("Required"), value: (i) => yesNo(i.required) },
        { label: t("Active"), value: (i) => yesNo(i.active) },
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
          h("button", { class: "btn ghost sm icon-only", type: "button", "aria-label": t("Move up"), disabled: index === 0, onclick: () => move(-1) }, icon("up")),
          h("button", { class: "btn ghost sm icon-only", type: "button", "aria-label": t("Move down"), disabled: index === rows.length - 1, onclick: () => move(1) }, icon("down")),
        ];
      },
    }),
    mistakes: entitySection({
      title: t("Mistakes"),
      text: {
        add: t("Add mistake"),
        edit: t("Edit mistake"),
        saved: t("Mistake saved"),
        deleted: t("Mistake deleted"),
        remove: t("Delete mistake"),
        removeAll: t("Delete mistake and its data"),
        empty: t("No mistakes yet."),
      },
      description: t("Your mistake library. Tag trades and daily reviews with these to see what each error costs you."),
      endpoint: "/api/mistakes",
      defaults: { active: true },
      fields: [
        { name: "name", label: t("Name"), full: true, maxlength: "60" },
        { name: "description", label: t("Description"), type: "textarea", full: true, rows: 2 },
        { name: "active", label: t("Active"), type: "checkbox" },
      ],
      columns: [
        { label: t("Mistake"), value: (m) => h("strong", {}, m.name) },
        { label: t("Description"), value: (m) => m.description || "—", wrap: true },
        { label: t("Active"), value: (m) => yesNo(m.active) },
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
    clear(tabs, TABS.map(([key, label]) => h("button", { type: "button", role: "tab", class: key === activeTab ? "on" : "", "aria-selected": String(key === activeTab), onclick: () => ((activeTab = key), show()) }, t(label))));
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

  clear(root, h("div", { class: "page-head" }, h("div", {}, h("h1", {}, t("Trading plan")), h("p", {}, t("Define how you trade. The OS enforces it.")))), tabs, content);
  await show();
}
