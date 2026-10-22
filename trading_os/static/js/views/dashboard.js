// Statistical dashboard: KPIs, equity curve, daily P&L, R distribution, breakdowns, cost of mistakes.

import { api, query } from "../api.js";
import { chartsAvailable, dailyPnlChart, distributionChart, equityChart } from "../charts.js";
import { clear, field, h, icon, select } from "../dom.js";
import { fmt, pnlClass, toInputDate } from "../format.js";
import { t } from "../i18n.js";
import { accountNow, state } from "../state.js";

const PRESETS = [
  ["all", "All time"],
  ["today", "Today"],
  ["7", "Last 7 days"],
  ["30", "Last 30 days"],
  ["90", "Last 90 days"],
  ["ytd", "Year to date"],
  ["custom", "Custom range"],
];

const BREAKDOWNS = [
  ["symbol", "Asset"],
  ["strategy", "Strategy"],
  ["session", "Session"],
  ["emotion", "Emotion"],
  ["direction", "Direction"],
  ["confluence", "Confluence"],
  ["weekday", "Weekday"],
  ["compliance", "Rule compliance"],
  ["mistake", "Mistake"],
];

const OVERLAP_NOTES = {
  confluence: "A trade counts once for every confluence it lists, so rows overlap.",
  mistake: "A trade counts once for every mistake tagged on it, so rows overlap.",
};

// Kept at module level so filters survive navigating away and back.
const filters = { preset: "all", date_from: "", date_to: "", symbol: "", strategy: "", session: "", direction: "" };
let activeTab = "symbol";

function dateRange() {
  const now = accountNow();
  const today = toInputDate(now);
  const daysAgo = (n) => toInputDate(new Date(now.getFullYear(), now.getMonth(), now.getDate() - n));
  switch (filters.preset) {
    case "today":
      return { date_from: today, date_to: today };
    case "7":
    case "30":
    case "90":
      return { date_from: daysAgo(Number(filters.preset) - 1), date_to: today };
    case "ytd":
      return { date_from: `${now.getFullYear()}-01-01`, date_to: today };
    case "custom":
      return { date_from: filters.date_from, date_to: filters.date_to };
    default:
      return {};
  }
}

const profitFactor = (stats) => (stats.profit_factor_infinite ? "∞" : stats.profit_factor === null ? "—" : fmt.num(stats.profit_factor));

function kpi(label, value, sub, valueClass = "") {
  return h("div", { class: "kpi" }, h("div", { class: "label" }, label), h("div", { class: `value ${valueClass}` }, value), sub ? h("div", { class: "sub" }, sub) : null);
}

function chartCard({ title, subtitle, legend, build, table, tall = false }) {
  let chart = null;
  let showTable = false;
  const body = h("div");
  const toggle = h("button", { class: "btn ghost sm", type: "button", onclick: () => ((showTable = !showTable), draw()) });

  function draw() {
    chart?.destroy();
    chart = null;
    clear(toggle, icon(showTable ? "chart" : "table"), showTable ? "Chart" : "Table");
    if (showTable || !chartsAvailable()) {
      clear(
        body,
        h(
          "div",
          { class: "table-wrap", style: { maxHeight: tall ? "320px" : "280px", overflowY: "auto" } },
          h("table", {}, h("thead", {}, h("tr", {}, table.head.map((c, i) => h("th", { class: i ? "num" : "" }, c)))), h("tbody", {}, table.rows.map((r) => h("tr", {}, r.map((c, i) => h("td", { class: i ? "num" : "" }, c)))))),
        ),
      );
      return;
    }
    const canvas = h("canvas", { role: "img", "aria-label": `${title} chart` });
    clear(body, h("div", { class: `chart-box${tall ? " tall" : ""}` }, canvas));
    chart = build(canvas);
  }

  const card = h(
    "div",
    { class: "card" },
    h("div", { class: "card-head" }, h("div", {}, h("h2", {}, title), subtitle ? h("p", {}, subtitle) : null), h("div", { class: "row" }, legend, toggle)),
    body,
  );
  draw();
  return { card, destroy: () => chart?.destroy() };
}

