// Strategy incubator: experimental strategies, backtest trades, metrics and backtest-vs-live comparison.

import { api } from "../api.js";
import { chartsAvailable, distributionChart, equityChart } from "../charts.js";
import { clear, confirmDialog, field, h, icon, modal, numberOrNull, segmented, toast } from "../dom.js";
import { fmt, pnlClass, toInputDateTime } from "../format.js";
import { N_, t } from "../i18n.js";
import { accountNow, loadPlan, state } from "../state.js";

// Shown with t(); "incubator" is the stored status of a strategy that is still being tested.
const STATUS_LABEL = { incubator: N_("In test"), live: N_("Live"), retired: N_("Retired") };
const STATUS_BADGE = { incubator: "warn", live: "good", retired: "" };
const VERDICT = {
  in_line: { cls: "ok", icon: "checkCircle", badge: "good", label: N_("Live in line") },
  above: { cls: "ok", icon: "trending", badge: "good", label: N_("Live above backtest") },
  below: { cls: "block", icon: "alert", badge: "bad", label: N_("Live below backtest") },
  insufficient: { cls: "warn", icon: "info", badge: "", label: N_("Not enough data to compare") },
};
const CSV_EXAMPLE = "date,symbol,direction,entry,stop,tp,exit,notes\n2026-09-01 09:30,XAUUSD,buy,2650,2645,2665,2665,London open retest\n2026-09-02 14:45,XAUUSD,sell,2672,2677,2657,2677,\n";

let selectedId = null;
const VISIBLE_ROWS = 25;

const pf = (m) => (m.profit_factor_infinite ? "∞" : m.profit_factor === null ? "—" : fmt.num(m.profit_factor));

function kpi(label, value, sub, cls = "") {
  return h("div", { class: "kpi" }, h("div", { class: "label" }, label), h("div", { class: `value ${cls}` }, value), sub ? h("div", { class: "sub" }, sub) : null);
}

function statusBadge(status) {
  return h("span", { class: `badge ${STATUS_BADGE[status] || ""}` }, STATUS_LABEL[status] ? t(STATUS_LABEL[status]) : status);
}

/** A comparison value in its unit (money, R, percent or number); infinite profit factors show as ∞. */
function compareValue(row, side) {
  if (row.infinite?.[side]) return "∞";
  const x = row[side];
  if (x === null || x === undefined) return "—";
  const size = row.key.startsWith("max_drawdown"); // a drawdown is a size, never a gain
  if (row.unit === "money") return size ? fmt.money(x) : fmt.signedMoney(x);
  if (row.unit === "r") return size ? `${fmt.num(x)}R` : fmt.r(x);
  if (row.unit === "pct") return fmt.pct(x);
  if (row.key === "trades") return String(x);
  return fmt.num(x);
}

function compareDelta(row) {
  const d = row.delta;
  if (d === null || d === undefined) return "—";
  if (row.unit === "money") return fmt.signedMoney(d);
  if (row.unit === "r") return fmt.r(d);
  const sign = d > 0 ? "+" : d < 0 ? "−" : "";
  const size = fmt.num(Math.abs(d), row.key === "trades" ? 0 : 2);
  return row.unit === "pct" ? t("{value} pp", { value: `${sign}${size}` }) : `${sign}${size}`;
}

function gapPanel(gap, report) {
  if (!gap) return null;
  const parts = [
    h("div", { class: "kpi" }, h("div", { class: "label" }, t("Expectancy gap")), h("div", { class: `value ${pnlClass(gap.gap_r)}` }, fmt.r(gap.gap_r)), h("div", { class: "sub" }, t("live {live} vs backtest {backtest} per trade", { live: fmt.r(gap.live_r), backtest: fmt.r(gap.backtest_r) }))),
    gap.gap_money_same_basis !== null
      ? h("div", { class: "kpi" }, h("div", { class: "label" }, t("Gap at backtest risk")), h("div", { class: `value ${pnlClass(gap.gap_money_same_basis)}` }, fmt.signedMoney(gap.gap_money_same_basis)), h("div", { class: "sub" }, t("per trade at {risk} per 1R", { risk: fmt.money(report.risk_per_trade) })))
      : null,
    h(
      "div",
      { class: "kpi" },
      h("div", { class: "label" }, t("Edge kept live")),
      h("div", { class: "value" }, gap.edge_retained_pct === null ? "—" : fmt.pct(gap.edge_retained_pct, 0)),
      h("div", { class: "sub" }, gap.edge_retained_pct === null ? t("needs a positive backtest expectancy") : t("of the backtest expectancy")),
    ),
    h("div", { class: "kpi" }, h("div", { class: "label" }, t("Live, actual")), h("div", { class: `value ${pnlClass(gap.live_money_actual)}` }, fmt.signedMoney(gap.live_money_actual)), h("div", { class: "sub" }, t("per trade with your real size and fees"))),
  ];
  return h("div", { class: "kpis bt-gap" }, parts);
}

