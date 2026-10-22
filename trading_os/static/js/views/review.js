// Post-market review: rate the day's discipline, name the mistakes and write the lessons
// down. The day's P&L is what was booked that day (partial exits on their fill day); the trade
// counts come from the trades closed that day. Edits save automatically.

import { api } from "../api.js";
import { clear, h, icon, toast } from "../dom.js";
import { currentLocale, fmt, pnlClass, toInputDate } from "../format.js";
import { N_, t, tn } from "../i18n.js";
import { loadStatus, state } from "../state.js";

// Label tables hold N_() texts; they are translated with t() where shown.
const RATING_LABELS = [N_("Poor"), N_("Weak"), N_("Average"), N_("Good"), N_("Excellent")];
const SAVE_DELAY_MS = 800;
const HISTORY_DAYS = 30;
const DAY_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const PROMPTS = [
  ["went_well", N_("What went well"), N_("Setups you executed cleanly, rules you respected, good patience…")],
  ["went_wrong", N_("What went wrong"), N_("Where you deviated from the plan, and what triggered it…")],
  ["lessons", N_("Lessons"), N_("What you will do differently next time…")],
  ["tomorrow_focus", N_("Focus for tomorrow"), N_("One or two concrete intentions for the next session…")],
];

// Built when first needed and rebuilt when the interface language changes (never at module load).
let rowDateFormat = null;
let rowDateLocale;
function formatRowDate(date) {
  const locale = currentLocale();
  if (!rowDateFormat || rowDateLocale !== locale) {
    rowDateFormat = new Intl.DateTimeFormat(locale, { weekday: "short", day: "numeric", month: "short" });
    rowDateLocale = locale;
  }
  return rowDateFormat.format(date);
}

function shiftDay(iso, days) {
  const [y, m, d] = iso.split("-").map(Number);
  return toInputDate(new Date(y, m - 1, d + days));
}

