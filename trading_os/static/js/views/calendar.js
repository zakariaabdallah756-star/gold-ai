// P&L calendar (month and week views; realised P&L on the day it was booked - each partial
// exit on its fill day, the rest of a trade on its close day) and the time-of-day heatmap
// (weekday x entry hour on the account clock).

import { api, query } from "../api.js";
import { clear, h, icon, modal, segmented, select, toast } from "../dom.js";
import { currentLocale, fmt, pnlClass, toInputDate } from "../format.js";
import { N_, t, tn } from "../i18n.js";
import { state } from "../state.js";
import { eventLabel } from "./positions.js";

const WEEKDAYS = [N_("Mon"), N_("Tue"), N_("Wed"), N_("Thu"), N_("Fri"), N_("Sat"), N_("Sun")];
const WEEKDAY_NAMES = [N_("Monday"), N_("Tuesday"), N_("Wednesday"), N_("Thursday"), N_("Friday"), N_("Saturday"), N_("Sunday")];
// The review moods the server offers (stored as these English values).
const MOOD_LABELS = new Map([
  ["Calm", N_("Calm")],
  ["Satisfied", N_("Satisfied")],
  ["Neutral", N_("Neutral")],
  ["Frustrated", N_("Frustrated")],
  ["Anxious", N_("Anxious")],
  ["Tilted", N_("Tilted")],
  ["Exhausted", N_("Exhausted")],
  ["Euphoric", N_("Euphoric")],
]);
const HOURS = Array.from({ length: 24 }, (_, hour) => hour);

// RGB triplets of the palette, so washes and heat cells can vary the alpha only.
const PROFIT = "38, 166, 154";
const LOSS = "239, 83, 80";
const NEUTRAL = "137, 135, 129";
const ACCENT = "57, 135, 229";

const METRICS = [
  ["pnl", N_("Net P&L")],
  ["win_rate", N_("Win rate")],
  ["trades", N_("Trades")],
];
const RANGES = [
  ["all", N_("All time")],
  ["365", N_("Last 12 months")],
  ["90", N_("Last 90 days")],
  ["30", N_("Last 30 days")],
];

// Kept at module level so the view survives navigating away and back.
const view = { tab: "month", month: null, weekStart: null, metric: "pnl", table: false, range: "all" };

// Built when used (not at load) so they follow the interface language.
const monthFormat = () => new Intl.DateTimeFormat(currentLocale(), { month: "long", year: "numeric" });
const weekdayDateFormat = () => new Intl.DateTimeFormat(currentLocale(), { weekday: "short", day: "numeric", month: "short" });
const capitalize = (text) => text.charAt(0).toLocaleUpperCase(currentLocale()) + text.slice(1);

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
const tradeCount = (count) => tn(count, "{n} trade", "{n} trades");
const tradingDays = (count) => tn(count, "{n} trading day", "{n} trading days");
const rgba = (rgb, alpha) => `rgba(${rgb}, ${alpha.toFixed(3)})`;
const isFlat = (value) => Math.abs(value) < 0.005;

/** "1.2K" / "85": an amount short enough for a phone-width calendar cell (thousands are abbreviated in every language). */
function compactAmount(abs) {
  if (abs < 1000) return String(Math.round(abs));
  const decimal = (value) => new Intl.NumberFormat(currentLocale(), { maximumFractionDigits: 1 }).format(value);
  const thousands = Math.round(abs / 100) / 10;
  if (thousands < 1000) return `${decimal(thousands)}K`;
  const millions = Math.round(abs / 1e5) / 10;
  if (millions < 1000) return `${decimal(millions)}M`;
  return `${decimal(Math.round(abs / 1e8) / 10)}B`;
}