function tradeForm(trade, onSubmit) {
  const t = trade || {};
  const num = (value, attrs = {}) => h("input", { type: "number", step: "any", inputmode: "decimal", value: value ?? "", ...attrs });
  const time = h("input", { type: "datetime-local", value: t.trade_time ? t.trade_time.slice(0, 16) : toInputDateTime(accountNow()) });
  const symbol = h("input", { value: t.symbol || "", list: "bt-symbols", style: { textTransform: "uppercase" }, maxlength: "20" });
  const direction = segmented([["long", "Long", "long"], ["short", "Short", "short"]], t.direction || "long");
  const entry = num(t.entry_price);
  const stop = num(t.stop_loss);
  const target = num(t.take_profit, { placeholder: "optional" });
  const exit = num(t.exit_price);
  const r = num(t.r_multiple, { placeholder: "if no prices" });
  const pnl = num(t.pnl, { placeholder: "optional" });
  const session = h("input", { value: t.session || "", maxlength: "40", list: "bt-sessions" });
  const timeframe = h("input", { value: t.timeframe || "", maxlength: "10" });
  const notes = h("textarea", { rows: 2, value: t.notes || "" });

  const body = h(
    "div",
    {},
    h("datalist", { id: "bt-symbols" }, state.plan.markets.map((m) => h("option", { value: m.symbol }))),
    h("datalist", { id: "bt-sessions" }, state.plan.sessions.map((s) => h("option", { value: s.name }))),
    h("div", { class: "row", style: { marginBottom: "12px" } }, direction.el),
    h(
      "div",
      { class: "form-grid" },
      field("Date & time", time),
      field("Symbol", symbol),
      field("Entry", entry),
      field("Stop loss", stop),
      field("Take profit", target),
      field("Exit", exit),
      field("Result (R)", r, { help: "Computed from prices when entry, stop and exit are set" }),
      field(`Money result (${state.plan.settings.currency})`, pnl, { help: "Blank = R × risk per 1R" }),
      field("Session", session),
      field("Timeframe", timeframe),
      field("Notes", notes, { full: true }),
    ),
  );
  const save = h(
    "button",
    {
      class: "btn primary",
      type: "button",
      onclick: async () => {
        save.disabled = true;
        try {
          await onSubmit({
            trade_time: time.value,
            symbol: symbol.value.trim().toUpperCase(),
            direction: direction.value,
            entry_price: numberOrNull(entry),
            stop_loss: numberOrNull(stop),
            take_profit: numberOrNull(target),
            exit_price: numberOrNull(exit),
            r_multiple: numberOrNull(r),
            pnl: numberOrNull(pnl),
            session: session.value.trim(),
            timeframe: timeframe.value.trim(),
            notes: notes.value,
          });
          ref.close();
        } catch (error) {
          toast(error.message, "error");
        } finally {
          save.disabled = false;
        }
      },
    },
    "Save",
  );
  const ref = modal({ title: trade ? "Edit backtest trade" : "Add backtest trade", body, wide: true, footer: [h("button", { class: "btn ghost", type: "button", onclick: () => ref.close() }, "Cancel"), save] });
}

