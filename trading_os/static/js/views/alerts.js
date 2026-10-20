// Strategy alerts: background scanner, alert feed and scanner rules.
// Alerts are informational - nothing is ever executed automatically. "Review in Signals"
// hands a setup to the Signals page, where sizing, guardrails and the Co-Pilot run.

import { api, query } from "../api.js";
import { clear, confirmDialog, field, h, icon, modal, numberOrNull, segmented, select, toast } from "../dom.js";
import { fmt } from "../format.js";
import { handoff, state } from "../state.js";

const NOTICE = "Alerts are informational: nothing is executed automatically - the final decision is always yours.";
const INTERVAL_LABELS = { 60: "Every minute", 300: "Every 5 minutes", 900: "Every 15 minutes", 1800: "Every 30 minutes", 3600: "Every hour" };
const RESULT_LABEL = { alert: "Alert", no_setup: "No setup", cooldown: "Cooling down", duplicate: "Already alerted", error: "Error" };
const RESULT_BADGE = { alert: "good", no_setup: "", cooldown: "warn", duplicate: "", error: "bad" };
const STATUS_LABEL = { new: "New", seen: "Seen", dismissed: "Dismissed", taken: "Taken" };
const STATUS_BADGE = { new: "open", seen: "", dismissed: "", taken: "good" };
const DIRECTION_LABEL = { both: "Long & short", long: "Long only", short: "Short only" };
const TEMPLATES = [
  {
    label: "Liquidity sweep + structure",
    conditions: [
      { type: "liquidity_sweep", params: {} },
      { type: "break_of_structure", params: {} },
    ],
    min: 0,
  },
  {
    label: "Trend pullback",
    conditions: [
      { type: "ema_trend", params: {} },
      { type: "rsi", params: { mode: "momentum" } },
      { type: "atr_filter", params: {} },
    ],
    min: 0,
  },
  {
    label: "Range breakout",
    conditions: [
      { type: "range_breakout", params: {} },
      { type: "atr_filter", params: {} },
    ],
    min: 0,
  },
];
const REFRESH_MS = 30_000;

let feedFilter = "new";

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

function directionBadge(direction) {
  const long = direction === "long";
  return h("span", { class: `badge ${direction} al-dir` }, icon(long ? "up" : "down"), long ? "Long" : "Short");
}

function switchControl(checked, label, onChange) {
  const input = h("input", { type: "checkbox", role: "switch", checked, "aria-label": label });
  const text = h("span", { class: "al-switch-text" }, checked ? "On" : "Off");
  input.addEventListener("change", async () => {
    text.textContent = input.checked ? "On" : "Off";
    input.disabled = true;
    try {
      await onChange(input.checked);
    } catch (error) {
      input.checked = !input.checked;
      text.textContent = input.checked ? "On" : "Off";
      toast(error.message, "error");
    } finally {
      input.disabled = false;
    }
  });
  return h("label", { class: "al-switch", title: label }, input, text);
}

function rulePayload(rule) {
  const keys = ["name", "enabled", "strategy_id", "symbols", "timeframe", "provider", "provider_symbols", "direction", "conditions", "min_conditions", "stop_method", "stop_buffer_pips", "atr_multiple", "targets_rr", "risk_pct", "cooldown_minutes", "notify"];
  return Object.fromEntries(keys.map((key) => [key, rule[key]]));
}

function scanSummary(results) {
  if (!results.length) return "No enabled rules to scan.";
  const counts = {};
  for (const row of results) counts[row.status] = (counts[row.status] || 0) + 1;
  return Object.entries(counts)
    .map(([status, n]) => (status === "alert" || status === "error" ? plural(n, status) : `${n} ${RESULT_LABEL[status]?.toLowerCase() || status}`))
    .join(" · ");
}

function resultsTable(results) {
  return h(
    "div",
    { class: "table-wrap al-results" },
    h(
      "table",
      {},
      h("thead", {}, h("tr", {}, ["Rule", "Symbol", "Result", "Details"].map((c) => h("th", {}, c)))),
      h(
        "tbody",
        {},
        results.map((row) =>
          h(
            "tr",
            {},
            h("td", {}, row.rule_name),
            h("td", {}, row.symbol),
            h("td", {}, h("span", { class: `badge ${RESULT_BADGE[row.status] || ""}` }, RESULT_LABEL[row.status] || row.status)),
            h("td", { class: "al-msg" }, row.message || "—"),
          ),
        ),
      ),
    ),
  );
}

// ----------------------------------------------------------------- notification hint

async function notificationHint() {
  const link = h("a", { href: "#/plan?tab=notifications" }, "Set up notifications");
  const hint = (title, text) => h("div", { class: "banner warn al-hint" }, icon("bell"), h("div", {}, h("strong", {}, title), text, " ", link));
  let data;
  try {
    data = await api.get("/api/notifications/settings");
  } catch {
    return hint("Alerts reach your phone only with notifications", "Until then they appear on this page.");
  }
  const settings = data.settings || data;
  const enabled = settings.notifications_enabled ?? settings.enabled;
  let channels = data.channels ?? settings.channels;
  if (channels === undefined) channels = await api.get("/api/notifications/channels").catch(() => undefined);
  const list = Array.isArray(channels) ? channels : channels?.channels;
  const active = Array.isArray(list) ? list.filter((channel) => channel.enabled !== false).length : null;
  const events = settings.notify_events;
  if (enabled === false) return hint("Push notifications are off", "Alerts will only appear on this page.");
  if (active === 0) return hint("No notification channel yet", "Add your phone or browser to receive alerts instantly.");
  if (Array.isArray(events) && !events.includes("strategy_alert")) return hint("Strategy alert notifications are switched off", "Alerts will only appear on this page.");
  return null;
}

// ----------------------------------------------------------------- alert feed

