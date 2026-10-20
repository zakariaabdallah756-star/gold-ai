// Position sizing calculator with live guardrail feedback.

import { api, ApiError } from "../api.js";
import { clear, debounce, field, h, icon, numberOrNull, segmented, select, toast } from "../dom.js";
import { fmt } from "../format.js";
import { state } from "../state.js";

// Remembered between visits.
const inputs = { market_id: null, equity: "", risk_pct: "", mode: "prices", entry: "", stop: "", tp: "", stop_pips: "", target_pips: "", fx_rate: "" };

export function violationList(violations, { okText } = {}) {
  if (!violations.length) {
    return okText ? h("div", { class: "violations" }, h("div", { class: "violation ok" }, icon("checkCircle"), h("div", {}, okText))) : null;
  }
  return h(
    "div",
    { class: "violations" },
    violations.map((v) =>
      h(
        "div",
        { class: `violation ${v.severity}` },
        icon(v.severity === "block" ? "lock" : "alert"),
        h("div", {}, h("div", { class: "tag" }, v.severity === "block" ? "Blocked" : "Warning"), v.message),
      ),
    ),
  );
}

function needsFx(market, currency) {
  return market && market.quote_currency !== currency && market.base_currency !== currency;
}

function metric(label, value, cls = "") {
  return h("div", {}, h("div", { class: "label" }, label), h("div", { class: `value ${cls}` }, value));
}

