// Strategy alerts: background scanner, alert feed and scanner rules.
// Alerts are informational - nothing is ever executed automatically. "Review in Signals"
// hands a setup to the Signals page, where sizing, guardrails and the Co-Pilot run.

import { api, query } from "../api.js";
import { clear, confirmDialog, field, h, icon, modal, numberOrNull, segmented, select, toast } from "../dom.js";
import { fmt } from "../format.js";
import { N_, t, tn } from "../i18n.js";
import { handoff, state } from "../state.js";

const NOTICE = N_("Alerts are informational: nothing is executed automatically - the final decision is always yours.");
const INTERVAL_LABELS = { 60: N_("Every minute"), 300: N_("Every 5 minutes"), 900: N_("Every 15 minutes"), 1800: N_("Every 30 minutes"), 3600: N_("Every hour") };
const RESULT_LABEL = { alert: N_("Alert"), no_setup: N_("No setup"), cooldown: N_("Cooling down"), duplicate: N_("Already alerted"), error: N_("Error") };
const RESULT_COUNT = { no_setup: N_("{n} no setup"), cooldown: N_("{n} cooling down"), duplicate: N_("{n} already alerted") };
const RESULT_BADGE = { alert: "good", no_setup: "", cooldown: "warn", duplicate: "", error: "bad" };
const STATUS_LABEL = { new: N_("New"), seen: N_("Seen"), dismissed: N_("Dismissed"), taken: N_("Taken") };
const STATUS_BADGE = { new: "open", seen: "", dismissed: "", taken: "good" };
const DIRECTION_LABEL = { both: N_("Long & short"), long: N_("Long only"), short: N_("Short only") };
const CHOICE_LABEL = { reversal: N_("Reversal"), momentum: N_("Momentum") }; // condition parameter choices (API codes)
const TEMPLATES = [
  {
    label: N_("Liquidity sweep + structure"),
    conditions: [
      { type: "liquidity_sweep", params: {} },
      { type: "break_of_structure", params: {} },
    ],
    min: 0,
  },
  {
    label: N_("Trend pullback"),
    conditions: [
      { type: "ema_trend", params: {} },
      { type: "rsi", params: { mode: "momentum" } },
      { type: "atr_filter", params: {} },
    ],
    min: 0,
  },
  {
    label: N_("Range breakout"),
    conditions: [
      { type: "range_breakout", params: {} },
      { type: "atr_filter", params: {} },
    ],
    min: 0,
  },
];
const REFRESH_MS = 30_000;
const RULE_SYMBOL = /^[A-Z0-9._-]{2,20}$/; // what the alert API accepts as a rule symbol

let feedFilter = "new";

/** The translated label of ``key`` in a table of N_() texts, or the key itself when the table has none. */
const labelOf = (table, key) => (table[key] ? t(table[key]) : key);

function directionBadge(direction) {
  const long = direction === "long";
  return h("span", { class: `badge ${direction} al-dir` }, icon(long ? "up" : "down"), long ? t("Long") : t("Short"));
}

