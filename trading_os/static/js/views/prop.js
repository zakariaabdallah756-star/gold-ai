// Prop firm challenge tracker: preset gallery, challenge form, live rules dashboard and history.

import { api } from "../api.js";
import { balanceChart, chartsAvailable } from "../charts.js";
import { clear, confirmDialog, field, h, icon, modal, numberOrNull, select, toast } from "../dom.js";
import { fmt, pnlClass, toInputDate } from "../format.js";
import { accountNow, loadStatus } from "../state.js";

const PHASES = [
  ["phase1", "Phase 1 (challenge)"],
  ["phase2", "Phase 2 (verification)"],
  ["funded", "Funded account"],
  ["custom", "Custom"],
];
const PHASE_LABEL = { phase1: "Phase 1", phase2: "Phase 2", funded: "Funded", custom: "Custom" };
const STATUS = {
  active: { label: "Running", cls: "open", icon: "play" },
  passed: { label: "Passed", cls: "good", icon: "checkCircle" },
  failed: { label: "Failed", cls: "bad", icon: "xCircle" },
  archived: { label: "Archived", cls: "", icon: "layers" },
};
// The preset a passed phase leads to.
const NEXT_PHASE = {
  ftmo_challenge: "ftmo_verification",
  ftmo_verification: "ftmo_funded",
  fundednext_stellar_2step_p1: "fundednext_stellar_2step_p2",
};

const statusBadge = (status) => h("span", { class: `badge ${STATUS[status]?.cls || ""}` }, icon(STATUS[status]?.icon || "info"), STATUS[status]?.label || status);

function rulesList(r) {
  return [
    ["Profit target", r.profit_target_pct ? `${r.profit_target_pct}%` : "None (funded)"],
    ["Max daily loss", `${r.max_daily_loss_pct}%`],
    ["Max drawdown", `${r.max_total_drawdown_pct}% · ${r.drawdown_mode}`],
    ["Min trading days", r.min_trading_days ? String(r.min_trading_days) : "None"],
    ["Time limit", r.max_calendar_days ? `${r.max_calendar_days} days` : "None"],
  ];
}

function statList(rows) {
  return h("dl", { class: "prop-rules" }, rows.map(([label, value]) => [h("dt", {}, label), h("dd", {}, value)]));
}

// ------------------------------------------------------------------ create / edit

