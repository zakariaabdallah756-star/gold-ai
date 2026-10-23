// Statistical dashboard: promo carousel, the account hero (balance, live equity, floating P&L, margin), then KPIs,
// equity curve, daily P&L, R distribution, breakdowns and the cost of mistakes.

import { accountMoney, ago, colorDot, kindBadge, logoMark } from "../accounts-ui.js";
import { api, query } from "../api.js";
import { chartsAvailable, dailyPnlChart, distributionChart, equityChart } from "../charts.js";
import { clear, field, h, icon, select, withNodes } from "../dom.js";
import { fmt, pnlClass, toInputDate } from "../format.js";
import { N_, t, tn } from "../i18n.js";
import { mountPromoCarousel } from "../promos.js";
import { accountNow, state } from "../state.js";

const HERO_POLL_MS = 12_000; // the account figures refresh while the dashboard is shown

const PRESETS = [
  ["all", N_("All time")],
  ["today", N_("Today")],
  ["7", N_("Last 7 days")],
  ["30", N_("Last 30 days")],
  ["90", N_("Last 90 days")],
  ["ytd", N_("Year to date")],
  ["custom", N_("Custom range")],
];

const BREAKDOWNS = [
  ["symbol", N_("Asset")],
  ["strategy", N_("Strategy")],
  ["session", N_("Session")],
  ["emotion", N_("Emotion")],
  ["direction", N_("Direction")],
  ["confluence", N_("Confluence")],
  ["weekday", N_("Weekday")],
  ["compliance", N_("Rule compliance")],
  ["mistake", N_("Mistake")],
];

const OVERLAP_NOTES = {
  confluence: N_("A trade counts once for every confluence it lists, so rows overlap."),
  mistake: N_("A trade counts once for every mistake tagged on it, so rows overlap."),
};

// The server names some breakdown rows with fixed English labels (weekdays, "no strategy", rule compliance...);
// everything else in a row key is the user's own data (symbols, strategies, sessions, emotions, confluences).
const ROW_LABELS = new Map([
  ["Unspecified", N_("Unspecified")],
  ["Off-session", N_("Off-session")],
  ["Not logged", N_("Not logged")],
  ["None recorded", N_("None recorded")],
  ["No mistakes", N_("No mistakes")],
  ["Not rated", N_("Not rated")],
  ["Followed plan", N_("Followed plan")],
  ["Broke rules", N_("Broke rules")],
  ["Long", N_("Long")],
  ["Short", N_("Short")],
  ["Monday", N_("Monday")],
  ["Tuesday", N_("Tuesday")],
  ["Wednesday", N_("Wednesday")],
  ["Thursday", N_("Thursday")],
  ["Friday", N_("Friday")],
  ["Saturday", N_("Saturday")],
  ["Sunday", N_("Sunday")],
  ["Calm", N_("Calm")],
  ["Confident", N_("Confident")],
  ["Focused", N_("Focused")],
  ["Anxious", N_("Anxious")],
  ["FOMO", N_("FOMO")],
  ["Greedy", N_("Greedy")],
  ["Fearful", N_("Fearful")],
  ["Impatient", N_("Impatient")],
  ["Revenge", N_("Revenge")],
  ["Bored", N_("Bored")],
  ["Euphoric", N_("Euphoric")],
  ["Tired", N_("Tired")],
]);
const rowLabel = (key) => (ROW_LABELS.has(key) ? t(ROW_LABELS.get(key)) : key);

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
    clear(toggle, icon(showTable ? "chart" : "table"), showTable ? t("Chart") : t("Table"));
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
    const canvas = h("canvas", { role: "img", "aria-label": t("{title} chart", { title }) });
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
      h("dt", {}, t("Trades")),
      h("dd", {}, stats.trades),
      h("dt", {}, t("Win rate")),
      h("dd", {}, fmt.pct(stats.win_rate)),
      h("dt", {}, t("Net P&L")),
      h("dd", { class: pnlClass(stats.net_pnl) }, fmt.signedMoney(stats.net_pnl)),
      h("dt", {}, t("Expectancy")),
      h("dd", { class: pnlClass(stats.expectancy) }, fmt.signedMoney(stats.expectancy)),
      h("dt", {}, t("Profit factor")),
      h("dd", {}, profitFactor(stats)),
    ),
  );
}

