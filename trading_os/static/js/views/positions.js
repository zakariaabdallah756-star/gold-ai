// Position management: open positions with scale-out targets, break-even and
// trailing stops, partial closes and an undoable event ledger. The targets
// ladder, event timeline and management summary are shared with the journal.

import { api } from "../api.js";
import { clear, confirmDialog, field, h, icon, modal, numberOrNull, select, toast, withNodes } from "../dom.js";
import { fmt, pnlClass } from "../format.js";
import { N_, t } from "../i18n.js";
import { loadStatus, marketBySymbol, onStatus, state } from "../state.js";
import { openTradeForm } from "./trade-form.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const QUICK_SPLITS = [25, 50, 75];

// t("Close{_}", { _: "" }) shows the same English as t("Close") but has its own entry in the catalogs:
// a word with two meanings (the button "Close" / the column "Close" = share closed) gets two translations.

const STOP_KINDS = {
  initial: { label: N_("Initial"), line: N_("Initial stop"), cls: "" },
  breakeven: { label: N_("Break-even"), line: N_("Break-even stop"), cls: "good" },
  trail: { label: N_("Trailing"), line: N_("Trailing stop"), cls: "open" },
  manual: { label: N_("Manual"), line: N_("Manual stop"), cls: "warn" },
};
const STOP_KIND_BY_EVENT = { breakeven: "breakeven", trail: "trail", stop_move: "manual" };

// {level} is the target number (TP1..TP4).
const EVENT_KINDS = {
  tp: { icon: "target", label: N_("TP{level} hit") },
  partial: { icon: "scissors", label: N_("Partial close") },
  stop: { icon: "xCircle", label: N_("Stopped out") },
  close: { icon: "checkCircle", label: N_("Position closed") },
  breakeven: { icon: "shield", label: N_("Stop to break-even") },
  trail: { icon: "trending", label: N_("Trailing stop moved") },
  stop_move: { icon: "edit", label: N_("Stop moved") },
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
  return h("span", { class: `badge ${spec.cls}` }, t(spec.label));
}

export function eventLabel(event) {
  const spec = EVENT_KINDS[event.kind];
  return spec ? t(spec.label, { level: event.target_level }) : event.kind;
}

/** The English label, as the server stores it in the default event notes. */
function englishEventLabel(event) {
  const spec = EVENT_KINDS[event.kind];
  return spec ? spec.label.replace("{level}", event.target_level) : event.kind;
}