function challengeForm({ challenge = null, preset = null, presets, onSaved }) {
  const base = challenge || {
    name: preset && preset.key !== "custom" ? `${preset.label} ${fmt.money(10_000).replace(/\.00$/, "")}` : "",
    firm: preset?.firm || "",
    preset: preset?.key || "custom",
    phase: preset?.phase || "phase1",
    account_size: 10_000,
    profit_target_pct: preset ? preset.profit_target_pct : 10,
    max_daily_loss_pct: preset?.max_daily_loss_pct ?? 5,
    max_total_drawdown_pct: preset?.max_total_drawdown_pct ?? 10,
    drawdown_mode: preset?.drawdown_mode || "static",
    min_trading_days: preset?.min_trading_days ?? 0,
    max_calendar_days: preset?.max_calendar_days ?? null,
    start_date: toInputDate(accountNow()),
    enforce: true,
    notes: "",
  };
  const num = (value, attrs = {}) => h("input", { type: "number", step: "any", inputmode: "decimal", value: value ?? "", ...attrs });
  const name = h("input", { value: base.name, maxlength: "80", placeholder: "e.g. FTMO 100k - Phase 1" });
  const firm = h("input", { value: base.firm, maxlength: "60" });
  const presetSelect = select(presets.map((p) => [p.key, p.label]), base.preset);
  const phase = select(PHASES, base.phase);
  const size = num(base.account_size, { min: "1" });
  const start = h("input", { type: "date", value: base.start_date });
  const target = num(base.profit_target_pct, { placeholder: "none" });
  const daily = num(base.max_daily_loss_pct);
  const drawdown = num(base.max_total_drawdown_pct);
  const mode = select([["static", "Static (from the initial balance)"], ["trailing", "Trailing (follows the best end-of-day balance)"]], base.drawdown_mode);
  const minDays = num(base.min_trading_days, { step: "1", min: "0" });
  const maxDays = num(base.max_calendar_days, { step: "1", min: "1", placeholder: "no limit" });
  const enforce = h("input", { type: "checkbox", checked: base.enforce });
  const activate = h("input", { type: "checkbox", checked: true });
  const notes = h("textarea", { rows: 2, value: base.notes, maxlength: "5000" });

  // Picking another preset (new challenges only) refills the rules.
  presetSelect.addEventListener("change", () => {
    const p = presets.find((item) => item.key === presetSelect.value);
    if (!p) return;
    firm.value = p.firm;
    phase.value = p.phase;
    target.value = p.profit_target_pct ?? "";
    daily.value = p.max_daily_loss_pct;
    drawdown.value = p.max_total_drawdown_pct;
    mode.value = p.drawdown_mode;
    minDays.value = p.min_trading_days;
    maxDays.value = p.max_calendar_days ?? "";
  });
  if (challenge) presetSelect.disabled = true;

  const save = h(
    "button",
    {
      class: "btn primary",
      type: "button",
      onclick: async () => {
        const body = {
          name: name.value,
          firm: firm.value,
          preset: presetSelect.value,
          phase: phase.value,
          account_size: numberOrNull(size),
          profit_target_pct: numberOrNull(target),
          max_daily_loss_pct: numberOrNull(daily),
          max_total_drawdown_pct: numberOrNull(drawdown),
          drawdown_mode: mode.value,
          min_trading_days: numberOrNull(minDays) ?? 0,
          max_calendar_days: numberOrNull(maxDays),
          start_date: start.value,
          enforce: enforce.checked,
          notes: notes.value,
        };
        save.disabled = true;
        try {
          const saved = challenge ? await api.put(`/api/prop/challenges/${challenge.id}`, body) : await api.post("/api/prop/challenges", { ...body, activate: activate.checked });
          ref.close();
          toast(challenge ? "Challenge updated" : "Challenge started", "success");
          await loadStatus();
          onSaved(saved);
        } catch (error) {
          toast(error.message, "error");
        } finally {
          save.disabled = false;
        }
      },
    },
    challenge ? "Save changes" : "Start challenge",
  );

  const ref = modal({
    title: challenge ? `Edit ${challenge.name}` : "Start a prop challenge",
    wide: true,
    body: h(
      "div",
      { class: "stack" },
      h("div", { class: "banner warn", role: "note" }, icon("info"), h("div", {}, h("strong", {}, "Check the rules"), "Presets are a starting point. Firms change their rules - verify every number on the firm's website.")),
      h(
        "div",
        { class: "form-grid" },
        field("Name", name),
        field("Preset", presetSelect),
        field("Firm", firm),
        field("Phase", phase),
        field("Account size", size),
        field("Start date", start, { help: "Only P&L realised from this day counts" }),
        field("Profit target %", target, { help: "Leave blank for a funded account" }),
        field("Max daily loss %", daily, { help: "Of the initial account size" }),
        field("Max drawdown %", drawdown, { help: "Of the initial account size" }),
        field("Drawdown mode", mode),
        field("Min trading days", minDays),
        field("Time limit (days)", maxDays),
        field("Notes", notes, { full: true }),
      ),
      h("label", { class: "check" }, enforce, "Enforce the rules: block live entries that would breach them (otherwise warn)"),
      challenge ? null : h("label", { class: "check" }, activate, "Turn on prop mode for this challenge now"),
    ),
    footer: [h("button", { class: "btn ghost", type: "button", onclick: () => ref.close() }, "Cancel"), save],
  });
}

