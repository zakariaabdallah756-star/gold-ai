// Signals: paste a Telegram / WhatsApp trade signal, size it with the account's
// risk rules, check every guardrail, then execute it as a live trade.

import { api, ApiError } from "../api.js";
import { clear, confirmDialog, debounce, field, h, icon, numberOrNull, segmented, select, toast } from "../dom.js";
import { fmt } from "../format.js";
import { handoff, loadStatus, state, strategyByName } from "../state.js";
import { violationList } from "./calculator.js";
import { copilotPanel, splitCopilot } from "./copilot.js";

const EXAMPLES = [
  "🟢 GOLD BUY 2648-2652\n🛑 SL: 2640\n🎯 TP1: 2664\n🎯 TP2: 2676\n🎯 TP3: 2712",
  "Sell EUR/USD @1.0850\nStop Loss 1.0880\nTake Profit 1: 1.0820\nTake Profit 2: 1.0760",
  "NAS100 SHORT 18,550 SL: 18,620 TP: 18,400",
];
const TRIGGER_LABELS = { tp1: "After TP1", immediate: "Immediately" };
const SIDE = { long: "Buy", short: "Sell" };

// Kept between visits: the pasted message and the ticket being edited.
const memory = { text: "", parsedText: null, parsed: null, draft: null, splitAuto: true, example: -1 };

const num = (value) => numberOrNull({ value: value ?? "" });
const inputValue = (value) => (value === null || value === undefined ? "" : String(value));
const filled = (target) => target.price !== null || target.pips !== null;
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

function normalizeDraft(draft) {
  return {
    ...draft,
    strategy: draft.strategy || "",
    timeframe: draft.timeframe || "",
    confluences: [...(draft.confluences || [])],
    targets: (draft.targets || []).map((t) => ({ price: t.price ?? null, pips: t.pips ?? null, close_pct: t.close_pct ?? null })),
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
  Object.assign(memory, { text: "", parsedText: null, parsed: null, draft: null, splitAuto: true });
}

/** Same markup as calculator.violationList, for non-guardrail messages. */
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
  let entry = ["warn", "No entry - add your fill price"];
  if (parsed.entry_low !== null) entry = ["ok", `Zone ${fmt.price(parsed.entry_low)}–${fmt.price(parsed.entry_high)} → ${fmt.price(parsed.entry)}`];
  else if (parsed.entry !== null) entry = ["ok", `Entry ${fmt.price(parsed.entry)}`];
  let stop = ["bad", "No stop loss"];
  if (parsed.stop_loss !== null) stop = ["ok", `SL ${fmt.price(parsed.stop_loss)}`];
  else if (parsed.stop_pips !== null) stop = ["ok", `SL ${fmt.num(parsed.stop_pips, 0)} pips`];
  const items = [
    parsed.symbol ? ["ok", `${parsed.symbol}${raw}`] : ["bad", "No symbol"],
    parsed.direction ? ["ok", `${SIDE[parsed.direction]} · ${parsed.order_type}`] : ["bad", "No direction"],
    entry,
    stop,
    targets ? ["ok", plural(targets, "target")] : ["warn", "No take profit"],
  ];
  const icons = { ok: "checkCircle", warn: "alert", bad: "xCircle" };
  return h(
    "div",
    { class: "sig-summary", "aria-label": "What the parser found" },
    items.map(([kind, text]) => h("span", { class: `chip ${kind}` }, icon(icons[kind]), text)),
  );
}

