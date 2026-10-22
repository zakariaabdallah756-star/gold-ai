// Pre-market routine: gated checklist, daily bias / levels / news, economic calendar, history.

import { api, query } from "../api.js";
import { clear, confirmDialog, debounce, field, h, icon, numberOrNull, segmented, select, toast, withNodes } from "../dom.js";
import { currentLocale, fmt, toInputDate, toInputDateTime } from "../format.js";
import { N_, t, tn } from "../i18n.js";
import { accountNow, loadStatus, state } from "../state.js";

const CALENDAR_DAYS = 7;
const IMPACTS = {
  high: { cls: "bad", icon: "alert", label: N_("High") },
  medium: { cls: "warn", icon: "zap", label: N_("Medium") },
  low: { cls: "", icon: "info", label: N_("Low") },
};
const IMPACT_SCOPE = { high: N_("high-impact releases"), medium: N_("medium- and high-impact releases"), low: N_("any release") };

function shiftDay(iso, days) {
  const [y, m, d] = iso.split("-").map(Number);
  return toInputDate(new Date(y, m - 1, d + days));
}

function parseDay(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

function impactBadge(impact) {
  const meta = IMPACTS[impact] || IMPACTS.low;
  return h("span", { class: `badge impact ${meta.cls}` }, icon(meta.icon), t(meta.label));
}

function guardrailNote() {
  const s = state.plan.settings;
  if (s.news_mode === "off") {
    return h(
      "div",
      { class: "news-note" },
      icon("info"),
      h(
        "span",
        {},
        withNodes(t("The news guardrail is off - events are for information only. Turn it on in {link}."), {
          link: h("a", { href: "#/plan?tab=rules" }, t("Trading Plan → Account & risk rules")),
        }),
      ),
    );
  }
  const action = s.news_mode === "block" ? t("Live entries are blocked") : t("Entries get a warning");
  const scope = s.news_match_currency ? t("in the traded market's currencies") : t("in any currency");
  const releases = IMPACT_SCOPE[s.news_min_impact] ? t(IMPACT_SCOPE[s.news_min_impact]) : undefined;
  return h(
    "div",
    { class: `news-note ${s.news_mode}` },
    icon("shield"),
    h(
      "span",
      {},
      t("{action} from {before} min before to {after} min after {releases} {scope}.", { action, before: s.news_before_min, after: s.news_after_min, releases, scope }),
    ),
  );
}

/** Economic calendar for ``anchor`` and the following days: list, add form, delete, ForexFactory import. */
function calendarCard(anchor, initialEvents) {
  const today = state.status.today;
  const s = state.plan.settings;
  const presets = state.plan.enums.news_presets;
  let events = initialEvents;
  const list = h("div", { class: "news-list" });
  const dayHeading = new Intl.DateTimeFormat(currentLocale(), { weekday: "short", day: "numeric", month: "short" });

  async function reload() {
    try {
      events = await api.get(`/api/news${query({ date_from: anchor, date_to: shiftDay(anchor, CALENDAR_DAYS) })}`);
      draw();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  async function changed() {
    await reload();
    await loadStatus().catch(() => {});
  }

  async function remove(event) {
    const ok = await confirmDialog(t('Delete "{title}" ({currency}) on {time}?', { title: event.title, currency: event.currency, time: fmt.datetime(event.time) }), {
      title: t("Delete event"),
      confirmText: t("Delete"),
      danger: true,
    });
    if (!ok) return;
    try {
      await api.del(`/api/news/${event.id}`);
      toast(t("Event deleted"), "success");
      await changed();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function row(event) {
    const past = new Date(event.window_end) < accountNow();
    const live = event.active && event.meets_threshold;
    return h(
      "div",
      { class: `news-item${live ? " active" : ""}${event.meets_threshold ? "" : " minor"}${past && !event.active ? " past" : ""}` },
      h("div", { class: "news-time num" }, fmt.time(event.time)),
      h(
        "div",
        { class: "news-main" },
        h("div", { class: "news-title" }, event.title),
        h(
          "div",
          { class: "news-meta" },
          h("span", { class: "badge" }, event.currency === "ALL" ? t("All markets") : event.currency),
          impactBadge(event.impact),
          h(
            "span",
            { class: "num", title: t("Restricted {before} min before to {after} min after", { before: event.before, after: event.after }) },
            t("Window {start}–{end}", { start: fmt.time(event.window_start), end: fmt.time(event.window_end) }),
          ),
          live ? h("span", { class: "badge warn" }, icon("clock"), t("Window open")) : null,
          event.meets_threshold ? null : h("span", {}, t("below your impact threshold")),
        ),
        event.notes ? h("div", { class: "muted small" }, event.notes) : null,
      ),
      h("button", { class: "btn ghost sm icon-only", type: "button", "aria-label": t("Delete {title}", { title: event.title }), onclick: () => remove(event) }, icon("trash")),
    );
  }

  function draw() {
    if (!events.length) {
      clear(
        list,
        h(
          "div",
          { class: "empty news-empty" },
          t("No events from {from} to {to}. Add the releases you trade around below.", { from: fmt.shortDate(anchor), to: fmt.shortDate(shiftDay(anchor, CALENDAR_DAYS)) }),
        ),
      );
      return;
    }
    const days = new Map();
    for (const event of events) {
      const key = event.time.slice(0, 10);
      if (!days.has(key)) days.set(key, []);
      days.get(key).push(event);
    }
    clear(
      list,
      [...days].map(([key, dayEvents]) => {
        const prefix = key === today ? `${t("Today")} · ` : key === shiftDay(today, 1) ? `${t("Tomorrow")} · ` : "";
        return h("div", { class: "news-day" }, h("h3", {}, prefix, dayHeading.format(parseDay(key))), dayEvents.map(row));
      }),
    );
  }

  // ---- add form
  const defaultTime = () => {
    if (anchor !== today) return `${anchor}T09:00`;
    const next = accountNow();
    next.setHours(next.getHours() + 1, 0, 0, 0);
    return toInputDateTime(next);
  };
  const when = h("input", { type: "datetime-local", required: true, value: defaultTime() });
  const currency = h("input", { type: "text", required: true, maxlength: "3", value: "USD", placeholder: t("USD or ALL"), autocapitalize: "characters", pattern: "[A-Za-z]{3}" });
  currency.addEventListener("input", () => (currency.value = currency.value.toUpperCase()));
  const title = h("input", { type: "text", required: true, maxlength: "120", list: "news-presets", placeholder: t("Pick a preset or type a title") });
  title.addEventListener("input", () => {
    const preset = presets.find((p) => p.title === title.value);
    if (preset) currency.value = preset.currency;
  });
  const impact = select(
    Object.entries(IMPACTS).map(([value, meta]) => [value, t(meta.label)]),
    "high",
  );
  const before = h("input", { type: "number", min: "0", max: "240", step: "1", placeholder: t("{minutes} (default)", { minutes: s.news_before_min }) });
  const after = h("input", { type: "number", min: "0", max: "240", step: "1", placeholder: t("{minutes} (default)", { minutes: s.news_after_min }) });
  const add = h("button", { class: "btn primary", type: "submit" }, icon("plus"), t("Add event"));
  const form = h(
    "form",
    { class: "news-form" },
    h("datalist", { id: "news-presets" }, presets.map((p) => h("option", { value: p.title }, p.currency))),
    h(
      "div",
      { class: "form-grid" },
      field(t("Time"), when),
      field(t("Title"), title, { full: true }),
      field(t("Currency"), currency, { help: t('"ALL" affects every market') }),
      field(t("Impact"), impact),
      field(t("Minutes before"), before),
      field(t("Minutes after"), after),
    ),
    h(
      "div",
      { class: "row" },
      h("span", { class: "muted small" }, t("Times in {timezone}. Blank minutes use your plan defaults.", { timezone: s.timezone })),
      h("span", { class: "spacer" }),
      add,
    ),
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    add.disabled = true;
    try {
      await api.post("/api/news", {
        time: when.value,
        title: title.value.trim(),
        currency: currency.value.trim(),
        impact: impact.value,
        before_min: numberOrNull(before),
        after_min: numberOrNull(after),
      });
      toast(t("Event added to the calendar"), "success");
      title.value = "";
      before.value = "";
      after.value = "";
      await changed();
    } catch (error) {
      toast(error.message, "error");
    } finally {
      add.disabled = false;
    }
  });

  // ---- ForexFactory import
  const importButton = h(
    "button",
    { class: "btn sm", type: "button", title: t("Download this week's ForexFactory calendar (needs internet)") },
    icon("download"),
    t("Import this week"),
  );
  importButton.addEventListener("click", async () => {
    importButton.disabled = true;
    try {
      const result = await api.post("/api/news/import-forexfactory");
      const extra = [
        result.duplicates ? t("{n} already in your calendar", { n: result.duplicates }) : "",
        result.below_threshold ? t("{n} below your impact threshold", { n: result.below_threshold }) : "",
      ]
        .filter(Boolean)
        .join(", ");
      const message = extra
        ? tn(result.imported, "Imported {n} event from ForexFactory ({skipped} skipped).", "Imported {n} events from ForexFactory ({skipped} skipped).", { skipped: extra })
        : tn(result.imported, "Imported {n} event from ForexFactory.", "Imported {n} events from ForexFactory.");
      toast(message, "success", 6000);
      await changed();
    } catch (error) {
      toast(error.message, "error", 6000);
    } finally {
      importButton.disabled = false;
    }
  });

  draw();
  return h(
    "div",
    { class: "card news-card" },
    h(
      "div",
      { class: "card-head" },
      h("div", {}, h("h2", {}, t("Economic calendar")), h("p", {}, t("{day} and the next {days} days", { day: anchor === today ? t("Today") : fmt.shortDate(anchor), days: CALENDAR_DAYS }))),
      importButton,
    ),
    guardrailNote(),
    list,
    form,
  );
}

function groupByCategory(items) {
  const groups = new Map();
  for (const item of items) {
    if (!groups.has(item.category)) groups.set(item.category, []);
    groups.get(item.category).push(item);
  }
  return groups;
}

export async function render(root, { params }) {
  const today = state.status.today;
  const day = params.get("date") || today;
  const isToday = day === today;
  const [record, history, newsEvents] = await Promise.all([
    api.get(`/api/checklist/${day}`),
    api.get("/api/checklist/history?days=35"),
    api.get(`/api/news${query({ date_from: day, date_to: shiftDay(day, CALENDAR_DAYS) })}`),
  ]);
  const items = state.plan.checklist_items.filter((item) => item.active);
  const checked = new Set(record.checked_item_ids);
  const requireGate = state.plan.settings.require_checklist;

  const progressBar = h("span");
  const progressWrap = h("div", { class: "progress" }, progressBar);
  const progressText = h("span", { class: "muted small" });
  const gate = h("div");
  const savedNote = h("span", { class: "muted small" });

  function payload() {
    return {
      checked_item_ids: [...checked],
      bias: bias.value,
      key_levels: keyLevels.value,
      news: news.value,
      notes: notes.value,
    };
  }

  async function save() {
    savedNote.textContent = t("Saving…");
    try {
      const result = await api.put(`/api/checklist/${day}`, payload());
      Object.assign(record, result);
      savedNote.textContent = t("All changes saved");
      updateProgress();
      if (isToday) await loadStatus();
    } catch (error) {
      savedNote.textContent = "";
      toast(error.message, "error");
    }
  }
  const saveSoon = debounce(save, 600);

  function updateProgress() {
    const required = items.filter((i) => i.required);
    const pool = required.length ? required : items;
    const done = pool.filter((i) => checked.has(i.id)).length;
    const complete = done === pool.length;
    progressBar.style.width = `${pool.length ? (done / pool.length) * 100 : 100}%`;
    progressWrap.classList.toggle("done", complete);
    progressText.textContent = t("{done} / {required} required · {checked} / {total} total", {
      done,
      required: pool.length,
      checked: items.filter((i) => checked.has(i.id)).length,
      total: items.length,
    });

    if (!isToday) {
      clear(gate);
    } else if (!requireGate) {
      clear(
        gate,
        h(
          "div",
          { class: "gate" },
          icon("info"),
          h("div", {}, h("strong", {}, t("Checklist gate is off")), h("span", { class: "muted small" }, t("Enable it in Trading Plan → Risk rules to lock trade entries until the routine is done."))),
        ),
      );
    } else {
      clear(
        gate,
        h(
          "div",
          { class: `gate ${complete ? "open" : "closed"}` },
          icon(complete ? "unlock" : "lock"),
          h(
            "div",
            {},
            h("strong", {}, complete ? t("Trade entries unlocked") : t("Trade entries locked")),
            h("span", { class: "muted small" }, complete ? t("Routine complete. Trade your plan, not your emotions.") : t("Check every required item to enable new trade entries.")),
          ),
        ),
      );
    }
  }

  const groups = [...groupByCategory(items)].map(([category, groupItems]) =>
    h(
      "div",
      { class: "check-group" },
      h("h3", {}, category),
      groupItems.map((item) => {
        const box = h("input", { type: "checkbox", checked: checked.has(item.id) });
        const row = h(
          "label",
          { class: `check-item${checked.has(item.id) ? " checked" : ""}` },
          box,
          h("span", { class: "text" }, item.label),
          item.required ? h("span", { class: "badge" }, t("Required")) : null,
        );
        box.addEventListener("change", () => {
          if (box.checked) checked.add(item.id);
          else checked.delete(item.id);
          row.classList.toggle("checked", box.checked);
          updateProgress();
          save();
        });
        return row;
      }),
    ),
  );

  const bias = segmented(
    [
      ["bullish", t("Bullish"), "long"],
      ["neutral", t("Neutral")],
      ["bearish", t("Bearish"), "short"],
    ],
    record.bias,
    () => save(),
  );
  const keyLevels = h("textarea", { rows: 4, placeholder: t("PDH 2412.5 · PDL 2391.0 · Weekly open 2400 · Asia high/low…"), value: record.key_levels, oninput: saveSoon });
  const news = h("textarea", { rows: 3, placeholder: t("How you'll handle today's releases - e.g. flat before CPI, no entries until 15:00"), value: record.news, oninput: saveSoon });
  const notes = h("textarea", { rows: 3, placeholder: t("Session plan, scenarios, invalidation…"), value: record.notes, oninput: saveSoon });

  const nav = h(
    "div",
    { class: "row" },
    h("a", { class: "btn ghost icon-only", href: `#/premarket?date=${shiftDay(day, -1)}`, "aria-label": t("Previous day") }, icon("chevronLeft")),
    h("a", { class: "btn ghost icon-only", href: `#/premarket?date=${shiftDay(day, 1)}`, "aria-label": t("Next day") }, icon("chevronRight")),
    isToday ? null : h("a", { class: "btn sm", href: "#/premarket" }, t("Today")),
  );

  const historyGrid = h(
    "div",
    { class: "history-grid" },
    [...history].reverse().map((entry) => {
      const status = entry.completed ? "done" : entry.done ? "partial" : "";
      const label = entry.completed ? t("complete") : entry.done ? t("{done}/{total} done", { done: entry.done, total: entry.total }) : t("not started");
      return h(
        "a",
        {
          class: `history-cell ${status}${entry.day === today ? " today" : ""}`,
          href: `#/premarket?date=${entry.day}`,
          title: `${fmt.date(entry.day)} - ${label}`,
          "aria-label": `${fmt.date(entry.day)}: ${label}`,
        },
        icon(entry.completed ? "check" : entry.done ? "clock" : "x"),
        entry.day.slice(8),
      );
    }),
  );
  const streak = (() => {
    let count = 0;
    for (const entry of history) {
      if (entry.completed) count += 1;
      else if (entry.day !== today) break;
    }
    return count;
  })();

  clear(
    root,
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, isToday ? t("Today's pre-market routine") : t("Pre-market routine")), h("p", {}, fmt.longDate(day))), nav),
    h(
      "div",
      { class: "grid split" },
      h(
        "div",
        { class: "stack" },
        gate,
        h(
          "div",
          { class: "card" },
          h("div", { class: "card-head" }, h("h2", {}, t("Checklist")), progressText),
          progressWrap,
          items.length ? groups : h("div", { class: "empty" }, t("No checklist items. Add them in Trading Plan → Checklist.")),
        ),
      ),
      h(
        "div",
        { class: "stack" },
        h(
          "div",
          { class: "card" },
          h("div", { class: "card-head" }, h("h2", {}, t("Daily plan")), savedNote),
          h("div", { class: "stack", style: { gap: "12px" } }, field(t("Directional bias"), bias.el), field(t("Key levels"), keyLevels), field(t("News plan"), news), field(t("Notes"), notes)),
        ),
        calendarCard(day, newsEvents),
        h(
          "div",
          { class: "card" },
          h("div", { class: "card-head" }, h("div", {}, h("h2", {}, t("Routine history")), h("p", {}, tn(streak, "Last 35 days · current streak {n} day", "Last 35 days · current streak {n} days")))),
          historyGrid,
        ),
      ),
    ),
  );
  updateProgress();
}