function ladder(alert) {
  const details = alert.details || {};
  const rr = details.targets_rr || [];
  const pcts = details.close_pcts || [];
  const targets = alert.take_profits.map((price, index) => ({
    label: `TP${index + 1}`,
    price,
    note: [rr[index] ? `+${fmt.num(rr[index], rr[index] % 1 ? 1 : 0)}R` : "", pcts[index] ? `close ${pcts[index]}%` : ""].filter(Boolean).join(" · "),
    cls: "tp",
  }));
  const rows = [...targets.reverse(), { label: "Entry", price: alert.entry_price, note: "last close", cls: "entry" }, { label: "SL", price: alert.stop_loss, note: details.stop_note || "−1R", cls: "sl" }];
  return h(
    "div",
    { class: "al-ladder", role: "table", "aria-label": "Trade levels" },
    rows.map((row) => h("div", { class: `al-step ${row.cls}`, role: "row" }, h("span", { class: "al-step-label", role: "cell" }, row.label), h("span", { class: "num", role: "cell" }, fmt.price(row.price)), h("span", { class: "al-step-note", role: "cell" }, row.note))),
  );
}

function precheckBox(precheck) {
  const blocking = precheck.filter((v) => v.severity === "block");
  const warnings = precheck.filter((v) => v.severity === "warn");
  if (blocking.length) {
    return h(
      "div",
      { class: "violation block" },
      icon("alert"),
      h("div", {}, h("div", { class: "tag" }, "Pre-check: would be blocked right now"), h("ul", { class: "al-list" }, blocking.map((v) => h("li", {}, v.message)))),
    );
  }
  return h(
    "div",
    { class: "violation ok" },
    icon("checkCircle"),
    h(
      "div",
      {},
      h("div", { class: "tag" }, "Pre-check: all guardrails pass"),
      warnings.length ? h("ul", { class: "al-list" }, warnings.map((v) => h("li", {}, v.message))) : null,
    ),
  );
}

function alertCard(alert, { onReview, onDismiss, onDelete }) {
  const details = alert.details || {};
  const demo = details.provider === "demo";
  const risk = alert.risk_amount ? `${fmt.money(alert.risk_amount)}${details.risk_pct ? ` (${details.risk_pct}%)` : ""}` : "—";
  return h(
    "article",
    { class: `al-alert ${alert.status}`, dataset: { alertId: alert.id } },
    h(
      "div",
      { class: "al-alert-head" },
      directionBadge(alert.direction),
      h("strong", { class: "al-symbol" }, alert.symbol),
      h("span", { class: "badge" }, alert.timeframe),
      alert.strategy ? h("span", { class: "al-strategy" }, alert.strategy) : h("span", { class: "al-strategy muted" }, details.rule_name || "Rule"),
      alert.in_test ? h("span", { class: "badge warn", title: "Strategy is still in the incubator" }, "In test") : null,
      h("span", { class: "spacer" }),
      h("span", { class: `badge ${STATUS_BADGE[alert.status] || ""}` }, STATUS_LABEL[alert.status] || alert.status),
    ),
    h("div", { class: "al-alert-time" }, icon("clock"), h("span", {}, `Signal candle closed ${fmt.datetime(alert.candle_time)}${details.session ? ` · ${details.session}` : ""}`)),
    alert.confluences.length ? h("div", { class: "chips" }, alert.confluences.map((name) => h("span", { class: "chip ok" }, icon("check"), name))) : null,
    h(
      "div",
      { class: "al-alert-body" },
      ladder(alert),
      h(
        "dl",
        { class: "al-dl" },
        h("div", {}, h("dt", {}, "Reward : risk"), h("dd", {}, fmt.rr(alert.planned_rr))),
        h("div", {}, h("dt", {}, "Size"), h("dd", {}, alert.lots ? `${fmt.lots(alert.lots)} lots` : "—")),
        h("div", {}, h("dt", {}, "Risk"), h("dd", {}, risk)),
        details.sizing_note ? h("p", { class: "small muted" }, details.sizing_note) : null,
      ),
    ),
    precheckBox(details.precheck || []),
    demo ? h("p", { class: "al-demo small" }, icon("info"), "Demo data (synthetic) - not real prices. Never trade from it.") : null,
    h(
      "div",
      { class: "al-actions" },
      h("button", { class: "btn primary sm", type: "button", onclick: () => onReview(alert) }, icon("signal"), "Review in Signals"),
      alert.status !== "dismissed" ? h("button", { class: "btn ghost sm", type: "button", onclick: () => onDismiss(alert) }, icon("x"), "Dismiss") : null,
      h("span", { class: "spacer" }),
      h("button", { class: "btn ghost sm icon-only danger", type: "button", "aria-label": `Delete ${alert.symbol} alert`, title: "Delete", onclick: () => onDelete(alert) }, icon("trash")),
    ),
  );
}

// ----------------------------------------------------------------- rule test (dry run)

function conditionRow(c) {
  return h(
    "li",
    { class: `al-check ${c.met ? "met" : ""}` },
    icon(c.met ? "checkCircle" : "xCircle"),
    h(
      "div",
      {},
      h("div", { class: "row al-check-head" }, h("strong", {}, c.label), c.confluence !== c.label ? h("span", { class: "muted small" }, `counts as ${c.confluence}`) : null, c.met && c.direction ? directionBadge(c.direction) : null, c.met && !c.direction ? h("span", { class: "badge" }, "Filter") : null, h("span", { class: "sr-only" }, c.met ? "met" : "not met")),
      h("div", { class: "small al-msg" }, c.explanation),
    ),
  );
}