function presetGallery(presets, onPick) {
  return h(
    "div",
    { class: "prop-presets" },
    presets.map((p) =>
      h(
        "article",
        { class: "prop-preset" },
        h("div", {}, h("h3", {}, p.label), h("div", { class: "muted small" }, [p.firm, PHASE_LABEL[p.phase]].filter(Boolean).join(" · "))),
        statList(rulesList(p)),
        h("button", { class: `btn ${p.key === "custom" ? "" : "primary"} sm`, type: "button", onclick: () => onPick(p) }, p.key === "custom" ? "Set custom rules" : "Use this preset"),
      ),
    ),
  );
}

// ------------------------------------------------------------------ dashboard pieces

function meterCard({ title, iconName, used, limit, valueText, sub, invert = false }) {
  const ratio = limit > 0 ? Math.min(Math.max(used / limit, 0), 1) : 0;
  const level = ratio >= 1 ? "bad" : ratio >= 0.75 ? "warn" : "";
  const levelIcon = level === "bad" ? "xCircle" : level === "warn" ? "alert" : "checkCircle";
  const levelText = level === "bad" ? "Limit reached" : level === "warn" ? "Close to the limit" : "Within limits";
  return h(
    "div",
    { class: `card prop-meter ${level}` },
    h("div", { class: "row prop-meter-head" }, icon(iconName), h("h2", {}, title), h("span", { class: `prop-level ${level}` }, icon(levelIcon), levelText)),
    h("div", { class: "prop-meter-value num" }, valueText),
    h("div", { class: `progress prop-progress ${level}`, role: "meter", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(Math.round(ratio * 100)), "aria-label": title }, h("span", { style: { width: `${(invert ? 1 - ratio : ratio) * 100}%` } })),
    h("div", { class: "muted small" }, sub),
  );
}

function targetCard(c, m) {
  if (m.target_amount === null) {
    return h("div", { class: "card prop-target" }, h("div", { class: "row prop-meter-head" }, icon("target"), h("h2", {}, "Profit")), h("div", { class: `prop-meter-value num ${pnlClass(m.profit)}` }, fmt.signedMoney(m.profit)), h("div", { class: "muted small" }, `Funded account - no profit target · ${fmt.pct(m.profit_pct, 2)} on ${fmt.money(m.account_size)}`));
  }
  const progress = Math.max(0, Math.min(m.progress_pct ?? 0, 100));
  const reached = m.target_reached;
  return h(
    "div",
    { class: "card prop-target" },
    h("div", { class: "row prop-meter-head" }, icon("target"), h("h2", {}, "Profit target"), reached ? h("span", { class: "prop-level good" }, icon("checkCircle"), "Reached") : null),
    h("div", { class: "prop-meter-value num" }, h("span", { class: pnlClass(m.profit) }, fmt.signedMoney(m.profit)), h("span", { class: "muted" }, ` / ${fmt.money(m.target_amount)}`)),
    h("div", { class: `progress ${reached ? "done" : ""}`, role: "meter", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(Math.round(progress)), "aria-label": "Progress to the profit target" }, h("span", { style: { width: `${progress}%` } })),
    h("div", { class: "muted small" }, `${fmt.pct(m.progress_pct)} of the ${c.profit_target_pct}% target · ${fmt.pct(m.profit_pct, 2)} on ${fmt.money(m.account_size)}`),
  );
}

function statusBanner(c, presets, onStartNext) {
  if (c.status === "active") return null;
  const next = presets.find((p) => p.key === NEXT_PHASE[c.preset]);
  const cls = c.status === "passed" ? "prop-passed" : c.status === "failed" ? "bad" : "warn";
  return h(
    "div",
    { class: `banner ${cls}`, role: "status" },
    icon(STATUS[c.status].icon),
    h(
      "div",
      {},
      h("strong", {}, c.status === "passed" ? "Challenge passed" : c.status === "failed" ? "Challenge failed" : "Challenge archived"),
      h("span", {}, `${c.status_reason || ""}${c.end_date ? ` · ended ${fmt.date(c.end_date)}` : ""}`),
      c.status === "passed" && next ? h("div", { style: { marginTop: "8px" } }, h("button", { class: "btn sm primary", type: "button", onclick: () => onStartNext(next) }, `Start ${next.label}`, icon("arrowRight"))) : null,
      c.active ? h("div", { class: "small", style: { marginTop: "4px" } }, "Prop mode is still on for this challenge - deactivate it to stop the warnings.") : null,
    ),
  );
}