function legend(items) {
  return h("div", { class: "chart-legend" }, items.map(([color, label]) => h("span", {}, h("i", { style: { background: color } }), label)));
}

function compareColumn(title, iconName, stats, cls) {
  return h(
    "div",
    {},
    h("div", { class: `head ${cls}` }, icon(iconName), title),
    h(
      "dl",
      {},
      h("dt", {}, "Trades"),
      h("dd", {}, stats.trades),
      h("dt", {}, "Win rate"),
      h("dd", {}, fmt.pct(stats.win_rate)),
      h("dt", {}, "Net P&L"),
      h("dd", { class: pnlClass(stats.net_pnl) }, fmt.signedMoney(stats.net_pnl)),
      h("dt", {}, "Expectancy"),
      h("dd", { class: pnlClass(stats.expectancy) }, fmt.signedMoney(stats.expectancy)),
      h("dt", {}, "Profit factor"),
      h("dd", {}, profitFactor(stats)),
    ),
  );
}

function compareCard(summary) {
  const followed = summary.followed_plan;
  const broke = summary.broke_rules;
  let verdict = "Rate your trades (rule compliance) to measure the cost of indiscipline.";
  if (followed.trades && broke.trades) {
    const diff = (followed.expectancy ?? 0) - (broke.expectancy ?? 0);
    verdict =
      diff >= 0
        ? `Following your plan earns ${fmt.money(diff)} more per trade than breaking it.`
        : `Rule-breaking trades currently out-earn plan trades by ${fmt.money(-diff)} per trade - review your rules.`;
  } else if (followed.trades) {
    verdict = "Every rated trade followed the plan. Keep it up.";
  }
  return h(
    "div",
    { class: "card" },
    h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Discipline"), h("p", {}, `Rule compliance ${fmt.pct(summary.compliance_rate)}`))),
    h("div", { class: "compare" }, compareColumn("Followed plan", "checkCircle", followed, "pos"), compareColumn("Broke rules", "xCircle", broke, "neg")),
    h("p", { class: "muted small", style: { marginTop: "12px" } }, verdict),
  );
}

function mistakeCard(summary) {
  const clean = summary.mistake_free;
  const flawed = summary.with_mistakes;
  const rows = summary.mistake_cost;
  const maxAbs = Math.max(1, ...rows.map((r) => Math.abs(r.net_pnl || 0)));
  let verdict = "No mistakes tagged in this range. Tag mistakes on your trades to see what they cost you.";
  if (flawed.trades && clean.trades) {
    const diff = (clean.expectancy ?? 0) - (flawed.expectancy ?? 0);
    verdict =
      diff >= 0
        ? `Mistake-free trades earn ${fmt.money(diff)} more per trade than trades with a mistake.`
        : `Trades with a mistake currently out-earn mistake-free trades by ${fmt.money(-diff)} per trade - check how you tag them.`;
  } else if (flawed.trades) {
    verdict = "Every trade in this range has a mistake tagged. Pick one to eliminate first.";
  }
  const table = rows.length
    ? h(
        "div",
        { class: "table-wrap" },
        h(
          "table",
          {},
          h("thead", {}, h("tr", {}, ["Mistake", "Trades", "Net P&L", "Avg P&L", "Win rate"].map((c, i) => h("th", { class: i ? "num" : "" }, c)))),
          h(
            "tbody",
            {},
            rows.map((r) =>
              h(
                "tr",
                {},
                h("td", {}, r.mistake),
                h("td", { class: "num" }, r.trades),
                h(
                  "td",
                  { class: "num" },
                  h("span", { class: pnlClass(r.net_pnl) }, fmt.signedMoney(r.net_pnl)),
                  h("span", {
                    class: "inline-bar",
                    style: { width: `${Math.max(2, (Math.abs(r.net_pnl) / maxAbs) * 56)}px`, background: r.net_pnl >= 0 ? "var(--profit)" : "var(--loss)" },
                  }),
                ),
                h("td", { class: `num ${pnlClass(r.avg_pnl)}` }, fmt.signedMoney(r.avg_pnl)),
                h("td", { class: "num" }, fmt.pct(r.win_rate)),
              ),
            ),
          ),
        ),
      )
    : h("div", { class: "empty" }, "No mistakes tagged");
  return h(
    "div",
    { class: "card" },
    h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Cost of mistakes"), h("p", {}, "Most expensive first · a trade counts towards every mistake tagged on it"))),
    h(
      "div",
      { class: "grid split" },
      h(
        "div",
        {},
        h("div", { class: "compare" }, compareColumn("Mistake-free", "checkCircle", clean, "pos"), compareColumn("With mistakes", "xCircle", flawed, "neg")),
        h("p", { class: "muted small", style: { marginTop: "12px" } }, verdict),
      ),
      table,
    ),
  );
}

