// Signals: paste a Telegram / WhatsApp trade signal, size it with the account's
// risk rules, check every guardrail, then execute it as a live trade - logged in
// the journal, and (v1.5) optionally sent as an order to a connected trading account.

import { api, ApiError } from "../api.js";
import { clear, confirmDialog, debounce, field, h, icon, modal, numberOrNull, segmented, select, toast } from "../dom.js";
import { fmt } from "../format.js";
import { N_, t, tn } from "../i18n.js";
import { accounts, activeAccount, handoff, loadStatus, state, strategyByName } from "../state.js";
import { connectionLabel } from "./account-connection.js";
import { violationList } from "./calculator.js";
import { copilotPanel, splitCopilot } from "./copilot.js";

const EXAMPLES = [
  "🟢 GOLD BUY 2648-2652\n🛑 SL: 2640\n🎯 TP1: 2664\n🎯 TP2: 2676\n🎯 TP3: 2712",
  "Sell EUR/USD @1.0850\nStop Loss 1.0880\nTake Profit 1: 1.0820\nTake Profit 2: 1.0760",
  "NAS100 SHORT 18,550 SL: 18,620 TP: 18,400",
];
const TRIGGER_LABELS = { tp1: N_("After TP1"), immediate: N_("Immediately") };
const SIDE = { long: N_("Buy"), short: N_("Sell") };
const ORDER_TYPES = [
  ["market", N_("Market")],
  ["limit", N_("Limit")],
  ["stop", N_("Stop")],
];
const ORDER_TYPE_LABEL = Object.fromEntries(ORDER_TYPES);
const ACCEPTED = ["filled", "sent", "pending"];

// Kept between visits: the pasted message and the ticket being edited.
// ``accountId``: the trading account chosen for the ticket; ``orderKey``: the idempotency key of the order this
// ticket may send (one per ticket, so a double click never sends twice).
const memory = { text: "", parsedText: null, parsed: null, draft: null, splitAuto: true, example: -1, context: null, alert: null, vision: null, accountId: null, orderKey: null };

const num = (value) => numberOrNull({ value: value ?? "" });
const inputValue = (value) => (value === null || value === undefined ? "" : String(value));
const filled = (target) => target.price !== null || target.pips !== null;

function normalizeDraft(draft) {
  return {
    ...draft,
    strategy: draft.strategy || "",
    timeframe: draft.timeframe || "",
    confluences: [...(draft.confluences || [])],
    account_id: draft.account_id ?? null,
    order_type: draft.order_type || "market",
    targets: (draft.targets || []).map((tp) => ({ price: tp.price ?? null, pips: tp.pips ?? null, close_pct: tp.close_pct ?? null })),
  };
}

/** Block-severity first, so the reasons an entry is refused lead the list. */
function bySeverity(violations) {
  return [...violations].sort((a, b) => (a.severity === b.severity ? 0 : a.severity === "block" ? -1 : 1));
}

/** Co-Pilot panel for the chosen strategy plus the remaining guardrail findings. */
function guardrailFindings(violations, strategyName, okText) {
  const { other } = splitCopilot(violations);
  return [copilotPanel(violations, strategyName), violationList(bySeverity(other), { okText })];
}

function resetMemory() {
  Object.assign(memory, { text: "", parsedText: null, parsed: null, draft: null, splitAuto: true, context: null, alert: null, vision: null, accountId: null, orderKey: null });
}

/** Active trading accounts a ticket can be logged on. */
function tradableAccounts() {
  return accounts().filter((account) => account.status === "active");
}

/** The account the ticket is for: the one chosen, else the account shown, else the default account. */
function chosenAccountId() {
  const list = tradableAccounts();
  for (const id of [memory.draft?.account_id, memory.accountId, activeAccount()?.id, state.plan?.settings.default_account_id]) {
    if (id !== null && id !== undefined && list.some((account) => account.id === id)) return id;
  }
  return list[0]?.id ?? null;
}

/** A fresh idempotency key (crypto.getRandomValues also works on plain-http LAN addresses). */
function newOrderKey() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return `sig-${Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("")}`;
}

/** Same markup as calculator.violationList, for non-guardrail messages. */
/** Anti-FOMO findings carried over from the Vision AI idea this ticket came from. */
function visionWarnings() {
  const warnings = memory.vision?.warnings || [];
  return issueList(warnings.map((w) => w.message), { kind: "warn", tag: t("Anti-FOMO (Vision AI)"), iconName: "alert" });
}

function issueList(messages, { kind, tag, iconName }) {
  if (!messages.length) return null;
  return h(
    "div",
    { class: "violations" },
    messages.map((message) => h("div", { class: `violation ${kind}` }, icon(iconName), h("div", {}, h("div", { class: "tag" }, tag), message))),
  );
}

function metric(label, value, cls = "") {
  return h("div", {}, h("div", { class: "label" }, label), h("div", { class: `value ${cls}` }, value));
}