function switchControl(checked, label, onChange) {
  const onOff = (on) => (on ? t("On") : t("Off"));
  const input = h("input", { type: "checkbox", role: "switch", checked, "aria-label": label });
  const text = h("span", { class: "al-switch-text" }, onOff(checked));
  input.addEventListener("change", async () => {
    text.textContent = onOff(input.checked);
    input.disabled = true;
    try {
      await onChange(input.checked);
    } catch (error) {
      input.checked = !input.checked;
      text.textContent = onOff(input.checked);
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
  if (!results.length) return t("No enabled rules to scan.");
  const counts = {};
  for (const row of results) counts[row.status] = (counts[row.status] || 0) + 1;
  return Object.entries(counts)
    .map(([status, n]) => {
      if (status === "alert") return tn(n, "{n} alert", "{n} alerts");
      if (status === "error") return tn(n, "{n} error", "{n} errors");
      return RESULT_COUNT[status] ? t(RESULT_COUNT[status], { n }) : `${n} ${status}`;
    })
    .join(" · ");
}

function resultsTable(results) {
  return h(
    "div",
    { class: "table-wrap al-results" },
    h(
      "table",
      {},
      h("thead", {}, h("tr", {}, [t("Rule"), t("Symbol"), t("Result"), t("Details")].map((c) => h("th", {}, c)))),
      h(
        "tbody",
        {},
        results.map((row) =>
          h(
            "tr",
            {},
            h("td", {}, row.rule_name),
            h("td", {}, row.symbol),
            h("td", {}, h("span", { class: `badge ${RESULT_BADGE[row.status] || ""}` }, labelOf(RESULT_LABEL, row.status))),
            h("td", { class: "al-msg" }, row.message || "—"),
          ),
        ),
      ),
    ),
  );
}

// ----------------------------------------------------------------- notification hint

async function notificationHint() {
  const link = h("a", { href: "#/plan?tab=notifications" }, t("Set up notifications"));
  const hint = (title, text) => h("div", { class: "banner warn al-hint" }, icon("bell"), h("div", {}, h("strong", {}, title), text, " ", link));
  let data;
  try {
    data = await api.get("/api/notifications/settings");
  } catch {
    return hint(t("Alerts reach your phone only with notifications"), t("Until then they appear on this page."));
  }
  const settings = data.settings || data;
  const enabled = settings.notifications_enabled ?? settings.enabled;
  let channels = data.channels ?? settings.channels;
  if (channels === undefined) channels = await api.get("/api/notifications/channels").catch(() => undefined);
  const list = Array.isArray(channels) ? channels : channels?.channels;
  // Only channels that would actually receive strategy alerts count (a channel may subscribe to other events).
  const receives = (channel) => channel.enabled !== false && (!channel.events?.length || channel.events.includes("strategy_alert"));
  const active = Array.isArray(list) ? list.filter(receives).length : null;
  const events = settings.events ?? settings.notify_events;
  if (enabled === false) return hint(t("Push notifications are off"), t("Alerts will only appear on this page."));
  if (active === 0) return hint(t("No notification channel yet"), t("Add your phone or browser to receive alerts instantly."));
  if (Array.isArray(events) && !events.includes("strategy_alert")) return hint(t("Strategy alert notifications are switched off"), t("Alerts will only appear on this page."));
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
    note: [rr[index] ? `+${fmt.num(rr[index], rr[index] % 1 ? 1 : 0)}R` : "", pcts[index] ? t("close {pct}%", { pct: pcts[index] }) : ""].filter(Boolean).join(" · "),
    cls: "tp",
  }));
  const rows = [...targets.reverse(), { label: t("Entry"), price: alert.entry_price, note: t("last close"), cls: "entry" }, { label: "SL", price: alert.stop_loss, note: details.stop_note || "−1R", cls: "sl" }];
  return h(
    "div",
    { class: "al-ladder", role: "table", "aria-label": t("Trade levels") },
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
      h("div", {}, h("div", { class: "tag" }, t("Pre-check: would be blocked right now")), h("ul", { class: "al-list" }, blocking.map((v) => h("li", {}, v.message)))),
    );
  }
  return h(
    "div",
    { class: "violation ok" },
    icon("checkCircle"),
    h(
      "div",
      {},
      h("div", { class: "tag" }, t("Pre-check: all guardrails pass")),
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
      alert.strategy ? h("span", { class: "al-strategy" }, alert.strategy) : h("span", { class: "al-strategy muted" }, details.rule_name || t("Rule")),
      alert.in_test ? h("span", { class: "badge warn", title: t("Strategy is still in the incubator") }, t("In test")) : null,
      alert.notification_status === "held"
        ? h("span", { class: "badge", title: t("Found outside your active sessions: shown here but not pushed (quiet hours).") }, icon("clock"), t("Not pushed"))
        : null,
      h("span", { class: "spacer" }),
      h("span", { class: `badge ${STATUS_BADGE[alert.status] || ""}` }, labelOf(STATUS_LABEL, alert.status)),
    ),
    h("div", { class: "al-alert-time" }, icon("clock"), h("span", {}, `${t("Signal candle closed {time}", { time: fmt.datetime(alert.candle_time) })}${details.session ? ` · ${details.session}` : ""}`)),
    alert.confluences.length ? h("div", { class: "chips" }, alert.confluences.map((name) => h("span", { class: "chip ok" }, icon("check"), name))) : null,
    h(
      "div",
      { class: "al-alert-body" },
      ladder(alert),
      h(
        "dl",
        { class: "al-dl" },
        h("div", {}, h("dt", {}, t("Reward : risk")), h("dd", {}, fmt.rr(alert.planned_rr))),
        h("div", {}, h("dt", {}, t("Size")), h("dd", {}, alert.lots ? t("{lots} lots", { lots: fmt.lots(alert.lots) }) : "—")),
        h("div", {}, h("dt", {}, t("Risk")), h("dd", {}, risk)),
        details.sizing_note ? h("p", { class: "small muted" }, details.sizing_note) : null,
      ),
    ),
    precheckBox(details.precheck || []),
    demo ? h("p", { class: "al-demo small" }, icon("info"), t("Demo data (synthetic) - not real prices. Never trade from it.")) : null,
    h(
      "div",
      { class: "al-actions" },
      h("button", { class: "btn primary sm", type: "button", onclick: () => onReview(alert) }, icon("signal"), t("Review in Signals")),
      alert.status !== "dismissed" ? h("button", { class: "btn ghost sm", type: "button", onclick: () => onDismiss(alert) }, icon("x"), t("Dismiss")) : null,
      h("span", { class: "spacer" }),
      h("button", { class: "btn ghost sm icon-only danger", type: "button", "aria-label": t("Delete {symbol} alert", { symbol: alert.symbol }), title: t("Delete"), onclick: () => onDelete(alert) }, icon("trash")),
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
      h("div", { class: "row al-check-head" }, h("strong", {}, c.label), c.confluence !== c.label ? h("span", { class: "muted small" }, t("counts as {confluence}", { confluence: c.confluence })) : null, c.met && c.direction ? directionBadge(c.direction) : null, c.met && !c.direction ? h("span", { class: "badge" }, t("Filter")) : null, h("span", { class: "sr-only" }, c.met ? t("met") : t("not met"))),
      h("div", { class: "small al-msg" }, c.explanation),
    ),
  );
}

