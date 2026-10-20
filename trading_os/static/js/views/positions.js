// Position management: open positions with scale-out targets, break-even and
// trailing stops, partial closes and an undoable event ledger. The targets
// ladder, event timeline and management summary are shared with the journal.

import { api } from "../api.js";
import { clear, confirmDialog, field, h, icon, modal, numberOrNull, select, toast } from "../dom.js";
import { fmt, pnlClass } from "../format.js";
import { loadStatus, marketBySymbol, onStatus, state } from "../state.js";
import { openTradeForm } from "./trade-form.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const QUICK_SPLITS = [25, 50, 75];

const STOP_KINDS = {
  initial: { label: "Initial", cls: "" },
  breakeven: { label: "Break-even", cls: "good" },
  trail: { label: "Trailing", cls: "open" },
  manual: { label: "Manual", cls: "warn" },
};
const STOP_KIND_BY_EVENT = { breakeven: "breakeven", trail: "trail", stop_move: "manual" };

const EVENT_KINDS = {
  tp: { icon: "target", label: (event) => `TP${event.target_level} hit` },
  partial: { icon: "scissors", label: () => "Partial close" },
  stop: { icon: "xCircle", label: () => "Stopped out" },
  close: { icon: "checkCircle", label: () => "Position closed" },
  breakeven: { icon: "shield", label: () => "Stop to break-even" },
  trail: { icon: "trending", label: () => "Trailing stop moved" },
  stop_move: { icon: "edit", label: () => "Stop moved" },
};

// --------------------------------------------------------------- shared views

/** Ledger order: time, then id. */
function orderedEvents(trade) {
  return [...(trade.events || [])].sort((a, b) => a.time.localeCompare(b.time) || a.id - b.id);
}

/** "initial" | "breakeven" | "trail" | "manual" - who set the stop in force. */
export function stopKindOf(trade) {
  if (trade.stop_kind) return trade.stop_kind;
  const moves = orderedEvents(trade).filter((event) => event.stop_after !== null);
  return STOP_KIND_BY_EVENT[moves.at(-1)?.kind] || "initial";
}

export function stopKindBadge(kind) {
  const spec = STOP_KINDS[kind] || STOP_KINDS.initial;
  return h("span", { class: `badge ${spec.cls}` }, spec.label);
}

export function eventLabel(event) {
  return (EVENT_KINDS[event.kind] || { label: () => event.kind }).label(event);
}

function riskFreeBadge() {
  return h("span", { class: "badge good pm-badge" }, icon("shield"), "Risk-free");
}

/** The scale-out plan; a trade without targets uses its take profit as one 100% target. */
function planTargets(trade) {
  if (trade.targets?.length) return trade.targets;
  if (trade.take_profit === null || trade.take_profit === undefined) return [];
  const risk = Math.abs(trade.entry_price - trade.stop_loss);
  return [
    {
      level: 1,
      price: trade.take_profit,
      close_pct: 100,
      hit: (trade.events || []).some((event) => event.kind === "tp" && event.target_level === 1),
      rr: risk ? Math.abs(trade.take_profit - trade.entry_price) / risk : null,
    },
  ];
}

function nextTargetLevel(trade) {
  if (trade.status !== "open") return null;
  if (trade.next_target !== undefined) return trade.next_target?.level ?? null;
  return planTargets(trade).find((target) => !target.hit)?.level ?? null;
}