function importDialog(strategy, onDone) {
  const text = h("textarea", { rows: 10, placeholder: CSV_EXAMPLE, style: { fontFamily: "var(--mono)", fontSize: "12px" } });
  const result = h("div", { class: "bt-import-result" });
  const run = async (dryRun) => {
    if (!text.value.trim()) {
      toast("Paste CSV rows first", "warn");
      return;
    }
    try {
      const r = await api.post(`/api/backtest/strategies/${strategy.id}/import`, { csv: text.value, dry_run: dryRun });
      clear(
        result,
        h("p", { class: "small" }, dryRun ? `${r.valid} valid row${r.valid === 1 ? "" : "s"} ready to import.` : `Imported ${r.imported} trade${r.imported === 1 ? "" : "s"}.`),
        r.errors.length
          ? h("div", { class: "violation warn" }, icon("alert"), h("div", {}, h("div", { class: "tag" }, `${r.errors.length} row${r.errors.length === 1 ? "" : "s"} skipped`), h("ul", {}, r.errors.slice(0, 12).map((e) => h("li", {}, `Line ${e.line}: ${e.message}`)))))
          : null,
        dryRun && r.preview.length
          ? h(
              "div",
              { class: "table-wrap", style: { maxHeight: "220px", overflowY: "auto", marginTop: "8px" } },
              h(
                "table",
                {},
                h("thead", {}, h("tr", {}, ["Date", "Symbol", "Side", "R", "Money"].map((c, i) => h("th", { class: i > 2 ? "num" : "" }, c)))),
                h("tbody", {}, r.preview.map((p) => h("tr", {}, h("td", {}, fmt.datetime(p.trade_time)), h("td", {}, p.symbol || "—"), h("td", {}, p.direction), h("td", { class: `num ${pnlClass(p.r_multiple)}` }, fmt.r(p.r_multiple)), h("td", { class: "num" }, p.pnl === null ? "—" : fmt.signedMoney(p.pnl))))),
              ),
            )
          : null,
      );
      if (!dryRun) {
        ref.close();
        onDone();
        toast(`Imported ${r.imported} backtest trades`, "success");
      }
    } catch (error) {
      clear(result, h("div", { class: "violation block" }, icon("alert"), h("div", {}, error.message)));
    }
  };
  const ref = modal({
    title: `Batch import · ${strategy.name}`,
    wide: true,
    body: h(
      "div",
      { class: "stack", style: { gap: "10px" } },
      h("p", { class: "muted small" }, "Paste CSV with a header row (comma, semicolon or tab separated). Required: date and direction (buy/sell/long/short), plus entry, stop and exit prices or an R column. Optional: symbol, tp, pnl, session, timeframe, notes. Dates: YYYY-MM-DD HH:MM or DD/MM/YYYY."),
      text,
      result,
    ),
    footer: [h("button", { class: "btn ghost", type: "button", onclick: () => ref.close() }, "Cancel"), h("button", { class: "btn", type: "button", onclick: () => run(true) }, "Preview"), h("button", { class: "btn primary", type: "button", onclick: () => run(false) }, icon("upload"), "Import")],
  });
}

function newStrategyDialog(onCreated) {
  const name = h("input", { maxlength: "60", placeholder: "e.g. London opening range breakout" });
  const description = h("textarea", { rows: 2 });
  const rules = h("textarea", { rows: 4, placeholder: "Entry trigger, stop placement, targets, filters…" });
  const save = h(
    "button",
    {
      class: "btn primary",
      type: "button",
      onclick: async () => {
        try {
          const created = await api.post("/api/backtest/strategies", { name: name.value, description: description.value, rules: rules.value });
          await loadPlan();
          ref.close();
          onCreated(created.id);
          toast(`${created.name} added to the incubator`, "success");
        } catch (error) {
          toast(error.message, "error");
        }
      },
    },
    "Create",
  );
  const ref = modal({
    title: "New test strategy",
    body: h("div", { class: "stack", style: { gap: "12px" } }, h("p", { class: "muted small" }, "Incubator strategies are for testing. The Co-Pilot warns if you trade one live before promoting it."), field("Name", name), field("Description", description), field("Rules", rules)),
    footer: [h("button", { class: "btn ghost", type: "button", onclick: () => ref.close() }, "Cancel"), save],
  });
}