function testResult(item) {
  const head = h("div", { class: "al-test-head" }, h("h3", {}, item.symbol), item.provider_symbol !== item.symbol ? h("span", { class: "muted small" }, t("as {symbol}", { symbol: item.provider_symbol })) : null);
  if (item.error) return h("section", { class: "al-test" }, head, h("div", { class: "violation block" }, icon("alert"), h("div", {}, item.error)));
  const levels = item.levels;
  const copilot = item.copilot;
  return h(
    "section",
    { class: "al-test" },
    head,
    h(
      "p",
      { class: "small muted" },
      `${t("{n} closed candles", { n: item.bars })} · ${t("signal candle closed {time}", { time: fmt.datetime(item.candle_time) })}${item.session ? ` (${item.session})` : ""} · ${t("close {price}", { price: fmt.price(item.close) })}`,
    ),
    item.stale ? h("div", { class: "violation warn" }, icon("clock"), h("div", {}, t("The latest candle is old - the market may be closed or the data delayed. The scanner does not alert on stale data."))) : null,
    h("ul", { class: "al-checks" }, item.conditions.map(conditionRow)),
    item.found
      ? h("div", { class: "violation ok" }, icon("checkCircle"), h("div", {}, h("div", { class: "tag" }, t("Setup found")), t("A scan now would send this alert (unless it was already sent or the rule is cooling down).")))
      : h("div", { class: "violation warn" }, icon("info"), h("div", {}, h("div", { class: "tag" }, t("No alert")), item.reason)),
    levels
      ? h(
          "dl",
          { class: "al-dl al-test-levels" },
          h("div", {}, h("dt", {}, t("Direction")), h("dd", {}, directionBadge(item.direction))),
          h("div", {}, h("dt", {}, t("Entry")), h("dd", {}, fmt.price(levels.entry))),
          h("div", {}, h("dt", {}, t("Stop loss")), h("dd", {}, `${fmt.price(levels.stop_loss)} (${levels.stop_note})`)),
          levels.take_profits.map((tp, i) => h("div", {}, h("dt", {}, `TP${i + 1}`), h("dd", {}, `${fmt.price(tp)} (+${levels.targets_rr[i]}R)`))),
          h("div", {}, h("dt", {}, t("Reward : risk")), h("dd", {}, fmt.rr(levels.planned_rr))),
          h("div", {}, h("dt", {}, t("Size")), h("dd", {}, item.lots ? `${t("{lots} lots", { lots: fmt.lots(item.lots) })} · ${fmt.money(item.risk_amount)}` : item.sizing_note || "—")),
        )
      : null,
    copilot === null
      ? h("p", { class: "small muted" }, item.strategy ? t("The {strategy} Co-Pilot checks run once the conditions line up.", { strategy: item.strategy }) : t("No strategy linked - only the rule's conditions decide."))
      : copilot.length
        ? h("div", { class: `copilot ${item.copilot_passes ? "neutral" : "wait"}` }, icon("shield"), h("div", {}, h("strong", {}, item.copilot_passes ? t("Co-Pilot: passes (with notes)") : t("Co-Pilot: wait")), h("ul", {}, copilot.map((v) => h("li", {}, v.message)))))
        : h("div", { class: "copilot ok" }, icon("shield"), h("div", {}, h("strong", {}, t("Co-Pilot: every {strategy} requirement is met", { strategy: item.strategy })))),
    item.precheck ? precheckBox(item.precheck) : null,
  );
}

async function testDialog(rule) {
  const body = h("div", { class: "al-test-body" }, h("div", { class: "loading" }, t("Checking the latest closed candles…")));
  const ref = modal({ title: t("Test · {name}", { name: rule.name }), body, wide: true, footer: [h("button", { class: "btn ghost", type: "button", onclick: () => ref.close() }, t("Close"))] });
  try {
    const result = await api.post(`/api/alerts/rules/${rule.id}/test`);
    clear(
      body,
      h("p", { class: "small muted" }, t("Dry run on {provider} at {time}. Nothing is saved or sent.", { provider: result.provider_label, time: fmt.datetime(result.ran_at) })),
      result.results.map(testResult),
    );
  } catch (error) {
    clear(body, h("div", { class: "violation block" }, icon("alert"), h("div", {}, error.message)));
  }
}

// ----------------------------------------------------------------- rule editor