/** TP1..TP4 with price, close %, R:R and hit / next status. */
export function targetsLadder(trade) {
  const targets = planTargets(trade);
  if (!targets.length) return h("p", { class: "muted small" }, "No take profit set.");
  const next = nextTargetLevel(trade);
  return h(
    "div",
    { class: "pm-ladder", role: "table", "aria-label": "Take-profit targets" },
    h(
      "div",
      { class: "pm-ladder-row head", role: "row" },
      ["Target", "Price", "Close", "R:R", "Status"].map((label) => h("span", { role: "columnheader" }, label)),
    ),
    targets.map((target) => {
      const isNext = target.level === next;
      let status;
      if (target.hit) status = h("span", { class: "pos pm-status" }, icon("check"), "Hit");
      else if (isNext) status = h("span", { class: "badge open" }, "Next");
      else status = h("span", { class: "muted" }, trade.status === "open" ? "Pending" : trade.managed ? "Not reached" : "—");
      return h(
        "div",
        { class: `pm-ladder-row${target.hit ? " hit" : ""}${isNext ? " next" : ""}`, role: "row" },
        h("span", { role: "cell" }, h("strong", {}, `TP${target.level}`)),
        h("span", { class: "num", role: "cell" }, fmt.price(target.price)),
        h("span", { class: "num", role: "cell" }, `${fmt.num(target.close_pct, 0)}%`),
        h("span", { class: "num", role: "cell" }, fmt.rr(target.rr)),
        h("span", { role: "cell" }, status),
      );
    }),
  );
}

/** Break-even and trailing-stop settings as two chips. */
export function managementSummary(trade) {
  const events = trade.events || [];
  const beDone = events.some((event) => event.kind === "breakeven");
  const trailing = events.some((event) => event.kind === "trail");
  const offset = trade.be_offset_pips ? ` +${fmt.num(trade.be_offset_pips, 1)} pips` : "";
  const be = trade.be_enabled ? `Break-even at TP1${offset}${beDone ? " · done" : ""}` : "Break-even off";
  const trigger = trade.trail_trigger === "immediate" ? "from entry" : "after TP1";
  const trail = trade.trail_enabled
    ? `Trailing ${fmt.num(trade.trail_distance_pips, 1)} pips ${trigger}${trailing ? " · active" : ""}`
    : "Trailing off";
  return h(
    "div",
    { class: "pm-manage" },
    h("span", { class: `chip${trade.be_enabled ? "" : " off"}` }, icon("shield"), be),
    h("span", { class: `chip${trade.trail_enabled ? "" : " off"}` }, icon("trending"), trail),
  );
}

function defaultNote(event) {
  return [`TP${event.target_level}`, "Closed", "Partial close", eventLabel(event)].includes(event.note);
}

/** Compact ledger: time, kind, price, lots, signed P&L, stop before -> after. */
export function eventTimeline(trade) {
  const events = orderedEvents(trade);
  if (!events.length) return h("p", { class: "muted small" }, "No position events yet.");
  return h(
    "ol",
    { class: "pm-timeline" },
    events.map((event) => {
      const kind = EVENT_KINDS[event.kind] || { icon: "info" };
      const details = [
        event.price !== null ? h("span", { class: "num" }, `@ ${fmt.price(event.price)}`) : null,
        event.lots ? h("span", { class: "num" }, `${fmt.lots(event.lots)} lots`) : null,
        event.lots && event.pnl !== null
          ? h("span", { class: `num ${pnlClass(event.pnl)}` }, fmt.signedMoney(event.pnl), event.pnl_manual ? " (manual)" : "")
          : null,
        event.stop_after !== null
          ? h("span", { class: "num" }, `Stop ${fmt.price(event.stop_before)} → ${fmt.price(event.stop_after)}`)
          : null,
      ];
      return h(
        "li",
        { class: `pm-event ${event.kind}` },
        h("time", { class: "muted", datetime: event.time }, fmt.shortDate(event.time), " ", fmt.time(event.time)),
        h("span", { class: "pm-event-kind" }, icon(kind.icon), eventLabel(event)),
        h("span", { class: "pm-event-detail" }, details),
        event.note && !defaultNote(event) ? h("span", { class: "pm-event-note muted" }, event.note) : null,
      );
    }),
  );
}

// --------------------------------------------------------------- price ladder

function svg(tag, attrs = {}, text) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
  if (text !== undefined) el.textContent = text;
  return el;
}