function parseSummary(parsed) {
  const targets = parsed.take_profits.length + parsed.target_pips.length;
  const raw = parsed.symbol_raw && parsed.symbol_raw.replace(/^#/, "").toUpperCase() !== parsed.symbol ? ` (${parsed.symbol_raw})` : "";
  let entry = ["warn", t("No entry - add your fill price")];
  if (parsed.entry_low !== null) {
    entry = ["ok", t("Zone {low}–{high} → {entry}", { low: fmt.price(parsed.entry_low), high: fmt.price(parsed.entry_high), entry: fmt.price(parsed.entry) })];
  } else if (parsed.entry !== null) entry = ["ok", t("Entry {price}", { price: fmt.price(parsed.entry) })];
  let stop = ["bad", t("No stop loss")];
  if (parsed.stop_loss !== null) stop = ["ok", t("SL {price}", { price: fmt.price(parsed.stop_loss) })];
  else if (parsed.stop_pips !== null) stop = ["ok", t("SL {pips} pips", { pips: fmt.num(parsed.stop_pips, 0) })];
  const items = [
    parsed.symbol ? ["ok", `${parsed.symbol}${raw}`] : ["bad", t("No symbol")],
    parsed.direction ? ["ok", `${t(SIDE[parsed.direction])} · ${parsed.order_type}`] : ["bad", t("No direction")],
    entry,
    stop,
    targets ? ["ok", tn(targets, "{n} target", "{n} targets")] : ["warn", t("No take profit")],
  ];
  const icons = { ok: "checkCircle", warn: "alert", bad: "xCircle" };
  return h(
    "div",
    { class: "sig-summary", "aria-label": t("What the parser found") },
    items.map(([kind, text]) => h("span", { class: `chip ${kind}` }, icon(icons[kind]), text)),
  );
}

export async function render(root) {
  if (handoff.signalText) {
    // A trade idea handed over by another page (Vision AI, a strategy alert) is a fresh paste, judged with the
    // strategy, timeframe, confluences and risk it came with - never the previous ticket's.
    const context = handoff.signalContext || {};
    Object.assign(memory, { text: handoff.signalText, parsedText: null, parsed: null, draft: null, splitAuto: true, context, alert: context.alert || null, vision: context.vision ? { ...context.vision, text: handoff.signalText } : null, accountId: context.account_id ?? null, orderKey: null });
    handoff.signalText = null;
    handoff.signalContext = null;
  }
  const { settings, enums } = state.plan;
  const markets = state.plan.markets.filter((m) => m.active);
  const splits = enums.tp_splits;
  let seq = 0; // only the newest parse/preview response is rendered
  let lastPreview = null;
  let refs = {}; // live controls of the current ticket form

  // ----------------------------------------------------------- message card
  const textarea = h("textarea", {
    class: "sig-text",
    rows: 8,
    spellcheck: false,
    "aria-label": t("Signal message"),
    // The example after the instruction is a real signal's text: it stays as signals are written.
    placeholder: t("Paste the signal here, e.g.") + "\nXAUUSD BUY NOW @ 2650\nSL 2640\nTP1 2660  TP2 2670  TP3 2685",
    value: memory.text,
    oninput: () => {
      memory.text = textarea.value;
      parseSoon();
    },
    onpaste: () =>
      setTimeout(() => {
        memory.text = textarea.value;
        parseNow();
      }, 0),
  });
  const parseBox = h("div", { class: "stack sig-parse" });
  const ticketBox = h("div", { class: "sig-ticket" });
  const resultBox = h("div", { class: "stack sig-result" });

  function tryExample() {
    memory.example = (memory.example + 1) % EXAMPLES.length;
    textarea.value = memory.text = EXAMPLES[memory.example];
    parseNow();
  }

  function clearAll() {
    seq++; // drop responses still in flight
    resetMemory();
    textarea.value = "";
    lastPreview = null;
    renderParsed();
    renderTicket();
    renderEmptyResult();
    textarea.focus();
  }

  // ----------------------------------------------------------- requests
  async function parseNow() {
    const text = memory.text;
    if (text === memory.parsedText && memory.draft) return;
    if (!text.trim()) {
      clearAll();
      return;
    }
    // Anti-FOMO warnings belong to the Vision idea handed over, not to a ticket pasted or typed afterwards.
    if (memory.vision && text !== memory.vision.text) memory.vision = null;
    const id = ++seq;
    try {
      // The trader's own context (risk, strategy, timeframe, confluences) survives a new paste; a hand-off
      // brings its own.
      const previous = memory.context || memory.draft;
      const r = await api.post("/api/signals/parse", {
        text,
        risk_pct: previous?.risk_pct ?? null,
        strategy: previous?.strategy || "",
        timeframe: previous?.timeframe || "",
        confluences: previous?.confluences || [],
        account_id: chosenAccountId(),
      });
      if (id !== seq) return;
      Object.assign(memory, { parsedText: text, parsed: r.parsed, draft: normalizeDraft(r.draft), splitAuto: true, context: null, orderKey: newOrderKey() });
      renderParsed();
      renderTicket();
      showPreview(r);
    } catch (error) {
      if (id === seq) showError(error);
    }
  }
  const parseSoon = debounce(parseNow, 400);

  async function previewNow() {
    if (!memory.draft) return;
    const id = ++seq;
    try {
      const r = await api.post("/api/signals/preview", payload());
      if (id === seq) showPreview(r);
    } catch (error) {
      if (id === seq) showError(error);
    }
  }
  const previewSoon = debounce(previewNow, 250);

  function payload() {
    const d = memory.draft;
    return {
      symbol: d.symbol || "",
      direction: d.direction || null,
      entry: d.entry,
      stop_loss: d.stop_loss,
      stop_pips: d.stop_pips,
      targets: d.targets.filter(filled).map((target) => ({
        price: target.price,
        pips: target.price === null ? target.pips : null,
        close_pct: memory.splitAuto ? null : target.close_pct,
      })),
      risk_pct: d.risk_pct,
      account_equity: d.account_equity ?? null,
      lots: d.lots,
      fx_rate: d.fx_rate ?? null,
      be_enabled: Boolean(d.be_enabled),
      be_offset_pips: d.be_offset_pips ?? 0,
      trail_enabled: Boolean(d.trail_enabled),
      trail_distance_pips: d.trail_distance_pips,
      trail_trigger: d.trail_trigger || "tp1",
      strategy: d.strategy,
      timeframe: d.timeframe,
      confluences: d.confluences,
      signal_text: memory.text,
      account_id: d.account_id ?? chosenAccountId(),
      order_type: d.order_type || "market",
    };
  }

  // ----------------------------------------------------------- parse result
  function renderParsed() {
    const parsed = memory.parsed;
    if (!parsed || !memory.text.trim()) {
      clear(parseBox);
      return;
    }
    clear(
      parseBox,
      parseSummary(parsed),
      violationList(parsed.warnings.map((message) => ({ severity: "warn", message }))),
    );
  }

  // ----------------------------------------------------------- ticket form
  function renderTicket() {
    const d = memory.draft;
    refs = {};
    if (!d) {
      clear(ticketBox, h("div", { class: "empty" }, h("strong", {}, t("No ticket yet")), t("Paste a signal above - everything it contains lands here for review.")));
      return;
    }
    const changed = () => previewSoon();
    const numInput = (key, attrs = {}) =>
      h("input", {
        type: "number",
        step: "any",
        min: "0",
        inputmode: "decimal",
        value: inputValue(d[key]),
        oninput: (e) => {
          d[key] = num(e.target.value);
          changed();
        },
        ...attrs,
      });

    const options = markets.map((m) => [m.symbol, `${m.symbol} - ${m.name}`]);
    if (!d.symbol) options.unshift(["", t("Choose instrument…")]);
    else if (!markets.some((m) => m.symbol === d.symbol)) options.unshift([d.symbol, t("{symbol} (not in your markets)", { symbol: d.symbol })]);
    const symbol = select(options, d.symbol, {
      onchange: (e) => {
        d.symbol = e.target.value;
        changed();
      },
    });
    const direction = segmented(
      [
        ["long", t("Long"), "long"],
        ["short", t("Short"), "short"],
      ],
      d.direction,
      (value) => {
        d.direction = value;
        changed();
      },
    );
    const accountOptions = tradableAccounts().map((account) => [String(account.id), account.name]);
    const accountSelect = select(accountOptions, String(d.account_id ?? chosenAccountId() ?? ""), {
      onchange: (e) => {
        d.account_id = Number(e.target.value);
        memory.accountId = d.account_id;
        memory.orderKey = newOrderKey(); // another account is another order
        changed();
      },
    });
    const orderTypeSelect = select(
      ORDER_TYPES.map(([value, label]) => [value, t(label)]),
      d.order_type || "market",
      {
        onchange: (e) => {
          d.order_type = e.target.value;
          changed();
        },
      },
    );
    const stopHelp = d.stop_pips
      ? t("Signal gave {pips} pips - leave blank to keep the stop that far from your entry.", { pips: fmt.num(d.stop_pips, 0) })
      : null;

    // Targets ---------------------------------------------------------------
    const targetsBox = h("div", { class: "sig-targets" });
    const splitControl = h("div", { class: "row sig-split" });
    refs.closeInputs = [];

    function renderTargets() {
      refs.closeInputs = [];
      const rows = d.targets.map((target, i) => {
        const price = h("input", {
          type: "number",
          step: "any",
          min: "0",
          inputmode: "decimal",
          value: inputValue(target.price),
          placeholder: target.pips ? t("+{pips} pips", { pips: fmt.num(target.pips, 0) }) : t("Price"),
          "aria-label": t("TP{n} price", { n: i + 1 }),
          oninput: (e) => {
            const was = filled(target);
            target.price = num(e.target.value);
            if (filled(target) !== was) refreshSplit();
            changed();
          },
        });
        const pct = h("input", {
          type: "number",
          step: "any",
          min: "0",
          max: "100",
          inputmode: "decimal",
          "aria-label": t("TP{n} close percent", { n: i + 1 }),
          oninput: (e) => {
            if (memory.splitAuto) freezeSplit();
            target.close_pct = num(e.target.value);
            changed();
          },
        });
        refs.closeInputs.push(pct);
        return h(
          "div",
          { class: "sig-tp-row" },
          h("span", { class: "sig-tp-label" }, `TP${i + 1}`),
          price,
          h("div", { class: "sig-pct" }, pct, h("span", { "aria-hidden": "true" }, "%")),
          h(
            "button",
            {
              type: "button",
              class: "btn ghost icon-only",
              "aria-label": t("Remove TP{n}", { n: i + 1 }),
              onclick: () => {
                d.targets.splice(i, 1);
                renderTargets();
                changed();
              },
            },
            icon("x"),
          ),
        );
      });
      clear(
        targetsBox,
        rows.length
          ? h("div", { class: "sig-tp-row sig-tp-head", "aria-hidden": "true" }, h("span", {}), h("span", {}, t("Price")), h("span", {}, t("Close")), h("span", {}))
          : h("p", { class: "muted small" }, t("No targets - the trade is logged without a take profit.")),
        rows,
        splitControl,
      );
      refreshSplit();
    }

    function addTarget() {
      d.targets.push({ price: null, pips: null, close_pct: null });
      renderTargets();
      targetsBox.querySelectorAll(".sig-tp-row:not(.sig-tp-head) input")[(d.targets.length - 1) * 2]?.focus();
    }

    function defaultsFor(count) {
      return splits[String(count)] || [];
    }

    // Close % follow the default split for the number of priced targets until edited.
    function refreshSplit() {
      const priced = d.targets.filter(filled);
      const defaults = defaultsFor(priced.length);
      if (memory.splitAuto) {
        d.targets.forEach((target, i) => {
          const input = refs.closeInputs[i];
          const k = priced.indexOf(target);
          input.value = k >= 0 ? String(defaults[k]) : "";
          input.placeholder = "";
        });
      }
      clear(
        splitControl,
        d.targets.length < enums.max_targets ? h("button", { type: "button", class: "btn sm", onclick: addTarget }, icon("plus"), t("Add target")) : null,
        h("span", { class: "spacer" }),
        memory.splitAuto
          ? d.targets.length
            ? h("span", { class: "muted small" }, t("Default split"))
            : null
          : h(
              "button",
              {
                type: "button",
                class: "btn ghost sm",
                onclick: () => {
                  memory.splitAuto = true;
                  for (const target of d.targets) target.close_pct = null;
                  renderTargets();
                  changed();
                },
              },
              icon("undo"),
              t("Default split"),
            ),
      );
    }

    function freezeSplit() {
      const priced = d.targets.filter(filled);
      const defaults = defaultsFor(priced.length);
      priced.forEach((target, k) => (target.close_pct = defaults[k] ?? null));
      memory.splitAuto = false;
      refreshSplit();
    }

    // Size ------------------------------------------------------------------
    const lotsHelp = h("div", { class: "help" });
    refs.lots = h("input", {
      type: "number",
      step: "any",
      min: "0",
      inputmode: "decimal",
      value: inputValue(d.lots ?? lastPreview?.sizing?.auto_lots),
      "aria-label": t("Lots"),
      oninput: (e) => {
        d.lots = num(e.target.value);
        renderLotsHelp();
        changed();
      },
    });
    function renderLotsHelp() {
      clear(
        lotsHelp,
        d.lots === null
          ? t("Auto from your risk rules")
          : [
              t("Manual size"),
              " · ",
              h(
                "button",
                {
                  type: "button",
                  class: "sig-link",
                  onclick: () => {
                    d.lots = null;
                    refs.lots.value = inputValue(lastPreview?.sizing?.auto_lots);
                    renderLotsHelp();
                    changed();
                  },
                },
                t("reset to auto"),
              ),
            ],
      );
    }
    renderLotsHelp();
    refs.fx = h("div", { class: "sig-fx" });
    const fxInput = numInput("fx_rate", { placeholder: t("e.g. 1.08") });

    // Strategy context, checked by the Strategy Co-Pilot ---------------------
    const strategyNames = state.plan.strategies.filter((x) => x.active || x.name === d.strategy).map((x) => x.name);
    const strategySelect = select([["", t("No strategy")], ...strategyNames], d.strategy, {
      onchange: (e) => {
        d.strategy = e.target.value;
        renderConfluences();
        changed();
      },
    });
    const timeframeSelect = select([["", "—"], ...enums.timeframes], d.timeframe, {
      onchange: (e) => {
        d.timeframe = e.target.value;
        changed();
      },
    });
    const confluenceBox = h("div", { class: "chips" });
    const confluenceHint = h("span", { class: "muted small" });

    function renderConfluences() {
      const strategy = strategyByName(d.strategy);
      const required = strategy?.required_confluences || [];
      const names = state.plan.confluences.filter((c) => c.active).map((c) => c.name);
      for (const name of [...required, ...d.confluences]) if (!names.includes(name)) names.push(name);
      clear(
        confluenceBox,
        names.length
          ? names.map((name) => {
              const on = d.confluences.includes(name);
              return h(
                "button",
                {
                  type: "button",
                  class: `chip-toggle${on ? " on" : ""}`,
                  "aria-pressed": String(on),
                  onclick: () => {
                    d.confluences = on ? d.confluences.filter((n) => n !== name) : [...d.confluences, name];
                    renderConfluences();
                    changed();
                  },
                },
                name,
                required.includes(name) ? h("span", { class: "sig-req" }, t("required")) : null,
              );
            })
          : h("p", { class: "muted small" }, t("No confluences defined yet - add them in Trading Plan.")),
      );
      const min = strategy?.min_confluences || 0;
      confluenceHint.textContent = min
        ? t("{count} / {min} required by {strategy}", { count: d.confluences.length, min, strategy: d.strategy })
        : t("{count} selected", { count: d.confluences.length });
      confluenceHint.className = min && d.confluences.length < min ? "small neg" : "muted small";
    }
    renderConfluences();

    // Management ------------------------------------------------------------
    const beOffset = numInput("be_offset_pips", { placeholder: "0", disabled: !d.be_enabled });
    const beBox = h(
      "div",
      { class: `sig-toggle${d.be_enabled ? "" : " off"}` },
      h(
        "label",
        { class: "check" },
        h("input", {
          type: "checkbox",
          checked: Boolean(d.be_enabled),
          onchange: (e) => {
            d.be_enabled = e.target.checked;
            beOffset.disabled = !d.be_enabled;
            beBox.classList.toggle("off", !d.be_enabled);
            changed();
          },
        }),
        h("span", {}, h("strong", {}, t("Break-even at TP1")), h("span", { class: "muted small" }, t("Stop moves to entry once TP1 fills"))),
      ),
      h("div", { class: "sig-toggle-fields" }, field(t("Lock in (pips)"), beOffset)),
    );
    const trailDistance = numInput("trail_distance_pips", { placeholder: t("pips"), disabled: !d.trail_enabled });
    const trailTrigger = select(
      enums.trail_triggers.map((value) => [value, TRIGGER_LABELS[value] ? t(TRIGGER_LABELS[value]) : value]),
      d.trail_trigger || "tp1",
      {
        disabled: !d.trail_enabled,
        onchange: (e) => {
          d.trail_trigger = e.target.value;
          changed();
        },
      },
    );
    const trailBox = h(
      "div",
      { class: `sig-toggle${d.trail_enabled ? "" : " off"}` },
      h(
        "label",
        { class: "check" },
        h("input", {
          type: "checkbox",
          checked: Boolean(d.trail_enabled),
          onchange: (e) => {
            d.trail_enabled = e.target.checked;
            trailDistance.disabled = trailTrigger.disabled = !d.trail_enabled;
            trailBox.classList.toggle("off", !d.trail_enabled);
            changed();
          },
        }),
        h("span", {}, h("strong", {}, t("Trailing stop")), h("span", { class: "muted small" }, t("Locks in profit as price moves"))),
      ),
      h("div", { class: "sig-toggle-fields" }, field(t("Distance (pips)"), trailDistance), field(t("Start at"), trailTrigger)),
    );

    clear(
      ticketBox,
      h(
        "div",
        { class: "form-grid" },
        field(t("Trading account"), accountSelect, { help: t("Sized with this account's equity and logged on it.") }),
        field(t("Order type"), orderTypeSelect, { help: t("How the order is placed when you send it to the account.") }),
      ),
      h("div", { class: "form-grid" }, field(t("Instrument"), symbol), field(t("Direction"), direction.el)),
      h(
        "div",
        { class: "form-grid" },
        field(t("Entry price"), numInput("entry", { placeholder: t("Fill price") })),
        field(t("Stop loss"), numInput("stop_loss", { placeholder: d.stop_pips ? t("{pips} pips from entry", { pips: fmt.num(d.stop_pips, 0) }) : t("Price") }), { help: stopHelp }),
      ),
      h("div", {}, h("div", { class: "sig-label" }, t("Take profits & scale-out")), targetsBox),
      h(
        "div",
        { class: "form-grid" },
        field(t("Risk per trade (%)"), numInput("risk_pct", { placeholder: String(settings.default_risk_pct), max: "100" }), {
          help: t("Blank = default {risk}%", { risk: settings.default_risk_pct }),
        }),
        h("div", { class: "field" }, h("label", {}, t("Lots")), refs.lots, lotsHelp),
      ),
      refs.fx,
      h("div", { class: "form-grid" }, field(t("Strategy"), strategySelect), field(t("Timeframe"), timeframeSelect)),
      h("div", {}, h("div", { class: "row sig-label" }, t("Confluences"), h("span", { class: "spacer" }), confluenceHint), confluenceBox),
      h("div", { class: "sig-manage" }, beBox, trailBox),
    );
    refs.fxField = fxInput;
    renderTargets();
  }

  // ----------------------------------------------------------- preview
  function showPreview(r) {
    lastPreview = r;
    const d = memory.draft;
    if (refs.lots && d && d.lots === null && document.activeElement !== refs.lots) {
      refs.lots.value = inputValue(r.sizing?.auto_lots);
    }
    const fxKey = r.needs_fx && r.market ? r.market.symbol : null;
    if (refs.fx && refs.fxKey !== fxKey) {
      // Rebuilt only when the instrument changes, so typing in the rate keeps focus.
      refs.fxKey = fxKey;
      const market = r.market;
      clear(
        refs.fx,
        fxKey
          ? field(t("Conversion rate: 1 {quote} = ? {currency}", { quote: market.quote_currency, currency: settings.currency }), refs.fxField, {
              help: t("{symbol} is quoted in {quote}; needed to convert risk into {currency}.", {
                symbol: market.symbol,
                quote: market.quote_currency,
                currency: settings.currency,
              }),
            })
          : null,
      );
    }
    if (refs.closeInputs && d && !memory.splitAuto && r.targets.length) {
      const priced = d.targets.filter(filled);
      d.targets.forEach((target, i) => {
        const k = priced.indexOf(target);
        if (target.close_pct === null && k >= 0 && r.targets[k]) refs.closeInputs[i].placeholder = fmt.num(r.targets[k].close_pct, 0);
      });
    }
    renderResult(r);
  }

  function showError(error) {
    clear(
      resultBox,
      h("div", { class: "card-head" }, h("h2", {}, t("Result"))),
      issueList([error instanceof ApiError ? error.message : String(error)], { kind: "block", tag: t("Error"), iconName: "alert" }),
    );
  }

  function renderEmptyResult() {
    clear(
      resultBox,
      h(
        "div",
        { class: "empty" },
        icon("signal", "icon sig-empty-icon"),
        h("strong", {}, t("Paste a signal to start")),
        t(
          "Symbol, direction, entry, stop and up to {max} targets are extracted, sized with your {risk}% risk rule and checked against every guardrail before you can execute.",
          { max: enums.max_targets, risk: settings.default_risk_pct },
        ),
        h("div", { style: { marginTop: "14px" } }, h("button", { type: "button", class: "btn", onclick: tryExample }, icon("play"), t("Try an example"))),
      ),
    );
  }

  function renderResult(r) {
    const d = memory.draft;
    const s = r.sizing;
    const ticket = r.ticket;
    const side = ticket?.direction || d?.direction;
    const stopPrice = ticket?.stop_loss ?? d?.stop_loss;
    const blocks = r.check ? r.check.violations.filter((v) => v.severity === "block") : [];

    const hero = s
      ? h(
          "div",
          { class: "result-hero" },
          h("div", { class: "label" }, t("Position size"), s.manual ? h("span", { class: "badge warn sig-badge" }, t("manual")) : null),
          h("div", { class: "value num" }, fmt.lots(s.lots), h("span", { class: "unit" }, t("lots"))),
          h(
            "div",
            { class: "muted small" },
            [
              side ? `${t(SIDE[side])} ${r.market?.symbol || d.symbol}` : r.market?.symbol,
              d?.entry ? t("entry {price}", { price: fmt.price(d.entry) }) : null,
              stopPrice
                ? t("stop {price} ({pips} pips)", { price: fmt.price(stopPrice), pips: fmt.num(s.stop_pips, 1) })
                : t("stop {pips} pips", { pips: fmt.num(s.stop_pips, 1) }),
            ]
              .filter(Boolean)
              .join(" · "),
          ),
        )
      : null;

    const metrics = s
      ? h(
          "div",
          { class: "live-metrics", style: { marginTop: 0 } },
          metric(t("Risk at stop"), fmt.signedMoney(-s.max_loss), "neg"),
          metric(t("Risk % of equity"), fmt.pct(s.actual_risk_pct, 2)),
          metric(t("Allowed risk"), `${fmt.money(s.risk_amount)} (${fmt.pct(s.risk_pct, 2)})`),
          metric(t("Pip value / lot"), fmt.money(s.pip_value_per_lot)),
          metric(t("Blended R:R"), fmt.rr(r.blended_rr)),
          metric(t("Potential profit"), r.potential_profit === null ? "—" : fmt.signedMoney(r.potential_profit), r.potential_profit > 0 ? "pos" : ""),
        )
      : null;

    const table = r.targets.length
      ? h(
          "div",
          { class: "table-wrap" },
          h(
            "table",
            { class: "sig-table" },
            h("thead", {}, h("tr", {}, [t("Target"), t("Price"), t("Close"), t("Lots"), "R:R", t("Profit")].map((c, i) => h("th", { class: i ? "num" : "" }, c)))),
            h(
              "tbody",
              {},
              r.targets.map((x) =>
                h(
                  "tr",
                  {},
                  h("td", {}, `TP${x.level}`),
                  h("td", { class: "num" }, fmt.price(x.price), x.pips !== null ? h("span", { class: "muted sig-pips" }, `${fmt.num(x.pips, 0)}p`) : null),
                  h("td", { class: "num" }, `${fmt.num(x.close_pct, Number.isInteger(x.close_pct) ? 0 : 1)}%`),
                  h("td", { class: "num" }, fmt.lots(x.lots)),
                  h("td", { class: "num" }, fmt.rr(x.rr)),
                  h("td", { class: `num ${x.profit > 0 ? "pos" : x.profit < 0 ? "neg" : ""}` }, x.profit === null ? "—" : fmt.signedMoney(x.profit)),
                ),
              ),
            ),
          ),
        )
      : null;

    const rrBar =
      s && r.blended_rr && r.potential_profit !== null
        ? h(
            "div",
            {},
            h(
              "div",
              { class: "rr-bar", role: "img", "aria-label": t("Risk {risk} versus blended reward {reward}", { risk: fmt.money(s.max_loss), reward: fmt.money(r.potential_profit) }) },
              h("div", { class: "risk", style: { flex: String(1 / (1 + r.blended_rr)) } }),
              h("div", { class: "reward", style: { flex: String(r.blended_rr / (1 + r.blended_rr)) } }),
            ),
            h("div", { class: "row small muted" }, h("span", {}, t("Risk {amount}", { amount: fmt.signedMoney(-s.max_loss) })), h("span", { class: "spacer" }), h("span", {}, t("Reward {amount}", { amount: fmt.signedMoney(r.potential_profit) }))),
          )
        : null;

    const management = ticket
      ? h(
          "div",
          { class: "sig-summary" },
          h(
            "span",
            { class: "chip" },
            icon("shield"),
            !ticket.be_enabled
              ? t("No auto break-even")
              : ticket.be_offset_pips
                ? t("Break-even at TP1 +{pips} pips", { pips: fmt.num(ticket.be_offset_pips, 1) })
                : t("Break-even at TP1"),
          ),
          h(
            "span",
            { class: "chip" },
            icon("trending"),
            !ticket.trail_enabled
              ? t("No trailing stop")
              : ticket.trail_trigger === "immediate"
                ? t("Trail {pips} pips from entry", { pips: fmt.num(ticket.trail_distance_pips, 1) })
                : t("Trail {pips} pips after TP1", { pips: fmt.num(ticket.trail_distance_pips, 1) }),
          ),
        )
      : null;

    const guardBox = h(
      "div",
      { class: "sig-section stack" },
      h("h3", {}, icon("shield"), t("Guardrails")),
      r.check
        ? guardrailFindings(r.check.violations, ticket.strategy, t("All guardrails pass - ready to execute."))
        : h("p", { class: "muted small" }, t("Checked as soon as the ticket is complete.")),
    );

    let reason = null;
    if (!ticket) reason = r.errors[0] || t("Complete the ticket to execute.");
    else if (r.check.blocked) reason = tn(blocks.length, "Blocked by {n} guardrail - see above.", "Blocked by {n} guardrails - see above.");
    const button = h("button", { type: "button", class: "btn primary sig-execute", disabled: Boolean(reason), onclick: () => execute(button, guardBox) }, icon("zap"), t("Execute & log trade"));
    const target = r.account;
    const sendReason = reason || (target && !target.can_execute ? target.reason : null);
    const sendButton = target
      ? h(
          "button",
          { type: "button", class: "btn sig-execute-on", disabled: Boolean(sendReason), onclick: () => executeOnAccount(sendButton, guardBox) },
          icon("arrowRight"),
          t("Execute on {account}", { account: target.name }),
        )
      : null;
    const sendRow = target
      ? h(
          "div",
          { class: "sig-execute-row sig-send-row" },
          sendButton,
          h(
            "p",
            { class: "small muted sig-reason" },
            icon(target.can_execute ? "link" : "lock"),
            target.can_execute
              ? t("Sends a {type} order through {connection} after you confirm it, then logs the trade.", {
                  type: t(ORDER_TYPE_LABEL[target.order_type] || ORDER_TYPE_LABEL.market),
                  connection: connectionLabel(target.connection),
                })
              : target.reason,
          ),
        )
      : null;

    clear(
      resultBox,
      h(
        "div",
        { class: "card-head" },
        h(
          "div",
          {},
          h("h2", {}, t("Result")),
          h("p", {}, t("{currency} account · min R:R 1:{rr} · max {risk}% per trade", { currency: r.account?.currency || settings.currency, rr: settings.min_rr, risk: settings.max_risk_pct })),
        ),
      ),
      issueList(r.errors, { kind: "block", tag: t("Fix first"), iconName: "alert" }),
      hero,
      metrics,
      table,
      rrBar,
      issueList(r.warnings, { kind: "warn", tag: t("Note"), iconName: "info" }),
      visionWarnings(),
      s && s.notes.length ? h("div", { class: "muted small sig-notes" }, s.notes.map((note) => h("p", {}, note))) : null,
      management,
      guardBox,
      h(
        "div",
        { class: "sig-execute-row" },
        button,
        h(
          "p",
          { class: "small muted sig-reason" },
          icon(reason ? "lock" : "info"),
          reason ||
            t("Logs a live {side} of {lots} lots {symbol} at {price}, timed now.", {
              side,
              lots: fmt.lots(ticket.position_size),
              symbol: ticket.symbol,
              price: fmt.price(ticket.entry_price),
            }),
        ),
      ),
      sendRow,
    );
  }

  // ----------------------------------------------------------- execute
  async function execute(button, guardBox) {
    button.disabled = true;
    try {
      // Re-check against the latest account state (and a fresh entry time) before sending.
      const fresh = await api.post("/api/signals/preview", payload());
      showPreview(fresh);
      if (!fresh.ticket || fresh.check.blocked) {
        toast(t("The ticket can't be executed as it stands - review the result panel."), "warn");
        return;
      }
      const warnings = [...fresh.check.violations.filter((v) => v.severity === "warn"), ...(memory.vision?.warnings || [])];
      if (warnings.length) {
        const message = tn(
          warnings.length,
          "This trade breaks {n} rule (warnings, not blocks). Log it anyway?",
          "This trade breaks {n} rules (warnings, not blocks). Log it anyway?",
        );
        const ok = await confirmDialog(message, { title: t("Execute with warnings"), confirmText: t("Execute anyway") });
        if (!ok) return;
      }
      const result = await api.post("/api/trades", fresh.ticket);
      const trade = result.trade;
      toast(
        t("{side} {lots} lots {symbol} logged", { side: t(SIDE[trade.direction]), lots: fmt.lots(trade.position_size), symbol: trade.symbol }),
        result.warnings.length ? "warn" : "success",
      );
      if (memory.alert && memory.alert.symbol === trade.symbol) {
        // The ticket came from a strategy alert: mark it taken and link the trade.
        api.post(`/api/alerts/${memory.alert.id}/status`, { status: "taken", trade_id: trade.id }).catch(() => {});
      }
      resetMemory();
      loadStatus().catch(() => {});
      location.hash = "#/positions";
    } catch (error) {
      if (error instanceof ApiError && error.status === 423) showBlocked(error, guardBox);
      else toast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  function showBlocked(error, guardBox) {
    clear(
      guardBox,
      h("h3", {}, icon("shield"), t("Guardrails")),
      h("p", { class: "small neg" }, t("Entry refused - fix these first:")),
      guardrailFindings(error.payload.violations || [], memory.draft?.strategy || ""),
    );
    guardBox.scrollIntoView({ behavior: "smooth", block: "center" });
    toast(t("Trade blocked by your risk rules"), "error");
    loadStatus().catch(() => {});
  }

  // ----------------------------------------------------------- execute on a trading account (v1.5)
  async function executeOnAccount(button, guardBox) {
    button.disabled = true;
    try {
      // The same fresh re-check as Execute; the server runs every guardrail again before sending anything.
      const fresh = await api.post("/api/signals/preview", payload());
      showPreview(fresh);
      if (!fresh.ticket || fresh.check.blocked) {
        toast(t("The ticket can't be executed as it stands - review the result panel."), "warn");
        return;
      }
      if (!fresh.account?.can_execute) {
        toast(fresh.account?.reason || t("Order sending is off for this account."), "warn");
        return;
      }
      const warnings = [...fresh.check.violations.filter((v) => v.severity === "warn"), ...(memory.vision?.warnings || [])];
      if (!(await confirmOrder(fresh, warnings))) return;
      memory.orderKey = memory.orderKey || newOrderKey();
      const result = await api.post("/api/execution/orders", {
        account_id: fresh.account.id,
        idempotency_key: memory.orderKey,
        order_type: fresh.account.order_type,
        trade: fresh.ticket,
      });
      const accepted = ACCEPTED.includes(result.execution.status);
      if (accepted) {
        if (result.trade && memory.alert && memory.alert.symbol === result.trade.symbol) {
          api.post(`/api/alerts/${memory.alert.id}/status`, { status: "taken", trade_id: result.trade.id }).catch(() => {});
        }
        resetMemory();
        loadStatus().catch(() => {});
      } else {
        memory.orderKey = newOrderKey(); // a rejected order may be sent again, on purpose
      }
      showOrderResult(result, accepted);
    } catch (error) {
      if (error instanceof ApiError && error.status === 423) showBlocked(error, guardBox);
      else toast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  /** The explicit confirmation of an order: account, symbol, side, lots, entry, stop loss and targets. */
  function confirmOrder(fresh, warnings) {
    const ticket = fresh.ticket;
    const target = fresh.account;
    const type = target.order_type || "market";
    const targets = ticket.targets.length ? ticket.targets : ticket.take_profit ? [{ price: ticket.take_profit, close_pct: 100 }] : [];
    const rows = [
      [t("Account"), `${target.name} · ${connectionLabel(target.connection)}`],
      [t("Instrument"), ticket.symbol],
      [t("Side"), h("span", { class: `badge ${ticket.direction}` }, t(SIDE[ticket.direction]))],
      [t("Lots"), fmt.lots(ticket.position_size)],
      [t("Order type"), t(ORDER_TYPE_LABEL[type])],
      [t("Entry"), type === "market" ? t("≈ {price} (market price)", { price: fmt.price(ticket.entry_price) }) : fmt.price(ticket.entry_price)],
      [t("Stop loss"), fmt.price(ticket.stop_loss)],
      [
        t("Take profits"),
        targets.length ? targets.map((tp, i) => `TP${i + 1} ${fmt.price(tp.price)} (${fmt.num(tp.close_pct, Number.isInteger(tp.close_pct) ? 0 : 1)}%)`).join(" · ") : t("None"),
      ],
    ];
    const live = target.connection !== "paper";
    return new Promise((resolve) => {
      let answered = false;
      const finish = (value) => {
        answered = true;
        resolve(value);
        ref.close();
      };
      const ref = modal({
        title: t("Send this order to {account}?", { account: target.name }),
        body: h(
          "div",
          { class: "stack sig-order-confirm" },
          h("dl", { class: "sig-order-list" }, rows.map(([label, value]) => [h("dt", {}, label), h("dd", {}, value)])),
          targets.length > 1
            ? h("p", { class: "small muted" }, t("The order carries the last target ({price}); exits at the other targets are tracked in the journal - manage them on your platform.", { price: fmt.price(ticket.take_profit) }))
            : null,
          warnings.length
            ? issueList([tn(warnings.length, "This trade breaks {n} rule (warnings, not blocks).", "This trade breaks {n} rules (warnings, not blocks).")], { kind: "warn", tag: t("Note"), iconName: "alert" })
            : null,
          live ? h("p", { class: "small" }, icon("alert"), " ", t("This is a real order on your trading account.")) : h("p", { class: "small muted" }, t("Paper trading: simulated, nothing reaches a broker.")),
        ),
        footer: [
          h("button", { type: "button", class: "btn ghost", onclick: () => finish(false) }, t("Cancel")),
          h("button", { type: "button", class: `btn ${live ? "danger" : "primary"}`, onclick: () => finish(true) }, icon("arrowRight"), t("Send order")),
        ],
        onClose: () => answered || resolve(false),
      });
    });
  }

  /** What happened to the order: filled at X with a ticket, queued for the MT terminal, or rejected and why. */
  function showOrderResult(result, accepted) {
    const ex = result.execution;
    let headline;
    if (ex.status === "filled") headline = t("Filled at {price} · ticket {ticket}", { price: fmt.price(ex.fill_price ?? ex.price), ticket: ex.broker_ref || "—" });
    else if (ex.status === "pending" && ex.bridge === "mt_bridge") headline = t("Queued for the MetaTrader terminal - the EA places it within seconds.");
    else if (ex.status === "pending") headline = t("Pending order waiting at {price}", { price: fmt.price(ex.price) });
    else if (ex.status === "sent") headline = t("Sent - waiting for the broker's confirmation.");
    else if (ex.status === "rejected") headline = t("Rejected: {reason}", { reason: ex.message });
    else headline = t("Not confirmed: {reason}", { reason: ex.message });
    const kind = accepted ? "success" : "error";
    toast(headline, kind, 6000);
    const ref = modal({
      title: accepted ? t("Order sent to {account}", { account: result.account.name }) : t("Order not executed"),
      body: h(
        "div",
        { class: "stack" },
        h("div", { class: `sig-order-result ${accepted ? "ok" : "bad"}`, role: "status" }, icon(accepted ? "checkCircle" : "xCircle"), h("strong", {}, headline)),
        ex.message && !["rejected", "error"].includes(ex.status) ? h("p", { class: "small muted" }, ex.message) : null,
        result.duplicate ? h("p", { class: "small muted" }, t("This ticket was already sent - this is the first result, nothing was sent again.")) : null,
        h("p", { class: "small" }, result.trade ? t("Logged in the journal on {account}.", { account: result.account.name }) : t("Nothing was logged in the journal.")),
      ),
      footer: [
        accepted
          ? h(
              "button",
              {
                type: "button",
                class: "btn ghost",
                onclick: () => {
                  ref.close();
                  location.hash = "#/positions";
                },
              },
              t("Open positions"),
            )
          : null,
        h("button", { type: "button", class: "btn primary", onclick: () => ref.close() }, t("Close")),
      ],
      onClose: () => {
        if (accepted) clearAll();
      },
    });
  }

  // ----------------------------------------------------------- layout
  clear(
    root,
    h(
      "div",
      { class: "page-head" },
      h(
        "div",
        {},
        h("h1", {}, t("Signal parser")),
        h("p", {}, t("Paste a Telegram / WhatsApp signal - it is parsed, sized with your risk rules and checked against every guardrail before you can execute it.")),
      ),
      h("a", { class: "btn ghost", href: "#/calculator" }, icon("calculator"), t("Risk calculator")),
    ),
    h(
      "div",
      { class: "grid split" },
      h(
        "div",
        { class: "stack" },
        h(
          "div",
          { class: "card stack sig-card" },
          h(
            "div",
            { class: "card-head" },
            h("div", {}, h("h2", {}, t("Paste a signal")), h("p", {}, t("Parsed automatically as you paste or type."))),
            h(
              "div",
              { class: "row" },
              h("button", { type: "button", class: "btn sm", onclick: tryExample }, icon("play"), t("Try an example")),
              h("button", { type: "button", class: "btn ghost sm", onclick: clearAll }, icon("x"), t("Clear")),
            ),
          ),
          textarea,
          parseBox,
        ),
        h("div", { class: "card" }, h("div", { class: "card-head" }, h("div", {}, h("h2", {}, t("Ticket")), h("p", {}, t("Review and correct anything the parser got wrong.")))), ticketBox),
      ),
      h("div", { class: "card" }, resultBox),
    ),
  );

  renderParsed();
  renderTicket();
  if (memory.text.trim() && memory.text !== memory.parsedText) parseNow();
  else if (memory.draft) previewNow();
  else renderEmptyResult();
}