function historyTable(report) {
  const rows = [...report.history].reverse().filter((d) => d.pnl !== 0 || d.breached);
  if (!rows.length) return h("div", { class: "empty" }, h("strong", {}, "No realised P&L yet"), "Closed trades and partial closes since the start date appear here.");
  return h(
    "div",
    { class: "table-wrap" },
    h(
      "table",
      {},
      h("thead", {}, h("tr", {}, h("th", {}, "Date"), h("th", { class: "num" }, "P&L"), h("th", { class: "num" }, "Balance"), h("th", { class: "num" }, "Daily loss used"), h("th", { class: "num" }, "Drawdown floor"), h("th", {}, ""))),
      h(
        "tbody",
        {},
        rows.map((d) =>
          h(
            "tr",
            {},
            h("td", {}, fmt.date(d.date)),
            h("td", { class: `num ${pnlClass(d.pnl)}` }, fmt.signedMoney(d.pnl)),
            h("td", { class: "num" }, fmt.money(d.balance)),
            h("td", { class: "num" }, `${fmt.money(d.daily_loss_used)} / ${fmt.money(report.metrics.daily_limit)}`),
            h("td", { class: "num" }, fmt.money(d.drawdown_floor)),
            h("td", {}, d.daily_breach ? h("span", { class: "badge bad" }, icon("xCircle"), "Daily limit") : d.drawdown_breach ? h("span", { class: "badge bad" }, icon("xCircle"), "Floor") : null),
          ),
        ),
      ),
    ),
  );
}

// ------------------------------------------------------------------ page