/** Spread label positions so they keep at least ``gap`` between them inside [top, bottom]. */
function spread(levels, gap, top, bottom) {
  const sorted = [...levels].sort((a, b) => a.y - b.y);
  sorted.forEach((level, index) => {
    level.labelY = index ? Math.max(level.y, sorted[index - 1].labelY + gap) : Math.max(level.y, top);
  });
  for (let index = sorted.length - 1; index >= 0; index--) {
    const limit = index === sorted.length - 1 ? bottom : sorted[index + 1].labelY - gap;
    sorted[index].labelY = Math.min(sorted[index].labelY, limit);
  }
  return sorted;
}

/** Vertical price ladder: initial SL, entry, current stop, targets and last price, with text labels. */
export function priceLadder(trade) {
  const width = 236;
  const height = 236;
  const pad = 14;
  const axis = 118;
  const stop = trade.current_stop ?? trade.stop_loss;
  const kind = stopKindOf(trade);
  const moved = Math.abs(stop - trade.stop_loss) > 1e-9;

  const levels = [
    { name: moved ? "Initial SL" : "Stop loss", price: trade.stop_loss, cls: moved ? "sl old" : "sl" },
    { name: "Entry", price: trade.entry_price, cls: "entry" },
    moved ? { name: `${STOP_KINDS[kind].label} stop`, price: stop, cls: trade.risk_free ? "stop safe" : "stop" } : null,
    ...planTargets(trade).map((target) => ({
      name: `TP${target.level}${target.hit ? " hit" : ""}`,
      price: target.price,
      cls: target.hit ? "tp hit" : "tp",
    })),
    trade.last_price !== null && trade.last_price !== undefined ? { name: "Last", price: trade.last_price, cls: "last" } : null,
  ].filter(Boolean);

  const prices = levels.map((level) => level.price);
  const low = Math.min(...prices);
  const high = Math.max(...prices);
  const span = high - low || Math.abs(high) * 0.001 || 1;
  const y = (price) => pad + ((high - price) / span) * (height - 2 * pad);
  for (const level of levels) level.y = y(level.price);
  spread(levels, 15, pad, height - pad);

  const summary = levels.map((level) => `${level.name} ${fmt.price(level.price)}`).join(", ");
  const root = svg("svg", { class: "pm-svg", viewBox: `0 0 ${width} ${height}`, width, height, role: "img", "aria-label": `Price ladder: ${summary}` });
  root.append(svg("line", { class: "axis", x1: axis, x2: axis, y1: pad - 6, y2: height - pad + 6 }));
  const zoneTop = Math.min(y(stop), y(trade.entry_price));
  root.append(
    svg("rect", {
      class: `zone${trade.risk_free ? " safe" : ""}`,
      x: axis - 5,
      width: 10,
      y: zoneTop,
      height: Math.max(Math.abs(y(stop) - y(trade.entry_price)), 1),
    }),
  );
  for (const level of levels) {
    const group = svg("g", { class: `lvl ${level.cls}` });
    group.append(svg("line", { class: "tick", x1: axis - 12, x2: axis + 12, y1: level.y, y2: level.y }));
    if (Math.abs(level.labelY - level.y) > 0.5) {
      group.append(svg("polyline", { class: "leader", points: `${axis - 16},${level.labelY} ${axis - 12},${level.y}` }));
      group.append(svg("polyline", { class: "leader", points: `${axis + 12},${level.y} ${axis + 16},${level.labelY}` }));
    }
    group.append(svg("text", { x: axis - 20, y: level.labelY, "text-anchor": "end", "dominant-baseline": "middle" }, level.name));
    group.append(svg("text", { class: "price", x: axis + 20, y: level.labelY, "dominant-baseline": "middle" }, fmt.price(level.price)));
    if (level.cls === "last") group.append(svg("circle", { cx: axis, cy: level.y, r: 4 }));
    root.append(group);
  }
  return root;
}

// ------------------------------------------------------------------- actions