function compareCard(summary) {
  const followed = summary.followed_plan;
  const broke = summary.broke_rules;
  let verdict = t("Rate your trades (rule compliance) to measure the cost of indiscipline.");
  if (followed.trades && broke.trades) {
    const diff = (followed.expectancy ?? 0) - (broke.expectancy ?? 0);
    verdict =
      diff >= 0
        ? t("Following your plan earns {amount} more per trade than breaking it.", { amount: fmt.money(diff) })
        : t("Rule-breaking trades currently out-earn plan trades by {amount} per trade - review your rules.", { amount: fmt.money(-diff) });
  } else if (followed.trades) {
    verdict = t("Every rated trade followed the plan. Keep it up.");
  }
  return h(
    "div",
    { class: "card" },
    h("div", { class: "card-head" }, h("div", {}, h("h2", {}, t("Discipline")), h("p", {}, t("Rule compliance {pct}", { pct: fmt.pct(summary.compliance_rate) })))),
    h("div", { class: "compare" }, compareColumn(t("Followed plan"), "checkCircle", followed, "pos"), compareColumn(t("Broke rules"), "xCircle", broke, "neg")),
    h("p", { class: "muted small", style: { marginTop: "12px" } }, verdict),
  );
}

function mistakeCard(summary) {
  const clean = summary.mistake_free;
  const flawed = summary.with_mistakes;
  const rows = summary.mistake_cost;
  const maxAbs = Math.max(1, ...rows.map((r) => Math.abs(r.net_pnl || 0)));
  let verdict = t("No mistakes tagged in this range. Tag mistakes on your trades to see what they cost you.");
  if (flawed.trades && clean.trades) {
    const diff = (clean.expectancy ?? 0) - (flawed.expectancy ?? 0);
    verdict =
      diff >= 0
        ? t("Mistake-free trades earn {amount} more per trade than trades with a mistake.", { amount: fmt.money(diff) })
        : t("Trades with a mistake currently out-earn mistake-free trades by {amount} per trade - check how you tag them.", { amount: fmt.money(-diff) });
  } else if (flawed.trades) {
    verdict = t("Every trade in this range has a mistake tagged. Pick one to eliminate first.");
  }
  const table = rows.length
    ? h(
        "div",
        { class: "table-wrap" },
        h(
          "table",
          {},
          h("thead", {}, h("tr", {}, [t("Mistake"), t("Trades"), t("Net P&L"), t("Avg P&L"), t("Win rate")].map((c, i) => h("th", { class: i ? "num" : "" }, c)))),
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
    : h("div", { class: "empty" }, t("No mistakes tagged"));
  return h(
    "div",
    { class: "card" },
    h("div", { class: "card-head" }, h("div", {}, h("h2", {}, t("Cost of mistakes")), h("p", {}, t("Most expensive first · a trade counts towards every mistake tagged on it")))),
    h(
      "div",
      { class: "grid split" },
      h(
        "div",
        {},
        h("div", { class: "compare" }, compareColumn(t("Mistake-free"), "checkCircle", clean, "pos"), compareColumn(t("With mistakes"), "xCircle", flawed, "neg")),
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
        h("button", { type: "button", role: "tab", class: key === activeTab ? "on" : "", "aria-selected": String(key === activeTab), onclick: () => ((activeTab = key), draw()) }, t(label)),
      ),
    );
    const rows = breakdowns[activeTab] || [];
    const maxAbs = Math.max(1, ...rows.map((r) => Math.abs(r.net_pnl || 0)));
    clear(
      body,
      OVERLAP_NOTES[activeTab] ? h("p", { class: "muted small", style: { marginBottom: "8px" } }, t(OVERLAP_NOTES[activeTab])) : null,
      rows.length
        ? h(
            "div",
            { class: "table-wrap" },
            h(
              "table",
              {},
              h("thead", {}, h("tr", {}, ["", t("Trades"), t("Win rate"), t("Net P&L"), t("Profit factor"), t("Expectancy"), t("Avg R")].map((c, i) => h("th", { class: i ? "num" : "" }, c)))),
              h(
                "tbody",
                {},
                rows.map((r) =>
                  h(
                    "tr",
                    {},
                    h("td", {}, rowLabel(r.key)),
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
        : h("div", { class: "empty" }, t("No data")),
    );
  }

  draw();
  return h("div", { class: "card" }, h("div", { class: "card-head" }, h("div", {}, h("h2", {}, t("Edge breakdown")), h("p", {}, t("Where your results come from")))), tabs, body);
}

function detailCard(s) {
  const items = [
    [t("Average win"), h("span", { class: "pos" }, fmt.signedMoney(s.avg_win))],
    [t("Average loss"), h("span", { class: "neg" }, fmt.signedMoney(s.avg_loss))],
    [t("Payoff ratio"), fmt.num(s.payoff_ratio)],
    [t("Largest win"), fmt.signedMoney(s.largest_win)],
    [t("Largest loss"), fmt.signedMoney(s.largest_loss)],
    [t("Gross profit"), fmt.money(s.gross_profit)],
    [t("Gross loss"), fmt.money(s.gross_loss)],
    [t("Total fees"), fmt.money(s.total_fees)],
    [t("Max consecutive wins"), s.max_consecutive_wins],
    [t("Max consecutive losses"), s.max_consecutive_losses],
    [t("Current streak"), s.current_streak > 0 ? tn(s.current_streak, "{n} win", "{n} wins") : s.current_streak < 0 ? tn(-s.current_streak, "{n} loss", "{n} losses") : "—"],
    [t("Avg planned R:R"), fmt.rr(s.avg_planned_rr)],
    [t("Avg executed R"), fmt.r(s.avg_executed_rr)],
    [t("Avg holding time"), fmt.duration(s.avg_hold_minutes)],
  ];
  return h(
    "div",
    { class: "card" },
    h("div", { class: "card-head" }, h("h2", {}, t("Trade statistics"))),
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
        h("strong", {}, t("No closed trades in this range")),
        data.open_trades ? t("{count} open trade(s) will appear here once closed. ", { count: data.open_trades }) : t("Log trades in the journal to start measuring your edge. "),
        h("div", { style: { marginTop: "12px" } }, h("a", { class: "btn primary", href: "#/journal?new=1" }, icon("plus"), t("Log a trade"))),
      ),
    ];
  }

  const equity = chartCard({
    title: t("Equity curve"),
    subtitle: t("Realised equity · {start} → {end}", { start: fmt.money(s.start_equity), end: fmt.money(s.end_equity) }),
    build: (canvas) => equityChart(canvas, data.equity_curve),
    table: {
      head: [t("Trade"), t("Exit"), t("Date"), t("Symbol"), t("P&L"), t("Equity")],
      rows: data.equity_curve.slice(1).map((p) => [`#${p.trade_id}`, p.closes ? t("Trade closed") : t("Partial exit"), fmt.date(p.t), p.symbol, fmt.signedMoney(p.pnl), fmt.money(p.equity)]),
    },
    tall: true,
  });
  const daily = chartCard({
    title: t("Daily P&L"),
    subtitle: tn(data.daily_pnl.length, "{n} trading day", "{n} trading days"),
    legend: legend([
      ["var(--profit)", t("Profit")],
      ["var(--loss)", t("Loss")],
    ]),
    build: (canvas) => dailyPnlChart(canvas, data.daily_pnl),
    table: {
      head: [t("Date"), t("Trades"), t("W / L"), t("Partial exits"), t("P&L"), t("Cumulative")],
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
        h("strong", {}, t("No closed trades in this range")),
        t("Partial exits booked in this range: {amount}. Trade statistics appear once their trades close.", { amount: fmt.signedMoney(s.realized_pnl) }),
      ),
      equity.card,
      daily.card,
    ];
  }
  const distribution = chartCard({
    title: t("Win / loss distribution"),
    subtitle: t("R-multiples · {wins} wins, {losses} losses, {breakeven} breakeven", { wins: s.wins, losses: s.losses, breakeven: s.breakeven }),
    legend: legend([
      ["var(--profit)", t("Win")],
      ["var(--loss)", t("Loss")],
    ]),
    build: (canvas) => distributionChart(canvas, data.distribution),
    table: {
      head: [t("R-multiple"), t("Trades"), t("Wins"), t("Losses")],
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
        h("div", { class: "label" }, t("Net P&L")),
        h("div", { class: `value ${pnlClass(s.net_pnl)}` }, fmt.signedMoney(s.net_pnl)),
        h(
          "div",
          { class: "sub" },
          tn(s.trades, "{pct} return on {equity} · {n} closed trade", "{pct} return on {equity} · {n} closed trades", { pct: fmt.pct(s.return_pct, 2), equity: fmt.money(s.start_equity) }),
          data.open_trades ? ` · ${t("{n} open", { n: data.open_trades })}` : "",
        ),
        realisedNote(s),
      ),
      compareCard(s),
    ),
    h(
      "div",
      { class: "kpis" },
      kpi(t("Total trades"), s.trades, t("{wins}W · {losses}L · {breakeven}BE", { wins: s.wins, losses: s.losses, breakeven: s.breakeven })),
      kpi(t("Win rate"), fmt.pct(s.win_rate), t("Payoff ratio {value}", { value: fmt.num(s.payoff_ratio) })),
      kpi(t("Profit factor"), profitFactor(s), `${fmt.compactMoney(s.gross_profit)} / ${fmt.compactMoney(Math.abs(s.gross_loss || 0))}`),
      kpi(t("Expectancy"), fmt.signedMoney(s.expectancy), t("{r} per trade", { r: fmt.r(s.avg_r) }), pnlClass(s.expectancy)),
      kpi(t("Max drawdown"), fmt.money(s.max_drawdown), t("{pct} from peak", { pct: fmt.pct(s.max_drawdown_pct) })),
      kpi(t("Rule compliance"), fmt.pct(s.compliance_rate, 0), t("{rated} of {total} rated", { rated: s.followed_plan.trades, total: s.followed_plan.trades + s.broke_rules.trades })),
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
  const customFields = h("div", { class: "row", style: { display: filters.preset === "custom" ? "flex" : "none" } }, field(t("From"), from), field(t("To"), to));

  const bind = (key, options, label) =>
    field(
      label,
      select([["", t("All")], ...options], filters[key], {
        onchange: (e) => {
          filters[key] = e.target.value;
          onChange();
        },
      }),
    );

  const preset = select(PRESETS.map(([value, label]) => [value, t(label)]), filters.preset, {
    onchange: (e) => {
      filters.preset = e.target.value;
      customFields.style.display = filters.preset === "custom" ? "flex" : "none";
      onChange();
    },
  });

  return h(
    "div",
    { class: "filters" },
    field(t("Date range"), preset),
    customFields,
    bind("symbol", plan.markets.map((m) => m.symbol), t("Asset")),
    bind("strategy", plan.strategies.map((s) => s.name), t("Strategy")),
    bind("session", plan.sessions.map((s) => s.name), t("Session")),
    bind("direction", [["long", t("Long")], ["short", t("Short")]], t("Direction")),
  );
}

// ------------------------------------------------------------------ account hero

function metricCard({ key, iconName, label, value, valueClass = "", sub, badge }) {
  return h(
    "div",
    { class: `metric metric-${key}` },
    h("div", { class: "metric-label" }, h("span", { class: "metric-icon" }, icon(iconName)), h("span", {}, label), badge || null),
    h("div", { class: `metric-value num ${valueClass}` }, value),
    h("div", { class: "metric-sub" }, sub),
  );
}

function heroScope(data) {
  const scope = data.scope;
  if (scope.account_id === null) {
    const excluded = scope.excluded.length;
    return h(
      "div",
      { class: "hero-scope" },
      h("span", { class: "hero-scope-all" }, icon("layers")),
      h("span", { class: "hero-scope-name" }, t("All accounts")),
      h("span", { class: "hero-scope-meta" }, tn(scope.accounts.length, "{n} account combined", "{n} accounts combined")),
      excluded ? h("span", { class: "hero-scope-meta", title: t("Accounts in another currency or archived are not added up") }, tn(excluded, "{n} left out", "{n} left out")) : null,
    );
  }
  const account = data.accounts.find((a) => a.id === scope.account_id);
  return h(
    "div",
    { class: "hero-scope" },
    colorDot(account?.color),
    h("span", { class: "hero-scope-name" }, scope.name),
    account ? kindBadge(account.kind) : null,
    account?.broker ? h("span", { class: "hero-scope-meta" }, account.broker) : null,
  );
}

/** Where the figures come from: the bridge (with the time of its last report) or the journal. */
function heroSource(data) {
  const counted = new Set(data.scope.accounts);
  const included = data.accounts.filter((a) => counted.has(a.id));
  const live = included.filter((a) => a.metrics?.source === "bridge");
  if (!live.length) return { live: false };
  const at = live.map((a) => a.metrics.updated_at).sort().at(-1);
  return { live: true, at, partial: live.length < included.length, count: live.length, total: included.length };
}

function sourceText(source) {
  if (!source.live) return t("From journal");
  const when = ago(source.at);
  return source.partial
    ? t("Live from bridge for {live} of {total} accounts · updated {when}", { live: source.count, total: source.total, when })
    : t("Live from bridge · updated {when}", { when });
}

function heroMetrics(data) {
  const totals = data.totals;
  const currency = data.scope.currency;
  const money = (value, signed = false) => accountMoney(value, currency, { signed });
  const start = totals.equity - totals.change;
  const changePct = start > 0 ? (totals.change / start) * 100 : null;
  const counted = new Set(data.scope.accounts);
  const unpriced = data.accounts
    .filter((a) => counted.has(a.id) && a.metrics?.source === "journal")
    .reduce((sum, a) => sum + Math.max(0, (a.metrics.open_positions || 0) - (a.metrics.priced_positions || 0)), 0);
  const marginSub =
    totals.margin > 0
      ? totals.margin_level !== null && totals.margin_level !== undefined
        ? t("Free {free} · level {level}", { free: money(totals.free_margin), level: fmt.pct(totals.margin_level, 0) })
        : t("Free {free}", { free: money(totals.free_margin) })
      : t("No margin in use");
  return h(
    "div",
    { class: "hero-metrics" },
    metricCard({
      key: "balance",
      iconName: "wallet",
      label: t("Account Balance"),
      value: money(totals.balance),
      sub: t("Started at {amount}", { amount: money(start) }),
    }),
    metricCard({
      key: "equity",
      iconName: "trending",
      label: t("Live Equity"),
      value: money(totals.equity),
      sub: withNodes(t("{change} since start"), {
        change: h("span", { class: pnlClass(totals.change) }, changePct === null ? money(totals.change, true) : `${money(totals.change, true)} (${changePct > 0.005 ? "+" : ""}${fmt.pct(changePct, 2)})`),
      }),
    }),
    metricCard({
      key: "floating",
      iconName: "activity",
      label: t("Floating P&L"),
      value: money(totals.floating_pnl, true),
      valueClass: pnlClass(totals.floating_pnl),
      sub: [
        tn(totals.open_positions, "{n} open position", "{n} open positions"),
        unpriced ? h("span", { class: "muted", title: t("Open positions without a last price count as zero") }, ` · ${t("{n} without a live price", { n: unpriced })}`) : null,
      ],
    }),
    metricCard({
      key: "margin",
      iconName: "gauge",
      label: t("Active Margin"),
      value: money(totals.margin),
      sub: marginSub,
      badge: totals.margin_estimated
        ? h("span", { class: "badge warn metric-badge", title: t("Some open positions have no margin figure (unknown contract or currency): link the account to a bridge for broker figures") }, t("estimated"))
        : null,
    }),
  );
}

/** The dashboard header: logo, name, the account shown and its live figures (polled while shown). */
function mountHero(box) {
  let stopped = false;
  let timer = null;
  let data = null;
  const fresh = h("span", { class: "hero-fresh" });
  let scope = h("div", { class: "hero-scope" }, h("span", { class: "hero-scope-name" }, " "));
  let metrics = h(
    "div",
    { class: "hero-metrics skeleton", "aria-busy": "true" },
    [0, 1, 2, 3].map(() => h("div", { class: "metric" }, h("div", { class: "metric-label" }, " "), h("div", { class: "metric-value" }, " "), h("div", { class: "metric-sub" }, " "))),
  );
  clear(
    box,
    h(
      "div",
      { class: "hero-top" },
      h("div", { class: "hero-brand" }, h("span", { class: "hero-logo" }, logoMark()), h("div", { class: "hero-ident" }, h("div", { class: "hero-name" }, "Trading OS"), scope)),
      fresh,
    ),
    metrics,
  );
  const swap = (old, next) => (old.replaceWith(next), next);

  // Long amounts ("164.712,77 USD") shrink until every card's figure fits, all four at the same size.
  // (Measured with a hidden copy: scrollWidth is rounded and misses a text a fraction of a pixel too wide.)
  function fitValues() {
    const values = [...metrics.querySelectorAll(".metric-value")];
    if (!values.length || !metrics.isConnected) return;
    values.forEach((el) => (el.style.fontSize = ""));
    const base = parseFloat(getComputedStyle(values[0]).fontSize);
    let scale = 1;
    for (const el of values) {
      const probe = h("span", { style: { position: "absolute", visibility: "hidden", whiteSpace: "nowrap" } }, el.textContent);
      el.append(probe);
      const natural = probe.getBoundingClientRect().width;
      probe.remove();
      if (natural > 0) scale = Math.min(scale, (el.clientWidth - 2) / natural);
    }
    if (scale < 1) {
      const size = `${Math.max(14, Math.floor(base * scale))}px`;
      values.forEach((el) => (el.style.fontSize = size));
    }
  }
  let resizeFrame = 0;
  const onResize = () => {
    cancelAnimationFrame(resizeFrame);
    resizeFrame = requestAnimationFrame(fitValues);
  };
  window.addEventListener("resize", onResize);

  function showSource() {
    if (!data) return;
    const source = heroSource(data);
    fresh.className = `hero-fresh${source.live ? " live" : ""}`;
    fresh.title = source.live ? t("Balance, equity and margin as last reported by the account's bridge") : t("Computed from your journal: closed trades plus open positions at their last known price. Link the account to a bridge for live broker figures.");
    clear(fresh, source.live ? h("span", { class: "live-dot", "aria-hidden": "true" }) : icon("journal"), h("span", {}, sourceText(source)));
  }

  async function load() {
    timer = null;
    if (!document.hidden || !data) {
      try {
        const next = await api.get("/api/accounts/summary");
        if (stopped) return;
        data = next;
        scope = swap(scope, heroScope(data));
        metrics = swap(metrics, heroMetrics(data));
        fitValues();
        showSource();
      } catch (error) {
        if (stopped) return;
        if (!data) clear(fresh, icon("alert"), h("span", {}, error.message));
      }
    }
    if (!stopped) timer = setTimeout(load, HERO_POLL_MS);
  }

  // "updated 12s ago" keeps counting between polls.
  const ticker = setInterval(() => data && heroSource(data).live && showSource(), 1000);
  load();
  return () => {
    stopped = true;
    clearTimeout(timer);
    clearInterval(ticker);
    cancelAnimationFrame(resizeFrame);
    window.removeEventListener("resize", onResize);
  };
}

export async function render(root) {
  const charts = [];
  const body = h("div", { class: "stack" });
  const promoSlot = h("div", { class: "promo-slot" });
  const hero = h("section", { class: "hero-head", "aria-label": t("Account overview") });
  const stopHero = mountHero(hero);
  let stopPromos = null;
  let left = false;
  Promise.resolve()
    .then(() => mountPromoCarousel(promoSlot))
    .then((stop) => (left ? stop?.() : (stopPromos = stop)))
    .catch(() => {}); // the carousel never breaks the dashboard
  const destroy = () => charts.splice(0).forEach((c) => c.destroy());
  const cleanup = () => {
    left = true;
    stopHero();
    if (typeof stopPromos === "function") stopPromos();
    stopPromos = null;
    destroy();
  };
  const exportLink = h(
    "a",
    { class: "btn", href: "/api/reports/performance.pdf", download: "", title: t("Download a PDF performance report for the current filters") },
    icon("download"),
    t("Export PDF"),
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
      clear(body, h("div", { class: "card empty" }, h("strong", {}, t("Could not load analytics")), error.message));
    } finally {
      body.classList.remove("refreshing");
    }
  }

  try {
    clear(
      root,
      promoSlot,
      hero,
      h("div", { class: "page-head" }, h("div", {}, h("h1", {}, t("Performance")), h("p", {}, t("Trade statistics by close date; daily P&L and equity by the day each exit was filled"))), exportLink),
      buildFilters(load),
      body,
    );
    await load();
  } catch (error) {
    cleanup(); // no poll may outlive a page that failed to render
    throw error;
  }
  return cleanup;
}