function testResult(item) {
  const head = h("div", { class: "al-test-head" }, h("h3", {}, item.symbol), item.provider_symbol !== item.symbol ? h("span", { class: "muted small" }, `as ${item.provider_symbol}`) : null);
  if (item.error) return h("section", { class: "al-test" }, head, h("div", { class: "violation block" }, icon("alert"), h("div", {}, item.error)));
  const levels = item.levels;
  const copilot = item.copilot;
  return h(
    "section",
    { class: "al-test" },
    head,
    h("p", { class: "small muted" }, `${item.bars} closed candles · signal candle closed ${fmt.datetime(item.candle_time)}${item.session ? ` (${item.session})` : ""} · close ${fmt.price(item.close)}`),
    item.stale ? h("div", { class: "violation warn" }, icon("clock"), h("div", {}, "The latest candle is old - the market may be closed or the data delayed. The scanner does not alert on stale data.")) : null,
    h("ul", { class: "al-checks" }, item.conditions.map(conditionRow)),
    item.found
      ? h("div", { class: "violation ok" }, icon("checkCircle"), h("div", {}, h("div", { class: "tag" }, "Setup found"), "A scan now would send this alert (unless it was already sent or the rule is cooling down)."))
      : h("div", { class: "violation warn" }, icon("info"), h("div", {}, h("div", { class: "tag" }, "No alert"), item.reason)),
    levels
      ? h(
          "dl",
          { class: "al-dl al-test-levels" },
          h("div", {}, h("dt", {}, "Direction"), h("dd", {}, directionBadge(item.direction))),
          h("div", {}, h("dt", {}, "Entry"), h("dd", {}, fmt.price(levels.entry))),
          h("div", {}, h("dt", {}, "Stop loss"), h("dd", {}, `${fmt.price(levels.stop_loss)} (${levels.stop_note})`)),
          levels.take_profits.map((tp, i) => h("div", {}, h("dt", {}, `TP${i + 1}`), h("dd", {}, `${fmt.price(tp)} (+${levels.targets_rr[i]}R)`))),
          h("div", {}, h("dt", {}, "Reward : risk"), h("dd", {}, fmt.rr(levels.planned_rr))),
          h("div", {}, h("dt", {}, "Size"), h("dd", {}, item.lots ? `${fmt.lots(item.lots)} lots · ${fmt.money(item.risk_amount)}` : item.sizing_note || "—")),
        )
      : null,
    copilot === null
      ? h("p", { class: "small muted" }, "No strategy linked - only the rule's conditions decide.")
      : copilot.length
        ? h("div", { class: `copilot ${item.copilot_passes ? "neutral" : "wait"}` }, icon("shield"), h("div", {}, h("strong", {}, item.copilot_passes ? "Co-Pilot: passes (with notes)" : "Co-Pilot: wait"), h("ul", {}, copilot.map((v) => h("li", {}, v.message)))))
        : h("div", { class: "copilot ok" }, icon("shield"), h("div", {}, h("strong", {}, `Co-Pilot: every ${item.strategy} requirement is met`))),
    item.precheck ? precheckBox(item.precheck) : null,
  );
}

async function testDialog(rule) {
  const body = h("div", { class: "al-test-body" }, h("div", { class: "loading" }, "Checking the latest closed candles…"));
  const ref = modal({ title: `Test · ${rule.name}`, body, wide: true, footer: [h("button", { class: "btn ghost", type: "button", onclick: () => ref.close() }, "Close")] });
  try {
    const result = await api.post(`/api/alerts/rules/${rule.id}/test`);
    clear(
      body,
      h("p", { class: "small muted" }, `Dry run on ${result.provider_label} at ${fmt.datetime(result.ran_at)}. Nothing is saved or sent.`),
      result.results.map(testResult),
    );
  } catch (error) {
    clear(body, h("div", { class: "violation block" }, icon("alert"), h("div", {}, error.message)));
  }
}

// ----------------------------------------------------------------- rule editor

function strategyRequirements(strategy, conditions, meta) {
  if (!strategy) return h("p", { class: "small muted" }, "No strategy: only this rule's conditions decide. Link a strategy to apply its Co-Pilot checks.");
  const covered = new Set(conditions.map((c) => c.confluence || meta.types[c.type]?.label));
  const missing = (strategy.required_confluences || []).filter((name) => !covered.has(name));
  const items = [
    ["Status", strategy.status === "incubator" ? "In test - alerts are tagged" : strategy.status === "retired" ? "Retired - never alerts" : "Live"],
    ["Required confluences", strategy.required_confluences?.join(", ") || "Any"],
    ["Min confluences", strategy.min_confluences || "Any"],
    ["Sessions", strategy.allowed_sessions?.join(", ") || "Any"],
    ["Timeframes", strategy.allowed_timeframes?.join(", ") || "Any"],
    ["Direction", DIRECTION_LABEL[strategy.allowed_directions] || "Long & short"],
    ["Min R:R", strategy.min_rr ? `1:${strategy.min_rr}` : "Account minimum"],
    ["Daily bias", strategy.require_bias_alignment ? "Must match" : "Not required"],
  ];
  return h(
    "div",
    { class: `copilot ${missing.length || strategy.status === "retired" ? "wait" : "neutral"}` },
    icon("shield"),
    h(
      "div",
      { class: "al-grow" },
      h("strong", {}, `Co-Pilot checks for ${strategy.name}`),
      h("dl", { class: "al-dl" }, items.map(([k, v]) => h("div", {}, h("dt", {}, k), h("dd", {}, String(v))))),
      missing.length ? h("p", { class: "small" }, `No condition counts as ${missing.join(", ")} yet - this rule can never alert until one does.`) : null,
    ),
  );
}