function comparisonCard(report) {
  const { comparison } = report;
  const v = VERDICT[comparison.verdict.status] || VERDICT.insufficient;
  const names = report.live_names?.length ? report.live_names : [report.strategy.name];
  return h(
    "div",
    { class: "card" },
    h(
      "div",
      { class: "card-head" },
      h(
        "div",
        {},
        h("h2", {}, "Backtest vs live"),
        h("p", {}, t("Live = closed journal trades logged with the strategy {names}", { names: names.join(", ") })),
      ),
    ),
    gapPanel(comparison.gap, report),
    h(
      "div",
      { class: "table-wrap" },
      h(
        "table",
        {},
        h("thead", {}, h("tr", {}, ["Metric", "Backtest", "Live", "Δ live − backtest"].map((c, i) => h("th", { class: i ? "num" : "" }, c)))),
        h(
          "tbody",
          {},
          comparison.rows.map((row) =>
            h("tr", {}, h("td", {}, row.label), h("td", { class: "num" }, compareValue(row, "backtest")), h("td", { class: "num" }, compareValue(row, "live")), h("td", { class: "num muted" }, compareDelta(row))),
          ),
        ),
      ),
    ),
    h("div", { class: `violation ${v.cls}`, style: { marginTop: "12px" } }, icon(v.icon), h("div", {}, comparison.verdict.message, comparison.verdict.notes.map((n) => h("div", { class: "muted small" }, n)))),
  );
}

function chartCard(title, subtitle, build) {
  const body = h("div", { class: "chart-box" });
  const card = h("div", { class: "card" }, h("div", { class: "card-head" }, h("div", {}, h("h2", {}, title), h("p", {}, subtitle))), body);
  let chart = null;
  if (chartsAvailable()) {
    const canvas = h("canvas", { role: "img", "aria-label": title });
    body.append(canvas);
    chart = build(canvas);
  } else {
    body.append(h("div", { class: "empty" }, "Charts unavailable"));
  }
  return { card, destroy: () => chart?.destroy() };
}