function describeEvents(events) {
  if (!events.length) return null;
  const fills = events.filter((event) => event.lots && event.pnl !== null);
  const pnl = fills.reduce((sum, event) => sum + event.pnl, 0);
  return [...events.map(eventLabel), fills.length ? fmt.signedMoney(pnl) : null].filter(Boolean).join(" · ");
}

function lotStep(symbol) {
  return marketBySymbol(symbol)?.lot_step || 0.01;
}

function floorToStep(value, step) {
  const decimals = Math.max(0, Math.ceil(-Math.log10(step) - 1e-9));
  return Number((Math.floor(value / step + 1e-9) * step).toFixed(decimals));
}

function breakEvenStop(trade) {
  const pip = marketBySymbol(trade.symbol)?.pip_size || 0;
  const sign = trade.direction === "long" ? 1 : -1;
  return Number((trade.entry_price + sign * (trade.be_offset_pips || 0) * pip).toFixed(8));
}

const numberInput = (value, attrs = {}) =>
  h("input", { type: "number", step: "any", inputmode: "decimal", value: value === null || value === undefined ? "" : String(value), ...attrs });
const timeInput = () => h("input", { type: "datetime-local" });
const noteInput = () => h("input", { maxlength: "200", placeholder: "optional" });
const optional = (input) => (input.value ? input.value : undefined);

/**
 * A small form modal. ``submit`` returns the API call's promise and a toast
 * message; the modal stays open (with the error) when the call fails.
 */
function actionModal({ title, intro, fields, submitText, danger = false, submit, onDone }) {
  const error = h("div", { class: "pm-form-error", role: "alert" });
  const button = h("button", { class: `btn ${danger ? "danger" : "primary"}`, type: "button", onclick: () => go() }, submitText);
  const form = h(
    "form",
    { class: "stack pm-form", onsubmit: (event) => (event.preventDefault(), go()) },
    intro ? h("p", { class: "muted small" }, intro) : null,
    h("div", { class: "form-grid" }, fields),
    error,
    h("button", { type: "submit", hidden: true, tabindex: "-1" }),
  );
  const ref = modal({ title, body: form, footer: [h("button", { class: "btn ghost", type: "button", onclick: () => ref.close() }, "Cancel"), button] });

  async function go() {
    error.textContent = "";
    button.disabled = true;
    try {
      const { message, kind = "success" } = await submit();
      ref.close();
      toast(message, kind);
      await onDone();
    } catch (err) {
      error.textContent = err.message;
    } finally {
      button.disabled = false;
    }
  }
}

function pnlField(input) {
  return field(`Fill P&L (${state.plan.settings.currency})`, input, { help: "Optional - leave blank to calculate from the price" });
}

function openPriceUpdate(trade, onDone) {
  const price = numberInput(null, { placeholder: trade.last_price ? fmt.price(trade.last_price) : "price" });
  const high = numberInput(null, { placeholder: "optional" });
  const low = numberInput(null, { placeholder: "optional" });
  const time = timeInput();
  actionModal({
    title: `Update price · ${trade.symbol}`,
    intro: "Enter the current price, and the high and low since your last update if price moved further. Targets, stop-outs, break-even and trailing moves are applied automatically.",
    fields: [field("Last price", price), field("High", high), field("Low", low), field("Time", time, { help: "Leave empty for now" })],
    submitText: "Apply price",
    onDone,
    submit: async () => {
      const body = { price: numberOrNull(price), high: numberOrNull(high), low: numberOrNull(low), time: optional(time) };
      if (body.price === null && (body.high === null || body.low === null)) throw new Error("Enter the last price, or both the high and the low.");
      const result = await api.post(`/api/trades/${trade.id}/price`, body);
      const text = describeEvents(result.events);
      return text ? { message: `${trade.symbol}: ${text}` } : { message: `${trade.symbol} price recorded - no target or stop reached`, kind: "info" };
    },
  });
}