function paramInput(spec, value, onChange) {
  if (spec.kind === "choice") return select(spec.choices.map((c) => [c, c[0].toUpperCase() + c.slice(1)]), value, { onchange: (e) => onChange(e.target.value) });
  if (spec.kind === "levels") {
    return h("input", {
      value: (value || []).join(", "),
      placeholder: "e.g. 2650, 2662.5",
      inputmode: "decimal",
      oninput: (e) =>
        onChange(
          e.target.value
            .split(/[\s,;]+/)
            .map((v) => Number(v))
            .filter((v) => Number.isFinite(v) && v > 0),
        ),
    });
  }
  return h("input", {
    type: "number",
    inputmode: "decimal",
    step: spec.kind === "int" ? "1" : "any",
    min: spec.min ?? undefined,
    max: spec.max ?? undefined,
    value: value ?? spec.default,
    oninput: (e) => {
      const n = numberOrNull(e.target);
      if (n !== null) onChange(n);
    },
  });
}

function ruleEditor(rule, meta, onSaved) {
  const plan = state.plan;
  const settings = plan.settings;
  const draft = rule
    ? structuredClone(rulePayload(rule))
    : {
        name: "",
        enabled: true,
        strategy_id: null,
        symbols: plan.markets.filter((m) => m.active).slice(0, 1).map((m) => m.symbol),
        timeframe: "M15",
        provider: "yahoo",
        provider_symbols: {},
        direction: "both",
        conditions: [],
        min_conditions: 0,
        stop_method: "swing",
        stop_buffer_pips: 0,
        atr_multiple: 1.5,
        targets_rr: [],
        risk_pct: null,
        cooldown_minutes: 60,
        notify: true,
      };
  const confluenceNames = plan.confluences.filter((c) => c.active).map((c) => c.name);
  const strategies = plan.strategies;
  const strategyOf = () => strategies.find((s) => s.id === draft.strategy_id) || null;
  const defaultConfluence = (type) => {
    const wanted = meta.types[type]?.confluence?.toLowerCase();
    return confluenceNames.find((name) => name.toLowerCase() === wanted) || "";
  };
  const newCondition = (type, params = {}) => ({ type, params: { ...Object.fromEntries(meta.types[type].params.map((p) => [p.name, p.default])), ...params }, confluence: defaultConfluence(type) });

  // --- basics
  const name = h("input", { value: draft.name, maxlength: "80", placeholder: "e.g. Gold London sweep", oninput: (e) => (draft.name = e.target.value) });
  const requirementsSlot = h("div");
  const drawRequirements = () => clear(requirementsSlot, strategyRequirements(strategyOf(), draft.conditions, meta));
  const strategySelect = select(
    [["", "No strategy"], ...strategies.map((s) => [String(s.id), s.status === "live" ? s.name : `${s.name} (${s.status === "incubator" ? "in test" : "retired"})`])],
    draft.strategy_id ? String(draft.strategy_id) : "",
    {
      onchange: (e) => {
        draft.strategy_id = e.target.value ? Number(e.target.value) : null;
        drawRequirements();
        drawTargetsHint();
      },
    },
  );
  const timeframe = select(meta.timeframes, draft.timeframe, { onchange: (e) => (draft.timeframe = e.target.value) });
  const direction = segmented(
    [
      ["both", "Both"],
      ["long", "Long only", "long"],
      ["short", "Short only", "short"],
    ],
    draft.direction,
    (value) => (draft.direction = value),
  );

  // --- provider & symbols
  const providerNote = h("div", { class: "help" });
  const overridesSlot = h("div");
  const providerSelect = select(
    meta.providers.map((p) => [p.key, p.label]),
    draft.provider,
    {
      onchange: (e) => {
        draft.provider = e.target.value;
        drawProvider();
      },
    },
  );
  const symbolChips = h("div", { class: "chips" });
  const customSymbol = h("input", { placeholder: "Add symbol", maxlength: "20", style: { textTransform: "uppercase" } });
  const addSymbol = () => {
    const value = customSymbol.value.trim().toUpperCase();
    if (!/^[A-Z0-9._-]{2,20}$/.test(value)) {
      toast("Symbols use 2-20 letters, digits, '.', '_' or '-'", "warn");
      return;
    }
    if (!draft.symbols.includes(value)) draft.symbols.push(value);
    customSymbol.value = "";
    drawSymbols();
  };
  customSymbol.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      addSymbol();
    }
  });
  function drawSymbols() {
    const options = [...new Set([...plan.markets.filter((m) => m.active).map((m) => m.symbol), ...draft.symbols])];
    clear(
      symbolChips,
      options.map((symbol) =>
        h(
          "button",
          {
            type: "button",
            class: `chip-toggle${draft.symbols.includes(symbol) ? " on" : ""}`,
            "aria-pressed": String(draft.symbols.includes(symbol)),
            onclick: () => {
              draft.symbols = draft.symbols.includes(symbol) ? draft.symbols.filter((s) => s !== symbol) : [...draft.symbols, symbol];
              drawSymbols();
            },
          },
          symbol,
        ),
      ),
    );
    drawProvider();
  }
  function drawProvider() {
    const provider = meta.providers.find((p) => p.key === draft.provider);
    clear(providerNote, provider.description, provider.needs_key && !provider.key_configured ? h("strong", { class: "al-warn-text" }, " Add your API key under Data providers first.") : null);
    if (draft.provider === "demo" || !draft.symbols.length) {
      clear(overridesSlot);
      return;
    }
    clear(
      overridesSlot,
      h("div", { class: "section-title" }, "Provider symbols"),
      h("p", { class: "small muted" }, `Only needed when ${provider.label} uses a different ticker than the default shown.`),
      h(
        "div",
        { class: "form-grid" },
        draft.symbols.map((symbol) =>
          field(
            symbol,
            h("input", {
              value: draft.provider_symbols[symbol] || "",
              placeholder: provider.symbol_map[symbol] || symbol,
              maxlength: "30",
              oninput: (e) => {
                const value = e.target.value.trim();
                if (value) draft.provider_symbols[symbol] = value;
                else delete draft.provider_symbols[symbol];
              },
            }),
          ),
        ),
      ),
    );
  }

  // --- conditions
  const conditionsSlot = h("div", { class: "al-conds" });
  const minSlot = h("div");
  const addType = select(Object.values(meta.types).map((t) => [t.key, t.label]), "liquidity_sweep");
  function drawMin() {
    const n = draft.conditions.length;
    if (draft.min_conditions > n) draft.min_conditions = 0;
    const options = [[0, "All conditions"], ...Array.from({ length: Math.max(n - 1, 0) }, (_, i) => [i + 1, `At least ${i + 1} of ${n}`])];
    clear(minSlot, field("Must be met", select(options, draft.min_conditions, { onchange: (e) => (draft.min_conditions = Number(e.target.value)) }), { help: "Directional conditions that are met must agree on long or short." }));
  }
  function drawConditions() {
    clear(
      conditionsSlot,
      draft.conditions.length
        ? draft.conditions.map((condition, index) => {
            const type = meta.types[condition.type];
            return h(
              "div",
              { class: "al-cond" },
              h(
                "div",
                { class: "al-cond-head" },
                h("div", { class: "al-grow" }, h("strong", {}, type.label), type.directional ? null : h("span", { class: "badge" }, "Filter"), h("p", { class: "small muted" }, type.description)),
                h(
                  "button",
                  {
                    class: "btn ghost sm icon-only danger",
                    type: "button",
                    "aria-label": `Remove ${type.label}`,
                    onclick: () => {
                      draft.conditions.splice(index, 1);
                      drawConditions();
                    },
                  },
                  icon("trash"),
                ),
              ),
              h(
                "div",
                { class: "form-grid" },
                type.params.map((spec) =>
                  field(
                    spec.label,
                    paramInput(spec, condition.params[spec.name], (value) => (condition.params[spec.name] = value)),
                    { help: spec.help || (spec.min !== null && spec.kind !== "choice" ? `${spec.min} - ${spec.max}` : "") },
                  ),
                ),
                field(
                  "Counts as confluence",
                  select([["", `${type.label} (condition name)`], ...confluenceNames.map((n) => [n, n])], condition.confluence, {
                    onchange: (e) => {
                      condition.confluence = e.target.value;
                      drawRequirements();
                    },
                  }),
                  { help: "Matched against your strategy's required confluences" },
                ),
              ),
            );
          })
        : h("p", { class: "small muted" }, "No conditions yet - start from a template or add one below."),
    );
    drawMin();
    drawRequirements();
  }
  const templates = rule
    ? null
    : h(
        "div",
        { class: "row al-templates" },
        h("span", { class: "small muted" }, "Start from:"),
        TEMPLATES.map((t) =>
          h(
            "button",
            {
              type: "button",
              class: "chip-toggle",
              onclick: () => {
                draft.conditions = t.conditions.map((c) => newCondition(c.type, c.params));
                draft.min_conditions = t.min;
                drawConditions();
              },
            },
            t.label,
          ),
        ),
      );

  // --- stop, targets, risk
  const atrField = h("div");
  const stopHelp = h("div", { class: "help" });
  const atrMultiple = h("input", { type: "number", step: "0.1", min: "0.1", max: "20", value: draft.atr_multiple, oninput: (e) => (draft.atr_multiple = numberOrNull(e.target) ?? draft.atr_multiple) });
  const drawStop = () => {
    stopHelp.textContent = meta.stop_methods.find((m) => m.key === draft.stop_method)?.description || "";
    clear(atrField, draft.stop_method === "atr" ? field("ATR multiple", atrMultiple) : null);
  };
  const stopMethod = select(
    meta.stop_methods.map((m) => [m.key, m.label]),
    draft.stop_method,
    {
      onchange: (e) => {
        draft.stop_method = e.target.value;
        drawStop();
      },
    },
  );
  const buffer = h("input", { type: "number", step: "any", min: "0", value: draft.stop_buffer_pips, inputmode: "decimal", oninput: (e) => (draft.stop_buffer_pips = numberOrNull(e.target) ?? 0) });
  const targetsHint = h("div", { class: "help" });
  const drawTargetsHint = () => {
    const rr = draft.targets_rr;
    if (!rr.length) {
      const min = Math.max(settings.min_rr || 0, strategyOf()?.min_rr || 0) || 1;
      targetsHint.textContent = `Empty = one target at your minimum R:R (1:${min}).`;
      return;
    }
    const split = plan.enums.tp_splits[String(rr.length)] || [];
    const blended = rr.reduce((sum, r, i) => sum + r * (split[i] || 0), 0) / 100;
    targetsHint.textContent = `Blended R:R 1:${blended.toFixed(2)} with the default ${split.join("/")}% split.`;
  };
  const targets = h("input", {
    value: draft.targets_rr.join(", "),
    placeholder: "e.g. 1, 2, 3",
    inputmode: "decimal",
    oninput: (e) => {
      draft.targets_rr = e.target.value
        .split(/[\s,;]+/)
        .map(Number)
        .filter((v) => Number.isFinite(v) && v > 0);
      drawTargetsHint();
    },
  });
  const risk = h("input", { type: "number", step: "0.05", min: "0.01", max: "10", value: draft.risk_pct ?? "", placeholder: `${settings.default_risk_pct} (account default)`, oninput: (e) => (draft.risk_pct = numberOrNull(e.target)) });
  const cooldown = h("input", { type: "number", step: "5", min: "0", max: "1440", value: draft.cooldown_minutes, oninput: (e) => (draft.cooldown_minutes = Math.round(numberOrNull(e.target) ?? 0)) });
  const notifyBox = h("input", { type: "checkbox", checked: draft.notify, onchange: (e) => (draft.notify = e.target.checked) });
  const enabledBox = h("input", { type: "checkbox", checked: draft.enabled, onchange: (e) => (draft.enabled = e.target.checked) });

  const body = h(
    "div",
    { class: "al-editor" },
    h("div", { class: "form-grid" }, field("Rule name", name), field("Strategy", strategySelect), field("Timeframe", timeframe), field("Direction", direction.el)),
    requirementsSlot,
    h("div", { class: "section-title" }, "Symbols & data"),
    symbolChips,
    h("div", { class: "row al-add-symbol" }, customSymbol, h("button", { class: "btn sm", type: "button", onclick: addSymbol }, icon("plus"), "Add")),
    h("div", { class: "form-grid" }, field("Market data", providerSelect, { full: true })),
    providerNote,
    overridesSlot,
    h("div", { class: "section-title" }, "Conditions on the closed candle"),
    templates,
    conditionsSlot,
    h(
      "div",
      { class: "row al-add-cond" },
      addType,
      h(
        "button",
        {
          class: "btn sm",
          type: "button",
          onclick: () => {
            if (draft.conditions.length >= meta.max_conditions) {
              toast(`A rule can have up to ${meta.max_conditions} conditions`, "warn");
              return;
            }
            draft.conditions.push(newCondition(addType.value));
            drawConditions();
          },
        },
        icon("plus"),
        "Add condition",
      ),
    ),
    minSlot,
    h("div", { class: "section-title" }, "Stop, targets & size"),
    h(
      "div",
      { class: "form-grid" },
      h("div", { class: "field" }, h("label", {}, "Stop loss"), stopMethod, stopHelp),
      field("Stop buffer (pips/points)", buffer),
      atrField,
      h("div", { class: "field" }, h("label", {}, "Targets (R multiples)"), targets, targetsHint),
      field("Risk per trade (%)", risk),
      field("Cooldown (minutes)", cooldown, { help: "Quiet time per symbol after an alert" }),
    ),
    h("div", { class: "row al-checks-row" }, h("label", { class: "check" }, notifyBox, "Send a push notification"), h("label", { class: "check" }, enabledBox, "Rule enabled")),
  );

  const save = h(
    "button",
    {
      class: "btn primary",
      type: "button",
      onclick: async () => {
        if (!draft.name.trim()) return toast("Give the rule a name", "warn");
        if (!draft.symbols.length) return toast("Choose at least one symbol", "warn");
        if (!draft.conditions.length) return toast("Add at least one condition", "warn");
        save.disabled = true;
        try {
          const payload = { ...draft, name: draft.name.trim() };
          const saved = rule ? await api.put(`/api/alerts/rules/${rule.id}`, payload) : await api.post("/api/alerts/rules", payload);
          toast(rule ? "Rule saved" : "Rule created - press Test to check it on the latest candles", "success");
          ref.close();
          onSaved(saved);
        } catch (error) {
          toast(error.message, "error");
        } finally {
          save.disabled = false;
        }
      },
    },
    rule ? "Save rule" : "Create rule",
  );
  const ref = modal({ title: rule ? `Edit rule · ${rule.name}` : "New alert rule", body, wide: true, footer: [h("button", { class: "btn ghost", type: "button", onclick: () => ref.close() }, "Cancel"), save] });
  drawSymbols();
  drawConditions();
  drawStop();
  drawTargetsHint();
}