/** "+1.2K" / "−85": a signed amount short enough for a phone-width calendar cell. */
function compactSigned(value) {
  const abs = Math.abs(value);
  if (abs < 0.5) return "0";
  return (value > 0 ? "+" : "−") + compactAmount(abs);
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

/** A day or week with P&L booked: a trade closed or a partial exit filled. */
const active = (stats) => (stats.trades || 0) + (stats.partials || 0);

/** Several days' detail summed up (week view); the counts follow the server's day stats. */
function totals(details) {
  const sum = (key) => details.reduce((total, d) => total + d.stats[key], 0);
  const trades = sum("trades");
  return {
    pnl: details.reduce((total, d) => total + d.pnl, 0),
    trades,
    wins: sum("wins"),
    losses: sum("losses"),
    partials: sum("partials"),
    win_rate: trades ? (sum("wins") / trades) * 100 : null,
  };
}

/** The activity lines of a day or week cell: "2 trades · 50%", then the partial exits. */
function activity(stats) {
  return [
    stats.trades ? h("span", { class: "cal-meta num" }, h("span", {}, tradeCount(stats.trades)), h("span", { class: "cal-wr" }, ` · ${fmt.pct(stats.win_rate, 0)}`)) : null,
    stats.partials ? h("span", { class: "cal-meta num" }, tn(stats.partials, "{n} partial", "{n} partials")) : null,
  ];
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
    day.checklist_complete ? h("span", { class: "cal-flag ok", title: t("Pre-market checklist complete") }, icon("check")) : null,
    day.has_review ? h("span", { class: "cal-flag", title: t("Post-market review written") }, icon("review")) : null,
  );
}

function describeDay(day, pnl, trades, winRate, partials = 0) {
  const parts = [fmt.longDate(day.date)];
  if (trades || !partials) {
    parts.push(trades ? t("{pnl} net, {trades}, {rate} win rate", { pnl: fmt.signedMoney(pnl), trades: tradeCount(trades), rate: fmt.pct(winRate, 0) }) : t("no trades"));
  }
  else parts.push(t("{pnl} net", { pnl: fmt.signedMoney(pnl) }));
  if (partials) parts.push(tn(partials, "{n} partial exit", "{n} partial exits"));
  if (day.checklist_complete) parts.push(t("pre-market checklist complete"));
  if (day.has_review) parts.push(t("review written"));
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
  return h("span", { class: `badge ${direction}` }, direction === "long" ? t("Long") : t("Short"));
}

// ------------------------------------------------------------- day details

function stars(rating) {
  return h(
    "span",
    { class: "cal-stars", role: "img", "aria-label": rating ? t("Discipline {rating} of 5", { rating }) : t("Discipline not rated") },
    [1, 2, 3, 4, 5].map((n) => h("span", { class: rating && n <= rating ? "on" : "" }, icon("star"))),
  );
}

/** What a booked exit says about its trade: still open, closed another day, or its total when exits span days. */
function fillNote(fill, date, bookedByTrade) {
  let text = null;
  if (!fill.closes_trade) {
    const closedOn = fill.trade_exit_time?.slice(0, 10);
    if (fill.trade_status === "open") text = t("Trade still open");
    else if (closedOn && closedOn !== date) text = t("Trade closed {date}", { date: fmt.shortDate(closedOn) });
  } else if (fill.trade_pnl !== null && Math.abs(bookedByTrade.get(fill.trade_id) - fill.trade_pnl) >= 0.005) {
    text = t("Trade total {pnl}", { pnl: fmt.signedMoney(fill.trade_pnl) });
  }
  return text ? h("div", { class: "muted small" }, text) : null;
}

/** The exits booked on a day - partial exits on their fill day, closes with the trade's fees - so the day's P&L adds up. */
function fillTable(fills, date) {
  if (!fills.length) return h("p", { class: "muted small" }, t("No P&L booked on this day."));
  const bookedByTrade = new Map();
  for (const fill of fills) bookedByTrade.set(fill.trade_id, (bookedByTrade.get(fill.trade_id) || 0) + fill.pnl);
  const table = h(
    "div",
    { class: "table-wrap" },
    h(
      "table",
      {},
      h("thead", {}, h("tr", {}, [t("Time"), t("Symbol"), t("Exit"), t("Lots"), t("P&L"), t("R")].map((c, i) => h("th", { class: `${i > 2 ? "num" : ""}${i === 3 || i === 5 ? " cal-fill-opt" : ""}` }, c)))),
      h(
        "tbody",
        {},
        fills.map((fill) =>
          h(
            "tr",
            {},
            h("td", { class: "nowrap" }, fmt.time(fill.time)),
            h("td", { class: "cal-fill-symbol" }, fill.symbol, " ", directionBadge(fill.direction)),
            h("td", { class: "cal-fill-exit" }, eventLabel(fill), fillNote(fill, date, bookedByTrade)),
            h("td", { class: "num cal-fill-opt" }, fmt.lots(fill.lots)),
            h("td", { class: `num ${pnlClass(fill.pnl)}` }, fmt.signedMoney(fill.pnl)),
            h("td", { class: "num cal-fill-opt" }, fill.closes_trade ? fmt.r(fill.r_multiple) : "—"),
          ),
        ),
      ),
    ),
  );
  return [table, h("p", { class: "muted small" }, t("Each exit is booked on the day it was filled; the closing exit also carries the trade's fees."))];
}