function openPartialClose(trade, onDone) {
  const remaining = trade.remaining_size ?? trade.position_size;
  const step = lotStep(trade.symbol);
  const lots = numberInput(null, { placeholder: "lots", min: "0" });
  const price = numberInput(trade.last_price);
  const time = timeInput();
  const pnl = numberInput(null, { placeholder: "auto" });
  const note = noteInput();
  const quick = h(
    "div",
    { class: "row pm-quick-split" },
    QUICK_SPLITS.map((pct) => h("button", { type: "button", class: "btn sm", onclick: () => (lots.value = String(floorToStep((remaining * pct) / 100, step))) }, `${pct}%`)),
  );
  actionModal({
    title: `Partial close · ${trade.symbol}`,
    intro: `${fmt.lots(remaining)} of ${fmt.lots(trade.position_size)} lots are open. Closing all of them records a full close.`,
    fields: [
      field("Lots to close", h("div", { class: "stack pm-lots-field" }, lots, quick), { help: `Rounded down to the ${fmt.lots(step)} lot step` }),
      field("Close price", price),
      field("Time", time, { help: "Leave empty for now" }),
      pnlField(pnl),
      field("Note", note, { full: true }),
    ],
    submitText: "Close lots",
    onDone,
    submit: async () => {
      const body = { lots: numberOrNull(lots), price: numberOrNull(price), time: optional(time), pnl: numberOrNull(pnl), note: note.value.trim() };
      if (!body.lots || !body.price) throw new Error("Enter the lots to close and the close price.");
      const result = await api.post(`/api/trades/${trade.id}/partial-close`, body);
      return { message: `${trade.symbol}: ${describeEvents(result.events)}` };
    },
  });
}

function openMoveStop(trade, onDone) {
  const current = trade.current_stop ?? trade.stop_loss;
  const stop = numberInput(current);
  const time = timeInput();
  const note = noteInput();
  const be = breakEvenStop(trade);
  const side = trade.direction === "long" ? "below" : "above";
  actionModal({
    title: `Move stop · ${trade.symbol}`,
    intro: `Current stop ${fmt.price(current)} (${STOP_KINDS[stopKindOf(trade)].label.toLowerCase()}). A ${trade.direction} stop must stay ${side} the last price${trade.last_price ? ` (${fmt.price(trade.last_price)})` : ""}.`,
    fields: [
      field(
        "New stop",
        h(
          "div",
          { class: "stack pm-lots-field" },
          stop,
          h(
            "div",
            { class: "row" },
            h("button", { type: "button", class: "btn sm", onclick: () => (stop.value = String(be)) }, icon("shield"), `Move to break-even (${fmt.price(be)})`),
          ),
        ),
      ),
      field("Time", time, { help: "Leave empty for now" }),
      field("Note", note, { full: true }),
    ],
    submitText: "Move stop",
    onDone,
    submit: async () => {
      const value = numberOrNull(stop);
      if (!value) throw new Error("Enter the new stop price.");
      await api.post(`/api/trades/${trade.id}/move-stop`, { stop: value, time: optional(time), note: note.value.trim() });
      return { message: `${trade.symbol}: stop moved to ${fmt.price(value)}` };
    },
  });
}

function openCloseAll(trade, onDone) {
  const price = numberInput(trade.last_price);
  const time = timeInput();
  const pnl = numberInput(null, { placeholder: "auto" });
  const note = noteInput();
  actionModal({
    title: `Close position · ${trade.symbol}`,
    intro: `Closes the remaining ${fmt.lots(trade.remaining_size ?? trade.position_size)} lots. Fees (${fmt.money(trade.fees)}) are deducted from the net P&L once the position is closed.`,
    fields: [field("Close price", price), field("Time", time, { help: "Leave empty for now" }), pnlField(pnl), field("Note", note, { full: true })],
    submitText: "Close position",
    danger: true,
    onDone,
    submit: async () => {
      const value = numberOrNull(price);
      if (!value) throw new Error("Enter the close price.");
      const result = await api.post(`/api/trades/${trade.id}/close`, { price: value, time: optional(time), pnl: numberOrNull(pnl), note: note.value.trim() });
      const net = result.trade.pnl;
      return { message: `${trade.symbol} closed${net === null ? "" : ` · net ${fmt.signedMoney(net)}`}`, kind: net !== null && net < 0 ? "warn" : "success" };
    },
  });
}

