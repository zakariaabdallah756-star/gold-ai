// P&L calendar (month and week views, trades attributed to their close day) and the
// time-of-day heatmap (weekday x entry hour on the account clock).

import { api, query } from "../api.js";
import { clear, h, icon, modal, segmented, select, toast } from "../dom.js";
import { fmt, pnlClass, toInputDate } from "../format.js";
import { state } from "../state.js";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const HOURS = Array.from({ length: 24 }, (_, hour) => hour);

// RGB triplets of the palette, so washes and heat cells can vary the alpha only.
const PROFIT = "38, 166, 154";
const LOSS = "239, 83, 80";
const NEUTRAL = "137, 135, 129";
const ACCENT = "57, 135, 229";

const METRICS = [
  ["pnl", "Net P&L"],
  ["win_rate", "Win rate"],
  ["trades", "Trades"],
];
const RANGES = [
  ["all", "All time"],
  ["365", "Last 12 months"],
  ["90", "Last 90 days"],
  ["30", "Last 30 days"],
];

// Kept at module level so the view survives navigating away and back.
const view = { tab: "month", month: null, weekStart: null, metric: "pnl", table: false, range: "all" };

const compactNumber = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });
const monthFormat = new Intl.DateTimeFormat(undefined, { month: "long", year: "numeric" });
const weekdayDateFormat = new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short" });

// ------------------------------------------------------------------ helpers

