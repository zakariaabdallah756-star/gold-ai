// Pre-market routine: gated checklist, daily bias / levels / news, history.

import { api } from "../api.js";
import { clear, debounce, field, h, icon, segmented, toast } from "../dom.js";
import { fmt, toInputDate } from "../format.js";
import { loadStatus, state } from "../state.js";

function shiftDay(iso, days) {
  const [y, m, d] = iso.split("-").map(Number);
  return toInputDate(new Date(y, m - 1, d + days));
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
  const [record, history] = await Promise.all([api.get(`/api/checklist/${day}`), api.get("/api/checklist/history?days=35")]);
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
    savedNote.textContent = "Saving…";
    try {
      const result = await api.put(`/api/checklist/${day}`, payload());
      Object.assign(record, result);
      savedNote.textContent = "All changes saved";
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
    progressText.textContent = `${done} / ${pool.length} required · ${items.filter((i) => checked.has(i.id)).length} / ${items.length} total`;

    if (!isToday) {
      clear(gate);
    } else if (!requireGate) {
      clear(gate, h("div", { class: "gate" }, icon("info"), h("div", {}, h("strong", {}, "Checklist gate is off"), h("span", { class: "muted small" }, "Enable it in Trading Plan → Risk rules to lock trade entries until the routine is done."))));
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
            h("strong", {}, complete ? "Trade entries unlocked" : "Trade entries locked"),
            h("span", { class: "muted small" }, complete ? "Routine complete. Trade your plan, not your emotions." : "Check every required item to enable new trade entries."),
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
          item.required ? h("span", { class: "badge" }, "Required") : null,
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
      ["bullish", "Bullish", "long"],
      ["neutral", "Neutral"],
      ["bearish", "Bearish", "short"],
    ],
    record.bias,
    () => save(),
  );
  const keyLevels = h("textarea", { rows: 4, placeholder: "PDH 2412.5 · PDL 2391.0 · Weekly open 2400 · Asia high/low…", value: record.key_levels, oninput: saveSoon });
  const news = h("textarea", { rows: 3, placeholder: "14:30 USD CPI (high impact) - no trades 15 min either side", value: record.news, oninput: saveSoon });
  const notes = h("textarea", { rows: 3, placeholder: "Session plan, scenarios, invalidation…", value: record.notes, oninput: saveSoon });

  const nav = h(
    "div",
    { class: "row" },
    h("a", { class: "btn ghost icon-only", href: `#/premarket?date=${shiftDay(day, -1)}`, "aria-label": "Previous day" }, icon("chevronLeft")),
    h("a", { class: "btn ghost icon-only", href: `#/premarket?date=${shiftDay(day, 1)}`, "aria-label": "Next day" }, icon("chevronRight")),
    isToday ? null : h("a", { class: "btn sm", href: "#/premarket" }, "Today"),
  );

  const historyGrid = h(
    "div",
    { class: "history-grid" },
    [...history].reverse().map((entry) => {
      const status = entry.completed ? "done" : entry.done ? "partial" : "";
      const label = entry.completed ? "complete" : entry.done ? `${entry.done}/${entry.total} done` : "not started";
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
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, isToday ? "Today's pre-market routine" : "Pre-market routine"), h("p", {}, fmt.longDate(day))), nav),
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
          h("div", { class: "card-head" }, h("h2", {}, "Checklist"), progressText),
          progressWrap,
          items.length ? groups : h("div", { class: "empty" }, "No checklist items. Add them in Trading Plan → Checklist."),
        ),
      ),
      h(
        "div",
        { class: "stack" },
        h(
          "div",
          { class: "card" },
          h("div", { class: "card-head" }, h("h2", {}, "Daily plan"), savedNote),
          h("div", { class: "stack", style: { gap: "12px" } }, field("Directional bias", bias.el), field("Key levels", keyLevels), field("News & events", news), field("Notes", notes)),
        ),
        h(
          "div",
          { class: "card" },
          h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Routine history"), h("p", {}, `Last 35 days · current streak ${streak} day${streak === 1 ? "" : "s"}`))),
          historyGrid,
        ),
      ),
    ),
  );
  updateProgress();
}
