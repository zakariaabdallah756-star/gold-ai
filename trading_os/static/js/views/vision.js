// Vision AI: Claude reads a chart screenshot - levels, liquidity, patterns, structure and trade
// ideas - and answers follow-up questions about it. Trade ideas hand off to Signals, where sizing,
// guardrails and the Strategy Co-Pilot run before anything is logged.

import { api } from "../api.js";
import { clear, confirmDialog, field, h, icon, lightbox, select, toast } from "../dom.js";
import { fmt } from "../format.js";
import { N_, t, tn } from "../i18n.js";
import { handoff, state } from "../state.js";

const MAX_BYTES = 5 * 1024 * 1024;
const IMAGE_TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp"];
const SVG_NS = "http://www.w3.org/2000/svg";

// [label, tone] - tones map to colour tokens in vision.css; every item also carries a text label.
const LEVEL_KINDS = {
  support: ["Support", "bull"],
  resistance: ["Resistance", "bear"],
  pivot: ["Pivot", "info"],
  round_number: ["Round number", "info"],
  previous_high: ["Previous high", "bear"],
  previous_low: ["Previous low", "bull"],
  other: ["Level", "neutral"],
};
const ZONE_KINDS = {
  demand: ["Demand", "bull"],
  supply: ["Supply", "bear"],
  buy_side_liquidity: ["Buy-side liquidity", "liq"],
  sell_side_liquidity: ["Sell-side liquidity", "liq"],
  order_block: ["Order block", "info"],
  fair_value_gap: ["Fair value gap", "info"],
  consolidation: ["Consolidation", "neutral"],
  imbalance: ["Imbalance", "info"],
  other: ["Zone", "neutral"],
};
const BIAS = {
  bullish: { label: "Bullish", tone: "bull", badge: "long", icon: "up" },
  bearish: { label: "Bearish", tone: "bear", badge: "short", icon: "down" },
  neutral: { label: "Neutral", tone: "info", badge: "", icon: null },
};
const TREND = {
  bullish: { label: "Bullish", badge: "long", icon: "up" },
  bearish: { label: "Bearish", badge: "short", icon: "down" },
  ranging: { label: "Ranging", badge: "", icon: "list" },
  unclear: { label: "Unclear", badge: "", icon: "info" },
};
const STATUS = {
  done: { label: "Done", badge: "good", icon: "check" },
  error: { label: "Failed", badge: "bad", icon: "x" },
  pending: { label: "Unfinished", badge: "warn", icon: "clock" },
};
const LEGEND = [
  ["bull", "Support · demand · bullish"],
  ["bear", "Resistance · supply · bearish"],
  ["liq", "Liquidity"],
  ["info", "Pivot · order block · imbalance"],
  ["neutral", "Range · other"],
];
const ENTRY_TYPES = { market: "Market", limit: "Limit", stop: "Stop" };
const EXTENSION = { normal: N_("Normal"), extended: N_("Extended"), overextended: N_("Overextended"), unclear: N_("Unclear") };
const CATEGORIES = { candlestick: "Candlestick", chart: "Chart pattern", structure: "Structure" };
const SETUP_DETAIL = "Claude is not configured yet - no API key or login was found.";

// Form values and the open analysis are remembered between visits (the file is not).
const memory = { selectedId: null, symbol: "", timeframe: "", strategy: "", notes: "" };

// ------------------------------------------------------------------ helpers

function svg(tag, attrs) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
  return el;
}

const pct = (value) => `${(value * 100).toFixed(2)}%`;
const isNum = (value) => typeof value === "number" && Number.isFinite(value);
const price = (value) => (isNum(value) ? fmt.price(value) : "—");

function badge(cls, iconName, text) {
  return h("span", { class: `badge ${cls}` }, iconName ? icon(iconName) : null, text);
}

function statusBadge(status) {
  const info = STATUS[status] || STATUS.pending;
  return badge(info.badge, info.icon, info.label);
}

function trendBadge(direction, suffix = "") {
  const info = TREND[direction] || TREND.unclear;
  return badge(info.badge, info.icon, info.label + suffix);
}

function confidenceBadge(confidence) {
  const cls = confidence === "high" ? "good" : confidence === "low" ? "warn" : "";
  return badge(cls, null, `${confidence[0].toUpperCase()}${confidence.slice(1)} confidence`);
}

function sectionTitle(text) {
  return h("h3", { class: "vi-section-title" }, text);
}

function bulletList(items, emptyText) {
  return items.length ? h("ul", { class: "vi-list" }, items.map((item) => h("li", {}, item))) : h("p", { class: "muted small" }, emptyText);
}

function displaySymbol(record) {
  return record.symbol || record.analysis?.symbol_guess || "";
}