function parseDay(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

function shiftDay(iso, days) {
  const date = parseDay(iso);
  date.setDate(date.getDate() + days);
  return toInputDate(date);
}

function mondayOf(iso) {
  return shiftDay(iso, -((parseDay(iso).getDay() + 6) % 7));
}

function shiftMonth(month, delta) {
  const [y, m] = month.split("-").map(Number);
  return toInputDate(new Date(y, m - 1 + delta, 1)).slice(0, 7);
}

const pad = (n) => String(n).padStart(2, "0");
const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;
const rgba = (rgb, alpha) => `rgba(${rgb}, ${alpha.toFixed(3)})`;
const isFlat = (value) => Math.abs(value) < 0.005;

/** "+1.2K" / "−85": a signed amount short enough for a phone-width calendar cell. */
function compactSigned(value) {
  const abs = Math.abs(value);
  if (abs < 0.5) return "0";
  return (value > 0 ? "+" : "−") + (abs < 1000 ? String(Math.round(abs)) : compactNumber.format(abs));
}

function tone(pnl, trades) {
  if (!trades) return "idle";
  if (isFlat(pnl)) return "even";
  return pnl > 0 ? "up" : "down";
}

/** Low-opacity profit/loss wash whose strength follows |pnl| relative to the period's largest day. */
function wash(pnl, trades, maxAbs) {
  const kind = tone(pnl, trades);
  if (kind === "idle") return null;
  if (kind === "even") return rgba(NEUTRAL, 0.14);
  return rgba(kind === "up" ? PROFIT : LOSS, 0.08 + 0.22 * Math.min(1, Math.abs(pnl) / maxAbs));
}

function totals(trades) {
  const wins = trades.filter((t) => t.outcome === "win").length;
  return {
    pnl: trades.reduce((sum, t) => sum + (t.pnl || 0), 0),
    trades: trades.length,
    wins,
    losses: trades.filter((t) => t.outcome === "loss").length,
    win_rate: trades.length ? (wins / trades.length) * 100 : null,
  };
}

function kpi(label, value, sub, valueClass = "") {
  return h("div", { class: "kpi" }, h("div", { class: "label" }, label), h("div", { class: `value num ${valueClass}` }, value), sub ? h("div", { class: "sub" }, sub) : null);
}

function navigator(label, { onPrev, onNext, onToday, prevLabel, nextLabel, todayLabel }) {
  return h(
    "div",
    { class: "cal-nav" },
    h("button", { type: "button", class: "btn ghost icon-only", "aria-label": prevLabel, title: prevLabel, onclick: onPrev }, icon("chevronLeft")),
    h("h2", { "aria-live": "polite" }, label),
    h("button", { type: "button", class: "btn ghost icon-only", "aria-label": nextLabel, title: nextLabel, onclick: onNext }, icon("chevronRight")),
    onToday ? h("button", { type: "button", class: "btn sm", onclick: onToday }, todayLabel) : null,
  );
}

function flags(day) {
  if (!day.has_review && !day.checklist_complete) return null;
  return h(
    "span",
    { class: "cal-flags" },
    day.checklist_complete ? h("span", { class: "cal-flag ok", title: "Pre-market checklist complete" }, icon("check")) : null,
    day.has_review ? h("span", { class: "cal-flag", title: "Post-market review written" }, icon("review")) : null,
  );
}

function describeDay(day, pnl, trades, winRate) {
  const parts = [fmt.longDate(day.date)];
  parts.push(trades ? `${fmt.signedMoney(pnl)} net, ${plural(trades, "trade")}, ${fmt.pct(winRate, 0)} win rate` : "no trades");
  if (day.checklist_complete) parts.push("pre-market checklist complete");
  if (day.has_review) parts.push("review written");
  return parts.join(", ");
}

function signedPnl(value, className = "") {
  return h(
    "span",
    { class: `num ${pnlClass(value)} ${className}` },
    h("span", { class: "full" }, fmt.signedMoney(value)),
    h("span", { class: "compact" }, compactSigned(value)),
  );
}

function directionBadge(direction) {
  return h("span", { class: `badge ${direction}` }, direction === "long" ? "Long" : "Short");
}

// ------------------------------------------------------------- day details

function stars(rating) {
  return h(
    "span",
    { class: "cal-stars", role: "img", "aria-label": rating ? `Discipline ${rating} of 5` : "Discipline not rated" },
    [1, 2, 3, 4, 5].map((n) => h("span", { class: rating && n <= rating ? "on" : "" }, icon("star"))),
  );
}

function tradeTable(trades) {
  if (!trades.length) return h("p", { class: "muted small" }, "No trades closed on this day.");
  return h(
    "div",
    { class: "table-wrap" },
    h(
      "table",
      {},
      h("thead", {}, h("tr", {}, ["Closed", "Symbol", "Side", "P&L", "R"].map((c, i) => h("th", { class: i > 2 ? "num" : "" }, c)))),
      h(
        "tbody",
        {},
        trades.map((t) =>
          h(
            "tr",
            {},
            h("td", { class: "nowrap" }, fmt.time(t.exit_time || t.entry_time)),
            h("td", {}, t.symbol),
            h("td", {}, directionBadge(t.direction)),
            h("td", { class: `num ${pnlClass(t.pnl)}` }, fmt.signedMoney(t.pnl)),
            h("td", { class: "num" }, fmt.r(t.r_multiple)),
          ),
        ),
      ),
    ),
  );
}

function reviewSummary(review) {
  if (!review) return h("p", { class: "muted small" }, "Not reviewed yet.");
  const plan = review.followed_plan === null ? null : review.followed_plan ? "Followed the plan" : "Broke the plan";
  const notes = [
    ["Lessons", review.lessons],
    ["Focus for tomorrow", review.tomorrow_focus],
  ].filter(([, text]) => text.trim());
  return h(
    "div",
    { class: "cal-review" },
    h(
      "div",
      { class: "row" },
      stars(review.discipline_rating),
      review.mood ? h("span", { class: "badge" }, review.mood) : null,
      plan ? h("span", { class: `badge ${review.followed_plan ? "good" : "bad"}` }, icon(review.followed_plan ? "check" : "x"), plan) : null,
    ),
    review.mistakes.length ? h("div", { class: "chips" }, review.mistakes.map((name) => h("span", { class: "badge warn" }, name))) : null,
    notes.map(([label, text]) => h("div", {}, h("div", { class: "muted small" }, label), h("p", { class: "cal-note" }, text))),
  );
}

async function openDay(date, today) {
  let detail;
  try {
    detail = await api.get(`/api/analytics/day/${date}`);
  } catch (error) {
    toast(error.message, "error");
    return;
  }
  const stats = totals(detail.trades);
  const canReview = date <= today;
  let ref = null;
  const body = h(
    "div",
    { class: "stack cal-modal" },
    h(
      "div",
      { class: "cal-modal-stats" },
      h("div", {}, h("span", { class: "muted small" }, "Net P&L"), h("strong", { class: `num ${pnlClass(detail.pnl)}` }, fmt.signedMoney(detail.pnl))),
      h("div", {}, h("span", { class: "muted small" }, "Trades"), h("strong", { class: "num" }, `${stats.trades} · ${stats.wins}W ${stats.losses}L`)),
      h("div", {}, h("span", { class: "muted small" }, "Win rate"), h("strong", { class: "num" }, fmt.pct(stats.win_rate, 0))),
      h(
        "div",
        {},
        h("span", { class: "muted small" }, "Checklist"),
        h("strong", { class: detail.checklist_complete ? "pos" : "flat" }, icon(detail.checklist_complete ? "checkCircle" : "x"), detail.checklist_complete ? " Complete" : " Not completed"),
      ),
    ),
    tradeTable(detail.trades),
    h("div", {}, h("div", { class: "section-title" }, "Post-market review"), reviewSummary(detail.review)),
  );
  ref = modal({
    title: fmt.longDate(date),
    body,
    footer: [
      h("button", { type: "button", class: "btn ghost", onclick: () => ref.close() }, "Close"),
      canReview ? h("a", { class: "btn primary", href: `#/review?date=${date}`, onclick: () => ref.close() }, icon("review"), detail.review ? "Open review" : "Write review") : null,
    ],
  });
}

// --------------------------------------------------------------- month view

function dayCell(day, today, maxAbs) {
  const kind = tone(day.pnl, day.trades);
  return h(
    "button",
    {
      type: "button",
      class: `cal-day ${kind}${day.in_month ? "" : " out"}${day.date === today ? " today" : ""}`,
      style: { background: wash(day.pnl, day.trades, maxAbs) },
      "aria-label": describeDay(day, day.pnl, day.trades, day.win_rate),
      onclick: () => openDay(day.date, today),
    },
    h("span", { class: "cal-top" }, h("span", { class: "cal-date num" }, String(Number(day.date.slice(8)))), flags(day)),
    day.trades ? signedPnl(day.pnl, "cal-pnl") : null,
    day.trades ? h("span", { class: "cal-meta num" }, h("span", {}, plural(day.trades, "trade")), h("span", { class: "cal-wr" }, ` · ${fmt.pct(day.win_rate, 0)}`)) : null,
  );
}

function weekCell(week) {
  const label = week.trades
    ? `Week of ${fmt.date(week.week_start)}: ${fmt.signedMoney(week.pnl)} net, ${plural(week.trades, "trade")}, ${fmt.pct(week.win_rate, 0)} win rate`
    : `Week of ${fmt.date(week.week_start)}: no trades`;
  return h(
    "div",
    { class: `cal-week ${tone(week.pnl, week.trades)}`, role: "note", title: label, "aria-label": label },
    h("span", { class: "cal-week-label" }, "Week"),
    week.trades ? signedPnl(week.pnl, "cal-pnl") : h("span", { class: "muted" }, "—"),
    week.trades ? h("span", { class: "cal-meta num" }, h("span", {}, plural(week.trades, "trade")), h("span", { class: "cal-wr" }, ` · ${fmt.pct(week.win_rate, 0)}`)) : null,
  );
}

function bestWorst(day) {
  return day ? [fmt.signedMoney(day.pnl), fmt.shortDate(day.date), pnlClass(day.pnl)] : ["—", "None this month", ""];
}

async function renderMonth(today, redraw) {
  const data = await api.get(`/api/analytics/calendar${query({ month: view.month })}`);
  const total = data.month_total;
  const traded = data.days.filter((d) => d.trades);
  const maxAbs = Math.max(1, ...traded.map((d) => Math.abs(d.pnl)));
  const [bestValue, bestSub, bestClass] = bestWorst(total.best_day);
  const [worstValue, worstSub, worstClass] = bestWorst(total.worst_day);
  const thisMonth = today.slice(0, 7);

  const cells = data.weeks.flatMap((week, row) => [...data.days.slice(row * 7, row * 7 + 7).map((day) => dayCell(day, today, maxAbs)), weekCell(week)]);

  return [
    h(
      "div",
      { class: "kpis" },
      kpi("Net P&L", fmt.signedMoney(total.pnl), plural(total.trading_days, "trading day"), pnlClass(total.pnl)),
      kpi("Trades", total.trades, `${total.wins}W · ${total.losses}L`),
      kpi("Win rate", fmt.pct(total.win_rate), "Wins / closed trades"),
      kpi("Green / red days", `${total.green_days} / ${total.red_days}`, `${plural(total.green_days, "green day")} · ${plural(total.red_days, "red day")}`),
      kpi("Best day", bestValue, bestSub, bestClass),
      kpi("Worst day", worstValue, worstSub, worstClass),
    ),
    h(
      "div",
      { class: "card cal-card" },
      h(
        "div",
        { class: "card-head" },
        navigator(monthFormat.format(parseDay(`${data.month}-01`)), {
          onPrev: () => redraw(() => (view.month = shiftMonth(view.month, -1))),
          onNext: () => redraw(() => (view.month = shiftMonth(view.month, 1))),
          onToday: data.month === thisMonth ? null : () => redraw(() => (view.month = thisMonth)),
          prevLabel: "Previous month",
          nextLabel: "Next month",
          todayLabel: "This month",
        }),
        h("p", { class: "cal-hint" }, "Select a day for its trades and review"),
      ),
      h(
        "div",
        { class: "cal-grid" },
        [...WEEKDAYS, "Week"].map((label) => h("div", { class: "cal-head", "aria-hidden": "true" }, label)),
        cells,
      ),
      h(
        "div",
        { class: "cal-legend muted small" },
        h("span", {}, h("i", { class: "sw up" }), "Profit day"),
        h("span", {}, h("i", { class: "sw down" }), "Loss day"),
        h("span", {}, h("i", { class: "sw idle" }), "No trades"),
        h("span", {}, h("span", { class: "cal-flag ok" }, icon("check")), "Checklist complete"),
        h("span", {}, h("span", { class: "cal-flag" }, icon("review")), "Review written"),
      ),
    ),
  ];
}

// ---------------------------------------------------------------- week view

function weekDayCard(date, detail, today, maxAbs) {
  const stats = totals(detail.trades);
  const kind = tone(detail.pnl, stats.trades);
  return h(
    "section",
    {
      class: `cal-weekday ${kind}${date === today ? " today" : ""}`,
      style: { background: wash(detail.pnl, stats.trades, maxAbs) },
      "aria-label": describeDay({ date, has_review: Boolean(detail.review), checklist_complete: detail.checklist_complete }, detail.pnl, stats.trades, stats.win_rate),
    },
    h(
      "div",
      { class: "cal-weekday-head" },
      h("strong", {}, weekdayDateFormat.format(parseDay(date))),
      flags({ has_review: Boolean(detail.review), checklist_complete: detail.checklist_complete }),
    ),
    stats.trades
      ? [
          h("div", { class: `cal-weekday-pnl num ${pnlClass(detail.pnl)}` }, fmt.signedMoney(detail.pnl)),
          h("div", { class: "muted small num" }, `${plural(stats.trades, "trade")} · ${fmt.pct(stats.win_rate, 0)} win rate`),
          h(
            "ul",
            { class: "cal-trades" },
            detail.trades.map((t) =>
              h(
                "li",
                {},
                h("span", { class: "cal-trade-time muted num" }, `Closed ${fmt.time(t.exit_time || t.entry_time)}`),
                h("span", { class: "sym" }, t.symbol),
                directionBadge(t.direction),
                h("span", { class: `num ${pnlClass(t.pnl)}` }, fmt.signedMoney(t.pnl)),
                h("span", { class: "cal-trade-r muted num" }, fmt.r(t.r_multiple)),
              ),
            ),
          ),
        ]
      : h("div", { class: "muted small cal-weekday-empty" }, "No trades"),
    date <= today ? h("a", { class: "cal-weekday-link small", href: `#/review?date=${date}` }, detail.review ? "Open review" : "Write review", icon("arrowRight")) : null,
  );
}

async function renderWeek(today, redraw) {
  const dates = Array.from({ length: 7 }, (_, i) => shiftDay(view.weekStart, i));
  const details = await Promise.all(dates.map((date) => api.get(`/api/analytics/day/${date}`)));
  const all = totals(details.flatMap((d) => d.trades));
  const traded = details.filter((d) => d.trades.length);
  const maxAbs = Math.max(1, ...traded.map((d) => Math.abs(d.pnl)));
  const reviewed = traded.filter((d) => d.review).length;
  const green = traded.filter((d) => d.pnl > 0.005).length;
  const red = traded.filter((d) => d.pnl < -0.005).length;
  const thisWeek = mondayOf(today);
  const range = `${fmt.shortDate(dates[0])} – ${fmt.date(dates[6])}`;

  return [
    h(
      "div",
      { class: "kpis" },
      kpi("Net P&L", fmt.signedMoney(all.pnl), plural(traded.length, "trading day"), pnlClass(all.pnl)),
      kpi("Trades", all.trades, `${all.wins}W · ${all.losses}L`),
      kpi("Win rate", fmt.pct(all.win_rate), "Wins / closed trades"),
      kpi("Green / red days", `${green} / ${red}`, `${plural(green, "green day")} · ${plural(red, "red day")}`),
      kpi("Reviews", `${reviewed} / ${traded.length}`, "Trading days reviewed"),
    ),
    h(
      "div",
      { class: "card cal-card" },
      h(
        "div",
        { class: "card-head" },
        navigator(range, {
          onPrev: () => redraw(() => (view.weekStart = shiftDay(view.weekStart, -7))),
          onNext: () => redraw(() => (view.weekStart = shiftDay(view.weekStart, 7))),
          onToday: view.weekStart === thisWeek ? null : () => redraw(() => (view.weekStart = thisWeek)),
          prevLabel: "Previous week",
          nextLabel: "Next week",
          todayLabel: "This week",
        }),
      ),
      h(
        "div",
        { class: "cal-week-grid" },
        dates.map((date, i) => weekDayCard(date, details[i], today, maxAbs)),
      ),
    ),
  ];
}

// -------------------------------------------------------------- time of day

function heatScale(cells) {
  return {
    maxAbs: Math.max(0.01, ...cells.map((c) => Math.abs(c.net_pnl))),
    maxTrades: Math.max(1, ...cells.map((c) => c.trades)),
  };
}

/** Background and in-cell mark of a heat cell; the mark keeps the reading independent of colour. */
function heatPaint(cell, metric, scale) {
  if (!cell) return { background: null, mark: "" };
  if (metric === "trades") return { background: rgba(ACCENT, 0.15 + 0.85 * (cell.trades / scale.maxTrades)), mark: String(cell.trades) };
  if (metric === "win_rate") {
    const lean = (cell.win_rate - 50) / 50;
    if (lean === 0) return { background: rgba(NEUTRAL, 0.45), mark: "50" };
    return { background: rgba(lean > 0 ? PROFIT : LOSS, 0.18 + 0.82 * Math.abs(lean)), mark: String(Math.round(cell.win_rate)) };
  }
  if (isFlat(cell.net_pnl)) return { background: rgba(NEUTRAL, 0.45), mark: "0" };
  const strength = Math.abs(cell.net_pnl) / scale.maxAbs;
  return { background: rgba(cell.net_pnl > 0 ? PROFIT : LOSS, 0.18 + 0.82 * strength), mark: cell.net_pnl > 0 ? "+" : "−" };
}

function hourRange(hour) {
  return `${pad(hour)}:00–${pad(hour)}:59`;
}

function describeStats(cell) {
  return `${plural(cell.trades, "trade")} (${cell.wins}W / ${cell.losses}L), win rate ${fmt.pct(cell.win_rate, 0)}, net ${fmt.signedMoney(cell.net_pnl)}, avg ${fmt.r(cell.avg_r)}`;
}

function heatGrid(heat, metric) {
  const scale = heatScale(heat.cells);
  const byKey = new Map(heat.cells.map((c) => [`${c.weekday}-${c.hour}`, c]));
  const rows = WEEKDAYS.map((label, weekday) => [
    h("div", { class: "heat-day", "aria-hidden": "true" }, label),
    HOURS.map((hour) => {
      const cell = byKey.get(`${weekday}-${hour}`);
      const paint = heatPaint(cell, metric, scale);
      const text = `${WEEKDAY_NAMES[weekday]} ${hourRange(hour)}: ${cell ? describeStats(cell) : "no trades"}`;
      return h("div", { class: `heat-cell${cell ? "" : " none"}`, role: "img", title: text, "aria-label": text, style: { background: paint.background } }, paint.mark);
    }),
  ]);
  return h(
    "div",
    { class: "heat-scroll", tabindex: "0", role: "group", "aria-label": "Trades by weekday and entry hour. Scroll horizontally for all hours." },
    h("div", { class: "heat" }, h("div", { class: "heat-corner" }), HOURS.map((hour) => h("div", { class: "heat-hour num", "aria-hidden": "true" }, pad(hour))), rows),
  );
}

function heatLegend(heat, metric) {
  const scale = heatScale(heat.cells);
  const empty = h("span", { class: "heat-legend-item" }, h("i", { class: "sw idle" }), "No trades");
  if (metric === "trades") {
    return h(
      "div",
      { class: "heat-legend muted small" },
      h("span", { class: "heat-legend-item" }, "1", h("i", { class: "ramp", style: { background: `linear-gradient(90deg, ${rgba(ACCENT, 0.15)}, ${rgba(ACCENT, 1)})` } }), `${scale.maxTrades} trades`),
      empty,
      h("span", {}, "Cells show the trade count."),
    );
  }
  const diverging = `linear-gradient(90deg, ${rgba(LOSS, 1)}, ${rgba(LOSS, 0.18)}, ${rgba(NEUTRAL, 0.45)}, ${rgba(PROFIT, 0.18)}, ${rgba(PROFIT, 1)})`;
  const [low, mid, high, note] =
    metric === "win_rate"
      ? ["0%", "50%", "100%", "Cells show the win rate in %."]
      : [fmt.signedMoney(-scale.maxAbs), "0", fmt.signedMoney(scale.maxAbs), "Cells show + for a net profit, − for a net loss."];
  return h(
    "div",
    { class: "heat-legend muted small" },
    h(
      "span",
      { class: "heat-legend-item" },
      h("span", { class: "neg" }, "Loss"),
      h("span", { class: "heat-scale" }, h("i", { class: "ramp", style: { background: diverging } }), h("span", { class: "heat-ticks num" }, h("span", {}, low), h("span", {}, mid), h("span", {}, high))),
      h("span", { class: "pos" }, "Profit"),
    ),
    empty,
    h("span", {}, note),
  );
}

function heatTable(heat) {
  return h(
    "div",
    { class: "table-wrap heat-table" },
    h(
      "table",
      {},
      h("thead", {}, h("tr", {}, ["Weekday", "Entry hour", "Trades", "W / L", "Win rate", "Net P&L", "Avg R"].map((c, i) => h("th", { class: i > 1 ? "num" : "" }, c)))),
      h(
        "tbody",
        {},
        heat.cells.map((c) =>
          h(
            "tr",
            {},
            h("td", {}, WEEKDAY_NAMES[c.weekday]),
            h("td", { class: "num" }, hourRange(c.hour)),
            h("td", { class: "num" }, c.trades),
            h("td", { class: "num" }, `${c.wins} / ${c.losses}`),
            h("td", { class: "num" }, fmt.pct(c.win_rate, 0)),
            h("td", { class: `num ${pnlClass(c.net_pnl)}` }, fmt.signedMoney(c.net_pnl)),
            h("td", { class: "num" }, fmt.r(c.avg_r)),
          ),
        ),
      ),
    ),
  );
}

/** Diverging bars around a centre axis: losses grow left, profits grow right. */
function barList(rows, labelOf) {
  const maxAbs = Math.max(0.01, ...rows.map((r) => Math.abs(r.net_pnl)));
  return h(
    "div",
    { class: "bar-list" },
    rows.map((r) => {
      const width = `${Math.max(r.trades ? 3 : 0, (Math.abs(r.net_pnl) / maxAbs) * 100)}%`;
      const label = labelOf(r);
      return h(
        "div",
        { class: "bar-row", title: r.trades ? `${label}: ${describeStats(r)}` : `${label}: no trades` },
        h("span", { class: "bar-label" }, label),
        h(
          "span",
          { class: "bar-track", "aria-hidden": "true" },
          h("span", { class: "half neg" }, r.net_pnl < 0 ? h("i", { style: { width } }) : null),
          h("span", { class: "half pos" }, r.net_pnl > 0 ? h("i", { style: { width } }) : null),
        ),
        r.trades ? h("span", { class: `num ${pnlClass(r.net_pnl)}` }, fmt.signedMoney(r.net_pnl)) : h("span", { class: "muted" }, "—"),
        h("span", { class: "muted small num bar-meta" }, r.trades ? `${plural(r.trades, "trade")} · ${fmt.pct(r.win_rate, 0)}` : "No trades"),
      );
    }),
  );
}

function extremes(rows, labelOf, kind) {
  const traded = rows.filter((r) => r.trades);
  const best = traded.reduce((top, r) => (top === null || r.net_pnl > top.net_pnl ? r : top), null);
  const worst = traded.reduce((low, r) => (low === null || r.net_pnl < low.net_pnl ? r : low), null);
  const item = (title, row, wanted) =>
    h(
      "div",
      { class: "heat-insight" },
      h("span", { class: "muted small" }, title),
      row && wanted(row.net_pnl) ? [h("strong", {}, labelOf(row)), h("span", { class: `num ${pnlClass(row.net_pnl)}` }, `${fmt.signedMoney(row.net_pnl)} · ${plural(row.trades, "trade")}`)] : h("strong", { class: "muted" }, "—"),
    );
  return [item(`Best ${kind}`, best, (v) => v > 0), item(`Worst ${kind}`, worst, (v) => v < 0)];
}

function timeOfDayCard(today) {
  const body = h("div", { class: "stack" });
  const tableButton = h("button", { type: "button", class: "btn ghost sm", "aria-pressed": String(view.table), onclick: () => ((view.table = !view.table), draw()) });
  let heat = null;
  let token = 0;

  function draw() {
    clear(tableButton, icon(view.table ? "grid" : "table"), view.table ? "Heatmap" : "Table");
    tableButton.setAttribute("aria-pressed", String(view.table));
    if (!heat) return;
    if (!heat.cells.length) {
      clear(body, h("div", { class: "empty" }, h("strong", {}, "No closed trades in this range"), "Your best and worst trading hours appear here once trades are closed."));
      return;
    }
    const hours = heat.by_hour.filter((r) => r.trades);
    clear(
      body,
      view.table ? heatTable(heat) : [heatGrid(heat, view.metric), heatLegend(heat, view.metric)],
      h("div", { class: "heat-insights" }, extremes(heat.by_hour, (r) => hourRange(r.hour), "hour"), extremes(heat.by_weekday, (r) => WEEKDAY_NAMES[r.weekday], "weekday")),
      h(
        "div",
        { class: "grid cols-2" },
        h("div", {}, h("div", { class: "section-title" }, "By weekday"), barList(heat.by_weekday, (r) => WEEKDAYS[r.weekday])),
        h("div", {}, h("div", { class: "section-title" }, "By entry hour"), barList(hours, (r) => `${pad(r.hour)}:00`)),
      ),
    );
  }

  async function load() {
    const current = ++token;
    body.classList.add("refreshing");
    const params = view.range === "all" ? {} : { date_from: shiftDay(today, 1 - Number(view.range)), date_to: today };
    try {
      const data = await api.get(`/api/analytics${query(params)}`);
      if (current !== token) return;
      heat = data.time_heatmap;
      draw();
    } catch (error) {
      if (current === token) clear(body, h("div", { class: "empty" }, h("strong", {}, "Could not load the heatmap"), error.message));
    } finally {
      if (current === token) body.classList.remove("refreshing");
    }
  }

  const metric = segmented(METRICS, view.metric, (value) => {
    view.metric = value;
    if (view.table) view.table = false;
    draw();
  });
  metric.el.setAttribute("aria-label", "Heatmap metric");
  const range = select(RANGES, view.range, {
    "aria-label": "Date range",
    onchange: (event) => {
      view.range = event.target.value;
      load();
    },
  });

  draw();
  load();
  return h(
    "section",
    { class: "card" },
    h(
      "div",
      { class: "card-head heat-head" },
      h("div", {}, h("h2", {}, "Time of day"), h("p", {}, `Closed trades by weekday and entry hour (${state.plan.settings.timezone} clock)`)),
      h("div", { class: "row" }, range, metric.el, tableButton),
    ),
    body,
  );
}

// ------------------------------------------------------------------ render

export async function render(root, { params }) {
  const today = state.status.today;
  const requested = params.get("month");
  if (requested && /^\d{4}-(0[1-9]|1[0-2])$/.test(requested)) view.month = requested;
  view.month ||= today.slice(0, 7);
  view.weekStart ||= mondayOf(today);

  const panel = h("div", { class: "stack" });
  const tabs = h("div", { class: "tabs", role: "tablist", "aria-label": "Calendar view" });
  let token = 0;

  async function drawPanel() {
    const current = ++token;
    clear(
      tabs,
      [
        ["month", "Month"],
        ["week", "Week"],
      ].map(([key, label]) =>
        h(
          "button",
          { type: "button", role: "tab", class: key === view.tab ? "on" : "", "aria-selected": String(key === view.tab), onclick: () => key !== view.tab && ((view.tab = key), drawPanel()) },
          label,
        ),
      ),
    );
    panel.classList.add("refreshing");
    try {
      const content = await (view.tab === "month" ? renderMonth : renderWeek)(today, redraw);
      if (current === token) clear(panel, content);
    } catch (error) {
      if (current === token) clear(panel, h("div", { class: "card empty" }, h("strong", {}, "Could not load the calendar"), error.message));
    } finally {
      if (current === token) panel.classList.remove("refreshing");
    }
  }

  function redraw(change) {
    change();
    drawPanel();
  }

  clear(
    root,
    h(
      "div",
      { class: "page-head" },
      h("div", {}, h("h1", {}, "P&L calendar"), h("p", {}, "Closed trades by the day they closed (partial exits included), with your review and checklist streaks")),
      h("a", { class: "btn", href: `#/review?date=${today}` }, icon("review"), "Today's review"),
    ),
    tabs,
    h("div", { class: "stack" }, panel, timeOfDayCard(today)),
  );
  await drawPanel();
}