function parseDay(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

function kpi(label, value, sub, valueClass = "") {
  return h("div", { class: "kpi" }, h("div", { class: "label" }, label), h("div", { class: `value num ${valueClass}` }, value), sub ? h("div", { class: "sub" }, sub) : null);
}

function pageHead(day, today, trailing) {
  return h(
    "div",
    { class: "page-head" },
    h("div", {}, h("h1", {}, t("Post-market review")), h("p", {}, fmt.longDate(day))),
    h(
      "div",
      { class: "row" },
      trailing,
      h("a", { class: "btn ghost icon-only", href: `#/review?date=${shiftDay(day, -1)}`, "aria-label": t("Previous day"), title: t("Previous day") }, icon("chevronLeft")),
      day < today ? h("a", { class: "btn ghost icon-only", href: `#/review?date=${shiftDay(day, 1)}`, "aria-label": t("Next day"), title: t("Next day") }, icon("chevronRight")) : null,
      day === today ? null : h("a", { class: "btn sm", href: "#/review" }, t("Today")),
    ),
  );
}

// ------------------------------------------------------------------ controls

/** Five star buttons; arrow keys, Home and End change the rating. Choosing the current rating clears it. */
function ratingControl(value, onChange) {
  let current = value;
  const label = h("span", { class: "rating-label" });
  const buttons = RATING_LABELS.map((rating, index) =>
    h(
      "button",
      {
        type: "button",
        class: "star",
        "aria-label": t("{n} of 5 - {label}", { n: index + 1, label: t(rating) }),
        title: `${index + 1} - ${t(rating)}`,
        onclick: () => set(current === index + 1 ? null : index + 1, true),
        onkeydown: (event) => {
          const moves = { ArrowRight: 1, ArrowUp: 1, ArrowLeft: -1, ArrowDown: -1 };
          let next = null;
          if (event.key in moves) next = Math.min(5, Math.max(1, (current || 0) + moves[event.key]));
          else if (event.key === "Home") next = 1;
          else if (event.key === "End") next = 5;
          if (next === null) return;
          event.preventDefault();
          set(next, true);
          buttons[next - 1].focus();
        },
      },
      icon("star"),
    ),
  );

  function set(next, notify) {
    current = next;
    buttons.forEach((button, index) => {
      button.classList.toggle("on", current !== null && index < current);
      button.setAttribute("aria-pressed", String(current === index + 1));
    });
    label.textContent = current ? `${current}/5 · ${t(RATING_LABELS[current - 1])}` : t("Not rated");
    if (notify) onChange(current);
  }

  set(value, false);
  return h("div", { class: "rating" }, h("div", { class: "stars", role: "group", "aria-label": t("Discipline rating, 1 to 5 stars") }, buttons), label);
}

/** Toggle chips. ``multiple`` keeps a set; otherwise one value (or none: picking it again clears it). */
function chipGroup(options, selected, { multiple = false, label, onChange }) {
  const picked = new Set(selected);
  const chips = options.map(({ value, text, extra, iconName }) => {
    const chip = h("button", { type: "button", class: "chip-toggle", "aria-pressed": "false", dataset: { value } }, iconName ? icon(iconName) : null, text, extra ? h("span", { class: "chip-count" }, extra) : null);
    chip.addEventListener("click", () => {
      if (picked.has(value)) picked.delete(value);
      else {
        if (!multiple) picked.clear();
        picked.add(value);
      }
      paint();
      onChange(multiple ? [...picked] : ([...picked][0] ?? null));
    });
    return chip;
  });
  function paint() {
    for (const chip of chips) {
      const on = picked.has(chip.dataset.value);
      chip.classList.toggle("on", on);
      chip.setAttribute("aria-pressed", String(on));
    }
  }
  paint();
  return h("div", { class: "chips", role: "group", "aria-label": label }, chips);
}

function block(title, help, control) {
  return h("div", { class: "review-block" }, h("div", { class: "review-label" }, h("h3", {}, title), help ? h("span", { class: "muted small" }, help) : null), control);
}

// ------------------------------------------------------------------- history

function miniStars(rating) {
  return h(
    "span",
    { class: "mini-stars", role: "img", "aria-label": t("Discipline {rating} of 5", { rating }) },
    [1, 2, 3, 4, 5].map((n) => h("span", { class: n <= rating ? "on" : "" }, icon("star"))),
  );
}

function historyRow(row, current) {
  const planIcon = row.followed_plan === null ? null : h("span", { class: row.followed_plan ? "pos" : "neg", title: row.followed_plan ? t("Followed the plan") : t("Broke the plan"), role: "img", "aria-label": row.followed_plan ? t("Followed the plan") : t("Broke the plan") }, icon(row.followed_plan ? "check" : "x"));
  return h(
    "a",
    { class: `review-row${row.day === current ? " current" : ""}`, href: `#/review?date=${row.day}`, "aria-current": row.day === current ? "page" : null },
    h("span", { class: "review-row-date" }, formatRowDate(parseDay(row.day))),
    h(
      "span",
      { class: "review-row-state" },
      row.exists
        ? [row.discipline_rating ? miniStars(row.discipline_rating) : h("span", { class: "muted small" }, t("Reviewed")), planIcon, row.mood ? h("span", { class: "muted small" }, row.mood) : null]
        : row.trades || row.partials
          ? h("span", { class: "badge warn" }, t("To review"))
          : h("span", { class: "muted small" }, t("Not reviewed")),
    ),
    h(
      "span",
      { class: "review-row-pnl" },
      row.trades || row.partials
        ? [h("span", { class: `num ${pnlClass(row.pnl)}` }, fmt.signedMoney(row.pnl)), h("span", { class: "muted small" }, row.trades ? tn(row.trades, "{n} trade", "{n} trades") : tn(row.partials, "{n} partial exit", "{n} partial exits"))]
        : h("span", { class: "muted small" }, t("No trades")),
    ),
  );
}

function historyCard(rows, current) {
  const reviewed = rows.filter((r) => r.exists).length;
  const missing = rows.filter((r) => !r.exists && (r.trades || r.partials)).length;
  return h(
    "div",
    { class: "card review-history" },
    h("div", { class: "card-head" }, h("div", {}, h("h2", {}, t("Last {days} days", { days: HISTORY_DAYS })), h("p", {}, `${t("{n} reviewed", { n: reviewed })}${missing ? ` · ${tn(missing, "{n} trading day to review", "{n} trading days to review")}` : ""}`))),
    h("div", { class: "review-list" }, rows.map((row) => historyRow(row, current))),
  );
}

// -------------------------------------------------------------------- render

function summaryStrip(stats) {
  const mistakeCount = stats.mistakes.reduce((sum, m) => sum + m.count, 0);
  const tally = (items) => items.map((m) => (m.count > 1 ? `${m.name} ×${m.count}` : m.name)).join(" · ");
  const partials = stats.partials ? ` · ${tn(stats.partials, "{n} partial exit", "{n} partial exits")}` : "";
  return [
    h(
      "div",
      { class: "kpis" },
      kpi(t("Net P&L"), fmt.signedMoney(stats.net_pnl), (stats.trades ? tn(stats.trades, "{n} trade closed", "{n} trades closed") : t("No trades closed")) + partials, pnlClass(stats.net_pnl)),
      kpi(t("Trades"), stats.trades, t("{wins}W · {losses}L · {opened} opened", { wins: stats.wins, losses: stats.losses, opened: stats.opened })),
      kpi(t("Win rate"), fmt.pct(stats.win_rate), t("Wins / closed trades")),
      kpi(t("Rule compliance"), fmt.pct(stats.compliance_rate, 0), t("Of rated trades")),
      kpi(t("Mistakes on trades"), mistakeCount, mistakeCount ? tally(stats.mistakes) : t("None tagged"), mistakeCount ? "neg" : ""),
    ),
    stats.emotions.length ? h("p", { class: "muted small" }, t("Emotions logged on trades: {list}", { list: tally(stats.emotions) })) : null,
  ];
}

export async function render(root, { params }) {
  const today = state.status.today;
  const requested = params.get("date");
  const day = requested && DAY_PATTERN.test(requested) ? requested : today;

  if (day > today) {
    clear(
      root,
      pageHead(day, today, null),
      h("div", { class: "card empty" }, h("strong", {}, t("This day hasn't happened yet")), t("Reviews are written after the session, for today or a past day."), h("div", { style: { marginTop: "12px" } }, h("a", { class: "btn primary", href: "#/review" }, t("Review today")))),
    );
    return undefined;
  }

  const [record, history] = await Promise.all([api.get(`/api/reviews/${day}`), api.get(`/api/reviews?days=${HISTORY_DAYS}`)]);
  const stats = record.stats;
  const tradeMistakes = new Map(stats.mistakes.map((m) => [m.name, m.count]));
  const values = {
    discipline_rating: record.discipline_rating,
    mood: record.mood,
    followed_plan: record.followed_plan,
    mistakes: record.exists ? [...record.mistakes] : [...tradeMistakes.keys()],
    went_well: record.went_well,
    went_wrong: record.went_wrong,
    lessons: record.lessons,
    tomorrow_focus: record.tomorrow_focus,
  };

  // ---------------------------------------------------------------- autosave
  const saveState = h("span", { class: "save-state", role: "status", "aria-live": "polite" });
  let dirty = false;
  let timer = null;
  let chain = Promise.resolve();

  function showState(kind) {
    const views = {
      draft: [null, t("Not saved yet · changes save automatically")],
      saved: ["checkCircle", t("Saved")],
      saving: ["clock", t("Saving…")],
      error: ["alert", t("Not saved")],
    };
    const [iconName, text] = views[kind];
    saveState.className = `save-state ${kind}`;
    clear(saveState, iconName ? icon(iconName) : null, text, kind === "error" ? h("button", { type: "button", class: "btn ghost sm", onclick: () => flush() }, t("Retry")) : null);
  }

  async function send() {
    showState("saving");
    try {
      await api.put(`/api/reviews/${day}`, values);
      if (!dirty) showState("saved");
      await loadStatus();
    } catch (error) {
      dirty = true; // keep the edits pending: Retry, the next change or leaving the page sends them again
      showState("error");
      toast(error.message, "error");
    }
  }

  function flush() {
    clearTimeout(timer);
    timer = null;
    if (!dirty) return chain;
    dirty = false;
    chain = chain.then(send);
    return chain;
  }

  function changed(key, value) {
    values[key] = value;
    dirty = true;
    showState("saving");
    clearTimeout(timer);
    timer = setTimeout(flush, SAVE_DELAY_MS);
  }

  const onUnload = (event) => {
    if (!dirty) return;
    flush();
    event.preventDefault();
    event.returnValue = "";
  };
  window.addEventListener("beforeunload", onUnload);

  // -------------------------------------------------------------------- form
  const mistakeNames = [...new Set([...state.plan.mistake_types.filter((m) => m.active).map((m) => m.name), ...tradeMistakes.keys(), ...values.mistakes])];
  const preselected = !record.exists && tradeMistakes.size > 0;

  const form = h(
    "div",
    { class: "card review-form" },
    h("div", { class: "card-head" }, h("div", {}, h("h2", {}, t("Reflection")), h("p", {}, t("Be honest - this is for you, not for the scoreboard"))), saveState),
    h(
      "div",
      { class: "review-pair" },
      block(t("Discipline"), t("How well did you follow your process?"), ratingControl(values.discipline_rating, (value) => changed("discipline_rating", value))),
      block(
        t("Followed your plan?"),
        null,
        chipGroup(
          [
            { value: "yes", text: t("Yes"), iconName: "checkCircle" },
            { value: "no", text: t("No"), iconName: "xCircle" },
          ],
          values.followed_plan === null ? [] : [values.followed_plan ? "yes" : "no"],
          { label: t("Followed your plan"), onChange: (value) => changed("followed_plan", value === null ? null : value === "yes") },
        ),
      ),
    ),
    block(
      t("Mood"),
      t("Your state of mind at the end of the session"),
      chipGroup(
        state.plan.enums.moods.map((mood) => ({ value: mood, text: mood })),
        values.mood ? [values.mood] : [],
        { label: t("Mood"), onChange: (value) => changed("mood", value ?? "") },
      ),
    ),
    block(
      t("Mistakes"),
      preselected ? t("Pre-selected from the mistakes tagged on this day's trades") : t("Counts show how often a mistake was tagged on this day's trades"),
      mistakeNames.length
        ? chipGroup(
            mistakeNames.map((name) => ({ value: name, text: name, extra: tradeMistakes.has(name) ? `×${tradeMistakes.get(name)}` : null })),
            values.mistakes,
            { multiple: true, label: t("Mistakes"), onChange: (value) => changed("mistakes", value) },
          )
        : h("p", { class: "muted small" }, t("Add mistake types in Trading Plan to tag them here.")),
    ),
    h(
      "div",
      { class: "review-notes" },
      PROMPTS.map(([key, label, placeholder]) => {
        const area = h("textarea", { rows: 4, maxlength: 10000, placeholder: t(placeholder), value: values[key], oninput: (event) => changed(key, event.target.value) });
        const id = `review-${key}`;
        area.id = id;
        return h("div", { class: "field" }, h("label", { for: id }, t(label)), area);
      }),
    ),
  );

  showState(record.exists ? "saved" : "draft");
  clear(
    root,
    pageHead(day, today, h("a", { class: "btn ghost sm", href: `#/calendar?month=${day.slice(0, 7)}` }, icon("calendar"), t("Calendar"))),
    h(
      "div",
      { class: "stack" },
      summaryStrip(stats),
      stats.trades ? null : h("p", { class: "muted small" }, t("No trades closed on this day. A review is still worth writing: missed setups, patience and rule-keeping count too.")),
      h("div", { class: "grid wide-left review-layout" }, form, historyCard(history, day)),
    ),
  );

  return () => {
    window.removeEventListener("beforeunload", onUnload);
    flush();
  };
}