// ----------------------------------------------------------------- rules list

function ruleItem(rule, meta, { onToggle, onTest, onEdit, onDelete }) {
  const demo = rule.provider === "demo";
  const when = (label, value) => (value ? `${label} ${fmt.datetime(value)}` : `${label} never`);
  return h(
    "div",
    { class: `al-rule${rule.enabled ? "" : " off"}` },
    h(
      "div",
      { class: "al-rule-head" },
      switchControl(rule.enabled, `Rule ${rule.name} enabled`, onToggle),
      h("strong", { class: "al-grow" }, rule.name),
      rule.strategy ? h("span", { class: `badge ${rule.strategy.status === "incubator" ? "warn" : rule.strategy.status === "retired" ? "bad" : ""}` }, rule.strategy.status === "incubator" ? `${rule.strategy.name} · in test` : rule.strategy.name) : null,
    ),
    h(
      "div",
      { class: "al-rule-meta small" },
      `${rule.symbols.join(", ")} · ${rule.timeframe} · ${DIRECTION_LABEL[rule.direction]} · `,
      demo ? h("span", { class: "badge warn" }, "Demo data (synthetic)") : rule.provider_label,
    ),
    h("div", { class: "chips" }, rule.conditions.map((c) => h("span", { class: "chip" }, meta.types[c.type]?.label || c.type))),
    h("div", { class: "al-rule-meta small muted" }, `${when("Checked", rule.last_checked_at)} · ${when("last alert", rule.last_alert_at)}`),
    rule.last_error ? h("div", { class: "violation block al-rule-error" }, icon("alert"), h("div", { class: "al-msg" }, rule.last_error)) : null,
    h(
      "div",
      { class: "al-actions" },
      h("button", { class: "btn sm", type: "button", onclick: onTest }, icon("play"), "Test"),
      h("button", { class: "btn ghost sm", type: "button", onclick: onEdit }, icon("edit"), "Edit"),
      h("span", { class: "spacer" }),
      h("button", { class: "btn ghost sm icon-only danger", type: "button", "aria-label": `Delete rule ${rule.name}`, title: "Delete", onclick: onDelete }, icon("trash")),
    ),
  );
}