export async function render(root) {
  const { settings } = state.plan;
  const markets = state.plan.markets.filter((m) => m.active);
  if (!markets.length) {
    clear(root, h("div", { class: "card empty" }, h("strong", {}, "No active markets"), "Add an instrument in ", h("a", { href: "#/plan" }, "Trading Plan → Markets"), "."));
    return;
  }
  if (!markets.some((m) => m.id === inputs.market_id)) {
    // Default to the instrument of the most recent trade.
    const latest = await api.get("/api/trades?limit=1").catch(() => ({ items: [] }));
    const recent = markets.find((m) => m.symbol === latest.items[0]?.symbol);
    inputs.market_id = (recent || markets[0]).id;
  }
  const market = () => markets.find((m) => m.id === Number(inputs.market_id));

  const result = h("div", { class: "stack" });
  const spec = h("div", { class: "spec" });
  const fxField = h("div");

  const bindInput = (key, attrs = {}) =>
    h("input", {
      type: "number",
      step: "any",
      inputmode: "decimal",
      value: inputs[key],
      dataset: { key },
      oninput: (e) => {
        inputs[key] = e.target.value;
        recompute();
      },
      ...attrs,
    });

  const marketSelect = select(
    markets.map((m) => [m.id, `${m.symbol} - ${m.name}`]),
    inputs.market_id,
    {
      onchange: (e) => {
        inputs.market_id = Number(e.target.value);
        describeMarket();
        recompute();
      },
    },
  );
  const equityInput = bindInput("equity", { placeholder: fmt.num(state.status.current_equity), min: "0" });
  const riskInput = bindInput("risk_pct", { placeholder: String(settings.default_risk_pct), min: "0", max: "100" });
  const riskButtons = h(
    "div",
    { class: "row", style: { gap: "6px" } },
    [0.25, 0.5, 1, 1.5, 2].map((pct) =>
      h("button", { type: "button", class: "btn sm", onclick: () => ((riskInput.value = inputs.risk_pct = String(pct)), recompute()) }, `${pct}%`),
    ),
  );

  const priceFields = h(
    "div",
    { class: "form-grid" },
    field("Entry price", bindInput("entry")),
    field("Stop loss", bindInput("stop")),
    field("Take profit", bindInput("tp", { placeholder: "optional" })),
  );
  const pipFields = h(
    "div",
    { class: "form-grid" },
    field("Stop size (pips / points)", bindInput("stop_pips")),
    field("Target (pips / points)", bindInput("target_pips", { placeholder: "optional" })),
    field("Entry price", bindInput("entry", { placeholder: "optional - for notional" })),
  );
  const mode = segmented(
    [
      ["prices", "Entry & stop prices"],
      ["pips", "Stop in pips / points"],
    ],
    inputs.mode,
    (value) => {
      inputs.mode = value;
      showMode();
      recompute();
    },
  );

  function showMode() {
    priceFields.style.display = inputs.mode === "prices" ? "" : "none";
    pipFields.style.display = inputs.mode === "pips" ? "" : "none";
    // The entry input exists in both groups; keep them in sync.
    for (const input of root.querySelectorAll('input[data-key="entry"]')) input.value = inputs.entry;
  }

  function describeMarket() {
    const m = market();
    const pipValue = m.quote_currency === settings.currency ? m.contract_size * m.pip_size : null;
    clear(
      spec,
      h("span", {}, "Contract ", h("b", {}, m.contract_size.toLocaleString())),
      h("span", {}, "Pip/point ", h("b", {}, String(m.pip_size))),
      h("span", {}, "Quote ", h("b", {}, m.quote_currency)),
      h("span", {}, "Lots ", h("b", {}, `${m.min_lot}–${m.max_lot} step ${m.lot_step}`)),
      pipValue !== null ? h("span", {}, "Pip value ", h("b", {}, `${fmt.money(pipValue)} / lot`)) : null,
    );
    clear(
      fxField,
      needsFx(m, settings.currency)
        ? field(`Conversion rate: 1 ${m.quote_currency} = ? ${settings.currency}`, bindInput("fx_rate", { placeholder: "e.g. 1.08" }), {
            help: `${m.symbol} is quoted in ${m.quote_currency}; needed to convert risk into ${settings.currency}.`,
          })
        : null,
    );
  }

  function body() {
    const n = (v) => numberOrNull({ value: v });
    const data = { market_id: Number(inputs.market_id), account_equity: n(inputs.equity), risk_pct: n(inputs.risk_pct), fx_rate: needsFx(market(), settings.currency) ? n(inputs.fx_rate) : null };
    if (inputs.mode === "prices") Object.assign(data, { entry_price: n(inputs.entry), stop_price: n(inputs.stop), take_profit: n(inputs.tp) });
    else Object.assign(data, { stop_pips: n(inputs.stop_pips), target_pips: n(inputs.target_pips), entry_price: n(inputs.entry) });
    return data;
  }

  function ready(data) {
    return inputs.mode === "prices" ? data.entry_price && data.stop_price : data.stop_pips;
  }

  let requestId = 0;
  const recompute = debounce(async () => {
    const data = body();
    if (!ready(data)) {
      clear(result, h("div", { class: "empty" }, h("strong", {}, "Enter your stop"), inputs.mode === "prices" ? "Entry and stop prices size the position automatically." : "Enter the stop distance in pips or points."));
      return;
    }
    const id = ++requestId;
    try {
      const r = await api.post("/api/risk/position-size", data);
      if (id === requestId) showResult(r, data);
    } catch (error) {
      if (id !== requestId) return;
      clear(result, h("div", { class: "violation block" }, icon("alert"), h("div", {}, error instanceof ApiError ? error.message : String(error))));
    }
  }, 200);

  function showResult(r, data) {
    const blocked = r.violations.some((v) => v.severity === "block");
    const params = new URLSearchParams({
      new: "1",
      symbol: r.symbol,
      size: String(r.lots),
      ...(r.direction ? { direction: r.direction } : {}),
      ...(data.entry_price ? { entry: String(data.entry_price) } : {}),
      ...(data.stop_price ? { sl: String(data.stop_price) } : {}),
      ...(data.take_profit ? { tp: String(data.take_profit) } : {}),
    });
    const riskShare = r.rr ? 1 / (1 + r.rr) : null;

    clear(
      result,
      h(
        "div",
        { class: "result-hero" },
        h("div", { class: "label" }, "Position size"),
        h("div", { class: "value num" }, fmt.lots(r.lots), h("span", { class: "unit" }, "lots")),
        h("div", { class: "muted small" }, `${r.units.toLocaleString(undefined, { maximumFractionDigits: 2 })} units · ${r.direction ? r.direction.toUpperCase() : "direction from prices"}`),
      ),
      h(
        "div",
        { class: "live-metrics", style: { marginTop: 0 } },
        metric("Max loss", fmt.money(r.max_loss), "neg"),
        metric("Actual risk", fmt.pct(r.actual_risk_pct, 2)),
        metric("Target risk", `${fmt.money(r.risk_amount)} (${fmt.pct(r.risk_pct, 2)})`),
        metric("Stop", `${fmt.num(r.stop_pips, 1)} pips`),
        metric("Pip value / lot", fmt.money(r.pip_value_per_lot)),
        metric("Reward : risk", fmt.rr(r.rr)),
        metric("Potential profit", r.potential_profit === null ? "—" : fmt.money(r.potential_profit), r.potential_profit ? "pos" : ""),
        metric("Notional", r.notional === null ? "—" : fmt.money(r.notional)),
        metric("Leverage", r.leverage === null ? "—" : `${fmt.num(r.leverage, 1)}×`),
      ),
      riskShare
        ? h(
            "div",
            {},
            h("div", { class: "rr-bar", role: "img", "aria-label": `Risk ${fmt.money(r.max_loss)} versus reward ${fmt.money(r.potential_profit)}` }, h("div", { class: "risk", style: { flex: String(riskShare) } }), h("div", { class: "reward", style: { flex: String(1 - riskShare) } })),
            h("div", { class: "row small muted" }, h("span", {}, `Risk ${fmt.money(r.max_loss)}`), h("span", { class: "spacer" }), h("span", {}, `Reward ${fmt.money(r.potential_profit)}`)),
          )
        : null,
      violationList(r.violations, { okText: "All risk rules pass for this position." }),
      r.notes.length ? h("div", { class: "muted small" }, r.notes.map((note) => h("p", {}, note))) : null,
      h(
        "div",
        { class: "row" },
        h(
          "button",
          {
            class: "btn",
            type: "button",
            onclick: async () => {
              try {
                await navigator.clipboard.writeText(String(r.lots));
                toast(`Copied ${r.lots} lots`, "success");
              } catch {
                toast("Clipboard not available", "warn");
              }
            },
          },
          icon("copy"),
          "Copy lots",
        ),
        h("span", { class: "spacer" }),
        h("a", { class: `btn ${blocked ? "" : "primary"}`, href: `#/journal?${params}` }, "Log this trade", icon("arrowRight")),
      ),
    );
  }

  const status = state.status;
  const budget = h(
    "div",
    { class: "card" },
    h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Today's risk budget"), h("p", {}, `${settings.max_daily_loss_pct}% max daily loss · ${settings.max_risk_pct}% max per trade · min R:R 1:${settings.min_rr}`))),
    h(
      "dl",
      { class: "stat-list", style: { gridTemplateColumns: "1fr" } },
      [
        ["Daily loss limit", fmt.money(status.daily_loss_limit)],
        ["Realised today", h("span", { class: status.realized_pnl_today < 0 ? "neg" : status.realized_pnl_today > 0 ? "pos" : "" }, fmt.signedMoney(status.realized_pnl_today))],
        ["Open risk", fmt.money(status.open_risk)],
        ["Remaining loss budget", fmt.money(Math.max(0, status.remaining_loss_budget - status.open_risk))],
      ].map(([label, value]) => h("div", {}, h("dt", {}, label), h("dd", {}, value))),
    ),
  );

  clear(
    root,
    h(
      "div",
      { class: "page-head" },
      h("div", {}, h("h1", {}, "Position size calculator"), h("p", {}, `Size every trade from your stop - never from a feeling. Account currency: ${settings.currency}`)),
      h("a", { class: "btn ghost", href: "#/signals" }, icon("signal"), "Paste a signal"),
    ),
    h(
      "div",
      { class: "grid split" },
      h(
        "div",
        { class: "stack" },
        h(
          "div",
          { class: "card stack", style: { gap: "14px" } },
          field("Instrument", marketSelect),
          spec,
          h(
            "div",
            { class: "form-grid" },
            field(`Account equity (${settings.currency})`, equityInput, { help: "Blank = current equity" }),
            field("Risk per trade (%)", riskInput, { help: `Blank = default ${settings.default_risk_pct}%` }),
          ),
          riskButtons,
          h("hr", { class: "divider", style: { margin: "4px 0" } }),
          mode.el,
          priceFields,
          pipFields,
          fxField,
        ),
        budget,
      ),
      h("div", { class: "card" }, result),
    ),
  );
  describeMarket();
  showMode();
  recompute();
}