/** Signal text the Signals parser understands, e.g. "XAUUSD BUY LIMIT @ 2650 SL 2640 TP1 2660 TP2 2670". */
export function signalText(idea, symbol) {
  const parts = symbol ? [symbol] : [];
  parts.push(idea.direction === "long" ? "BUY" : "SELL");
  if (idea.entry_type !== "market") parts.push(idea.entry_type.toUpperCase());
  if (isNum(idea.entry)) parts.push("@", String(idea.entry));
  else if (idea.entry_type === "market") parts.push("NOW");
  if (isNum(idea.stop_loss)) parts.push("SL", String(idea.stop_loss));
  idea.take_profits.forEach((tp, index) => parts.push(`TP${index + 1}`, String(tp)));
  return parts.join(" ");
}

/** Reward:risk to the first target, when entry (or the last price for market ideas), stop and target are consistent. */
function rewardRisk(idea, analysis) {
  const entry = isNum(idea.entry) ? idea.entry : idea.entry_type === "market" ? analysis.price_axis.current_price : null;
  const [stop, target] = [idea.stop_loss, idea.take_profits[0]];
  if (![entry, stop, target].every(isNum)) return null;
  const sign = idea.direction === "long" ? 1 : -1;
  const risk = (entry - stop) * sign;
  const reward = (target - entry) * sign;
  return risk > 0 && reward > 0 ? reward / risk : null;
}

/** Only "Claude is not configured" carries setup steps; a 503 for a network problem keeps the working key. */
const needsSetup = (error) => error.status === 503 && Array.isArray(error.payload?.setup);

function findingList(findings) {
  if (!findings?.length) return null;
  return h(
    "div",
    { class: "violations vi-fomo-list" },
    findings.map((f) => h("div", { class: `violation ${f.severity === "warn" ? "warn" : "info"}` }, icon(f.severity === "warn" ? "alert" : "info"), h("div", {}, f.message))),
  );
}

async function sendToSignals(idea, record, findings = []) {
  const warnings = findings.filter((f) => f.severity === "warn");
  if (warnings.length) {
    const ok = await confirmDialog(
      h("span", {}, t("The anti-FOMO check flags this idea:"), findingList(warnings), t("Send it to Signals anyway? The warnings travel with the ticket.")),
      { title: t("Anti-FOMO check"), confirmText: t("Send anyway") },
    );
    if (!ok) return;
  }
  const symbol = displaySymbol(record).toUpperCase().replace(/[^A-Z0-9.]/g, "");
  handoff.signalText = signalText(idea, symbol);
  // The chart was analysed against this strategy and timeframe: the Co-Pilot checks the idea against them too.
  handoff.signalContext = {
    strategy: record.strategy || "",
    timeframe: record.timeframe || "",
    confluences: [],
    risk_pct: null,
    vision: warnings.length ? { analysis_id: record.id, warnings } : null,
  };
  location.hash = "#/signals";
}

/** The chart-level anti-FOMO callout (extension of the move, flagged ideas). */
function fomoCallout(record) {
  const fomo = record.fomo;
  if (!fomo || fomo.level === "none") return null;
  const flagged = fomo.ideas.filter((findings) => findings.some((f) => f.severity === "warn")).length;
  return h(
    "div",
    { class: `vi-fomo ${fomo.level === "warn" ? "warn" : "info"}` },
    h("div", { class: "row" }, icon("shield"), h("strong", {}, t("Anti-FOMO check")), flagged ? h("span", { class: "badge warn" }, tn(flagged, "{n} idea flagged", "{n} ideas flagged")) : null),
    findingList(fomo.chart),
    h("p", { class: "muted small" }, t("Warnings only: an extended move far from its logical stop invites chasing. The decision stays yours.")),
  );
}

function setupCard(detail) {
  return h(
    "div",
    { class: "card vi-setup" },
    h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Connect Claude to use Vision AI"), h("p", {}, detail || SETUP_DETAIL))),
    h(
      "ol",
      { class: "vi-steps" },
      h("li", {}, "Set the ", h("code", {}, "ANTHROPIC_API_KEY"), " environment variable, then restart Trading OS, or"),
      h("li", {}, "run ", h("code", {}, "ant auth login"), " once to sign in with the Anthropic CLI, or"),
      h("li", {}, "save an API key in ", h("a", { href: "#/plan?tab=ai" }, "Trading plan → AI assistant"), "."),
    ),
    h("p", { class: "muted small" }, "Charts are analysed by Claude through Anthropic's API; usage is billed to your Anthropic account."),
  );
}

// ------------------------------------------------------------------ chart overlay

function levelLabel(level) {
  return LEVEL_KINDS[level.kind] || LEVEL_KINDS.other;
}

function zoneLabel(zone) {
  return ZONE_KINDS[zone.kind] || ZONE_KINDS.other;
}