export async function render(root, { params }) {
  let chart = null;
  let selectedId = params.get("id") ? Number(params.get("id")) : null;
  const { presets } = await api.get("/api/prop/presets");

  const startFrom = (preset) => challengeForm({ preset, presets, onSaved: (saved) => ((selectedId = saved.id), draw()) });
  const openGallery = () => {
    const ref = modal({ title: "Start a challenge", wide: true, body: presetGallery(presets, (p) => (ref.close(), startFrom(p))) });
  };

  async function act(action, message) {
    try {
      await action();
      if (message) toast(message, "success");
      await loadStatus();
      await draw();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  async function draw() {
    chart?.destroy();
    chart = null;
    const challenges = await api.get("/api/prop/challenges");
    const head = h(
      "div",
      { class: "page-head" },
      h("div", {}, h("h1", {}, "Prop firm challenge"), h("p", {}, "Track a funded-account evaluation against the firm's risk rules")),
      challenges.length ? h("button", { class: "btn primary", type: "button", onclick: openGallery }, icon("plus"), "New challenge") : null,
    );

    if (!challenges.length) {
      clear(
        root,
        head,
        h(
          "div",
          { class: "card prop-hero" },
          h("h2", {}, "Start a challenge"),
          h("p", { class: "muted" }, "Pick your programme. While a challenge is active (prop mode), every live entry is also checked against its daily loss limit and maximum drawdown - with enforcement on, trades that would breach them are blocked."),
          h("p", { class: "muted small" }, "Presets are a starting point: firms change their rules, so verify every number on the firm's website."),
          presetGallery(presets, startFrom),
        ),
      );
      return;
    }

    const current = challenges.find((c) => c.id === selectedId) || challenges.find((c) => c.active) || challenges[0];
    selectedId = current.id;
    const report = await api.get(`/api/prop/challenges/${current.id}/report`);
    const c = report;
    const m = report.metrics;
    const allowance = (m.account_size * c.max_total_drawdown_pct) / 100;
    const room = m.balance - m.drawdown_floor;

    const actions = h(
      "div",
      { class: "row" },
      h("button", { class: "btn sm", type: "button", onclick: () => challengeForm({ challenge: c, presets, onSaved: () => (loadStatus(), draw()) }) }, icon("edit"), "Edit"),
      c.active
        ? h("button", { class: "btn sm", type: "button", onclick: () => act(() => api.post(`/api/prop/challenges/${c.id}/deactivate`), "Prop mode off") }, icon("power"), "Deactivate")
        : c.status !== "archived"
          ? h("button", { class: "btn sm primary", type: "button", onclick: () => act(() => api.post(`/api/prop/challenges/${c.id}/activate`), "Prop mode on") }, icon("shield"), "Activate")
          : null,
      c.status !== "archived"
        ? h(
            "button",
            {
              class: "btn sm ghost",
              type: "button",
              onclick: async () => {
                if (await confirmDialog(`Archive ${c.name}? It stays in your history but no longer drives prop mode.`, { title: "Archive challenge", confirmText: "Archive" })) {
                  act(() => api.post(`/api/prop/challenges/${c.id}/archive`), "Challenge archived");
                }
              },
            },
            icon("layers"),
            "Archive",
          )
        : null,
    );

    const chartBody = h("div", { class: "chart-box" });
    const chartCard = h(
      "div",
      { class: "card" },
      h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Balance"), h("p", {}, "Closing balance per day with the drawdown floor and profit target"))),
      chartBody,
      h(
        "div",
        { class: "chart-legend" },
        h("span", {}, h("i", { class: "legend-swatch accent" }), "Balance"),
        h("span", {}, h("i", { class: "legend-swatch loss" }), "Drawdown floor"),
        m.target_amount !== null ? h("span", {}, h("i", { class: "legend-swatch profit" }), "Profit target") : null,
      ),
    );

    const others = challenges.filter((x) => x.id !== c.id);
    clear(
      root,
      head,
      h(
        "div",
        { class: "card" },
        h(
          "div",
          { class: "card-head", style: { flexWrap: "wrap" } },
          h(
            "div",
            {},
            h("div", { class: "row" }, h("h2", {}, c.name), statusBadge(c.status), h("span", { class: "badge" }, PHASE_LABEL[c.phase] || c.phase), c.active ? h("span", { class: `badge ${c.enforce ? "open" : "warn"}` }, icon("shield"), c.enforce ? "Prop mode · enforced" : "Prop mode · warnings only") : null),
            h("p", {}, [c.firm, `${fmt.money(c.account_size)} account`, `started ${fmt.date(c.start_date)}`].filter(Boolean).join(" · ")),
          ),
          actions,
        ),
        statusBanner(c, presets, startFrom),
      ),
      h(
        "div",
        { class: "prop-grid" },
        targetCard(c, m),
        meterCard({
          title: "Daily loss",
          iconName: "flame",
          used: m.daily_loss_used + m.open_risk,
          limit: m.daily_limit,
          valueText: `${fmt.money(m.daily_loss_used)} / ${fmt.money(m.daily_limit)}`,
          sub: `${fmt.money(Math.max(m.daily_remaining, 0))} room left today${m.open_risk ? ` after ${fmt.money(m.open_risk)} open risk` : ""}`,
        }),
        meterCard({
          title: "Max drawdown",
          iconName: "shield",
          used: allowance - room + m.open_risk,
          limit: allowance,
          valueText: `${fmt.money(Math.max(room, 0))} above the floor`,
          sub: `Floor ${fmt.money(m.drawdown_floor)} (${c.drawdown_mode}) · ${fmt.money(Math.max(m.total_remaining, 0))} room after open risk`,
        }),
      ),
      h(
        "div",
        { class: "kpis" },
        h("div", { class: "kpi" }, h("div", { class: "label" }, "Balance"), h("div", { class: "value num" }, fmt.money(m.balance)), h("div", { class: `sub ${pnlClass(m.profit)}` }, `${fmt.signedMoney(m.profit)} · ${fmt.pct(m.profit_pct, 2)}`)),
        h(
          "div",
          { class: "kpi" },
          h("div", { class: "label" }, "Trading days"),
          h("div", { class: "value" }, c.min_trading_days ? `${m.trading_days} / ${c.min_trading_days}` : String(m.trading_days)),
          h("div", { class: "sub" }, !c.min_trading_days ? "No minimum" : m.trading_days >= c.min_trading_days ? [icon("check"), " Minimum met"] : `${c.min_trading_days - m.trading_days} more needed`),
        ),
        h("div", { class: "kpi" }, h("div", { class: "label" }, "Days left"), h("div", { class: "value" }, m.days_left === null ? "∞" : String(m.days_left)), h("div", { class: "sub" }, m.days_left === null ? "No time limit" : `Day ${m.days_elapsed} of ${c.max_calendar_days}`)),
        h("div", { class: "kpi" }, h("div", { class: "label" }, "Open risk"), h("div", { class: "value num" }, fmt.money(m.open_risk)), h("div", { class: "sub" }, "At the current stops")),
      ),
      h("div", { class: "grid wide-left" }, chartCard, h("div", { class: "card" }, h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Rules"), h("p", {}, report.rules_note))), statList(rulesList(c)), h("p", { class: "muted small prop-mode-note" }, icon("shield"), " While a challenge is active, live entries are checked against these rules in addition to your account rules: the daily loss limit (including the open risk of other positions), the drawdown floor and the room left for the new trade's risk. ", c.enforce ? "Breaches are blocked." : "Breaches only warn."))),
      h("div", { class: "card" }, h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Daily history"), h("p", {}, "Days with realised P&L, newest first"))), historyTable(report)),
      others.length
        ? h(
            "div",
            { class: "card" },
            h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Other challenges"))),
            h(
              "div",
              { class: "table-wrap" },
              h(
                "table",
                {},
                h("thead", {}, h("tr", {}, h("th", {}, "Challenge"), h("th", {}, "Status"), h("th", {}, "Period"), h("th", { class: "num" }, "Profit"), h("th", {}))),
                h(
                  "tbody",
                  {},
                  others.map((x) =>
                    h(
                      "tr",
                      {},
                      h("td", {}, h("button", { class: "link-button", type: "button", onclick: () => ((selectedId = x.id), draw()) }, x.name), h("div", { class: "muted small" }, [x.firm, PHASE_LABEL[x.phase], x.active ? "prop mode on" : ""].filter(Boolean).join(" · "))),
                      h("td", {}, statusBadge(x.status)),
                      h("td", { class: "small" }, `${fmt.date(x.start_date)} – ${x.end_date ? fmt.date(x.end_date) : "now"}`),
                      h("td", { class: `num ${pnlClass(x.metrics.profit)}` }, `${fmt.signedMoney(x.metrics.profit)} (${fmt.pct(x.metrics.profit_pct, 2)})`),
                      h(
                        "td",
                        { class: "num" },
                        h(
                          "button",
                          {
                            class: "btn ghost sm icon-only",
                            type: "button",
                            "aria-label": `Delete ${x.name}`,
                            onclick: async () => {
                              if (await confirmDialog(`Delete ${x.name}? Your trades are not affected.`, { title: "Delete challenge", confirmText: "Delete", danger: true })) {
                                act(() => api.del(`/api/prop/challenges/${x.id}`), "Challenge deleted");
                              }
                            },
                          },
                          icon("trash"),
                        ),
                      ),
                    ),
                  ),
                ),
              ),
            ),
          )
        : null,
    );

    if (chartsAvailable() && report.history.length) {
      const canvas = h("canvas", { role: "img", "aria-label": `Balance from ${fmt.money(m.account_size)} to ${fmt.money(m.balance)}; drawdown floor ${fmt.money(m.drawdown_floor)}` });
      chartBody.append(canvas);
      chart = balanceChart(canvas, report.history, { start: m.account_size, target: m.target_amount === null ? null : m.account_size + m.target_amount });
    } else {
      chartBody.append(h("div", { class: "empty" }, "Charts unavailable"));
    }
  }

  await draw();
  return () => chart?.destroy();
}
