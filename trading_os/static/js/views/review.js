// Post-market review: rate the day's discipline, name the mistakes and write the lessons
// down. The day's numbers come from the trades closed that day; edits save automatically.

import { api } from "../api.js";
import { clear, h, icon, toast } from "../dom.js";
import { fmt, pnlClass, toInputDate } from "../format.js";
import { loadStatus, state } from "../state.js";

const RATING_LABELS = ["Poor", "Weak", "Average", "Good", "Excellent"];
const SAVE_DELAY_MS = 800;
const HISTORY_DAYS = 30;
const DAY_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const PROMPTS = [
  ["went_well", "What went well", "Setups you executed cleanly, rules you respected, good patience…"],
  ["went_wrong", "What went wrong", "Where you deviated from the plan, and what triggered it…"],
  ["lessons", "Lessons", "What you will do differently next time…"],
  ["tomorrow_focus", "Focus for tomorrow", "One or two concrete intentions for the next session…"],
];

const rowDateFormat = new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short" });

function shiftDay(iso, days) {
  const [y, m, d] = iso.split("-").map(Number);
  return toInputDate(new Date(y, m - 1, d + days));
}

function parseDay(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;

function kpi(label, value, sub, valueClass = "") {
  return h("div", { class: "kpi" }, h("div", { class: "label" }, label), h("div", { class: `value num ${valueClass}` }, value), sub ? h("div", { class: "sub" }, sub) : null);
}

function pageHead(day, today, trailing) {
  return h(
    "div",
    { class: "page-head" },
    h("div", {}, h("h1", {}, "Post-market review"), h("p", {}, fmt.longDate(day))),
    h(
      "div",
      { class: "row" },
      trailing,
      h("a", { class: "btn ghost icon-only", href: `#/review?date=${shiftDay(day, -1)}`, "aria-label": "Previous day", title: "Previous day" }, icon("chevronLeft")),
      day < today ? h("a", { class: "btn ghost icon-only", href: `#/review?date=${shiftDay(day, 1)}`, "aria-label": "Next day", title: "Next day" }, icon("chevronRight")) : null,
      day === today ? null : h("a", { class: "btn sm", href: "#/review" }, "Today"),
    ),
  );
}

// ------------------------------------------------------------------ controls

/** Five star buttons; arrow keys, Home and End change the rating. Choosing the current rating clears it. */
function ratingControl(value, onChange) {
  let current = value;
  const label = h("span", { class: "rating-label" });
  const buttons = RATING_LABELS.map((text, index) =>
    h(
      "button",
      {
        type: "button",
        class: "star",
        "aria-label": `${index + 1} of 5 - ${text}`,
        title: `${index + 1} - ${text}`,
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
    label.textContent = current ? `${current}/5 · ${RATING_LABELS[current - 1]}` : "Not rated";
    if (notify) onChange(current);
  }

  set(value, false);
  return h("div", { class: "rating" }, h("div", { class: "stars", role: "group", "aria-label": "Discipline rating, 1 to 5 stars" }, buttons), label);
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
    { class: "mini-stars", role: "img", "aria-label": `Discipline ${rating} of 5` },
    [1, 2, 3, 4, 5].map((n) => h("span", { class: n <= rating ? "on" : "" }, icon("star"))),
  );
}

function historyRow(row, current) {
  const planIcon = row.followed_plan === null ? null : h("span", { class: row.followed_plan ? "pos" : "neg", title: row.followed_plan ? "Followed the plan" : "Broke the plan", role: "img", "aria-label": row.followed_plan ? "Followed the plan" : "Broke the plan" }, icon(row.followed_plan ? "check" : "x"));
  return h(
    "a",
    { class: `review-row${row.day === current ? " current" : ""}`, href: `#/review?date=${row.day}`, "aria-current": row.day === current ? "page" : null },
    h("span", { class: "review-row-date" }, rowDateFormat.format(parseDay(row.day))),
    h(
      "span",
      { class: "review-row-state" },
      row.exists
        ? [row.discipline_rating ? miniStars(row.discipline_rating) : h("span", { class: "muted small" }, "Reviewed"), planIcon, row.mood ? h("span", { class: "muted small" }, row.mood) : null]
        : row.trades
          ? h("span", { class: "badge warn" }, "To review")
          : h("span", { class: "muted small" }, "Not reviewed"),
    ),
    h(
      "span",
      { class: "review-row-pnl" },
      row.trades ? [h("span", { class: `num ${pnlClass(row.pnl)}` }, fmt.signedMoney(row.pnl)), h("span", { class: "muted small" }, plural(row.trades, "trade"))] : h("span", { class: "muted small" }, "No trades"),
    ),
  );
}

function historyCard(rows, current) {
  const reviewed = rows.filter((r) => r.exists).length;
  const missing = rows.filter((r) => !r.exists && r.trades).length;
  return h(
    "div",
    { class: "card review-history" },
    h("div", { class: "card-head" }, h("div", {}, h("h2", {}, `Last ${HISTORY_DAYS} days`), h("p", {}, `${reviewed} reviewed${missing ? ` · ${missing} trading ${missing === 1 ? "day" : "days"} to review` : ""}`))),
    h("div", { class: "review-list" }, rows.map((row) => historyRow(row, current))),
  );
}

// -------------------------------------------------------------------- render

function summaryStrip(stats) {
  const mistakeCount = stats.mistakes.reduce((sum, m) => sum + m.count, 0);
  const tally = (items) => items.map((m) => (m.count > 1 ? `${m.name} ×${m.count}` : m.name)).join(" · ");
  return [
    h(
      "div",
      { class: "kpis" },
      kpi("Net P&L", fmt.signedMoney(stats.net_pnl), stats.trades ? `${plural(stats.trades, "trade")} closed` : "No trades closed", pnlClass(stats.net_pnl)),
      kpi("Trades", stats.trades, `${stats.wins}W · ${stats.losses}L · ${stats.opened} opened`),
      kpi("Win rate", fmt.pct(stats.win_rate), "Wins / closed trades"),
      kpi("Rule compliance", fmt.pct(stats.compliance_rate, 0), "Of rated trades"),
      kpi("Mistakes on trades", mistakeCount, mistakeCount ? tally(stats.mistakes) : "None tagged", mistakeCount ? "neg" : ""),
    ),
    stats.emotions.length ? h("p", { class: "muted small" }, `Emotions logged on trades: ${tally(stats.emotions)}`) : null,
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
      h("div", { class: "card empty" }, h("strong", {}, "This day hasn't happened yet"), "Reviews are written after the session, for today or a past day.", h("div", { style: { marginTop: "12px" } }, h("a", { class: "btn primary", href: "#/review" }, "Review today"))),
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
      draft: [null, "Not saved yet · changes save automatically"],
      saved: ["checkCircle", "Saved"],
      saving: ["clock", "Saving…"],
      error: ["alert", "Not saved"],
    };
    const [iconName, text] = views[kind];
    saveState.className = `save-state ${kind}`;
    clear(saveState, iconName ? icon(iconName) : null, text, kind === "error" ? h("button", { type: "button", class: "btn ghost sm", onclick: () => flush() }, "Retry") : null);
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
    h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Reflection"), h("p", {}, "Be honest - this is for you, not for the scoreboard")), saveState),
    h(
      "div",
      { class: "review-pair" },
      block("Discipline", "How well did you follow your process?", ratingControl(values.discipline_rating, (value) => changed("discipline_rating", value))),
      block(
        "Followed your plan?",
        null,
        chipGroup(
          [
            { value: "yes", text: "Yes", iconName: "checkCircle" },
            { value: "no", text: "No", iconName: "xCircle" },
          ],
          values.followed_plan === null ? [] : [values.followed_plan ? "yes" : "no"],
          { label: "Followed your plan", onChange: (value) => changed("followed_plan", value === null ? null : value === "yes") },
        ),
      ),
    ),
    block(
      "Mood",
      "Your state of mind at the end of the session",
      chipGroup(
        state.plan.enums.moods.map((mood) => ({ value: mood, text: mood })),
        values.mood ? [values.mood] : [],
        { label: "Mood", onChange: (value) => changed("mood", value ?? "") },
      ),
    ),
    block(
      "Mistakes",
      preselected ? "Pre-selected from the mistakes tagged on this day's trades" : "Counts show how often a mistake was tagged on this day's trades",
      mistakeNames.length
        ? chipGroup(
            mistakeNames.map((name) => ({ value: name, text: name, extra: tradeMistakes.has(name) ? `×${tradeMistakes.get(name)}` : null })),
            values.mistakes,
            { multiple: true, label: "Mistakes", onChange: (value) => changed("mistakes", value) },
          )
        : h("p", { class: "muted small" }, "Add mistake types in Trading Plan to tag them here."),
    ),
    h(
      "div",
      { class: "review-notes" },
      PROMPTS.map(([key, label, placeholder]) => {
        const area = h("textarea", { rows: 4, maxlength: 10000, placeholder, value: values[key], oninput: (event) => changed(key, event.target.value) });
        const id = `review-${key}`;
        area.id = id;
        return h("div", { class: "field" }, h("label", { for: id }, label), area);
      }),
    ),
  );

  showState(record.exists ? "saved" : "draft");
  clear(
    root,
    pageHead(day, today, h("a", { class: "btn ghost sm", href: `#/calendar?month=${day.slice(0, 7)}` }, icon("calendar"), "Calendar")),
    h(
      "div",
      { class: "stack" },
      summaryStrip(stats),
      stats.trades ? null : h("p", { class: "muted small" }, "No trades closed on this day. A review is still worth writing: missed setups, patience and rule-keeping count too."),
      h("div", { class: "grid wide-left review-layout" }, form, historyCard(history, day)),
    ),
  );

  return () => {
    window.removeEventListener("beforeunload", onUnload);
    flush();
  };
}