function reviewSummary(review) {
  if (!review) return h("p", { class: "muted small" }, t("Not reviewed yet."));
  const plan = review.followed_plan === null ? null : review.followed_plan ? t("Followed the plan") : t("Broke the plan");
  const notes = [
    [t("Lessons"), review.lessons],
    [t("Focus for tomorrow"), review.tomorrow_focus],
  ].filter(([, text]) => text.trim());
  return h(
    "div",
    { class: "cal-review" },
    h(
      "div",
      { class: "row" },
      stars(review.discipline_rating),
      review.mood ? h("span", { class: "badge" }, MOOD_LABELS.has(review.mood) ? t(MOOD_LABELS.get(review.mood)) : review.mood) : null,
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
  const stats = detail.stats;
  const canReview = date <= today;
  let ref = null;
  const body = h(
    "div",
    { class: "stack cal-modal" },
    h(
      "div",
      { class: "cal-modal-stats" },
      h("div", {}, h("span", { class: "muted small" }, t("Net P&L")), h("strong", { class: `num ${pnlClass(detail.pnl)}` }, fmt.signedMoney(detail.pnl))),
      h("div", {}, h("span", { class: "muted small" }, t("Trades")), h("strong", { class: "num" }, t("{trades} · {wins}W {losses}L", { trades: stats.trades, wins: stats.wins, losses: stats.losses }))),
      h("div", {}, h("span", { class: "muted small" }, t("Win rate")), h("strong", { class: "num" }, fmt.pct(stats.win_rate, 0))),
      stats.partials ? h("div", {}, h("span", { class: "muted small" }, t("Partial exits")), h("strong", { class: "num" }, stats.partials)) : null,
      h(
        "div",
        {},
        h("span", { class: "muted small" }, t("Checklist")),
        h("strong", { class: detail.checklist_complete ? "pos" : "flat" }, icon(detail.checklist_complete ? "checkCircle" : "x"), " ", detail.checklist_complete ? t("Complete") : t("Not completed")),
      ),
    ),
    fillTable(detail.fills, date),
    h("div", {}, h("div", { class: "section-title" }, t("Post-market review")), reviewSummary(detail.review)),
  );
  ref = modal({
    title: fmt.longDate(date),
    body,
    footer: [
      h("button", { type: "button", class: "btn ghost", onclick: () => ref.close() }, t("Close")),
      canReview ? h("a", { class: "btn primary", href: `#/review?date=${date}`, onclick: () => ref.close() }, icon("review"), detail.review ? t("Open review") : t("Write review")) : null,
    ],
  });
}

// --------------------------------------------------------------- month view

function dayCell(day, today, maxAbs) {
  const kind = tone(day.pnl, active(day));
  return h(
    "button",
    {
      type: "button",
      class: `cal-day ${kind}${day.in_month ? "" : " out"}${day.date === today ? " today" : ""}`,
      style: { background: wash(day.pnl, active(day), maxAbs) },
      "aria-label": describeDay(day, day.pnl, day.trades, day.win_rate, day.partials),
      onclick: () => openDay(day.date, today),
    },
    h("span", { class: "cal-top" }, h("span", { class: "cal-date num" }, String(Number(day.date.slice(8)))), flags(day)),
    active(day) ? signedPnl(day.pnl, "cal-pnl") : null,
    active(day) ? activity(day) : null,
  );
}

function weekCell(week) {
  const date = fmt.date(week.week_start);
  let label = week.trades
    ? t("Week of {date}: {pnl} net, {trades}, {rate} win rate", { date, pnl: fmt.signedMoney(week.pnl), trades: tradeCount(week.trades), rate: fmt.pct(week.win_rate, 0) })
    : t("Week of {date}: no trades", { date });
  if (!week.trades && week.partials) label = t("Week of {date}: {pnl} net", { date, pnl: fmt.signedMoney(week.pnl) });
  if (week.partials) label += `, ${tn(week.partials, "{n} partial exit", "{n} partial exits")}`;
  return h(
    "div",
    { class: `cal-week ${tone(week.pnl, active(week))}`, role: "note", title: label, "aria-label": label },
    h("span", { class: "cal-week-label" }, t("Week")),
    active(week) ? signedPnl(week.pnl, "cal-pnl") : h("span", { class: "muted" }, "—"),
    active(week) ? activity(week) : null,
  );
}

function bestWorst(day) {
  return day ? [fmt.signedMoney(day.pnl), fmt.shortDate(day.date), pnlClass(day.pnl)] : ["—", t("None this month"), ""];
}

async function renderMonth(today, redraw) {
  const data = await api.get(`/api/analytics/calendar${query({ month: view.month })}`);
  const total = data.month_total;
  const traded = data.days.filter(active);
  const maxAbs = Math.max(1, ...traded.map((d) => Math.abs(d.pnl)));
  const [bestValue, bestSub, bestClass] = bestWorst(total.best_day);
  const [worstValue, worstSub, worstClass] = bestWorst(total.worst_day);
  const thisMonth = today.slice(0, 7);
  const partialsNote = total.partials ? ` · ${tn(total.partials, "{n} partial exit", "{n} partial exits")}` : "";

  const cells = data.weeks.flatMap((week, row) => [...data.days.slice(row * 7, row * 7 + 7).map((day) => dayCell(day, today, maxAbs)), weekCell(week)]);

  return [
    h(
      "div",
      { class: "kpis" },
      kpi(t("Net P&L"), fmt.signedMoney(total.pnl), tradingDays(total.trading_days), pnlClass(total.pnl)),
      kpi(t("Trades"), total.trades, `${t("{wins}W · {losses}L", { wins: total.wins, losses: total.losses })}${partialsNote}`),
      kpi(t("Win rate"), fmt.pct(total.win_rate), t("Wins / closed trades")),
      kpi(t("Green / red days"), `${total.green_days} / ${total.red_days}`, `${tn(total.green_days, "{n} green day", "{n} green days")} · ${tn(total.red_days, "{n} red day", "{n} red days")}`),
      kpi(t("Best day"), bestValue, bestSub, bestClass),
      kpi(t("Worst day"), worstValue, worstSub, worstClass),
    ),
    h(
      "div",
      { class: "card cal-card" },
      h(
        "div",
        { class: "card-head" },
        navigator(capitalize(monthFormat().format(parseDay(`${data.month}-01`))), {
          onPrev: () => redraw(() => (view.month = shiftMonth(view.month, -1))),
          onNext: () => redraw(() => (view.month = shiftMonth(view.month, 1))),
          onToday: data.month === thisMonth ? null : () => redraw(() => (view.month = thisMonth)),
          prevLabel: t("Previous month"),
          nextLabel: t("Next month"),
          todayLabel: t("This month"),
        }),
        h("p", { class: "cal-hint" }, t("Select a day for its trades and review")),
      ),
      h(
        "div",
        { class: "cal-grid" },
        [...WEEKDAYS.map((day) => t(day)), t("Week")].map((label) => h("div", { class: "cal-head", "aria-hidden": "true" }, label)),
        cells,
      ),
      h(
        "div",
        { class: "cal-legend muted small" },
        h("span", {}, h("i", { class: "sw up" }), t("Profit day")),
        h("span", {}, h("i", { class: "sw down" }), t("Loss day")),
        h("span", {}, h("i", { class: "sw idle" }), t("No trades")),
        h("span", {}, h("span", { class: "cal-flag ok" }, icon("check")), t("Checklist complete")),
        h("span", {}, h("span", { class: "cal-flag" }, icon("review")), t("Review written")),
      ),
    ),
  ];
}

// ---------------------------------------------------------------- week view

function weekDayCard(date, detail, today, maxAbs) {
  const stats = detail.stats;
  const kind = tone(detail.pnl, active(stats));
  const counts = [stats.trades ? t("{trades} · {rate} win rate", { trades: tradeCount(stats.trades), rate: fmt.pct(stats.win_rate, 0) }) : null, stats.partials ? tn(stats.partials, "{n} partial exit", "{n} partial exits") : null];
  return h(
    "section",
    {
      class: `cal-weekday ${kind}${date === today ? " today" : ""}`,
      style: { background: wash(detail.pnl, active(stats), maxAbs) },
      "aria-label": describeDay({ date, has_review: Boolean(detail.review), checklist_complete: detail.checklist_complete }, detail.pnl, stats.trades, stats.win_rate, stats.partials),
    },
    h(
      "div",
      { class: "cal-weekday-head" },
      h("strong", {}, capitalize(weekdayDateFormat().format(parseDay(date)))),
      flags({ has_review: Boolean(detail.review), checklist_complete: detail.checklist_complete }),
    ),
    active(stats)
      ? [
          h("div", { class: `cal-weekday-pnl num ${pnlClass(detail.pnl)}` }, fmt.signedMoney(detail.pnl)),
          h("div", { class: "muted small num" }, counts.filter(Boolean).join(" · ")),
          h(
            "ul",
            { class: "cal-trades" },
            detail.fills.map((fill) =>
              h(
                "li",
                {},
                h("span", { class: "cal-trade-time muted num" }, `${eventLabel(fill)} · ${fmt.time(fill.time)}`),
                h("span", { class: "sym" }, fill.symbol),
                directionBadge(fill.direction),
                h("span", { class: `num ${pnlClass(fill.pnl)}` }, fmt.signedMoney(fill.pnl)),
                h("span", { class: "cal-trade-r muted num" }, fill.closes_trade ? fmt.r(fill.r_multiple) : "—"),
              ),
            ),
          ),
        ]
      : h("div", { class: "muted small cal-weekday-empty" }, t("No trades")),
    date <= today ? h("a", { class: "cal-weekday-link small", href: `#/review?date=${date}` }, detail.review ? t("Open review") : t("Write review"), icon("arrowRight")) : null,
  );
}

async function renderWeek(today, redraw) {
  const dates = Array.from({ length: 7 }, (_, i) => shiftDay(view.weekStart, i));
  const details = await Promise.all(dates.map((date) => api.get(`/api/analytics/day/${date}`)));
  const all = totals(details);
  const traded = details.filter((d) => d.fills.length);
  const maxAbs = Math.max(1, ...traded.map((d) => Math.abs(d.pnl)));
  const reviewed = traded.filter((d) => d.review).length;
  const green = traded.filter((d) => d.pnl > 0.005).length;
  const red = traded.filter((d) => d.pnl < -0.005).length;
  const thisWeek = mondayOf(today);
  const range = `${fmt.shortDate(dates[0])} – ${fmt.date(dates[6])}`;
  const partialsNote = all.partials ? ` · ${tn(all.partials, "{n} partial exit", "{n} partial exits")}` : "";

  return [
    h(
      "div",
      { class: "kpis" },
      kpi(t("Net P&L"), fmt.signedMoney(all.pnl), tradingDays(traded.length), pnlClass(all.pnl)),
      kpi(t("Trades"), all.trades, `${t("{wins}W · {losses}L", { wins: all.wins, losses: all.losses })}${partialsNote}`),
      kpi(t("Win rate"), fmt.pct(all.win_rate), t("Wins / closed trades")),
      kpi(t("Green / red days"), `${green} / ${red}`, `${tn(green, "{n} green day", "{n} green days")} · ${tn(red, "{n} red day", "{n} red days")}`),
      kpi(t("Reviews"), `${reviewed} / ${traded.length}`, t("Trading days reviewed")),
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
          prevLabel: t("Previous week"),
          nextLabel: t("Next week"),
          todayLabel: t("This week"),
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
  return t("{trades} ({wins}W / {losses}L), win rate {rate}, net {pnl}, avg {r}", {
    trades: tradeCount(cell.trades),
    wins: cell.wins,
    losses: cell.losses,
    rate: fmt.pct(cell.win_rate, 0),
    pnl: fmt.signedMoney(cell.net_pnl),
    r: fmt.r(cell.avg_r),
  });
}

function heatGrid(heat, metric) {
  const scale = heatScale(heat.cells);
  const byKey = new Map(heat.cells.map((c) => [`${c.weekday}-${c.hour}`, c]));
  const rows = WEEKDAYS.map((label, weekday) => [
    h("div", { class: "heat-day", "aria-hidden": "true" }, t(label)),
    HOURS.map((hour) => {
      const cell = byKey.get(`${weekday}-${hour}`);
      const paint = heatPaint(cell, metric, scale);
      const text = `${t(WEEKDAY_NAMES[weekday])} ${hourRange(hour)}: ${cell ? describeStats(cell) : t("no trades")}`;
      return h("div", { class: `heat-cell${cell ? "" : " none"}`, role: "img", title: text, "aria-label": text, style: { background: paint.background } }, paint.mark);
    }),
  ]);
  return h(
    "div",
    { class: "heat-scroll", tabindex: "0", role: "group", "aria-label": t("Trades by weekday and entry hour. Scroll horizontally for all hours.") },
    h("div", { class: "heat" }, h("div", { class: "heat-corner" }), HOURS.map((hour) => h("div", { class: "heat-hour num", "aria-hidden": "true" }, pad(hour))), rows),
  );
}

function heatLegend(heat, metric) {
  const scale = heatScale(heat.cells);
  const empty = h("span", { class: "heat-legend-item" }, h("i", { class: "sw idle" }), t("No trades"));
  if (metric === "trades") {
    return h(
      "div",
      { class: "heat-legend muted small" },
      h("span", { class: "heat-legend-item" }, "1", h("i", { class: "ramp", style: { background: `linear-gradient(90deg, ${rgba(ACCENT, 0.15)}, ${rgba(ACCENT, 1)})` } }), tradeCount(scale.maxTrades)),
      empty,
      h("span", {}, t("Cells show the trade count.")),
    );
  }
  const diverging = `linear-gradient(90deg, ${rgba(LOSS, 1)}, ${rgba(LOSS, 0.18)}, ${rgba(NEUTRAL, 0.45)}, ${rgba(PROFIT, 0.18)}, ${rgba(PROFIT, 1)})`;
  const [low, mid, high, note] =
    metric === "win_rate"
      ? ["0%", "50%", "100%", t("Cells show the win rate in %.")]
      : [fmt.signedMoney(-scale.maxAbs), "0", fmt.signedMoney(scale.maxAbs), t("Cells show + for a net profit, − for a net loss.")];
  return h(
    "div",
    { class: "heat-legend muted small" },
    h(
      "span",
      { class: "heat-legend-item" },
      h("span", { class: "neg" }, t("Loss")),
      h("span", { class: "heat-scale" }, h("i", { class: "ramp", style: { background: diverging } }), h("span", { class: "heat-ticks num" }, h("span", {}, low), h("span", {}, mid), h("span", {}, high))),
      h("span", { class: "pos" }, t("Profit")),
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
      h("thead", {}, h("tr", {}, [t("Weekday"), t("Entry hour"), t("Trades"), t("W / L"), t("Win rate"), t("Net P&L"), t("Avg R")].map((c, i) => h("th", { class: i > 1 ? "num" : "" }, c)))),
      h(
        "tbody",
        {},
        heat.cells.map((c) =>
          h(
            "tr",
            {},
            h("td", {}, t(WEEKDAY_NAMES[c.weekday])),
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
        { class: "bar-row", title: r.trades ? `${label}: ${describeStats(r)}` : `${label}: ${t("no trades")}` },
        h("span", { class: "bar-label" }, label),
        h(
          "span",
          { class: "bar-track", "aria-hidden": "true" },
          h("span", { class: "half neg" }, r.net_pnl < 0 ? h("i", { style: { width } }) : null),
          h("span", { class: "half pos" }, r.net_pnl > 0 ? h("i", { style: { width } }) : null),
        ),
        r.trades ? h("span", { class: `num ${pnlClass(r.net_pnl)}` }, fmt.signedMoney(r.net_pnl)) : h("span", { class: "muted" }, "—"),
        h("span", { class: "muted small num bar-meta" }, r.trades ? `${tradeCount(r.trades)} · ${fmt.pct(r.win_rate, 0)}` : t("No trades")),
      );
    }),
  );
}

function extremes(rows, labelOf, bestTitle, worstTitle) {
  const traded = rows.filter((r) => r.trades);
  const best = traded.reduce((top, r) => (top === null || r.net_pnl > top.net_pnl ? r : top), null);
  const worst = traded.reduce((low, r) => (low === null || r.net_pnl < low.net_pnl ? r : low), null);
  const item = (title, row, wanted) =>
    h(
      "div",
      { class: "heat-insight" },
      h("span", { class: "muted small" }, title),
      row && wanted(row.net_pnl) ? [h("strong", {}, labelOf(row)), h("span", { class: `num ${pnlClass(row.net_pnl)}` }, `${fmt.signedMoney(row.net_pnl)} · ${tradeCount(row.trades)}`)] : h("strong", { class: "muted" }, "—"),
    );
  return [item(bestTitle, best, (v) => v > 0), item(worstTitle, worst, (v) => v < 0)];
}

function timeOfDayCard(today) {
  const body = h("div", { class: "stack" });
  const tableButton = h("button", { type: "button", class: "btn ghost sm", "aria-pressed": String(view.table), onclick: () => ((view.table = !view.table), draw()) });
  let heat = null;
  let token = 0;

  function draw() {
    clear(tableButton, icon(view.table ? "grid" : "table"), view.table ? t("Heatmap") : t("Table"));
    tableButton.setAttribute("aria-pressed", String(view.table));
    if (!heat) return;
    if (!heat.cells.length) {
      clear(body, h("div", { class: "empty" }, h("strong", {}, t("No closed trades in this range")), t("Your best and worst trading hours appear here once trades are closed.")));
      return;
    }
    const hours = heat.by_hour.filter((r) => r.trades);
    clear(
      body,
      view.table ? heatTable(heat) : [heatGrid(heat, view.metric), heatLegend(heat, view.metric)],
      h(
        "div",
        { class: "heat-insights" },
        extremes(heat.by_hour, (r) => hourRange(r.hour), t("Best hour"), t("Worst hour")),
        extremes(heat.by_weekday, (r) => t(WEEKDAY_NAMES[r.weekday]), t("Best weekday"), t("Worst weekday")),
      ),
      h(
        "div",
        { class: "grid cols-2" },
        h("div", {}, h("div", { class: "section-title" }, t("By weekday")), barList(heat.by_weekday, (r) => t(WEEKDAYS[r.weekday]))),
        h("div", {}, h("div", { class: "section-title" }, t("By entry hour")), barList(hours, (r) => `${pad(r.hour)}:00`)),
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
      if (current === token) clear(body, h("div", { class: "empty" }, h("strong", {}, t("Could not load the heatmap")), error.message));
    } finally {
      if (current === token) body.classList.remove("refreshing");
    }
  }

  const metric = segmented(METRICS.map(([value, label]) => [value, t(label)]), view.metric, (value) => {
    view.metric = value;
    if (view.table) view.table = false;
    draw();
  });
  metric.el.setAttribute("aria-label", t("Heatmap metric"));
  const range = select(RANGES.map(([value, label]) => [value, t(label)]), view.range, {
    "aria-label": t("Date range"),
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
      h("div", {}, h("h2", {}, t("Time of day")), h("p", {}, t("Closed trades by weekday and entry hour ({timezone} clock)", { timezone: state.plan.settings.timezone }))),
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
  const tabs = h("div", { class: "tabs", role: "tablist", "aria-label": t("Calendar view") });
  let token = 0;

  async function drawPanel() {
    const current = ++token;
    clear(
      tabs,
      [
        ["month", t("Month")],
        ["week", t("Week")],
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
      if (current === token) clear(panel, h("div", { class: "card empty" }, h("strong", {}, t("Could not load the calendar")), error.message));
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
      h("div", {}, h("h1", {}, t("P&L calendar")), h("p", {}, t("Realised P&L on the day it was booked - each partial exit on the day it was filled - with your review and checklist streaks"))),
      h("a", { class: "btn", href: `#/review?date=${today}` }, icon("review"), t("Today's review")),
    ),
    tabs,
    h("div", { class: "stack" }, panel, timeOfDayCard(today)),
  );
  await drawPanel();
}