function breakdownCard(breakdowns) {
  const body = h("div");
  const tabs = h("div", { class: "tabs", role: "tablist" });

  function draw() {
    clear(
      tabs,
      BREAKDOWNS.map(([key, label]) =>
        h("button", { type: "button", role: "tab", class: key === activeTab ? "on" : "", "aria-selected": String(key === activeTab), onclick: () => ((activeTab = key), draw()) }, label),
      ),
    );
    const rows = breakdowns[activeTab] || [];
    const maxAbs = Math.max(1, ...rows.map((r) => Math.abs(r.net_pnl || 0)));
    clear(
      body,
      OVERLAP_NOTES[activeTab] ? h("p", { class: "muted small", style: { marginBottom: "8px" } }, OVERLAP_NOTES[activeTab]) : null,
      rows.length
        ? h(
            "div",
            { class: "table-wrap" },
            h(
              "table",
              {},
              h("thead", {}, h("tr", {}, ["", "Trades", "Win rate", "Net P&L", "Profit factor", "Expectancy", "Avg R"].map((c, i) => h("th", { class: i ? "num" : "" }, c)))),
              h(
                "tbody",
                {},
                rows.map((r) =>
                  h(
                    "tr",
                    {},
                    h("td", {}, r.key),
                    h("td", { class: "num" }, r.trades),
                    h("td", { class: "num" }, fmt.pct(r.win_rate)),
                    h(
                      "td",
                      { class: "num" },
                      h("span", { class: pnlClass(r.net_pnl) }, fmt.signedMoney(r.net_pnl)),
                      h("span", {
                        class: "inline-bar",
                        style: { width: `${Math.max(2, (Math.abs(r.net_pnl) / maxAbs) * 56)}px`, background: r.net_pnl >= 0 ? "var(--profit)" : "var(--loss)" },
                      }),
                    ),
                    h("td", { class: "num" }, profitFactor(r)),
                    h("td", { class: `num ${pnlClass(r.expectancy)}` }, fmt.signedMoney(r.expectancy)),
                    h("td", { class: "num" }, fmt.r(r.avg_r)),
                  ),
                ),
              ),
            ),
          )
        : h("div", { class: "empty" }, "No data"),
    );
  }

  draw();
  return h("div", { class: "card" }, h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Edge breakdown"), h("p", {}, "Where your results come from"))), tabs, body);
}

function detailCard(s) {
  const items = [
    ["Average win", h("span", { class: "pos" }, fmt.signedMoney(s.avg_win))],
    ["Average loss", h("span", { class: "neg" }, fmt.signedMoney(s.avg_loss))],
    ["Payoff ratio", fmt.num(s.payoff_ratio)],
    ["Largest win", fmt.signedMoney(s.largest_win)],
    ["Largest loss", fmt.signedMoney(s.largest_loss)],
    ["Gross profit", fmt.money(s.gross_profit)],
    ["Gross loss", fmt.money(s.gross_loss)],
    ["Total fees", fmt.money(s.total_fees)],
    ["Max consecutive wins", s.max_consecutive_wins],
    ["Max consecutive losses", s.max_consecutive_losses],
    ["Current streak", s.current_streak > 0 ? `${s.current_streak} win${s.current_streak > 1 ? "s" : ""}` : s.current_streak < 0 ? `${-s.current_streak} loss${s.current_streak < -1 ? "es" : ""}` : "—"],
    ["Avg planned R:R", fmt.rr(s.avg_planned_rr)],
    ["Avg executed R", fmt.r(s.avg_executed_rr)],
    ["Avg holding time", fmt.duration(s.avg_hold_minutes)],
  ];
  return h(
    "div",
    { class: "card" },
    h("div", { class: "card-head" }, h("h2", {}, "Trade statistics")),
    h("dl", { class: "stat-list", style: { gridTemplateColumns: "1fr" } }, items.map(([label, value]) => h("div", {}, h("dt", {}, label), h("dd", {}, value)))),
  );
}

/** Realised P&L in the range when it differs from the closed trades' P&L (partial exits on either side of the range). */
function realisedNote(s) {
  if (Math.abs((s.realized_pnl ?? 0) - (s.net_pnl ?? 0)) < 0.005) return null;
  return h("div", { class: "sub" }, t("Realised in this range: {amount} - partial exits count on the day they were filled", { amount: fmt.signedMoney(s.realized_pnl) }));
}

function renderReport(data, charts) {
  const s = data.summary;
  if (!s.trades && !data.daily_pnl.length) {
    return [
      h(
        "div",
        { class: "card empty" },
        h("strong", {}, "No closed trades in this range"),
        data.open_trades ? `${data.open_trades} open trade(s) will appear here once closed. ` : "Log trades in the journal to start measuring your edge. ",
        h("div", { style: { marginTop: "12px" } }, h("a", { class: "btn primary", href: "#/journal?new=1" }, icon("plus"), "Log a trade")),
      ),
    ];
  }

  const equity = chartCard({
    title: "Equity curve",
    subtitle: t("Realised equity · {start} → {end}", { start: fmt.money(s.start_equity), end: fmt.money(s.end_equity) }),
    build: (canvas) => equityChart(canvas, data.equity_curve),
    table: {
      head: [t("Trade"), t("Exit"), t("Date"), t("Symbol"), t("P&L"), t("Equity")],
      rows: data.equity_curve.slice(1).map((p) => [`#${p.trade_id}`, p.closes ? t("Trade closed") : t("Partial exit"), fmt.date(p.t), p.symbol, fmt.signedMoney(p.pnl), fmt.money(p.equity)]),
    },
    tall: true,
  });
  const daily = chartCard({
    title: "Daily P&L",
    subtitle: `${data.daily_pnl.length} trading days`,
    legend: legend([
      ["var(--profit)", "Profit"],
      ["var(--loss)", "Loss"],
    ]),
    build: (canvas) => dailyPnlChart(canvas, data.daily_pnl),
    table: {
      head: ["Date", "Trades", "W / L", t("Partial exits"), "P&L", "Cumulative"],
      rows: [...data.daily_pnl].reverse().map((d) => [fmt.date(d.date), d.trades, `${d.wins} / ${d.losses}`, d.partials, fmt.signedMoney(d.pnl), fmt.signedMoney(d.cumulative)]),
    },
  });
  if (!s.trades) {
    // Only partial exits were booked in the range: their trades close later, or are still open.
    charts.push(equity, daily);
    return [
      h(
        "div",
        { class: "card empty" },
        h("strong", {}, "No closed trades in this range"),
        t("Partial exits booked in this range: {amount}. Trade statistics appear once their trades close.", { amount: fmt.signedMoney(s.realized_pnl) }),
      ),
      equity.card,
      daily.card,
    ];
  }
  const distribution = chartCard({
    title: "Win / loss distribution",
    subtitle: `R-multiples · ${s.wins} wins, ${s.losses} losses, ${s.breakeven} breakeven`,
    legend: legend([
      ["var(--profit)", "Win"],
      ["var(--loss)", "Loss"],
    ]),
    build: (canvas) => distributionChart(canvas, data.distribution),
    table: {
      head: ["R-multiple", "Trades", "Wins", "Losses"],
      rows: data.distribution.filter((b) => b.count).map((b) => [b.label, b.count, b.wins, b.losses]),
    },
  });
  charts.push(equity, daily, distribution);

  return [
    h(
      "div",
      { class: "grid split" },
      h(
        "div",
        { class: "card hero" },
        h("div", { class: "label" }, "Net P&L"),
        h("div", { class: `value ${pnlClass(s.net_pnl)}` }, fmt.signedMoney(s.net_pnl)),
        h("div", { class: "sub" }, `${fmt.pct(s.return_pct, 2)} return on ${fmt.money(s.start_equity)} · ${s.trades} closed trades`, data.open_trades ? ` · ${data.open_trades} open` : ""),
        realisedNote(s),
      ),
      compareCard(s),
    ),
    h(
      "div",
      { class: "kpis" },
      kpi("Total trades", s.trades, `${s.wins}W · ${s.losses}L · ${s.breakeven}BE`),
      kpi("Win rate", fmt.pct(s.win_rate), `Payoff ratio ${fmt.num(s.payoff_ratio)}`),
      kpi("Profit factor", profitFactor(s), `${fmt.compactMoney(s.gross_profit)} / ${fmt.compactMoney(Math.abs(s.gross_loss || 0))}`),
      kpi("Expectancy", fmt.signedMoney(s.expectancy), `${fmt.r(s.avg_r)} per trade`, pnlClass(s.expectancy)),
      kpi("Max drawdown", fmt.money(s.max_drawdown), `${fmt.pct(s.max_drawdown_pct)} from peak`),
      kpi("Rule compliance", fmt.pct(s.compliance_rate, 0), `${s.followed_plan.trades} of ${s.followed_plan.trades + s.broke_rules.trades} rated`),
    ),
    equity.card,
    h("div", { class: "grid cols-2" }, daily.card, distribution.card),
    h("div", { class: "grid wide-left" }, breakdownCard(data.breakdowns), detailCard(s)),
    mistakeCard(s),
  ];
}

function buildFilters(onChange) {
  const plan = state.plan;
  const from = h("input", { type: "date", value: filters.date_from, onchange: (e) => ((filters.date_from = e.target.value), onChange()) });
  const to = h("input", { type: "date", value: filters.date_to, onchange: (e) => ((filters.date_to = e.target.value), onChange()) });
  const customFields = h("div", { class: "row", style: { display: filters.preset === "custom" ? "flex" : "none" } }, field("From", from), field("To", to));

  const bind = (key, options, label) =>
    field(
      label,
      select([["", "All"], ...options], filters[key], {
        onchange: (e) => {
          filters[key] = e.target.value;
          onChange();
        },
      }),
    );

  const preset = select(PRESETS, filters.preset, {
    onchange: (e) => {
      filters.preset = e.target.value;
      customFields.style.display = filters.preset === "custom" ? "flex" : "none";
      onChange();
    },
  });

  return h(
    "div",
    { class: "filters" },
    field("Date range", preset),
    customFields,
    bind("symbol", plan.markets.map((m) => m.symbol), "Asset"),
    bind("strategy", plan.strategies.map((s) => s.name), "Strategy"),
    bind("session", plan.sessions.map((s) => s.name), "Session"),
    bind("direction", [["long", "Long"], ["short", "Short"]], "Direction"),
  );
}

export async function render(root) {
  const charts = [];
  const destroy = () => charts.splice(0).forEach((c) => c.destroy());
  const body = h("div", { class: "stack" });
  const exportLink = h(
    "a",
    { class: "btn", href: "/api/reports/performance.pdf", download: "", title: "Download a PDF performance report for the current filters" },
    icon("download"),
    "Export PDF",
  );

  async function load() {
    body.classList.add("refreshing");
    const { preset, date_from, date_to, ...rest } = filters;
    const params = query({ ...rest, ...dateRange() });
    exportLink.href = `/api/reports/performance.pdf${params}`;
    try {
      const data = await api.get(`/api/analytics${params}`);
      destroy();
      clear(body, renderReport(data, charts));
    } catch (error) {
      clear(body, h("div", { class: "card empty" }, h("strong", {}, "Could not load analytics"), error.message));
    } finally {
      body.classList.remove("refreshing");
    }
  }

  clear(
    root,
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Performance"), h("p", {}, t("Trade statistics by close date; daily P&L and equity by the day each exit was filled"))), exportLink),
    buildFilters(load),
    body,
  );
  await load();
  return destroy;
}