// ----------------------------------------------------------------- data providers

function keyForm(label, provider, info, help) {
  const input = h("input", { type: "password", autocomplete: "off", placeholder: info.key_configured ? `Saved (${info.key_hint})` : "Not set", maxlength: "200" });
  const status = h("span", { class: `badge ${info.key_configured ? "good" : ""}` }, info.key_configured ? "Saved" : "Not set");
  const submit = async (value) => {
    try {
      const result = await api.put(`/api/alerts/providers/${provider}/key`, { api_key: value });
      info.key_configured = result.configured;
      info.key_hint = result.hint;
      input.value = "";
      input.placeholder = result.configured ? `Saved (${result.hint})` : "Not set";
      status.className = `badge ${result.configured ? "good" : ""}`;
      status.textContent = result.configured ? "Saved" : "Not set";
      toast(result.configured ? `${label} saved` : `${label} removed`, "success");
    } catch (error) {
      toast(error.message, "error");
    }
  };
  return h(
    "div",
    { class: "field al-key" },
    h("label", {}, label, " ", status),
    h(
      "div",
      { class: "row al-key-row" },
      input,
      h("button", { class: "btn sm", type: "button", onclick: () => (input.value.trim() ? submit(input.value.trim()) : toast("Enter a value first", "warn")) }, "Save"),
      h("button", { class: "btn ghost sm", type: "button", onclick: () => submit("") }, "Remove"),
    ),
    h("div", { class: "help" }, help),
  );
}