function chartFigure(record) {
  const { analysis, overlay } = record;
  const figure = h("div", { class: "vi-figure" });
  const image = h("img", { src: record.image_src, alt: "Analysed chart screenshot", title: "Open full size", onclick: () => lightbox(record.image_src) });
  figure.append(image);
  if (!overlay) return { figure, toolbar: null, note: null };

  const shapes = svg("svg", { class: "vi-svg", viewBox: "0 0 100 100", preserveAspectRatio: "none", "aria-hidden": "true" });
  const labels = h("div", { class: "vi-labels" });
  for (const spot of overlay.zones) {
    const zone = analysis.zones[spot.index];
    const [label, tone] = zoneLabel(zone);
    shapes.append(
      svg("rect", {
        class: `vi-zone tone-${tone}`,
        x: spot.x0 * 100,
        y: spot.y0 * 100,
        width: Math.max((spot.x1 - spot.x0) * 100, 0.4),
        height: Math.max((spot.y1 - spot.y0) * 100, 0.4),
      }),
    );
    labels.append(h("span", { class: `vi-tag vi-zone-tag tone-${tone}`, style: { left: pct(spot.x0), top: pct(spot.y0) } }, `Z${spot.index + 1}`, h("span", { class: "vi-kind" }, ` ${label}`)));
  }
  for (const spot of overlay.levels) {
    const level = analysis.levels[spot.index];
    const [label, tone] = levelLabel(level);
    const y = spot.y * 100;
    shapes.append(svg("line", { class: `vi-level tone-${tone}`, x1: 0, x2: 100, y1: y, y2: y }));
    labels.append(
      h(
        "span",
        { class: `vi-tag vi-level-tag tone-${tone}${spot.y < 0.06 ? " below" : ""}`, style: { top: pct(spot.y) } },
        `L${spot.index + 1}`,
        h("span", { class: "vi-kind" }, ` ${label}`),
        isNum(level.price) ? ` ${fmt.price(level.price)}` : "",
      ),
    );
  }
  for (const spot of overlay.patterns) {
    const pattern = analysis.patterns[spot.index];
    const bias = BIAS[pattern.bias] || BIAS.neutral;
    labels.append(h("span", { class: `vi-marker tone-${bias.tone}`, style: { left: pct(spot.x), top: pct(spot.y) }, title: `${pattern.name} (${bias.label.toLowerCase()})` }, `P${spot.index + 1}`));
  }
  figure.append(shapes, labels);

  const layers = [
    ["levels", "Levels", overlay.levels.length, analysis.levels.length],
    ["zones", "Zones", overlay.zones.length, analysis.zones.length],
    ["patterns", "Patterns", overlay.patterns.length, analysis.patterns.length],
  ];
  const toggles = layers.map(([key, label, placed]) =>
    h(
      "button",
      {
        type: "button",
        class: "chip-toggle on",
        "aria-pressed": "true",
        disabled: !placed,
        onclick: (event) => {
          const on = figure.classList.toggle(`hide-${key}`) === false;
          event.currentTarget.classList.toggle("on", on);
          event.currentTarget.setAttribute("aria-pressed", String(on));
        },
      },
      `${label} (${placed})`,
    ),
  );
  const toolbar = h(
    "div",
    { class: "vi-toolbar" },
    h("div", { class: "chips", role: "group", "aria-label": "Chart layers" }, toggles),
    h("div", { class: "vi-legend" }, LEGEND.map(([tone, text]) => h("span", {}, h("i", { class: `tone-${tone}` }), text))),
  );
  const unplaced = layers.reduce((sum, [, , placed, total]) => sum + total - placed, 0);
  const note = unplaced
    ? h("p", { class: "muted small" }, `${unplaced} ${unplaced === 1 ? "item has" : "items have"} no position on the image and ${unplaced === 1 ? "is" : "are"} only listed below.`)
    : null;
  return { figure, toolbar, note };
}

// ------------------------------------------------------------------ analysis sections

function summaryCard(record) {
  const { analysis } = record;
  const axis = analysis.price_axis;
  const range = axis.visible && isNum(axis.min_price) && isNum(axis.max_price) ? `${fmt.price(axis.min_price)} – ${fmt.price(axis.max_price)}` : "Not legible";
  const rows = [
    ["Price axis", range],
    ["Last price", price(axis.current_price)],
    ["Last break of structure", analysis.structure.last_bos || "None identified"],
    ["Last change of character", analysis.structure.last_choch || "None identified"],
  ];
  const ext = analysis.extension;
  if (ext && EXTENSION[ext.state]) {
    const parts = [t(EXTENSION[ext.state])];
    if (isNum(ext.distance_atr)) parts.push(t("{distance}× the typical candle from {reference}", { distance: fmt.num(ext.distance_atr, 1), reference: ext.reference || t("the mean") }));
    rows.push([t("Move extension"), parts.join(" · ")]);
  }
  if (analysis.symbol_guess && analysis.symbol_guess !== record.symbol) rows.push(["Symbol on chart", analysis.symbol_guess]);
  if (analysis.timeframe_guess && analysis.timeframe_guess !== record.timeframe) rows.push(["Timeframe on chart", analysis.timeframe_guess]);
  return h(
    "div",
    { class: "card" },
    h("div", { class: "card-head" }, h("h2", {}, "Summary")),
    fomoCallout(record),
    h("p", { class: "vi-summary" }, analysis.summary),
    h(
      "div",
      { class: "vi-trend" },
      h("div", { class: "row" }, trendBadge(analysis.trend.direction, " trend"), badge("", null, `${analysis.trend.strength[0].toUpperCase()}${analysis.trend.strength.slice(1)}`)),
      h("p", {}, analysis.trend.description),
    ),
    sectionTitle("Market structure"),
    h("p", { class: "vi-body" }, analysis.structure.description),
    h(
      "dl",
      { class: "stat-list vi-stats" },
      rows.map(([label, value]) => h("div", {}, h("dt", {}, label), h("dd", {}, value))),
    ),
  );
}