function strategyRequirements(strategy, conditions, meta) {
  if (!strategy) return h("p", { class: "small muted" }, t("No strategy: only this rule's conditions decide. Link a strategy to apply its Co-Pilot checks."));
  const covered = new Set(conditions.map((c) => c.confluence || meta.types[c.type]?.label));
  const missing = (strategy.required_confluences || []).filter((name) => !covered.has(name));
  const any = t("Any");
  const items = [
    [t("Status"), strategy.status === "incubator" ? t("In test - alerts are tagged") : strategy.status === "retired" ? t("Retired - never alerts") : t("Live")],
    [t("Required confluences"), strategy.required_confluences?.join(", ") || any],
    [t("Min confluences"), strategy.min_confluences || any],
    [t("Sessions"), strategy.allowed_sessions?.join(", ") || any],
    [t("Timeframes"), strategy.allowed_timeframes?.join(", ") || any],
    [t("Direction"), DIRECTION_LABEL[strategy.allowed_directions] ? t(DIRECTION_LABEL[strategy.allowed_directions]) : t("Long & short")],
    [t("Min R:R"), strategy.min_rr ? `1:${strategy.min_rr}` : t("Account minimum")],
    [t("Daily bias"), strategy.require_bias_alignment ? t("Must match") : t("Not required")],
  ];
  return h(
    "div",
    { class: `copilot ${missing.length || strategy.status === "retired" ? "wait" : "neutral"}` },
    icon("shield"),
    h(
      "div",
      { class: "al-grow" },
      h("strong", {}, t("Co-Pilot checks for {name}", { name: strategy.name })),
      h("dl", { class: "al-dl" }, items.map(([k, v]) => h("div", {}, h("dt", {}, k), h("dd", {}, String(v))))),
      missing.length ? h("p", { class: "small" }, t("No condition counts as {names} yet - this rule can never alert until one does.", { names: missing.join(", ") })) : null,
    ),
  );
}