function providersCard(meta) {
  const by = Object.fromEntries(meta.providers.map((p) => [p.key, p]));
  return h(
    "div",
    { class: "card" },
    h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Data providers"), h("p", {}, "Where the scanner reads candles. Keys stay on this computer and are never shown again."))),
    h("ul", { class: "al-providers" }, meta.providers.map((p) => h("li", {}, h("strong", {}, p.label), h("span", { class: "small muted" }, p.description)))),
    keyForm("Twelve Data API key", "twelvedata", by.twelvedata, "Only used by rules with the Twelve Data provider."),
    keyForm("Push token", "push", by.push, "When set, every pushed request must include it as \"token\"."),
    h(
      "div",
      { class: "al-docs" },
      h("div", { class: "section-title" }, "Push candles from your platform"),
      h("p", { class: "small muted" }, `Send closed candles from an MT4/MT5 script, a TradingView webhook or your own script. Times are bar open times (ISO 8601); times with a zone are converted to your account time zone. Up to ${meta.push.max_bars} bars per request; resending a bar updates it.`),
      h("code", { class: "al-endpoint" }, `POST ${location.origin}${meta.push.endpoint}`),
      h("pre", { class: "al-pre" }, JSON.stringify(meta.push.example, null, 2)),
    ),
  );
}

// ----------------------------------------------------------------- page