function ideaCard(idea, record, index) {
  const findings = record.fomo?.ideas?.[index] || [];
  const flagged = findings.some((f) => f.severity === "warn");
  const long = idea.direction === "long";
  const rr = rewardRisk(idea, record.analysis);
  const entry = isNum(idea.entry) ? fmt.price(idea.entry) : idea.entry_type === "market" ? "At market" : "—";
  const cells = [
    ["Entry", entry],
    ["Stop loss", price(idea.stop_loss)],
    ...idea.take_profits.map((tp, index) => [`TP${index + 1}`, fmt.price(tp)]),
    ["R:R to TP1", rr ? fmt.rr(rr) : "—"],
  ];
  return h(
    "article",
    { class: `vi-idea tone-${long ? "bull" : "bear"}${flagged ? " fomo" : ""}` },
    h("div", { class: "row" }, badge(long ? "long" : "short", long ? "up" : "down", long ? "Long" : "Short"), badge("", null, ENTRY_TYPES[idea.entry_type] || idea.entry_type), h("span", { class: "spacer" }), confidenceBadge(idea.confidence)),
    h("dl", { class: "vi-idea-levels" }, cells.map(([label, value]) => h("div", {}, h("dt", {}, label), h("dd", {}, value)))),
    h(
      "div",
      { class: "vi-idea-text" },
      [
        ["Trigger", idea.trigger],
        ["Rationale", idea.rationale],
        ["Invalidation", idea.invalidation],
      ].map(([label, text]) => h("div", {}, h("strong", {}, label), text)),
    ),
    findingList(findings),
    h("button", { class: "btn sm", type: "button", onclick: () => sendToSignals(idea, record, findings) }, icon("signal"), flagged ? t("Send anyway…") : "Send to Signals"),
  );
}

function ideasCard(record) {
  const ideas = record.analysis.trade_ideas;
  return h(
    "div",
    { class: "card" },
    h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Trade ideas"), h("p", {}, "Potential setups from structure. Send one to Signals to size it and run your guardrails - the decision stays yours."))),
    ideas.length ? h("div", { class: "vi-ideas" }, ideas.map((idea, index) => ideaCard(idea, record, index))) : h("p", { class: "muted" }, "No clear setup on this chart - waiting is a position too."),
  );
}

function refCell(prefix, index, tone, placed) {
  return h("td", {}, h("span", { class: `vi-ref tone-${tone}`, title: placed ? "Drawn on the chart" : "Listed only - no position on the image" }, `${prefix}${index + 1}`));
}

function tableCard(title, subtitle, headers, rows, emptyText) {
  return h(
    "div",
    { class: "card" },
    h("div", { class: "card-head" }, h("div", {}, h("h2", {}, title), subtitle ? h("p", {}, subtitle) : null)),
    rows.length
      ? h("div", { class: "table-wrap" }, h("table", { class: "vi-table" }, h("thead", {}, h("tr", {}, headers.map(([text, cls]) => h("th", { class: cls }, text)))), h("tbody", {}, rows)))
      : h("p", { class: "muted" }, emptyText),
  );
}

function levelsCard(record) {
  const placed = new Set((record.overlay?.levels || []).map((spot) => spot.index));
  const rows = record.analysis.levels.map((level, index) => {
    const [label, tone] = levelLabel(level);
    return h(
      "tr",
      {},
      refCell("L", index, tone, placed.has(index)),
      h("td", {}, label),
      h("td", { class: "num" }, price(level.price)),
      h("td", {}, level.strength),
      h("td", { class: "vi-note-cell" }, level.note),
    );
  });
  return tableCard("Key levels", "Support, resistance and reference prices", [["#"], ["Type"], ["Price", "num"], ["Strength"], ["Note"]], rows, "No key levels identified.");
}

function zonesCard(record) {
  const placed = new Set((record.overlay?.zones || []).map((spot) => spot.index));
  const rows = record.analysis.zones.map((zone, index) => {
    const [label, tone] = zoneLabel(zone);
    return h(
      "tr",
      {},
      refCell("Z", index, tone, placed.has(index)),
      h("td", {}, label),
      h("td", { class: "num" }, price(zone.price_top)),
      h("td", { class: "num" }, price(zone.price_bottom)),
      h("td", { class: "vi-note-cell" }, zone.note),
    );
  });
  return tableCard("Zones & liquidity", "Supply and demand, liquidity pools, order blocks and imbalances", [["#"], ["Type"], ["Top", "num"], ["Bottom", "num"], ["Note"]], rows, "No zones identified.");
}