function paramInput(spec, value, onChange) {
  if (spec.kind === "choice") return select(spec.choices.map((c) => [c, CHOICE_LABEL[c] ? t(CHOICE_LABEL[c]) : c[0].toUpperCase() + c.slice(1)]), value, { onchange: (e) => onChange(e.target.value) });
  if (spec.kind === "levels") {
    return h("input", {
      value: (value || []).join(", "),
      placeholder: t("e.g. 2650, 2662.5"),
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
        symbols: plan.markets.filter((m) => m.active && RULE_SYMBOL.test(m.symbol)).slice(0, 1).map((m) => m.symbol),
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
  const name = h("input", { value: draft.name, maxlength: "80", placeholder: t("e.g. Gold London sweep"), oninput: (e) => (draft.name = e.target.value) });
  const requirementsSlot = h("div");
  const drawRequirements = () => clear(requirementsSlot, strategyRequirements(strategyOf(), draft.conditions, meta));
  const strategySelect = select(
    [["", t("No strategy")], ...strategies.map((s) => [String(s.id), s.status === "live" ? s.name : s.status === "incubator" ? t("{name} (in test)", { name: s.name }) : t("{name} (retired)", { name: s.name })])],
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
      ["both", t("Both")],
      ["long", t("Long only"), "long"],
      ["short", t("Short only"), "short"],
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
  const customSymbol = h("input", { placeholder: t("Add symbol"), maxlength: "20", style: { textTransform: "uppercase" } });
  const addSymbol = () => {
    const value = customSymbol.value.trim().toUpperCase();
    if (!RULE_SYMBOL.test(value)) {
      toast(t("Symbols use 2-20 letters, digits, '.', '_' or '-'"), "warn");
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
    // Plan symbols such as "EUR/USD" cannot be scanned (the API refuses them), so they are not offered.
    const planSymbols = plan.markets.filter((m) => m.active && RULE_SYMBOL.test(m.symbol)).map((m) => m.symbol);
    const options = [...new Set([...planSymbols, ...draft.symbols])];
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
              if (draft.symbols.includes(symbol)) {
                draft.symbols = draft.symbols.filter((s) => s !== symbol);
                delete draft.provider_symbols[symbol]; // its override input disappears with it
              } else {
                draft.symbols = [...draft.symbols, symbol];
              }
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
    clear(providerNote, provider.description, provider.needs_key && !provider.key_configured ? h("strong", { class: "al-warn-text" }, " ", t("Add your API key under Data providers first.")) : null);
    if (draft.provider === "demo" || !draft.symbols.length) {
      clear(overridesSlot);
      return;
    }
    clear(
      overridesSlot,
      h("div", { class: "section-title" }, t("Provider symbols")),
      h("p", { class: "small muted" }, t("Only needed when {provider} uses a different ticker than the default shown.", { provider: provider.label })),
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
  const addType = select(Object.values(meta.types).map((type) => [type.key, type.label]), "liquidity_sweep");
  function drawMin() {
    const n = draft.conditions.length;
    if (draft.min_conditions > n) draft.min_conditions = 0;
    const options = [[0, t("All conditions")], ...Array.from({ length: Math.max(n - 1, 0) }, (_, i) => [i + 1, t("At least {count} of {total}", { count: i + 1, total: n })])];
    clear(minSlot, field(t("Must be met"), select(options, draft.min_conditions, { onchange: (e) => (draft.min_conditions = Number(e.target.value)) }), { help: t("Directional conditions that are met must agree on long or short.") }));
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
                h("div", { class: "al-grow" }, h("strong", {}, type.label), type.directional ? null : h("span", { class: "badge" }, t("Filter")), h("p", { class: "small muted" }, type.description)),
                h(
                  "button",
                  {
                    class: "btn ghost sm icon-only danger",
                    type: "button",
                    "aria-label": t("Remove {name}", { name: type.label }),
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
                  t("Counts as confluence"),
                  select([["", t("{name} (condition name)", { name: type.label })], ...confluenceNames.map((n) => [n, n])], condition.confluence, {
                    onchange: (e) => {
                      condition.confluence = e.target.value;
                      drawRequirements();
                    },
                  }),
                  { help: t("Matched against your strategy's required confluences") },
                ),
              ),
            );
          })
        : h("p", { class: "small muted" }, t("No conditions yet - start from a template or add one below.")),
    );
    drawMin();
    drawRequirements();
  }
  const templates = rule
    ? null
    : h(
        "div",
        { class: "row al-templates" },
        h("span", { class: "small muted" }, t("Start from:")),
        TEMPLATES.map((template) =>
          h(
            "button",
            {
              type: "button",
              class: "chip-toggle",
              onclick: () => {
                draft.conditions = template.conditions.map((c) => newCondition(c.type, c.params));
                draft.min_conditions = template.min;
                drawConditions();
              },
            },
            t(template.label),
          ),
        ),
      );

  // --- stop, targets, risk
  const atrField = h("div");
  const stopHelp = h("div", { class: "help" });
  const atrMultiple = h("input", { type: "number", step: "0.1", min: "0.1", max: "20", value: draft.atr_multiple, oninput: (e) => (draft.atr_multiple = numberOrNull(e.target) ?? draft.atr_multiple) });
  const drawStop = () => {
    stopHelp.textContent = meta.stop_methods.find((m) => m.key === draft.stop_method)?.description || "";
    clear(atrField, draft.stop_method === "atr" ? field(t("ATR multiple"), atrMultiple) : null);
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
      targetsHint.textContent = t("Empty = one target at your minimum R:R (1:{min}).", { min });
      return;
    }
    const split = plan.enums.tp_splits[String(rr.length)] || [];
    const blended = rr.reduce((sum, r, i) => sum + r * (split[i] || 0), 0) / 100;
    targetsHint.textContent = t("Blended R:R 1:{rr} with the default {split}% split.", { rr: blended.toFixed(2), split: split.join("/") });
  };
  const targets = h("input", {
    value: draft.targets_rr.join(", "),
    placeholder: t("e.g. 1, 2, 3"),
    inputmode: "decimal",
    oninput: (e) => {
      draft.targets_rr = e.target.value
        .split(/[\s,;]+/)
        .map(Number)
        .filter((v) => Number.isFinite(v) && v > 0);
      drawTargetsHint();
    },
  });
  const risk = h("input", { type: "number", step: "0.05", min: "0.01", max: "10", value: draft.risk_pct ?? "", placeholder: t("{risk} (account default)", { risk: settings.default_risk_pct }), oninput: (e) => (draft.risk_pct = numberOrNull(e.target)) });
  const cooldown = h("input", { type: "number", step: "5", min: "0", max: "1440", value: draft.cooldown_minutes, oninput: (e) => (draft.cooldown_minutes = Math.round(numberOrNull(e.target) ?? 0)) });
  const notifyBox = h("input", { type: "checkbox", checked: draft.notify, onchange: (e) => (draft.notify = e.target.checked) });
  const enabledBox = h("input", { type: "checkbox", checked: draft.enabled, onchange: (e) => (draft.enabled = e.target.checked) });

  const body = h(
    "div",
    { class: "al-editor" },
    h("div", { class: "form-grid" }, field(t("Rule name"), name), field(t("Strategy"), strategySelect), field(t("Timeframe"), timeframe), field(t("Direction"), direction.el)),
    requirementsSlot,
    h("div", { class: "section-title" }, t("Symbols & data")),
    symbolChips,
    h("div", { class: "row al-add-symbol" }, customSymbol, h("button", { class: "btn sm", type: "button", onclick: addSymbol }, icon("plus"), t("Add"))),
    h("div", { class: "form-grid" }, field(t("Market data"), providerSelect, { full: true })),
    providerNote,
    overridesSlot,
    h("div", { class: "section-title" }, t("Conditions on the closed candle")),
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
              toast(t("A rule can have up to {max} conditions", { max: meta.max_conditions }), "warn");
              return;
            }
            draft.conditions.push(newCondition(addType.value));
            drawConditions();
          },
        },
        icon("plus"),
        t("Add condition"),
      ),
    ),
    minSlot,
    h("div", { class: "section-title" }, t("Stop, targets & size")),
    h(
      "div",
      { class: "form-grid" },
      h("div", { class: "field" }, h("label", {}, t("Stop loss")), stopMethod, stopHelp),
      field(t("Stop buffer (pips/points)"), buffer),
      atrField,
      h("div", { class: "field" }, h("label", {}, t("Targets (R multiples)")), targets, targetsHint),
      field(t("Risk per trade (%)"), risk),
      field(t("Cooldown (minutes)"), cooldown, { help: t("Quiet time per symbol after an alert") }),
    ),
    h("div", { class: "row al-checks-row" }, h("label", { class: "check" }, notifyBox, t("Send a push notification")), h("label", { class: "check" }, enabledBox, t("Rule enabled"))),
  );

  const save = h(
    "button",
    {
      class: "btn primary",
      type: "button",
      onclick: async () => {
        if (!draft.name.trim()) return toast(t("Give the rule a name"), "warn");
        if (!draft.symbols.length) return toast(t("Choose at least one symbol"), "warn");
        if (!draft.conditions.length) return toast(t("Add at least one condition"), "warn");
        save.disabled = true;
        try {
          const overrides = Object.fromEntries(Object.entries(draft.provider_symbols).filter(([symbol]) => draft.symbols.includes(symbol)));
          const payload = { ...draft, name: draft.name.trim(), provider_symbols: overrides };
          const saved = rule ? await api.put(`/api/alerts/rules/${rule.id}`, payload) : await api.post("/api/alerts/rules", payload);
          toast(rule ? t("Rule saved") : t("Rule created - press Test to check it on the latest candles"), "success");
          ref.close();
          onSaved(saved);
        } catch (error) {
          toast(error.message, "error");
        } finally {
          save.disabled = false;
        }
      },
    },
    rule ? t("Save rule") : t("Create rule"),
  );
  const ref = modal({ title: rule ? t("Edit rule · {name}", { name: rule.name }) : t("New alert rule"), body, wide: true, footer: [h("button", { class: "btn ghost", type: "button", onclick: () => ref.close() }, t("Cancel")), save] });
  drawSymbols();
  drawConditions();
  drawStop();
  drawTargetsHint();
}