async function undoLast(trade, onDone) {
  const last = orderedEvents(trade).at(-1);
  if (!last) return;
  const effect = last.lots
    ? `The ${fmt.lots(last.lots)} closed lots return to the position.`
    : last.stop_before !== null
      ? `The stop returns to ${fmt.price(last.stop_before)}.`
      : "";
  const ok = await confirmDialog(`Undo "${eventLabel(last)}" recorded ${fmt.datetime(last.time)}? ${effect}`, { title: "Undo last event", confirmText: "Undo" });
  if (!ok) return;
  try {
    await api.del(`/api/trades/${trade.id}/events/${last.id}`);
    toast(`${trade.symbol}: "${eventLabel(last)}" undone`, "success");
    await onDone();
  } catch (error) {
    toast(error.message, "error");
  }
}

// ---------------------------------------------------------------------- page

function fact(label, value, extra = null) {
  return h("div", { class: "pm-fact" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value), extra);
}

function positionCard(p, refresh) {
  const original = p.position_size;
  const remaining = p.remaining_size ?? original;
  const closedPct = original ? Math.min(100, Math.max(0, (1 - remaining / original) * 100)) : 0;
  const stop = p.current_stop ?? p.stop_loss;
  const next = p.next_target;
  const sized = Boolean(original);
  const risk = p.remaining_risk ?? p.risk_amount;

  const lotsValue = sized
    ? [h("span", { class: "num" }, `${fmt.lots(remaining)} / ${fmt.lots(original)} lots`)]
    : [h("span", { class: "muted" }, "No size set")];
  const lotsExtra = sized
    ? [
        h("div", { class: `progress pm-progress${closedPct >= 99.99 ? " done" : ""}`, role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(Math.round(closedPct)), "aria-label": "Share of the position closed" }, h("span", { style: { width: `${closedPct}%` } })),
        h("div", { class: "muted small" }, `${fmt.num(closedPct, 0)}% closed`),
      ]
    : h("div", { class: "muted small" }, "Edit the trade to add lots");

  const unrealized =
    p.unrealized_pnl === null
      ? h("span", { class: "muted" }, "—")
      : h("span", { class: `num ${pnlClass(p.unrealized_pnl)}` }, fmt.signedMoney(p.unrealized_pnl));

  const facts = h(
    "div",
    { class: "pm-facts" },
    fact("Entry", h("span", { class: "num" }, fmt.price(p.entry_price))),
    fact("Stop", h("span", { class: "row pm-inline" }, h("span", { class: "num" }, fmt.price(stop)), stopKindBadge(p.stop_kind))),
    fact("Remaining", lotsValue, lotsExtra),
    fact("Realised (gross)", h("span", { class: `num ${pnlClass(p.realized_pnl)}` }, fmt.signedMoney(p.realized_pnl))),
    fact(
      "Unrealized",
      unrealized,
      h("div", { class: "muted small" }, p.last_price === null ? "Update the price to see it" : `at ${fmt.price(p.last_price)} · ${fmt.time(p.last_price_at)}`),
    ),
    fact("Remaining risk", p.risk_free ? riskFreeBadge() : h("span", { class: "num" }, risk === null ? "—" : fmt.money(risk))),
  );

  const events = p.events || [];
  const actions = h(
    "div",
    { class: "pm-actions" },
    h("button", { class: "btn sm primary", type: "button", disabled: !sized, onclick: () => openPriceUpdate(p, refresh) }, icon("refresh"), "Update price"),
    h("button", { class: "btn sm", type: "button", disabled: !sized, onclick: () => openPartialClose(p, refresh) }, icon("scissors"), "Partial close"),
    h("button", { class: "btn sm", type: "button", onclick: () => openMoveStop(p, refresh) }, icon("shield"), "Move stop"),
    h("button", { class: "btn sm", type: "button", disabled: !sized, onclick: () => openCloseAll(p, refresh) }, icon("x"), "Close all"),
    h("span", { class: "spacer" }),
    h("button", { class: "btn sm ghost", type: "button", disabled: !events.length, title: events.length ? "Undo the most recent event" : "No events to undo", onclick: () => undoLast(p, refresh) }, icon("undo"), "Undo last"),
    h("button", { class: "btn sm ghost", type: "button", onclick: () => openTradeForm({ trade: p, onSaved: () => refresh() }) }, icon("edit"), "Edit"),
  );

  return h(
    "article",
    { class: "card pm-card", "aria-label": `${p.symbol} ${p.direction} position` },
    h(
      "div",
      { class: "pm-head" },
      h(
        "div",
        { class: "pm-title" },
        h("h2", {}, p.symbol),
        h("span", { class: `badge ${p.direction}` }, p.direction === "long" ? "Long" : "Short"),
        p.source === "signal" ? h("span", { class: "badge open pm-badge" }, icon("signal"), "Signal") : null,
        p.risk_free ? riskFreeBadge() : null,
      ),
      h("div", { class: "muted small" }, [p.strategy || "No strategy", `opened ${fmt.datetime(p.entry_time)}`, `#${p.id}`].join(" · ")),
      next
        ? h("div", { class: "chip pm-next" }, icon("target"), "Next: ", h("strong", {}, `TP${next.level} ${fmt.price(next.price)}`), ` · ${fmt.num(next.close_pct, 0)}%`)
        : h("div", { class: "chip pm-next" }, icon("check"), "No open target"),
    ),
    h("div", { class: "pm-split" }, h("div", { class: "stack pm-main" }, facts, managementSummary(p)), h("div", { class: "pm-chart" }, priceLadder(p))),
    h("div", {}, h("div", { class: "section-title" }, "Targets"), targetsLadder(p)),
    actions,
    h("div", {}, h("div", { class: "section-title" }, `Events (${events.length})`), events.length ? eventTimeline(p) : h("p", { class: "muted small" }, "No events yet - update the price as the market moves.")),
  );
}