function patternsCard(record) {
  const placed = new Set((record.overlay?.patterns || []).map((spot) => spot.index));
  const rows = record.analysis.patterns.map((pattern, index) => {
    const bias = BIAS[pattern.bias] || BIAS.neutral;
    return h(
      "tr",
      {},
      refCell("P", index, bias.tone, placed.has(index)),
      h("td", {}, pattern.name),
      h("td", {}, CATEGORIES[pattern.category] || pattern.category),
      h("td", {}, badge(bias.badge, bias.icon, bias.label)),
      h("td", {}, pattern.confidence),
      h("td", { class: "vi-note-cell" }, pattern.note),
    );
  });
  return tableCard("Patterns", "Candlestick, chart and structure patterns", [["#"], ["Pattern"], ["Type"], ["Bias"], ["Confidence"], ["Note"]], rows, "No patterns identified.");
}

function strategyFitCard(record) {
  const fit = record.analysis.strategy_fit;
  const name = fit.strategy || record.strategy;
  let body;
  if (!name) {
    body = h("p", { class: "muted" }, "No strategy was selected. Pick one before analysing to check the chart against its rules.");
  } else {
    const verdict =
      fit.fits === true ? badge("good", "check", `Fits ${name}`) : fit.fits === false ? badge("bad", "x", `Does not fit ${name}`) : badge("warn", "alert", `Fit with ${name} is unclear`);
    body = [
      h("div", { class: "row" }, verdict),
      fit.missing.length ? [sectionTitle("Missing for this strategy"), bulletList(fit.missing, "")] : null,
      fit.notes ? h("p", { class: "vi-body" }, fit.notes) : null,
    ];
  }
  return h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Strategy fit")), h("div", { class: "stack vi-tight" }, body));
}

function risksCard(analysis) {
  return h(
    "div",
    { class: "card" },
    h("div", { class: "card-head" }, h("h2", {}, "Risks & caveats")),
    h("div", { class: "stack vi-tight" }, h("div", {}, sectionTitle("Risks"), bulletList(analysis.risks, "None noted.")), h("div", {}, sectionTitle("Caveats"), bulletList(analysis.caveats, "None noted."))),
  );
}

// ------------------------------------------------------------------ page