// ----------------------------------------------------------------- rules list

function ruleItem(rule, meta, { onToggle, onTest, onEdit, onDelete }) {
  const demo = rule.provider === "demo";
  const checked = rule.last_checked_at ? t("Checked {time}", { time: fmt.datetime(rule.last_checked_at) }) : t("Checked never");
  const lastAlert = rule.last_alert_at ? t("last alert {time}", { time: fmt.datetime(rule.last_alert_at) }) : t("last alert never");
  return h(
    "div",
    { class: `al-rule${rule.enabled ? "" : " off"}` },
    h(
      "div",
      { class: "al-rule-head" },
      switchControl(rule.enabled, t("Rule {name} enabled", { name: rule.name }), onToggle),
      h("strong", { class: "al-grow" }, rule.name),
      rule.strategy ? h("span", { class: `badge ${rule.strategy.status === "incubator" ? "warn" : rule.strategy.status === "retired" ? "bad" : ""}` }, rule.strategy.status === "incubator" ? t("{name} · in test", { name: rule.strategy.name }) : rule.strategy.name) : null,
    ),
    h(
      "div",
      { class: "al-rule-meta small" },
      `${rule.symbols.join(", ")} · ${rule.timeframe} · ${labelOf(DIRECTION_LABEL, rule.direction)} · `,
      demo ? h("span", { class: "badge warn" }, t("Demo data (synthetic)")) : rule.provider_label,
    ),
    h("div", { class: "chips" }, rule.conditions.map((c) => h("span", { class: "chip" }, meta.types[c.type]?.label || c.type))),
    h("div", { class: "al-rule-meta small muted" }, `${checked} · ${lastAlert}`),
    rule.last_error ? h("div", { class: "violation block al-rule-error" }, icon("alert"), h("div", { class: "al-msg" }, rule.last_error)) : null,
    h(
      "div",
      { class: "al-actions" },
      h("button", { class: "btn sm", type: "button", onclick: onTest }, icon("play"), t("Test")),
      h("button", { class: "btn ghost sm", type: "button", onclick: onEdit }, icon("edit"), t("Edit")),
      h("span", { class: "spacer" }),
      h("button", { class: "btn ghost sm icon-only danger", type: "button", "aria-label": t("Delete rule {name}", { name: rule.name }), title: t("Delete"), onclick: onDelete }, icon("trash")),
    ),
  );
}

// ----------------------------------------------------------------- data providers

/** ``done`` holds the translated toasts {saved, removed} (full sentences, so each language can agree them with the label). */
function keyForm(label, provider, info, help, done) {
  const hintText = (configured, hint) => (configured ? t("Saved ({hint})", { hint }) : t("Not set"));
  const statusText = (configured) => (configured ? t("Saved") : t("Not set"));
  const input = h("input", { type: "password", autocomplete: "off", placeholder: hintText(info.key_configured, info.key_hint), maxlength: "200" });
  const status = h("span", { class: `badge ${info.key_configured ? "good" : ""}` }, statusText(info.key_configured));
  const submit = async (value) => {
    try {
      const result = await api.put(`/api/alerts/providers/${provider}/key`, { api_key: value });
      info.key_configured = result.configured;
      info.key_hint = result.hint;
      input.value = "";
      input.placeholder = hintText(result.configured, result.hint);
      status.className = `badge ${result.configured ? "good" : ""}`;
      status.textContent = statusText(result.configured);
      toast(result.configured ? done.saved : done.removed, "success");
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
      h("button", { class: "btn sm", type: "button", onclick: () => (input.value.trim() ? submit(input.value.trim()) : toast(t("Enter a value first"), "warn")) }, t("Save")),
      h("button", { class: "btn ghost sm", type: "button", onclick: () => submit("") }, t("Remove")),
    ),
    h("div", { class: "help" }, help),
  );
}