export async function render(root, { params }) {
  const focusId = params.get("id") ? Number(params.get("id")) : null;
  if (focusId) feedFilter = "all";
  const metaRaw = await api.get("/api/alerts/meta");
  const meta = { ...metaRaw, types: Object.fromEntries(metaRaw.condition_types.map((t) => [t.key, t])) };

  const hintSlot = h("div");
  const scannerSlot = h("div", { class: "card al-scanner" });
  const feedSlot = h("div", { class: "card al-feed" });
  const rulesSlot = h("div", { class: "card" });

  async function refreshAll() {
    await Promise.all([drawScanner(), drawFeed(), drawRules()]);
  }

  // --- scanner
  async function drawScanner() {
    const scanner = await api.get("/api/alerts/scanner");
    const choices = meta.interval_choices.includes(scanner.interval_sec) ? meta.interval_choices : [...meta.interval_choices, scanner.interval_sec].sort((a, b) => a - b);
    const interval = select(
      choices.map((s) => [s, INTERVAL_LABELS[s] || `Every ${Math.round(s / 60)} min`]),
      scanner.interval_sec,
      { "aria-label": "Scan interval" },
    );
    const save = (enabled) => api.put("/api/alerts/scanner", { enabled, interval_sec: Number(interval.value) });
    const toggle = switchControl(scanner.enabled, "Background scanner", async (on) => {
      await save(on);
      toast(on ? "Background scanner on" : "Background scanner off", "success");
      await drawScanner();
    });
    interval.addEventListener("change", async () => {
      try {
        await save(scanner.enabled);
        toast("Scan interval saved", "success");
      } catch (error) {
        toast(error.message, "error");
      }
    });
    const scanButton = h(
      "button",
      {
        class: "btn primary",
        type: "button",
        onclick: async () => {
          scanButton.disabled = true;
          scanButton.lastChild.textContent = "Scanning…";
          try {
            const result = await api.post("/api/alerts/scan");
            toast(`Scan finished: ${scanSummary(result.results)}`, result.errors ? "warn" : result.alerts ? "success" : "info", 6000);
            await refreshAll();
          } catch (error) {
            toast(error.message, "error");
            scanButton.disabled = false;
            scanButton.lastChild.textContent = "Scan now";
          }
        },
      },
      icon("refresh"),
      h("span", {}, "Scan now"),
    );
    const running = scanner.enabled
      ? scanner.thread_running
        ? h("span", { class: "chip ok" }, icon("checkCircle"), "Scanning in the background")
        : h("span", { class: "chip warn" }, icon("alert"), "Background jobs are not running - use Scan now")
      : h("span", { class: "chip" }, icon("power"), "Background scanner off");
    clear(
      scannerSlot,
      h(
        "div",
        { class: "card-head" },
        h("div", {}, h("h2", {}, "Scanner"), h("p", {}, "Checks every enabled rule on each newly closed candle.")),
      ),
      h("div", { class: "al-scanner-controls" }, h("div", { class: "row" }, toggle, h("span", { class: "small muted" }, "Background scanner")), interval, scanButton),
      h(
        "div",
        { class: "row al-status" },
        running,
        h("span", { class: "chip" }, icon("clock"), scanner.last_run_at ? `Last scan ${fmt.datetime(scanner.last_run_at)}${scanner.last_manual ? " (manual)" : ""}` : "No scan yet"),
        h("span", { class: "chip" }, icon("list"), plural(scanner.rules_enabled, "enabled rule")),
      ),
      scanner.last_error ? h("div", { class: "violation block" }, icon("alert"), h("div", { class: "al-msg" }, `Last scan failed: ${scanner.last_error}`)) : null,
      scanner.last_results.length
        ? h("details", { class: "al-details" }, h("summary", {}, `Last scan results · ${scanSummary(scanner.last_results)}`), resultsTable(scanner.last_results))
        : null,
    );
  }

  // --- feed
  async function setStatus(alert, status) {
    await api.post(`/api/alerts/${alert.id}/status`, { status });
  }
  const feedActions = {
    onReview: async (alert) => {
      try {
        if (alert.status === "new") await setStatus(alert, "seen");
      } catch (error) {
        toast(error.message, "error");
      }
      handoff.signalText = alert.signal_text;
      location.hash = "#/signals";
    },
    onDismiss: async (alert) => {
      try {
        await setStatus(alert, "dismissed");
        await drawFeed();
      } catch (error) {
        toast(error.message, "error");
      }
    },
    onDelete: async (alert) => {
      if (!(await confirmDialog(`Delete the ${alert.symbol} ${alert.direction} alert?`, { title: "Delete alert", confirmText: "Delete", danger: true }))) return;
      try {
        await api.del(`/api/alerts/${alert.id}`);
        await drawFeed();
      } catch (error) {
        toast(error.message, "error");
      }
    },
  };

  async function drawFeed(focus = null) {
    const data = await api.get(`/api/alerts${query({ status: feedFilter === "new" ? "new" : "", limit: 100 })}`);
    let alerts = data.alerts;
    if (focus && !alerts.some((a) => a.id === focus)) {
      const single = await api.get(`/api/alerts/${focus}`).catch(() => null);
      if (single) alerts = [single, ...alerts];
      else toast("That alert no longer exists", "warn");
    }
    const filter = segmented(
      [
        ["new", `New (${data.counts.new})`],
        ["all", `All (${data.counts.all})`],
      ],
      feedFilter,
      (value) => {
        feedFilter = value;
        drawFeed();
      },
    );
    clear(
      feedSlot,
      h(
        "div",
        { class: "card-head" },
        h("div", {}, h("h2", {}, "Alerts ", data.counts.new ? h("span", { class: "badge open", "aria-label": `${data.counts.new} unread` }, String(data.counts.new)) : null), h("p", {}, "Setups found on closed candles, newest first.")),
        filter.el,
      ),
      alerts.length
        ? h("div", { class: "al-list-feed" }, alerts.map((alert) => alertCard(alert, feedActions)))
        : h(
            "div",
            { class: "empty" },
            h("strong", {}, feedFilter === "new" ? "No new alerts" : "No alerts yet"),
            feedFilter === "new" && data.counts.all ? "You have seen every alert. Switch to All to look back." : "Create a rule, then turn the scanner on or press Scan now.",
          ),
    );
    if (focus) {
      const card = feedSlot.querySelector(`[data-alert-id="${focus}"]`);
      if (card) {
        card.classList.add("al-focus");
        requestAnimationFrame(() => card.scrollIntoView({ behavior: "smooth", block: "center" }));
        const alert = alerts.find((a) => a.id === focus);
        if (alert?.status === "new") {
          await setStatus(alert, "seen").catch(() => {});
          const badge = card.querySelector(".al-alert-head .badge.open");
          if (badge) {
            badge.className = "badge";
            badge.textContent = STATUS_LABEL.seen;
          }
        }
      }
    }
  }

  // --- rules
  async function drawRules() {
    const rules = await api.get("/api/alerts/rules");
    const reload = () => Promise.all([drawRules(), drawScanner()]);
    clear(
      rulesSlot,
      h(
        "div",
        { class: "card-head" },
        h("div", {}, h("h2", {}, "Rules"), h("p", {}, "What the scanner watches and when it alerts.")),
        h("button", { class: "btn sm", type: "button", onclick: () => ruleEditor(null, meta, reload) }, icon("plus"), "New rule"),
      ),
      rules.length
        ? h(
            "div",
            { class: "al-rules" },
            rules.map((rule) =>
              ruleItem(rule, meta, {
                onToggle: async (enabled) => {
                  await api.put(`/api/alerts/rules/${rule.id}`, { ...rulePayload(rule), enabled });
                  rule.enabled = enabled;
                  await reload();
                },
                onTest: () => testDialog(rule),
                onEdit: () => ruleEditor(rule, meta, reload),
                onDelete: async () => {
                  if (!(await confirmDialog(`Delete the rule "${rule.name}"? Its past alerts stay in the feed.`, { title: "Delete rule", confirmText: "Delete", danger: true }))) return;
                  try {
                    await api.del(`/api/alerts/rules/${rule.id}`);
                    await reload();
                  } catch (error) {
                    toast(error.message, "error");
                  }
                },
              }),
            ),
          )
        : h("div", { class: "empty" }, h("strong", {}, "No rules yet"), "A rule watches symbols on one timeframe and alerts when its conditions line up.", h("div", { class: "al-empty-action" }, h("button", { class: "btn primary sm", type: "button", onclick: () => ruleEditor(null, meta, reload) }, icon("plus"), "Create your first rule"))),
    );
  }

  clear(
    root,
    h(
      "div",
      { class: "al-page" },
      h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Strategy alerts"), h("p", {}, "Get notified when a strategy's conditions line up on a closed candle."))),
      h("div", { class: "banner al-notice" }, icon("shield"), h("div", {}, h("strong", {}, "You decide every trade"), NOTICE)),
      hintSlot,
      scannerSlot,
      h("div", { class: "al-layout" }, feedSlot, h("div", { class: "stack" }, rulesSlot, providersCard(meta))),
    ),
  );
  await Promise.all([drawScanner(), drawFeed(focusId), drawRules()]);
  notificationHint().then((node) => clear(hintSlot, node));

  const timer = setInterval(() => {
    if (document.visibilityState === "visible" && !document.querySelector(".modal-backdrop")) {
      Promise.all([drawFeed(), drawScanner()]).catch(() => {});
    }
  }, REFRESH_MS);
  return () => clearInterval(timer);
}