export async function render(root) {
  if (handoff.signalText) {
    // A trade idea handed over by another page (e.g. Vision AI) is treated as a fresh paste.
    Object.assign(memory, { text: handoff.signalText, parsedText: null, parsed: null });
    handoff.signalText = null;
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
    "aria-label": "Signal message",
    placeholder: "Paste the signal here, e.g.\nXAUUSD BUY NOW @ 2650\nSL 2640\nTP1 2660  TP2 2670  TP3 2685",
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
    const id = ++seq;
    try {
      // The trader's own context (risk, strategy, timeframe, confluences) survives a new paste.
      const previous = memory.draft;
      const r = await api.post("/api/signals/parse", {
        text,
        risk_pct: previous?.risk_pct ?? null,
        strategy: previous?.strategy || "",
        timeframe: previous?.timeframe || "",
        confluences: previous?.confluences || [],
      });
      if (id !== seq) return;
      Object.assign(memory, { parsedText: text, parsed: r.parsed, draft: normalizeDraft(r.draft), splitAuto: true });
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
      targets: d.targets.filter(filled).map((t) => ({
        price: t.price,
        pips: t.price === null ? t.pips : null,
        close_pct: memory.splitAuto ? null : t.close_pct,
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
      clear(ticketBox, h("div", { class: "empty" }, h("strong", {}, "No ticket yet"), "Paste a signal above - everything it contains lands here for review."));
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
    if (!d.symbol) options.unshift(["", "Choose instrument…"]);
    else if (!markets.some((m) => m.symbol === d.symbol)) options.unshift([d.symbol, `${d.symbol} (not in your markets)`]);
    const symbol = select(options, d.symbol, {
      onchange: (e) => {
        d.symbol = e.target.value;
        changed();
      },
    });
    const direction = segmented(
      [
        ["long", "Long", "long"],
        ["short", "Short", "short"],
      ],
      d.direction,
      (value) => {
        d.direction = value;
        changed();
      },
    );
    const stopHelp = d.stop_pips ? `Signal gave ${fmt.num(d.stop_pips, 0)} pips - leave blank to keep the stop that far from your entry.` : null;

    // Targets ---------------------------------------------------------------
    const targetsBox = h("div", { class: "sig-targets" });
    const splitControl = h("div", { class: "row sig-split" });
    refs.closeInputs = [];

    function renderTargets() {
      refs.closeInputs = [];
      const rows = d.targets.map((t, i) => {
        const price = h("input", {
          type: "number",
          step: "any",
          min: "0",
          inputmode: "decimal",
          value: inputValue(t.price),
          placeholder: t.pips ? `+${fmt.num(t.pips, 0)} pips` : "Price",
          "aria-label": `TP${i + 1} price`,
          oninput: (e) => {
            const was = filled(t);
            t.price = num(e.target.value);
            if (filled(t) !== was) refreshSplit();
            changed();
          },
        });
        const pct = h("input", {
          type: "number",
          step: "any",
          min: "0",
          max: "100",
          inputmode: "decimal",
          "aria-label": `TP${i + 1} close percent`,
          oninput: (e) => {
            if (memory.splitAuto) freezeSplit();
            t.close_pct = num(e.target.value);
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
              "aria-label": `Remove TP${i + 1}`,
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
          ? h("div", { class: "sig-tp-row sig-tp-head", "aria-hidden": "true" }, h("span", {}), h("span", {}, "Price"), h("span", {}, "Close"), h("span", {}))
          : h("p", { class: "muted small" }, "No targets - the trade is logged without a take profit."),
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
        d.targets.forEach((t, i) => {
          const input = refs.closeInputs[i];
          const k = priced.indexOf(t);
          input.value = k >= 0 ? String(defaults[k]) : "";
          input.placeholder = "";
        });
      }
      clear(
        splitControl,
        d.targets.length < enums.max_targets ? h("button", { type: "button", class: "btn sm", onclick: addTarget }, icon("plus"), "Add target") : null,
        h("span", { class: "spacer" }),
        memory.splitAuto
          ? d.targets.length
            ? h("span", { class: "muted small" }, "Default split")
            : null
          : h(
              "button",
              {
                type: "button",
                class: "btn ghost sm",
                onclick: () => {
                  memory.splitAuto = true;
                  for (const t of d.targets) t.close_pct = null;
                  renderTargets();
                  changed();
                },
              },
              icon("undo"),
              "Default split",
            ),
      );
    }

    function freezeSplit() {
      const priced = d.targets.filter(filled);
      const defaults = defaultsFor(priced.length);
      priced.forEach((t, k) => (t.close_pct = defaults[k] ?? null));
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
      "aria-label": "Lots",
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
          ? "Auto from your risk rules"
          : [
              "Manual size · ",
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
                "reset to auto",
              ),
            ],
      );
    }
    renderLotsHelp();
    refs.fx = h("div", { class: "sig-fx" });
    const fxInput = numInput("fx_rate", { placeholder: "e.g. 1.08" });

    // Strategy context, checked by the Strategy Co-Pilot ---------------------
    const strategyNames = state.plan.strategies.filter((x) => x.active || x.name === d.strategy).map((x) => x.name);
    const strategySelect = select([["", "No strategy"], ...strategyNames], d.strategy, {
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
                required.includes(name) ? h("span", { class: "sig-req" }, "required") : null,
              );
            })
          : h("p", { class: "muted small" }, "No confluences defined yet - add them in Trading Plan."),
      );
      const min = strategy?.min_confluences || 0;
      confluenceHint.textContent = min ? `${d.confluences.length} / ${min} required by ${d.strategy}` : `${d.confluences.length} selected`;
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
        h("span", {}, h("strong", {}, "Break-even at TP1"), h("span", { class: "muted small" }, "Stop moves to entry once TP1 fills")),
      ),
      h("div", { class: "sig-toggle-fields" }, field("Lock in (pips)", beOffset)),
    );
    const trailDistance = numInput("trail_distance_pips", { placeholder: "pips", disabled: !d.trail_enabled });
    const trailTrigger = select(
      enums.trail_triggers.map((value) => [value, TRIGGER_LABELS[value] || value]),
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
        h("span", {}, h("strong", {}, "Trailing stop"), h("span", { class: "muted small" }, "Locks in profit as price moves")),
      ),
      h("div", { class: "sig-toggle-fields" }, field("Distance (pips)", trailDistance), field("Start", trailTrigger)),
    );

    clear(
      ticketBox,
      h("div", { class: "form-grid" }, field("Instrument", symbol), field("Direction", direction.el)),
      h(
        "div",
        { class: "form-grid" },
        field("Entry price", numInput("entry", { placeholder: "Fill price" })),
        field("Stop loss", numInput("stop_loss", { placeholder: d.stop_pips ? `${fmt.num(d.stop_pips, 0)} pips from entry` : "Price" }), { help: stopHelp }),
      ),
      h("div", {}, h("div", { class: "sig-label" }, "Take profits & scale-out"), targetsBox),
      h(
        "div",
        { class: "form-grid" },
        field("Risk per trade (%)", numInput("risk_pct", { placeholder: String(settings.default_risk_pct), max: "100" }), {
          help: `Blank = default ${settings.default_risk_pct}%`,
        }),
        h("div", { class: "field" }, h("label", {}, "Lots"), refs.lots, lotsHelp),
      ),
      refs.fx,
      h("div", { class: "form-grid" }, field("Strategy", strategySelect), field("Timeframe", timeframeSelect)),
      h("div", {}, h("div", { class: "row sig-label" }, "Confluences", h("span", { class: "spacer" }), confluenceHint), confluenceBox),
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
          ? field(`Conversion rate: 1 ${market.quote_currency} = ? ${settings.currency}`, refs.fxField, {
              help: `${market.symbol} is quoted in ${market.quote_currency}; needed to convert risk into ${settings.currency}.`,
            })
          : null,
      );
    }
    if (refs.closeInputs && d && !memory.splitAuto && r.targets.length) {
      const priced = d.targets.filter(filled);
      d.targets.forEach((t, i) => {
        const k = priced.indexOf(t);
        if (t.close_pct === null && k >= 0 && r.targets[k]) refs.closeInputs[i].placeholder = fmt.num(r.targets[k].close_pct, 0);
      });
    }
    renderResult(r);
  }

  function showError(error) {
    clear(
      resultBox,
      h("div", { class: "card-head" }, h("h2", {}, "Result")),
      issueList([error instanceof ApiError ? error.message : String(error)], { kind: "block", tag: "Error", iconName: "alert" }),
    );
  }

  function renderEmptyResult() {
    clear(
      resultBox,
      h(
        "div",
        { class: "empty" },
        icon("signal", "icon sig-empty-icon"),
        h("strong", {}, "Paste a signal to start"),
        `Symbol, direction, entry, stop and up to ${enums.max_targets} targets are extracted, sized with your ${settings.default_risk_pct}% risk rule and checked against every guardrail before you can execute.`,
        h("div", { style: { marginTop: "14px" } }, h("button", { type: "button", class: "btn", onclick: tryExample }, icon("play"), "Try an example")),
      ),
    );
  }

  function renderResult(r) {
    const d = memory.draft;
    const s = r.sizing;
    const t = r.ticket;
    const side = t?.direction || d?.direction;
    const stopPrice = t?.stop_loss ?? d?.stop_loss;
    const blocks = r.check ? r.check.violations.filter((v) => v.severity === "block") : [];

    const hero = s
      ? h(
          "div",
          { class: "result-hero" },
          h("div", { class: "label" }, "Position size", s.manual ? h("span", { class: "badge warn sig-badge" }, "manual") : null),
          h("div", { class: "value num" }, fmt.lots(s.lots), h("span", { class: "unit" }, "lots")),
          h(
            "div",
            { class: "muted small" },
            [
              side ? `${SIDE[side]} ${r.market?.symbol || d.symbol}` : r.market?.symbol,
              d?.entry ? `entry ${fmt.price(d.entry)}` : null,
              stopPrice ? `stop ${fmt.price(stopPrice)} (${fmt.num(s.stop_pips, 1)} pips)` : `stop ${fmt.num(s.stop_pips, 1)} pips`,
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
          metric("Risk at stop", fmt.signedMoney(-s.max_loss), "neg"),
          metric("Risk % of equity", fmt.pct(s.actual_risk_pct, 2)),
          metric("Allowed risk", `${fmt.money(s.risk_amount)} (${fmt.pct(s.risk_pct, 2)})`),
          metric("Pip value / lot", fmt.money(s.pip_value_per_lot)),
          metric("Blended R:R", fmt.rr(r.blended_rr)),
          metric("Potential profit", r.potential_profit === null ? "—" : fmt.signedMoney(r.potential_profit), r.potential_profit > 0 ? "pos" : ""),
        )
      : null;

    const table = r.targets.length
      ? h(
          "div",
          { class: "table-wrap" },
          h(
            "table",
            { class: "sig-table" },
            h("thead", {}, h("tr", {}, ["Target", "Price", "Close", "Lots", "R:R", "Profit"].map((c, i) => h("th", { class: i ? "num" : "" }, c)))),
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
              { class: "rr-bar", role: "img", "aria-label": `Risk ${fmt.money(s.max_loss)} versus blended reward ${fmt.money(r.potential_profit)}` },
              h("div", { class: "risk", style: { flex: String(1 / (1 + r.blended_rr)) } }),
              h("div", { class: "reward", style: { flex: String(r.blended_rr / (1 + r.blended_rr)) } }),
            ),
            h("div", { class: "row small muted" }, h("span", {}, `Risk ${fmt.signedMoney(-s.max_loss)}`), h("span", { class: "spacer" }), h("span", {}, `Reward ${fmt.signedMoney(r.potential_profit)}`)),
          )
        : null;

    const management = t
      ? h(
          "div",
          { class: "sig-summary" },
          h("span", { class: "chip" }, icon("shield"), t.be_enabled ? `Break-even at TP1${t.be_offset_pips ? ` +${fmt.num(t.be_offset_pips, 1)} pips` : ""}` : "No auto break-even"),
          h(
            "span",
            { class: "chip" },
            icon("trending"),
            t.trail_enabled ? `Trail ${fmt.num(t.trail_distance_pips, 1)} pips ${t.trail_trigger === "immediate" ? "from entry" : "after TP1"}` : "No trailing stop",
          ),
        )
      : null;

    const guardBox = h(
      "div",
      { class: "sig-section stack" },
      h("h3", {}, icon("shield"), "Guardrails"),
      r.check
        ? guardrailFindings(r.check.violations, t.strategy, "All guardrails pass - ready to execute.")
        : h("p", { class: "muted small" }, "Checked as soon as the ticket is complete."),
    );

    let reason = null;
    if (!t) reason = r.errors[0] || "Complete the ticket to execute.";
    else if (r.check.blocked) reason = `Blocked by ${plural(blocks.length, "guardrail")} - see above.`;
    const button = h("button", { type: "button", class: "btn primary sig-execute", disabled: Boolean(reason), onclick: () => execute(button, guardBox) }, icon("zap"), "Execute & log trade");

    clear(
      resultBox,
      h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Result"), h("p", {}, `${settings.currency} account · min R:R 1:${settings.min_rr} · max ${settings.max_risk_pct}% per trade`))),
      issueList(r.errors, { kind: "block", tag: "Fix first", iconName: "alert" }),
      hero,
      metrics,
      table,
      rrBar,
      issueList(r.warnings, { kind: "warn", tag: "Note", iconName: "info" }),
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
          reason || `Logs a live ${side} of ${fmt.lots(t.position_size)} lots ${t.symbol} at ${fmt.price(t.entry_price)}, timed now.`,
        ),
      ),
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
        toast("The ticket can't be executed as it stands - review the result panel.", "warn");
        return;
      }
      const warnings = fresh.check.violations.filter((v) => v.severity === "warn");
      if (warnings.length) {
        const ok = await confirmDialog(`This trade breaks ${plural(warnings.length, "rule")} (warnings, not blocks). Log it anyway?`, {
          title: "Execute with warnings",
          confirmText: "Execute anyway",
        });
        if (!ok) return;
      }
      const result = await api.post("/api/trades", fresh.ticket);
      const trade = result.trade;
      toast(`${SIDE[trade.direction]} ${fmt.lots(trade.position_size)} lots ${trade.symbol} logged`, result.warnings.length ? "warn" : "success");
      resetMemory();
      loadStatus().catch(() => {});
      location.hash = "#/positions";
    } catch (error) {
      if (error instanceof ApiError && error.status === 423) {
        clear(
          guardBox,
          h("h3", {}, icon("shield"), "Guardrails"),
          h("p", { class: "small neg" }, "Entry refused - fix these first:"),
          guardrailFindings(error.payload.violations || [], memory.draft?.strategy || ""),
        );
        guardBox.scrollIntoView({ behavior: "smooth", block: "center" });
        toast("Trade blocked by your risk rules", "error");
        loadStatus().catch(() => {});
      } else {
        toast(error.message, "error");
      }
    } finally {
      button.disabled = false;
    }
  }

  // ----------------------------------------------------------- layout
  clear(
    root,
    h(
      "div",
      { class: "page-head" },
      h("div", {}, h("h1", {}, "Signal parser"), h("p", {}, "Paste a Telegram / WhatsApp signal - it is parsed, sized with your risk rules and checked against every guardrail before you can execute it.")),
      h("a", { class: "btn ghost", href: "#/calculator" }, icon("calculator"), "Risk calculator"),
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
            h("div", {}, h("h2", {}, "Paste a signal"), h("p", {}, "Parsed automatically as you paste or type.")),
            h("div", { class: "row" }, h("button", { type: "button", class: "btn sm", onclick: tryExample }, icon("play"), "Try an example"), h("button", { type: "button", class: "btn ghost sm", onclick: clearAll }, icon("x"), "Clear")),
          ),
          textarea,
          parseBox,
        ),
        h("div", { class: "card" }, h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Ticket"), h("p", {}, "Review and correct anything the parser got wrong."))), ticketBox),
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