export async function render(root, { params } = {}) {
  const requested = Number(params?.get("id"));
  if (requested) memory.selectedId = requested;
  const [history, aiSettings] = await Promise.all([api.get("/api/vision/analyses"), api.get("/api/ai/settings").catch(() => null)]);

  let file = null;
  let previewUrl = null;
  let busy = false;
  let timer = null;
  let items = history;

  const modelName = (id) => aiSettings?.models.find((m) => m.id === id)?.name || id;
  const setupSlot = h("div");
  const resultSlot = h("div", { class: "stack" });
  const historyList = h("div", { class: "vi-hist-list" });
  const historyCount = h("span", { class: "muted small" });

  function showSetup(detail) {
    clear(setupSlot, setupCard(detail));
    setupSlot.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function stopTimer() {
    clearInterval(timer);
    timer = null;
  }

  function startProgress(target, text) {
    const started = Date.now();
    const tick = () => clear(target, h("span", { class: "vi-spinner", "aria-hidden": "true" }), `${text} (${Math.round((Date.now() - started) / 1000)} s)`);
    stopTimer();
    tick();
    timer = setInterval(tick, 1000);
  }

  // ---------------------------------------------------------------- upload

  const fileInput = h("input", { type: "file", accept: IMAGE_TYPES.join(","), class: "sr-only", tabindex: "-1", onchange: () => fileInput.files[0] && choose(fileInput.files[0]) });
  const drop = h("div", {
    class: "vi-drop",
    tabindex: "0",
    role: "button",
    "aria-label": "Choose a chart screenshot",
    onclick: () => !file && fileInput.click(),
    onkeydown: (event) => {
      if (!file && (event.key === "Enter" || event.key === " ")) {
        event.preventDefault();
        fileInput.click();
      }
    },
    ondragover: (event) => {
      event.preventDefault();
      drop.classList.add("over");
    },
    ondragleave: () => drop.classList.remove("over"),
    ondrop: (event) => {
      event.preventDefault();
      drop.classList.remove("over");
      const dropped = event.dataTransfer?.files?.[0];
      if (dropped) choose(dropped);
    },
  });

  function renderDrop() {
    drop.classList.toggle("has-file", Boolean(file));
    drop.setAttribute("role", file ? "group" : "button");
    drop.setAttribute("aria-label", file ? "Selected chart" : "Choose a chart screenshot");
    if (!file) {
      clear(
        drop,
        icon("upload"),
        h("strong", {}, "Drop a chart screenshot here"),
        h("span", { class: "muted small" }, "or paste it with ", h("kbd", {}, "Ctrl"), "+", h("kbd", {}, "V"), " · ", h("span", { class: "vi-browse" }, "browse files")),
        h("span", { class: "muted small" }, "PNG, JPEG, GIF or WebP up to 5 MB"),
      );
      return;
    }
    clear(
      drop,
      h("img", { class: "vi-preview", src: previewUrl, alt: "Selected chart preview" }),
      h(
        "div",
        { class: "vi-file-meta" },
        h("strong", {}, file.name || "Pasted screenshot"),
        h("span", { class: "muted small" }, `${Math.max(1, Math.round(file.size / 1024)).toLocaleString()} KB`),
        h(
          "div",
          { class: "row" },
          h("button", { type: "button", class: "btn sm", onclick: () => fileInput.click() }, icon("refresh"), "Replace"),
          h("button", { type: "button", class: "btn sm ghost", onclick: () => resetFile() }, icon("x"), "Remove"),
        ),
      ),
    );
  }

  function choose(candidate) {
    if (candidate.type && !IMAGE_TYPES.includes(candidate.type)) {
      toast("Use a PNG, JPEG, GIF or WebP image.", "error");
      return;
    }
    if (candidate.size > MAX_BYTES) {
      toast("Chart images must be 5 MB or smaller.", "error");
      return;
    }
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    file = candidate;
    previewUrl = URL.createObjectURL(candidate);
    fileInput.value = "";
    renderDrop();
    analyzeBtn.disabled = busy;
  }

  function resetFile() {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    file = null;
    previewUrl = null;
    fileInput.value = "";
    renderDrop();
    analyzeBtn.disabled = true;
  }

  const onPaste = (event) => {
    const pasted = [...(event.clipboardData?.files || [])].find((item) => item.type.startsWith("image/"));
    if (!pasted || busy) return;
    event.preventDefault();
    choose(pasted);
  };
  document.addEventListener("paste", onPaste);

  const remember = (key) => (event) => {
    memory[key] = event.target.value;
  };
  const markets = state.plan.markets.filter((m) => m.active);
  const symbolSelect = select([["", "Detect from chart"], ...markets.map((m) => m.symbol)], memory.symbol, { onchange: remember("symbol") });
  const timeframeSelect = select([["", "Detect from chart"], ...state.plan.enums.timeframes], memory.timeframe, { onchange: remember("timeframe") });
  const strategies = state.plan.strategies.filter((s) => s.status !== "retired");
  const strategySelect = select([["", "No strategy"], ...strategies.map((s) => s.name)], memory.strategy, { onchange: remember("strategy") });
  const notesInput = h("textarea", { rows: 2, maxlength: 2000, placeholder: "Optional context, e.g. waiting for the London open, bias long above 2640", value: memory.notes, oninput: remember("notes") });
  const analyzeBtn = h("button", { class: "btn primary", type: "button", disabled: true, onclick: () => analyse() }, icon("brain"), "Analyze chart");
  const progress = h("div", { class: "vi-progress", role: "status", "aria-live": "polite" });

  async function analyse() {
    if (!file || busy) return;
    busy = true;
    analyzeBtn.disabled = true;
    startProgress(progress, "Analysing - this can take up to a minute");
    const form = new FormData();
    form.append("file", file, file.name || "chart.png");
    form.append("symbol", symbolSelect.value);
    form.append("timeframe", timeframeSelect.value);
    form.append("strategy", strategySelect.value);
    form.append("notes", notesInput.value.trim());
    try {
      const record = await api.upload("/api/vision/analyses", form);
      clear(setupSlot);
      resetFile();
      memory.selectedId = record.id;
      showRecord(record);
      if (record.status === "error") toast("The analysis did not complete - see the details below.", "warn");
      await refreshHistory();
      resultSlot.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
      if (needsSetup(error)) {
        showSetup(error.message);
      } else {
        toast(error.message, "error");
      }
      if (error.payload?.analysis_id) {
        resetFile();
        memory.selectedId = error.payload.analysis_id;
        await Promise.all([openAnalysis(error.payload.analysis_id), refreshHistory()]);
      }
    } finally {
      busy = false;
      stopTimer();
      clear(progress);
      analyzeBtn.disabled = !file;
    }
  }

  renderDrop();
  const uploadCard = h(
    "div",
    { class: "card" },
    h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Analyse a chart"), h("p", {}, "Claude reports every key element it can see - not only what fits your strategy."))),
    drop,
    fileInput,
    h(
      "div",
      { class: "form-grid vi-form" },
      field("Symbol", symbolSelect),
      field("Timeframe", timeframeSelect),
      field("Strategy", strategySelect, { help: "Used only to judge the fit." }),
      field("Notes", notesInput, { full: true }),
    ),
    h(
      "div",
      { class: "vi-actions" },
      analyzeBtn,
      progress,
      aiSettings ? h("span", { class: "muted small vi-model" }, `${modelName(aiSettings.model)} · ${aiSettings.effort} effort · `, h("a", { href: "#/plan?tab=ai" }, "Change")) : null,
    ),
  );

  // ---------------------------------------------------------------- result

  async function openAnalysis(id) {
    try {
      const record = await api.get(`/api/vision/analyses/${id}`);
      memory.selectedId = record.id;
      showRecord(record);
    } catch (error) {
      if (error.status === 404) {
        memory.selectedId = null;
        clear(resultSlot);
        return;
      }
      toast(error.message, "error");
    }
  }

  async function rerun(record, button, status) {
    if (busy) return;
    if (record.messages.length) {
      const ok = await confirmDialog("Running the analysis again replaces it and clears the questions and answers below.", { title: "Run again", confirmText: "Run again" });
      if (!ok) return;
    }
    busy = true;
    button.disabled = true;
    analyzeBtn.disabled = true;
    startProgress(status, "Analysing - this can take up to a minute");
    try {
      const updated = await api.post(`/api/vision/analyses/${record.id}/retry`);
      clear(setupSlot);
      showRecord(updated);
      if (updated.status === "error") toast("The analysis did not complete - see the details below.", "warn");
    } catch (error) {
      if (needsSetup(error)) showSetup(error.message);
      else toast(error.message, "error");
      await openAnalysis(record.id);
    } finally {
      busy = false;
      stopTimer();
      button.disabled = false;
      analyzeBtn.disabled = !file;
      await refreshHistory();
    }
  }

  async function remove(item) {
    const ok = await confirmDialog("Delete this analysis and its chart image? This cannot be undone.", { title: "Delete analysis", confirmText: "Delete", danger: true });
    if (!ok) return;
    try {
      await api.del(`/api/vision/analyses/${item.id}`);
      toast("Analysis deleted", "success");
      if (memory.selectedId === item.id) {
        memory.selectedId = null;
        clear(resultSlot);
      }
      await refreshHistory();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function headCard(record) {
    const status = h("div", { class: "vi-progress", role: "status", "aria-live": "polite" });
    const done = record.status === "done";
    const rerunBtn = h("button", { class: `btn sm${done ? "" : " primary"}`, type: "button", onclick: (event) => rerun(record, event.currentTarget, status) }, icon("refresh"), done ? "Run again" : "Retry");
    const meta = [fmt.datetime(record.created_at), modelName(record.model), record.strategy ? `Strategy: ${record.strategy}` : null].filter(Boolean).join(" · ");
    return h(
      "div",
      { class: "card vi-head" },
      h(
        "div",
        { class: "vi-head-row" },
        h(
          "div",
          { class: "vi-head-title" },
          h("div", { class: "row" }, h("h2", {}, displaySymbol(record) || "Unknown symbol"), record.timeframe || record.analysis?.timeframe_guess ? badge("", null, record.timeframe || record.analysis.timeframe_guess) : null, statusBadge(record.status)),
          h("p", { class: "muted small" }, meta),
          record.user_notes ? h("p", { class: "small vi-notes" }, h("span", { class: "muted" }, "Your notes: "), record.user_notes) : null,
        ),
        h(
          "div",
          { class: "row vi-head-actions" },
          done ? h("button", { class: "btn sm ghost", type: "button", onclick: () => document.getElementById("vi-chat")?.scrollIntoView({ behavior: "smooth" }) }, icon("signal"), "Ask a question") : null,
          rerunBtn,
          h("button", { class: "btn sm ghost danger", type: "button", onclick: () => remove(record) }, icon("trash"), "Delete"),
        ),
      ),
      status,
      record.status === "error" ? h("div", { class: "vi-callout bad" }, icon("xCircle"), h("div", {}, h("strong", {}, "The analysis failed"), record.error || "Unknown error.")) : null,
      record.status === "pending" ? h("div", { class: "vi-callout warn" }, icon("alert"), h("div", {}, h("strong", {}, "This analysis did not finish"), "Run it again to get a result.")) : null,
    );
  }

  function showRecord(record) {
    for (const node of historyList.querySelectorAll(".vi-hist")) node.classList.toggle("active", Number(node.dataset.id) === record.id);
    if (record.status !== "done" || !record.analysis) {
      clear(resultSlot, headCard(record), h("div", { class: "card" }, h("img", { class: "vi-failed-image", src: record.image_src, alt: "Chart screenshot", onclick: () => lightbox(record.image_src) })));
      return;
    }
    const { figure, toolbar, note } = chartFigure(record);
    clear(
      resultSlot,
      headCard(record),
      h("div", { class: "card" }, toolbar, figure, note),
      summaryCard(record),
      ideasCard(record),
      levelsCard(record),
      zonesCard(record),
      patternsCard(record),
      h("div", { class: "grid cols-2 vi-pair" }, strategyFitCard(record), risksCard(record.analysis)),
      h("p", { class: "vi-disclaimer" }, icon("info"), "AI analysis is educational, not financial advice. Verify every level on your own chart - the final decision is always yours."),
      chatCard(record),
    );
  }

  // ---------------------------------------------------------------- Q&A

  function bubble(message) {
    return h(
      "div",
      { class: `vi-msg ${message.role}` },
      h("div", { class: "vi-msg-role" }, message.role === "user" ? "You" : "Claude"),
      message.text.split(/\n{2,}/).map((paragraph) => h("p", {}, paragraph)),
    );
  }

  function chatCard(record) {
    let pending = false;
    const list = h("div", { class: "vi-chat-list", "aria-live": "polite" });
    const input = h("textarea", {
      rows: 2,
      maxlength: 2000,
      placeholder: "Ask a follow-up question about this chart…",
      "aria-label": "Question",
      onkeydown: (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
          event.preventDefault();
          send(input.value);
        }
      },
    });
    const sendBtn = h("button", { class: "btn primary", type: "button", onclick: () => send(input.value) }, icon("arrowRight"), "Send");
    const suggestions = [
      "Where would you place the stop?",
      record.strategy ? `Is this a valid setup for ${record.strategy}?` : null,
      "What would invalidate this idea?",
      "Which liquidity is likely to be taken next?",
      t("Is this move overextended?"),
    ].filter(Boolean);
    const chips = suggestions.map((question) => h("button", { type: "button", class: "chip-toggle", onclick: () => send(question) }, question));

    const draw = () => {
      clear(list, record.messages.length ? record.messages.map(bubble) : h("p", { class: "muted small" }, "Ask about levels, stops, invalidation or liquidity. Claude answers from this chart and its analysis."));
      list.scrollTop = list.scrollHeight;
    };

    async function send(text) {
      const question = text.trim();
      if (!question || pending) return;
      pending = true;
      for (const control of [input, sendBtn, ...chips]) control.disabled = true;
      list.querySelector(".muted")?.remove();
      list.append(bubble({ role: "user", text: question }), h("div", { class: "vi-msg assistant vi-thinking" }, h("span", { class: "vi-spinner", "aria-hidden": "true" }), "Claude is thinking…"));
      list.scrollTop = list.scrollHeight;
      try {
        const reply = await api.post(`/api/vision/analyses/${record.id}/messages`, { question });
        record.messages.push(reply.question, reply.answer);
        input.value = "";
      } catch (error) {
        if (needsSetup(error)) showSetup(error.message);
        toast(error.message, "error");
        input.value = question;
      } finally {
        pending = false;
        for (const control of [input, sendBtn, ...chips]) control.disabled = false;
        draw();
        input.focus();
      }
    }

    draw();
    return h(
      "div",
      { class: "card vi-chat", id: "vi-chat" },
      h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Ask about this chart"), h("p", {}, "Follow-up questions keep the chart and the analysis in context."))),
      list,
      h("div", { class: "chips vi-suggest" }, chips),
      h("div", { class: "vi-chat-input" }, input, sendBtn),
    );
  }

  // ---------------------------------------------------------------- history

  function historyItem(item) {
    const title = [item.symbol || "Unknown symbol", item.timeframe].filter(Boolean).join(" · ");
    return h(
      "div",
      { class: `vi-hist${item.id === memory.selectedId ? " active" : ""}`, dataset: { id: item.id } },
      h(
        "button",
        { type: "button", class: "vi-hist-open", onclick: () => openAnalysis(item.id) },
        h("img", { src: item.image_src, alt: "", loading: "lazy" }),
        h("span", { class: "vi-hist-meta" }, h("strong", {}, title), h("span", { class: "muted small" }, fmt.datetime(item.created_at)), h("span", { class: "row" }, statusBadge(item.status), item.trend ? trendBadge(item.trend) : null)),
      ),
      h("button", { type: "button", class: "btn ghost icon-only", "aria-label": `Delete analysis ${title}`, title: "Delete", onclick: () => remove(item) }, icon("trash")),
    );
  }

  function drawHistory() {
    clear(historyList, items.length ? items.map(historyItem) : h("p", { class: "muted small vi-hist-empty" }, "Your analysed charts appear here."));
    historyCount.textContent = `${items.length} saved`;
  }

  async function refreshHistory() {
    try {
      items = await api.get("/api/vision/analyses");
      drawHistory();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  drawHistory();
  if (aiSettings && (!aiSettings.package_installed || aiSettings.key_source === "none")) {
    clear(setupSlot, setupCard(aiSettings.package_installed ? SETUP_DETAIL : 'The "anthropic" Python package is not installed. Run: pip install anthropic'));
  }

  clear(
    root,
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Vision AI"), h("p", {}, "Upload a chart for an objective read of levels, liquidity, patterns, structure and trade ideas."))),
    h(
      "div",
      { class: "vi-layout" },
      h("div", { class: "vi-main stack" }, uploadCard, setupSlot, resultSlot),
      h("aside", { class: "card vi-side" }, h("div", { class: "card-head" }, h("h2", {}, "History"), historyCount), historyList),
    ),
  );

  const initial = items.find((item) => item.id === memory.selectedId) || items[0];
  if (initial) await openAnalysis(initial.id);

  return () => {
    document.removeEventListener("paste", onPaste);
    stopTimer();
    if (previewUrl) URL.revokeObjectURL(previewUrl);
  };
}