export async function render(root, { params }) {
  if (params.get("id")) selectedId = Number(params.get("id"));
  const list = h("div", { class: "stack bt-list" });
  const detail = h("div", { class: "stack" });
  let charts = [];
  const destroyCharts = () => charts.splice(0).forEach((c) => c.destroy());

  async function loadList() {
    const strategies = await api.get("/api/backtest/strategies");
    if (!strategies.some((s) => s.id === selectedId)) selectedId = (strategies.find((s) => s.status === "incubator") || strategies[0])?.id ?? null;
    const groups = ["incubator", "live", "retired"].map((status) => [status, strategies.filter((s) => s.status === status)]);
    clear(
      list,
      groups.map(([status, items]) =>
        items.length
          ? h(
              "div",
              {},
              h("div", { class: "section-title" }, `${t(STATUS_LABEL[status])} (${items.length})`),
              items.map((s) =>
                h(
                  "button",
                  { type: "button", class: `bt-strategy${s.id === selectedId ? " selected" : ""}`, onclick: () => ((selectedId = s.id), loadList(), loadDetail()) },
                  h("div", { class: "row", style: { justifyContent: "space-between", flexWrap: "nowrap" } }, h("strong", {}, s.name), statusBadge(s.status)),
                  h("div", { class: "bt-strategy-stats" }, h("span", {}, `${s.trades} backtest${s.trades === 1 ? "" : "s"}`), h("span", {}, `WR ${fmt.pct(s.win_rate, 0)}`), h("span", {}, `PF ${pf(s)}`), h("span", { class: pnlClass(s.expectancy_r) }, `${fmt.r(s.expectancy_r)}/trade`)),
                  s.live_trades && s.trades
                    ? h(
                        "div",
                        { class: "bt-strategy-stats" },
                        h("span", { class: `badge ${(VERDICT[s.verdict] || VERDICT.insufficient).badge}` }, t((VERDICT[s.verdict] || VERDICT.insufficient).label)),
                        h("span", { class: pnlClass(s.live_expectancy_r) }, t("live {r}/trade ({n})", { r: fmt.r(s.live_expectancy_r), n: s.live_trades })),
                      )
                    : null,
                ),
              ),
            )
          : null,
      ),
      strategies.length ? null : h("div", { class: "empty" }, "No strategies yet."),
    );
    return strategies;
  }

  async function loadDetail() {
    destroyCharts();
    if (selectedId === null) {
      clear(detail, h("div", { class: "card empty" }, h("strong", {}, "Create a test strategy"), "Log historical trades to measure its edge before risking real money."));
      return;
    }
    detail.classList.add("refreshing");
    const [report, trades] = await Promise.all([api.get(`/api/backtest/strategies/${selectedId}/report`), api.get(`/api/backtest/strategies/${selectedId}/trades`)]);
    detail.classList.remove("refreshing");
    const m = report.metrics;
    const strategy = report.strategy;
    const reload = async () => {
      await loadList();
      await loadDetail();
    };
    const setStatus = async (status) => {
      await api.put(`/api/backtest/strategies/${strategy.id}/status`, { status });
      await loadPlan();
      toast(t("{name} is now: {status}", { name: strategy.name, status: t(STATUS_LABEL[status]) }), "success");
      reload();
    };
    const riskInput = h("input", { type: "number", step: "any", min: "0", value: report.risk_per_trade, style: { width: "110px" } });
    riskInput.addEventListener("change", async () => {
      await api.put(`/api/backtest/strategies/${strategy.id}/settings`, { backtest_risk_per_trade: numberOrNull(riskInput) });
      reload();
    });

    const actions = h(
      "div",
      { class: "row" },
      strategy.status !== "live" ? h("button", { class: "btn primary sm", type: "button", onclick: () => setStatus("live") }, icon("trending"), "Promote to live") : null,
      strategy.status !== "incubator" ? h("button", { class: "btn sm", type: "button", onclick: () => setStatus("incubator") }, "Back to incubator") : null,
      strategy.status !== "retired" ? h("button", { class: "btn ghost sm", type: "button", onclick: () => setStatus("retired") }, "Retire") : null,
      h("a", { class: "btn sm", href: `/api/backtest/strategies/${strategy.id}/export.csv`, download: "" }, icon("download"), "CSV"),
      h("a", { class: "btn sm", href: `/api/reports/backtest/${strategy.id}.pdf`, download: "" }, icon("download"), "PDF report"),
    );

    const equity = chartCard("Hypothetical equity", `${fmt.money(report.risk_per_trade)} per 1R from ${fmt.money(m.start_equity)}`, (canvas) => equityChart(canvas, report.equity_curve));
    const dist = chartCard("R-multiple distribution", `${m.wins} wins · ${m.losses} losses · ${m.breakeven} breakeven`, (canvas) => distributionChart(canvas, report.distribution));
    charts = m.trades ? [equity, dist] : [];

    const tradeRows = [...trades].reverse().map((t) =>
      h(
        "tr",
        {},
        h("td", {}, fmt.datetime(t.trade_time)),
        h("td", {}, t.symbol || "—"),
        h("td", {}, h("span", { class: `badge ${t.direction}` }, t.direction === "long" ? "Long" : "Short")),
        h("td", { class: "num" }, fmt.price(t.entry_price)),
        h("td", { class: "num" }, fmt.price(t.stop_loss)),
        h("td", { class: "num" }, fmt.price(t.exit_price)),
        h("td", { class: "num" }, fmt.rr(t.planned_rr)),
        h("td", { class: `num ${pnlClass(t.r_multiple)}` }, fmt.r(t.r_multiple)),
        h("td", { class: "bt-notes", title: t.notes }, t.notes || ""),
        h(
          "td",
          { class: "num" },
          h("button", { class: "btn ghost sm icon-only", type: "button", "aria-label": "Edit", onclick: () => tradeForm(t, async (data) => (await api.put(`/api/backtest/trades/${t.id}`, data), reload())) }, icon("edit")),
          h(
            "button",
            {
              class: "btn ghost sm icon-only",
              type: "button",
              "aria-label": "Delete",
              onclick: async () => {
                if (await confirmDialog("Delete this backtest trade?", { title: "Delete trade", confirmText: "Delete", danger: true })) {
                  await api.del(`/api/backtest/trades/${t.id}`);
                  reload();
                }
              },
            },
            icon("trash"),
          ),
        ),
      ),
    );

    // Newest first; long backtests start collapsed to the latest rows.
    const tbody = h("tbody", {}, tradeRows.slice(0, VISIBLE_ROWS));
    const showAll =
      tradeRows.length > VISIBLE_ROWS
        ? h("button", { class: "btn ghost sm bt-show-all", type: "button", onclick: () => (tbody.append(...tradeRows.slice(VISIBLE_ROWS)), showAll.remove()) }, `Show all ${tradeRows.length} trades`)
        : null;

    clear(
      detail,
      h(
        "div",
        { class: "card" },
        h("div", { class: "card-head", style: { flexWrap: "wrap" } }, h("div", {}, h("div", { class: "row" }, h("h2", {}, strategy.name), statusBadge(strategy.status)), h("p", {}, "Backtest results never affect the live journal, equity or daily limits.")), actions),
        h("div", { class: "row small muted" }, h("label", { class: "row", style: { gap: "8px" } }, `Risk per 1R (${state.plan.settings.currency})`, riskInput), h("span", {}, "Money figures = R × this amount unless a money result was entered.")),
      ),
      h(
        "div",
        { class: "kpis" },
        kpi("Trades", m.trades, `${m.wins}W · ${m.losses}L · ${m.breakeven}BE`),
        kpi("Win rate", fmt.pct(m.win_rate)),
        kpi("Profit factor", pf(m)),
        kpi("Expectancy", fmt.signedMoney(m.expectancy), `${fmt.r(m.expectancy_r)} per trade`, pnlClass(m.expectancy)),
        kpi("Avg win : loss", m.avg_rr === null ? "—" : `${fmt.num(m.avg_rr)} : 1`, `Planned R:R ${fmt.rr(m.avg_planned_rr)}`),
        kpi("Max drawdown", `${fmt.num(m.max_drawdown_r)}R`, `${fmt.money(m.max_drawdown)} · ${fmt.pct(m.max_drawdown_pct)}`),
      ),
      m.trades ? h("div", { class: "grid cols-2" }, equity.card, dist.card) : null,
      comparisonCard(report),
      h(
        "div",
        { class: "card" },
        h(
          "div",
          { class: "card-head" },
          h("div", {}, h("h2", {}, "Backtest trades"), h("p", {}, `${trades.length} recorded`)),
          h(
            "div",
            { class: "row" },
            h("button", { class: "btn sm", type: "button", onclick: () => importDialog(strategy, reload) }, icon("upload"), "Batch import"),
            h("button", { class: "btn primary sm", type: "button", onclick: () => tradeForm(null, async (data) => (await api.post(`/api/backtest/strategies/${strategy.id}/trades`, data), reload())) }, icon("plus"), "Add trade"),
          ),
        ),
        trades.length
          ? h(
              "div",
              { class: "table-wrap" },
              h(
                "table",
                {},
                h("thead", {}, h("tr", {}, ["Date", "Symbol", "Side", "Entry", "Stop", "Exit", "Plan R:R", "Result", "Notes", ""].map((c, i) => h("th", { class: i >= 3 && i <= 7 ? "num" : "" }, c)))),
                tbody,
              ),
              showAll,
            )
          : h("div", { class: "empty" }, h("strong", {}, "No backtest trades yet"), "Add trades one by one or paste a CSV export from your backtesting tool."),
      ),
    );
  }

  clear(
    root,
    h(
      "div",
      { class: "page-head" },
      h("div", {}, h("h1", {}, "Strategy incubator"), h("p", {}, "Prove a strategy's edge on historical trades before trading it live")),
      h("button", { class: "btn primary", type: "button", onclick: () => newStrategyDialog(async (id) => ((selectedId = id), await loadList(), loadDetail())) }, icon("plus"), "New test strategy"),
    ),
    h("div", { class: "bt-layout" }, list, detail),
  );
  await loadList();
  await loadDetail();
  return destroyCharts;
}