function providersCard(meta) {
  const by = Object.fromEntries(meta.providers.map((p) => [p.key, p]));
  return h(
    "div",
    { class: "card" },
    h("div", { class: "card-head" }, h("div", {}, h("h2", {}, t("Data providers")), h("p", {}, t("Where the scanner reads candles. Keys stay on this computer and are never shown again.")))),
    h("ul", { class: "al-providers" }, meta.providers.map((p) => h("li", {}, h("strong", {}, p.label), h("span", { class: "small muted" }, p.description)))),
    keyForm(t("Twelve Data API key"), "twelvedata", by.twelvedata, t("Only used by rules with the Twelve Data provider."), {
      saved: t("Twelve Data API key saved"),
      removed: t("Twelve Data API key removed"),
    }),
    keyForm(t("Push token"), "push", by.push, t("When set, every pushed request must include it as \"token\"."), { saved: t("Push token saved"), removed: t("Push token removed") }),
    h(
      "div",
      { class: "al-docs" },
      h("div", { class: "section-title" }, t("Push candles from your platform")),
      h(
        "p",
        { class: "small muted" },
        t("Send closed candles from an MT4/MT5 script, a TradingView webhook or your own script. Times are bar open times (ISO 8601); times with a zone are converted to your account time zone. Up to {max} bars per request; resending a bar updates it.", { max: meta.push.max_bars }),
      ),
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
  const meta = { ...metaRaw, types: Object.fromEntries(metaRaw.condition_types.map((type) => [type.key, type])) };

  const hintSlot = h("div");
  const scannerSlot = h("div", { class: "card al-scanner" });
  const feedSlot = h("div", { class: "card al-feed" });
  const rulesSlot = h("div", { class: "card" });

  async function refreshAll() {
    await Promise.all([drawScanner(), drawFeed(), drawRules()]);
  }

  // --- scanner
  let scanning = false; // a manual scan is running: re-renders keep the button busy
  async function drawScanner() {
    const scanner = await api.get("/api/alerts/scanner");
    const wasOpen = scannerSlot.querySelector("details")?.open;
    const choices = meta.interval_choices.includes(scanner.interval_sec) ? meta.interval_choices : [...meta.interval_choices, scanner.interval_sec].sort((a, b) => a - b);
    const interval = select(
      choices.map((s) => [s, INTERVAL_LABELS[s] ? t(INTERVAL_LABELS[s]) : t("Every {n} min", { n: Math.round(s / 60) })]),
      scanner.interval_sec,
      { "aria-label": t("Scan interval") },
    );
    const save = (enabled) => api.put("/api/alerts/scanner", { enabled, interval_sec: Number(interval.value) });
    const toggle = switchControl(scanner.enabled, t("Background scanner"), async (on) => {
      await save(on);
      toast(on ? t("Background scanner on") : t("Background scanner off"), "success");
      await drawScanner();
    });
    interval.addEventListener("change", async () => {
      try {
        await save(scanner.enabled);
        toast(t("Scan interval saved"), "success");
      } catch (error) {
        toast(error.message, "error");
      }
    });
    const scanButton = h(
      "button",
      {
        class: "btn primary",
        type: "button",
        disabled: scanning,
        onclick: async () => {
          scanning = true;
          scanButton.disabled = true;
          scanButton.lastChild.textContent = t("Scanning…");
          try {
            const result = await api.post("/api/alerts/scan");
            toast(t("Scan finished: {summary}", { summary: scanSummary(result.results) }), result.errors ? "warn" : result.alerts ? "success" : "info", 6000);
          } catch (error) {
            toast(error.message, "error");
          } finally {
            scanning = false;
          }
          await refreshAll().catch(() => {});
        },
      },
      icon("refresh"),
      h("span", {}, scanning ? t("Scanning…") : t("Scan now")),
    );
    const gapFree = scanner.gap_free_interval_sec;
    const gapFreeLabel = INTERVAL_LABELS[meta.interval_choices.filter((s) => s <= gapFree).pop()];
    const gaps =
      gapFree && scanner.interval_sec > gapFree
        ? h(
            "div",
            { class: "violation warn" },
            icon("alert"),
            h(
              "div",
              { class: "al-msg" },
              t("Scans this far apart can skip candles of your shortest rule timeframe - choose {interval} or less to check every candle.", {
                interval: gapFreeLabel ? t(gapFreeLabel).toLowerCase() : t("a shorter interval"),
              }),
            ),
          )
        : null;
    const running = scanner.enabled
      ? scanner.thread_running
        ? h("span", { class: "chip ok" }, icon("checkCircle"), t("Scanning in the background"))
        : h("span", { class: "chip warn" }, icon("alert"), t("Background jobs are not running - use Scan now"))
      : h("span", { class: "chip" }, icon("power"), t("Background scanner off"));
    const lastScan = scanner.last_run_at
      ? scanner.last_manual
        ? t("Last scan {time} (manual)", { time: fmt.datetime(scanner.last_run_at) })
        : t("Last scan {time}", { time: fmt.datetime(scanner.last_run_at) })
      : t("No scan yet");
    clear(
      scannerSlot,
      h(
        "div",
        { class: "card-head" },
        h("div", {}, h("h2", {}, t("Scanner")), h("p", {}, t("Checks every enabled rule on each newly closed candle."))),
      ),
      h("div", { class: "al-scanner-controls" }, h("div", { class: "row" }, toggle, h("span", { class: "small muted" }, t("Background scanner"))), interval, scanButton),
      h(
        "div",
        { class: "row al-status" },
        running,
        h("span", { class: "chip" }, icon("clock"), lastScan),
        h("span", { class: "chip" }, icon("list"), tn(scanner.rules_enabled, "{n} enabled rule", "{n} enabled rules")),
      ),
      gaps,
      scanner.last_error ? h("div", { class: "violation block" }, icon("alert"), h("div", { class: "al-msg" }, t("Last scan failed: {error}", { error: scanner.last_error }))) : null,
      scanner.last_results.length
        ? h("details", { class: "al-details", open: wasOpen || undefined }, h("summary", {}, t("Last scan results · {summary}", { summary: scanSummary(scanner.last_results) })), resultsTable(scanner.last_results))
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
      handoff.signalContext = {
        strategy: alert.strategy || "",
        timeframe: alert.timeframe || "",
        confluences: alert.confluences || [],
        risk_pct: alert.details?.risk_pct ?? null,
        alert: { id: alert.id, symbol: alert.symbol },
      };
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
      if (!(await confirmDialog(t("Delete the {symbol} {direction} alert?", { symbol: alert.symbol, direction: alert.direction }), { title: t("Delete alert"), confirmText: t("Delete"), danger: true }))) return;
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
      else toast(t("That alert no longer exists"), "warn");
    }
    const filter = segmented(
      [
        ["new", t("New ({n})", { n: data.counts.new })],
        ["all", t("All ({n})", { n: data.counts.all })],
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
        h(
          "div",
          {},
          h("h2", {}, t("Alerts"), " ", data.counts.new ? h("span", { class: "badge open", "aria-label": t("{n} unread", { n: data.counts.new }) }, String(data.counts.new)) : null),
          h("p", {}, t("Setups found on closed candles, newest first.")),
        ),
        filter.el,
      ),
      alerts.length
        ? h("div", { class: "al-list-feed" }, alerts.map((alert) => alertCard(alert, feedActions)))
        : h(
            "div",
            { class: "empty" },
            h("strong", {}, feedFilter === "new" ? t("No new alerts") : t("No alerts yet")),
            feedFilter === "new" && data.counts.all ? t("You have seen every alert. Switch to All to look back.") : t("Create a rule, then turn the scanner on or press Scan now."),
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
            badge.textContent = t(STATUS_LABEL.seen);
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
        h("div", {}, h("h2", {}, t("Rules")), h("p", {}, t("What the scanner watches and when it alerts."))),
        h("button", { class: "btn sm", type: "button", onclick: () => ruleEditor(null, meta, reload) }, icon("plus"), t("New rule")),
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
                  if (!(await confirmDialog(t("Delete the rule \"{name}\"? Its past alerts stay in the feed.", { name: rule.name }), { title: t("Delete rule"), confirmText: t("Delete"), danger: true }))) return;
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
        : h(
            "div",
            { class: "empty" },
            h("strong", {}, t("No rules yet")),
            t("A rule watches symbols on one timeframe and alerts when its conditions line up."),
            h("div", { class: "al-empty-action" }, h("button", { class: "btn primary sm", type: "button", onclick: () => ruleEditor(null, meta, reload) }, icon("plus"), t("Create your first rule"))),
          ),
    );
  }

  clear(
    root,
    h(
      "div",
      { class: "al-page" },
      h("div", { class: "page-head" }, h("div", {}, h("h1", {}, t("Strategy alerts")), h("p", {}, t("Get notified when a strategy's conditions line up on a closed candle.")))),
      h("div", { class: "banner al-notice" }, icon("shield"), h("div", {}, h("strong", {}, t("You decide every trade")), t(NOTICE))),
      hintSlot,
      scannerSlot,
      h("div", { class: "al-layout" }, feedSlot, h("div", { class: "stack" }, rulesSlot, providersCard(meta))),
    ),
  );
  await Promise.all([drawScanner(), drawFeed(focusId), drawRules()]);
  notificationHint().then((node) => clear(hintSlot, node));

  const timer = setInterval(() => {
    if (document.visibilityState !== "visible" || document.querySelector(".modal-backdrop")) return;
    drawFeed().catch(() => {});
    // Leave the scanner card alone while a scan runs or one of its controls is in use.
    if (!scanning && !scannerSlot.contains(document.activeElement)) drawScanner().catch(() => {});
  }, REFRESH_MS);
  return () => clearInterval(timer);
}