function kpi(label, value, sub, cls = "") {
  return h("div", { class: "kpi" }, h("div", { class: "label" }, label), h("div", { class: `value ${cls}` }, value), sub ? h("div", { class: "sub" }, sub) : null);
}

export async function render(root) {
  let data = null;
  const totalsBox = h("div", { class: "kpis pm-kpis" });
  const quickBox = h("div");
  const listBox = h("div", { class: "pm-list" });

  function drawTotals() {
    if (!data) return;
    const { totals } = data;
    const status = state.status;
    const equity = status?.current_equity;
    const today = status?.realized_pnl_today ?? null;
    clear(
      totalsBox,
      kpi("Open positions", String(totals.open_positions), `${new Set(data.positions.map((p) => p.symbol)).size} symbol(s)`),
      kpi("Remaining risk", fmt.money(totals.remaining_risk), equity ? `${fmt.pct((totals.remaining_risk / equity) * 100, 2)} of equity` : null),
      kpi("Unrealized P&L", fmt.signedMoney(totals.unrealized_pnl), totals.unrealized_pnl === null ? "Update prices to see it" : "At last prices, before fees", pnlClass(totals.unrealized_pnl)),
      kpi("Realised today", fmt.signedMoney(today), `${fmt.signedMoney(totals.realized_open)} banked on open positions`, pnlClass(today)),
      kpi("Risk-free", h("span", { class: "row pm-inline" }, icon("shield"), `${totals.risk_free_count} / ${totals.open_positions}`), "Stop at or beyond entry"),
    );
  }

  function drawQuick() {
    const symbols = [...new Set(data.positions.map((p) => p.symbol))].sort();
    if (!symbols.length) {
      clear(quickBox);
      return;
    }
    const symbol = select(symbols, symbols[0], { "aria-label": "Symbol" });
    const price = numberInput(null, { placeholder: "price", "aria-label": "Last price" });
    const high = numberInput(null, { placeholder: "optional", "aria-label": "High" });
    const low = numberInput(null, { placeholder: "optional", "aria-label": "Low" });
    const apply = h("button", { class: "btn primary", type: "submit" }, icon("refresh"), "Apply price");
    const submit = async (event) => {
      event.preventDefault();
      const body = { symbol: symbol.value, price: numberOrNull(price), high: numberOrNull(high), low: numberOrNull(low) };
      if (body.price === null && (body.high === null || body.low === null)) {
        toast("Enter the last price, or both the high and the low", "warn");
        return;
      }
      apply.disabled = true;
      try {
        const result = await api.post("/api/positions/price", body);
        const applied = result.results.filter((r) => !r.skipped);
        const text = applied
          .filter((r) => r.events.length)
          .map((r) => `#${r.trade_id} ${describeEvents(r.events)}`)
          .join("; ");
        toast(
          text ? `${body.symbol}: ${text}` : `${body.symbol} price recorded on ${applied.length} position(s) - no target or stop reached`,
          text ? "success" : "info",
        );
        for (const skipped of result.results.filter((r) => r.skipped)) toast(`Trade #${skipped.trade_id} skipped: ${skipped.skipped}`, "warn");
        await refresh();
      } catch (error) {
        toast(error.message, "error");
      } finally {
        apply.disabled = false;
      }
    };
    clear(
      quickBox,
      h(
        "form",
        { class: "card pm-quick", onsubmit: submit },
        h("div", { class: "pm-quick-head" }, h("h3", {}, icon("zap"), "Quick price update"), h("p", { class: "muted small" }, "Applies one price to every open position of the symbol.")),
        h("div", { class: "pm-quick-fields" }, field("Symbol", symbol), field("Last", price), field("High", high), field("Low", low), apply),
      ),
    );
  }

  function drawList() {
    if (!data.positions.length) {
      clear(
        listBox,
        h(
          "div",
          { class: "card empty pm-empty" },
          icon("layers", "icon pm-empty-icon"),
          h("strong", {}, "No open positions"),
          h("p", {}, "Take a trade from a signal or log one in the journal - its targets, stops and partial closes are managed here."),
          h(
            "div",
            { class: "row pm-empty-actions" },
            h("a", { class: "btn primary", href: "#/signals" }, icon("signal"), "Paste a signal"),
            h("a", { class: "btn", href: "#/journal" }, icon("journal"), "Open the journal"),
          ),
        ),
      );
      return;
    }
    clear(listBox, h("div", { class: "pm-grid" }, data.positions.map((p) => positionCard(p, refresh))));
  }

  async function load() {
    listBox.classList.add("refreshing");
    try {
      data = await api.get("/api/positions");
      drawTotals();
      drawQuick();
      drawList();
    } catch (error) {
      clear(listBox, h("div", { class: "card empty" }, h("strong", {}, "Positions could not be loaded"), error.message));
    } finally {
      listBox.classList.remove("refreshing");
    }
  }

  async function refresh() {
    await Promise.all([load(), loadStatus().catch(() => {})]);
  }

  clear(
    root,
    h(
      "div",
      { class: "page-head" },
      h("div", {}, h("h1", {}, "Positions"), h("p", {}, "Scale out at your targets, protect profits with break-even and trailing stops, and keep every fill on record.")),
      h("div", { class: "row" }, h("button", { class: "btn", type: "button", onclick: () => refresh() }, icon("refresh"), "Refresh")),
    ),
    totalsBox,
    quickBox,
    listBox,
  );

  await load();
  return onStatus(() => drawTotals());
}