function riskFreeBadge() {
  return h("span", { class: "badge good pm-badge" }, icon("shield"), t("Risk-free"));
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
  if (!targets.length) return h("p", { class: "muted small" }, t("No take profit set."));
  const next = nextTargetLevel(trade);
  return h(
    "div",
    { class: "pm-ladder", role: "table", "aria-label": t("Take-profit targets") },
    h(
      "div",
      { class: "pm-ladder-row head", role: "row" },
      [t("Target"), t("Price"), t("Close{_}", { _: "" }), t("R:R"), t("Status")].map((label) => h("span", { role: "columnheader" }, label)),
    ),
    targets.map((target) => {
      const isNext = target.level === next;
      let status;
      if (target.hit) status = h("span", { class: "pos pm-status" }, icon("check"), t("Hit"));
      else if (isNext) status = h("span", { class: "badge open" }, t("Next"));
      else status = h("span", { class: "muted" }, trade.status === "open" ? t("Pending") : trade.managed ? t("Not reached") : "—");
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
  let be = t("Break-even off");
  if (trade.be_enabled) {
    be = trade.be_offset_pips ? t("Break-even at TP1 +{pips} pips", { pips: fmt.num(trade.be_offset_pips, 1) }) : t("Break-even at TP1");
    if (beDone) be = t("{setting} · done", { setting: be });
  }
  let trail = t("Trailing off");
  if (trade.trail_enabled) {
    const pips = fmt.num(trade.trail_distance_pips, 1);
    trail = trade.trail_trigger === "immediate" ? t("Trailing {pips} pips from entry", { pips }) : t("Trailing {pips} pips after TP1", { pips });
    if (trailing) trail = t("{setting} · active", { setting: trail });
  }
  return h(
    "div",
    { class: "pm-manage" },
    h("span", { class: `chip${trade.be_enabled ? "" : " off"}` }, icon("shield"), be),
    h("span", { class: `chip${trade.trail_enabled ? "" : " off"}` }, icon("trending"), trail),
  );
}

/** A note the server wrote itself (stored in English), which the event label already says. */
function defaultNote(event) {
  return [`TP${event.target_level}`, "Closed", "Partial close", englishEventLabel(event), eventLabel(event)].includes(event.note);
}

/** Compact ledger: time, kind, price, lots, signed P&L, stop before -> after. */
export function eventTimeline(trade) {
  const events = orderedEvents(trade);
  if (!events.length) return h("p", { class: "muted small" }, t("No position events yet."));
  return h(
    "ol",
    { class: "pm-timeline" },
    events.map((event) => {
      const kind = EVENT_KINDS[event.kind] || { icon: "info" };
      const details = [
        event.price !== null ? h("span", { class: "num" }, `@ ${fmt.price(event.price)}`) : null,
        event.lots ? h("span", { class: "num" }, t("{lots} lots", { lots: fmt.lots(event.lots) })) : null,
        event.lots && event.pnl !== null
          ? h("span", { class: `num ${pnlClass(event.pnl)}` }, fmt.signedMoney(event.pnl), event.pnl_manual ? ` (${t("manual")})` : "")
          : null,
        event.stop_after !== null
          ? h("span", { class: "num" }, t("Stop {before} → {after}", { before: fmt.price(event.stop_before), after: fmt.price(event.stop_after) }))
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
    { name: moved ? t("Initial SL") : t("Stop loss"), price: trade.stop_loss, cls: moved ? "sl old" : "sl" },
    { name: t("Entry"), price: trade.entry_price, cls: "entry" },
    moved ? { name: t(STOP_KINDS[kind].line), price: stop, cls: trade.risk_free ? "stop safe" : "stop" } : null,
    ...planTargets(trade).map((target) => ({
      name: target.hit ? t("TP{level} hit", { level: target.level }) : `TP${target.level}`,
      price: target.price,
      cls: target.hit ? "tp hit" : "tp",
    })),
    trade.last_price !== null && trade.last_price !== undefined ? { name: t("Last"), price: trade.last_price, cls: "last" } : null,
  ].filter(Boolean);

  const prices = levels.map((level) => level.price);
  const low = Math.min(...prices);
  const high = Math.max(...prices);
  const span = high - low || Math.abs(high) * 0.001 || 1;
  const y = (price) => pad + ((high - price) / span) * (height - 2 * pad);
  for (const level of levels) level.y = y(level.price);
  spread(levels, 15, pad, height - pad);

  const summary = levels.map((level) => `${level.name} ${fmt.price(level.price)}`).join(", ");
  const root = svg("svg", { class: "pm-svg", viewBox: `0 0 ${width} ${height}`, width, height, role: "img", "aria-label": t("Price ladder: {summary}", { summary }) });
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
const noteInput = () => h("input", { maxlength: "200", placeholder: t("optional") });
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
  const ref = modal({ title, body: form, footer: [h("button", { class: "btn ghost", type: "button", onclick: () => ref.close() }, t("Cancel")), button] });

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
  return field(t("Fill P&L ({currency})", { currency: state.plan.settings.currency }), input, { help: t("Optional - leave blank to calculate from the price") });
}

function openPriceUpdate(trade, onDone) {
  const price = numberInput(null, { placeholder: trade.last_price ? fmt.price(trade.last_price) : t("price") });
  const high = numberInput(null, { placeholder: t("optional") });
  const low = numberInput(null, { placeholder: t("optional") });
  const time = timeInput();
  actionModal({
    title: t("Update price · {symbol}", { symbol: trade.symbol }),
    intro: t("Enter the current price, and the high and low since your last update if price moved further. Targets, stop-outs, break-even and trailing moves are applied automatically."),
    fields: [
      field(t("Last price"), price),
      field(t("High{_}", { _: "" }), high),
      field(t("Low{_}", { _: "" }), low),
      field(t("Time"), time, { help: t("Leave empty for now") }),
    ],
    submitText: t("Apply price"),
    onDone,
    submit: async () => {
      const body = { price: numberOrNull(price), high: numberOrNull(high), low: numberOrNull(low), time: optional(time) };
      if (body.price === null && (body.high === null || body.low === null)) throw new Error(t("Enter the last price, or both the high and the low."));
      const result = await api.post(`/api/trades/${trade.id}/price`, body);
      const text = describeEvents(result.events);
      return text
        ? { message: `${trade.symbol}: ${text}` }
        : { message: t("{symbol} price recorded - no target or stop reached", { symbol: trade.symbol }), kind: "info" };
    },
  });
}

function openPartialClose(trade, onDone) {
  const remaining = trade.remaining_size ?? trade.position_size;
  const step = lotStep(trade.symbol);
  const lots = numberInput(null, { placeholder: t("lots"), min: "0" });
  const price = numberInput(trade.last_price);
  const time = timeInput();
  const pnl = numberInput(null, { placeholder: t("auto") });
  const note = noteInput();
  const quick = h(
    "div",
    { class: "row pm-quick-split" },
    QUICK_SPLITS.map((pct) => h("button", { type: "button", class: "btn sm", onclick: () => (lots.value = String(floorToStep((remaining * pct) / 100, step))) }, `${pct}%`)),
  );
  actionModal({
    title: t("Partial close · {symbol}", { symbol: trade.symbol }),
    intro: t("{remaining} of {total} lots are open. Closing all of them records a full close.", { remaining: fmt.lots(remaining), total: fmt.lots(trade.position_size) }),
    fields: [
      field(t("Lots to close"), h("div", { class: "stack pm-lots-field" }, lots, quick), { help: t("Rounded down to the {step} lot step", { step: fmt.lots(step) }) }),
      field(t("Close price"), price),
      field(t("Time"), time, { help: t("Leave empty for now") }),
      pnlField(pnl),
      field(t("Note"), note, { full: true }),
    ],
    submitText: t("Close lots"),
    onDone,
    submit: async () => {
      const body = { lots: numberOrNull(lots), price: numberOrNull(price), time: optional(time), pnl: numberOrNull(pnl), note: note.value.trim() };
      if (!body.lots || !body.price) throw new Error(t("Enter the lots to close and the close price."));
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
  const introParams = {
    price: fmt.price(current),
    kind: t(STOP_KINDS[stopKindOf(trade)].label).toLowerCase(),
    last: trade.last_price ? ` (${fmt.price(trade.last_price)})` : "",
  };
  actionModal({
    title: t("Move stop · {symbol}", { symbol: trade.symbol }),
    intro:
      trade.direction === "long"
        ? t("Current stop {price} ({kind}). A long stop must stay below the last price{last}.", introParams)
        : t("Current stop {price} ({kind}). A short stop must stay above the last price{last}.", introParams),
    fields: [
      field(
        t("New stop"),
        h(
          "div",
          { class: "stack pm-lots-field" },
          stop,
          h(
            "div",
            { class: "row" },
            h("button", { type: "button", class: "btn sm", onclick: () => (stop.value = String(be)) }, icon("shield"), t("Move to break-even ({price})", { price: fmt.price(be) })),
          ),
        ),
      ),
      field(t("Time"), time, { help: t("Leave empty for now") }),
      field(t("Note"), note, { full: true }),
    ],
    submitText: t("Move stop"),
    onDone,
    submit: async () => {
      const value = numberOrNull(stop);
      if (!value) throw new Error(t("Enter the new stop price."));
      await api.post(`/api/trades/${trade.id}/move-stop`, { stop: value, time: optional(time), note: note.value.trim() });
      return { message: t("{symbol}: stop moved to {price}", { symbol: trade.symbol, price: fmt.price(value) }) };
    },
  });
}

function openCloseAll(trade, onDone) {
  const price = numberInput(trade.last_price);
  const time = timeInput();
  const pnl = numberInput(null, { placeholder: t("auto") });
  const note = noteInput();
  actionModal({
    title: t("Close position · {symbol}", { symbol: trade.symbol }),
    intro: t("Closes the remaining {lots} lots. Fees ({fees}) are deducted from the net P&L once the position is closed.", {
      lots: fmt.lots(trade.remaining_size ?? trade.position_size),
      fees: fmt.money(trade.fees),
    }),
    fields: [field(t("Close price"), price), field(t("Time"), time, { help: t("Leave empty for now") }), pnlField(pnl), field(t("Note"), note, { full: true })],
    submitText: t("Close position"),
    danger: true,
    onDone,
    submit: async () => {
      const value = numberOrNull(price);
      if (!value) throw new Error(t("Enter the close price."));
      const result = await api.post(`/api/trades/${trade.id}/close`, { price: value, time: optional(time), pnl: numberOrNull(pnl), note: note.value.trim() });
      const net = result.trade.pnl;
      const message = net === null ? t("{symbol} closed", { symbol: trade.symbol }) : t("{symbol} closed · net {pnl}", { symbol: trade.symbol, pnl: fmt.signedMoney(net) });
      return { message, kind: net !== null && net < 0 ? "warn" : "success" };
    },
  });
}

async function undoLast(trade, onDone) {
  const last = orderedEvents(trade).at(-1);
  if (!last) return;
  const effect = last.lots
    ? t("The {lots} closed lots return to the position.", { lots: fmt.lots(last.lots) })
    : last.stop_before !== null
      ? t("The stop returns to {price}.", { price: fmt.price(last.stop_before) })
      : "";
  const question = t('Undo "{event}" recorded {time}? {effect}', { event: eventLabel(last), time: fmt.datetime(last.time), effect });
  const ok = await confirmDialog(question, { title: t("Undo last event"), confirmText: t("Undo") });
  if (!ok) return;
  try {
    await api.del(`/api/trades/${trade.id}/events/${last.id}`);
    toast(t('{symbol}: "{event}" undone', { symbol: trade.symbol, event: eventLabel(last) }), "success");
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
    ? [h("span", { class: "num" }, t("{remaining} / {total} lots", { remaining: fmt.lots(remaining), total: fmt.lots(original) }))]
    : [h("span", { class: "muted" }, t("No size set"))];
  const lotsExtra = sized
    ? [
        h("div", { class: `progress pm-progress${closedPct >= 99.99 ? " done" : ""}`, role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(Math.round(closedPct)), "aria-label": t("Share of the position closed") }, h("span", { style: { width: `${closedPct}%` } })),
        h("div", { class: "muted small" }, t("{pct}% closed", { pct: fmt.num(closedPct, 0) })),
      ]
    : h("div", { class: "muted small" }, t("Edit the trade to add lots"));

  const unrealized =
    p.unrealized_pnl === null
      ? h("span", { class: "muted" }, "—")
      : h("span", { class: `num ${pnlClass(p.unrealized_pnl)}` }, fmt.signedMoney(p.unrealized_pnl));

  const facts = h(
    "div",
    { class: "pm-facts" },
    fact(t("Entry"), h("span", { class: "num" }, fmt.price(p.entry_price))),
    fact(t("Stop"), h("span", { class: "row pm-inline" }, h("span", { class: "num" }, fmt.price(stop)), stopKindBadge(p.stop_kind))),
    fact(t("Remaining"), lotsValue, lotsExtra),
    fact(t("Realised (gross)"), h("span", { class: `num ${pnlClass(p.realized_pnl)}` }, fmt.signedMoney(p.realized_pnl))),
    fact(
      t("Unrealized"),
      unrealized,
      h(
        "div",
        { class: "muted small" },
        p.last_price === null ? t("Update the price to see it") : t("at {price} · {time}", { price: fmt.price(p.last_price), time: fmt.time(p.last_price_at) }),
      ),
    ),
    fact(t("Remaining risk"), p.risk_free ? riskFreeBadge() : h("span", { class: "num" }, risk === null ? "—" : fmt.money(risk))),
  );

  const events = p.events || [];
  const actions = h(
    "div",
    { class: "pm-actions" },
    h("button", { class: "btn sm primary", type: "button", disabled: !sized, onclick: () => openPriceUpdate(p, refresh) }, icon("refresh"), t("Update price")),
    h("button", { class: "btn sm", type: "button", disabled: !sized, onclick: () => openPartialClose(p, refresh) }, icon("scissors"), t("Partial close")),
    h("button", { class: "btn sm", type: "button", onclick: () => openMoveStop(p, refresh) }, icon("shield"), t("Move stop")),
    h("button", { class: "btn sm", type: "button", disabled: !sized, onclick: () => openCloseAll(p, refresh) }, icon("x"), t("Close all")),
    h("span", { class: "spacer" }),
    h("button", { class: "btn sm ghost", type: "button", disabled: !events.length, title: events.length ? t("Undo the most recent event") : t("No events to undo"), onclick: () => undoLast(p, refresh) }, icon("undo"), t("Undo last")),
    h("button", { class: "btn sm ghost", type: "button", onclick: () => openTradeForm({ trade: p, onSaved: () => refresh() }) }, icon("edit"), t("Edit")),
  );

  return h(
    "article",
    {
      class: "card pm-card",
      "aria-label": p.direction === "long" ? t("{symbol} long position", { symbol: p.symbol }) : t("{symbol} short position", { symbol: p.symbol }),
    },
    h(
      "div",
      { class: "pm-head" },
      h(
        "div",
        { class: "pm-title" },
        h("h2", {}, p.symbol),
        h("span", { class: `badge ${p.direction}` }, p.direction === "long" ? t("Long") : t("Short")),
        p.source === "signal" ? h("span", { class: "badge open pm-badge" }, icon("signal"), t("Signal")) : null,
        p.risk_free ? riskFreeBadge() : null,
      ),
      h("div", { class: "muted small" }, [p.strategy || t("No strategy"), t("opened {time}", { time: fmt.datetime(p.entry_time) }), `#${p.id}`].join(" · ")),
      next
        ? h(
            "div",
            { class: "chip pm-next" },
            icon("target"),
            withNodes(t("Next: {target}"), { target: h("strong", {}, `TP${next.level} ${fmt.price(next.price)}`) }),
            ` · ${fmt.num(next.close_pct, 0)}%`,
          )
        : h("div", { class: "chip pm-next" }, icon("check"), t("No open target")),
    ),
    h("div", { class: "pm-split" }, h("div", { class: "stack pm-main" }, facts, managementSummary(p)), h("div", { class: "pm-chart" }, priceLadder(p))),
    h("div", {}, h("div", { class: "section-title" }, t("Targets")), targetsLadder(p)),
    actions,
    h(
      "div",
      {},
      h("div", { class: "section-title" }, t("Events ({count})", { count: events.length })),
      events.length ? eventTimeline(p) : h("p", { class: "muted small" }, t("No events yet - update the price as the market moves.")),
    ),
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
      kpi(t("Open positions"), String(totals.open_positions), t("{n} symbol(s)", { n: new Set(data.positions.map((p) => p.symbol)).size })),
      kpi(
        t("Remaining risk"),
        fmt.money(totals.remaining_risk),
        equity ? t("{pct} of equity", { pct: fmt.pct((totals.remaining_risk / equity) * 100, 2) }) : null,
      ),
      kpi(
        t("Unrealized P&L"),
        fmt.signedMoney(totals.unrealized_pnl),
        totals.unrealized_pnl === null ? t("Update prices to see it") : t("At last prices, before fees"),
        pnlClass(totals.unrealized_pnl),
      ),
      kpi(t("Realised today"), fmt.signedMoney(today), t("{amount} banked on open positions", { amount: fmt.signedMoney(totals.realized_open) }), pnlClass(today)),
      kpi(t("Risk-free"), h("span", { class: "row pm-inline" }, icon("shield"), `${totals.risk_free_count} / ${totals.open_positions}`), t("Stop at or beyond entry")),
    );
  }

  function drawQuick() {
    const symbols = [...new Set(data.positions.map((p) => p.symbol))].sort();
    if (!symbols.length) {
      clear(quickBox);
      return;
    }
    const symbol = select(symbols, symbols[0], { "aria-label": t("Symbol") });
    const price = numberInput(null, { placeholder: t("price"), "aria-label": t("Last price") });
    const high = numberInput(null, { placeholder: t("optional"), "aria-label": t("High{_}", { _: "" }) });
    const low = numberInput(null, { placeholder: t("optional"), "aria-label": t("Low{_}", { _: "" }) });
    const apply = h("button", { class: "btn primary", type: "submit" }, icon("refresh"), t("Apply price"));
    const submit = async (event) => {
      event.preventDefault();
      const body = { symbol: symbol.value, price: numberOrNull(price), high: numberOrNull(high), low: numberOrNull(low) };
      if (body.price === null && (body.high === null || body.low === null)) {
        toast(t("Enter the last price, or both the high and the low"), "warn");
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
          text
            ? `${body.symbol}: ${text}`
            : t("{symbol} price recorded on {n} position(s) - no target or stop reached", { symbol: body.symbol, n: applied.length }),
          text ? "success" : "info",
        );
        for (const skipped of result.results.filter((r) => r.skipped)) {
          toast(t("Trade #{id} skipped: {reason}", { id: skipped.trade_id, reason: skipped.skipped }), "warn");
        }
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
        h(
          "div",
          { class: "pm-quick-head" },
          h("h3", {}, icon("zap"), t("Quick price update")),
          h("p", { class: "muted small" }, t("Applies one price to every open position of the symbol.")),
        ),
        h(
          "div",
          { class: "pm-quick-fields" },
          field(t("Symbol"), symbol),
          field(t("Last"), price),
          field(t("High{_}", { _: "" }), high),
          field(t("Low{_}", { _: "" }), low),
          apply,
        ),
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
          h("strong", {}, t("No open positions")),
          h("p", {}, t("Take a trade from a signal or log one in the journal - its targets, stops and partial closes are managed here.")),
          h(
            "div",
            { class: "row pm-empty-actions" },
            h("a", { class: "btn primary", href: "#/signals" }, icon("signal"), t("Paste a signal")),
            h("a", { class: "btn", href: "#/journal" }, icon("journal"), t("Open the journal")),
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
      clear(listBox, h("div", { class: "card empty" }, h("strong", {}, t("Positions could not be loaded")), error.message));
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
      h(
        "div",
        {},
        h("h1", {}, t("Positions")),
        h("p", {}, t("Scale out at your targets, protect profits with break-even and trailing stops, and keep every fill on record.")),
      ),
      h("div", { class: "row" }, h("button", { class: "btn", type: "button", onclick: () => refresh() }, icon("refresh"), t("Refresh"))),
    ),
    totalsBox,
    quickBox,
    listBox,
  );

  await load();
  return onStatus(() => drawTotals());
}
