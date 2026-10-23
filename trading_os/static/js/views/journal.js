// Trade journal: filterable, sortable table with detail view and CRUD.

import { api, query } from "../api.js";
import { clear, confirmDialog, debounce, field, h, icon, modal, select, toast } from "../dom.js";
import { fmt, pnlClass } from "../format.js";
import { N_, t, tn } from "../i18n.js";
import { valueLabel } from "../labels.js";
import { accounts, activeAccount, loadStatus, state } from "../state.js";
import { violationList } from "./calculator.js";
import { eventTimeline, managementSummary, stopKindBadge, stopKindOf, targetsLadder } from "./positions.js";
import { openTradeForm, screenshotGallery } from "./trade-form.js";

const PAGE_SIZE = 50;
const filters = { q: "", status: "", outcome: "", symbol: "", strategy: "", date_from: "", date_to: "" };
const sorting = { sort: "entry_time", order: "desc" };

const OUTCOME_BADGE = { win: ["good", N_("Win")], loss: ["bad", N_("Loss")], breakeven: ["", N_("Breakeven")] };

function complianceCell(trade) {
  const count = trade.violations.length;
  if (trade.rule_compliant === null) return h("span", { class: "muted" }, "—");
  return h(
    "span",
    { class: "row", style: { gap: "4px", flexWrap: "nowrap" }, title: trade.violations.map((v) => v.message).join("\n") || t("No violations") },
    h("span", { class: trade.rule_compliant ? "pos" : "neg", "aria-label": trade.rule_compliant ? t("Followed plan") : t("Broke rules") }, icon(trade.rule_compliant ? "check" : "x")),
    count ? h("span", { class: "badge warn" }, count) : null,
  );
}

function detailItem(label, value, cls = "") {
  return h("div", {}, h("div", { class: "label" }, label), h("div", { class: `value ${cls}` }, value));
}

function holdTime(trade) {
  if (!trade.exit_time) return "—";
  return fmt.duration((new Date(trade.exit_time) - new Date(trade.entry_time)) / 60000);
}

function pnlSource(trade) {
  if (trade.pnl === null) return "—";
  if (trade.managed) return t("Position ledger");
  return trade.pnl_manual ? t("Manual") : t("Auto from prices");
}

/** Targets, break-even / trailing settings, ledger figures and the event timeline. */
function positionSection(trade) {
  if (trade.status !== "open" && !trade.targets.length && !trade.events.length && !trade.managed) return null;
  const tracked = trade.managed || trade.events.length > 0;
  const open = trade.status === "open";
  const stop = trade.current_stop ?? trade.stop_loss;
  const remainingRisk = trade.risk_free ? h("span", { class: "badge good pm-badge" }, icon("shield"), t("Risk-free")) : fmt.money(trade.remaining_risk);
  return h(
    "div",
    {},
    h("div", { class: "section-title" }, t("Position management")),
    tracked
      ? h(
          "div",
          { class: "detail-grid", style: { marginBottom: "12px" } },
          detailItem(t("Realised (gross)"), fmt.signedMoney(trade.realized_pnl), pnlClass(trade.realized_pnl)),
          detailItem(
            t("Remaining"),
            trade.position_size === null ? "—" : t("{remaining} / {total} lots", { remaining: fmt.lots(trade.remaining_size), total: fmt.lots(trade.position_size) }),
          ),
          detailItem(open ? t("Current stop") : t("Final stop"), h("span", { class: "row", style: { gap: "6px" } }, fmt.price(stop), stopKindBadge(stopKindOf(trade)))),
          open ? detailItem(t("Remaining risk"), remainingRisk) : null,
        )
      : null,
    h("div", { class: "stack", style: { gap: "12px" } }, targetsLadder(trade), managementSummary(trade), trade.events.length ? eventTimeline(trade) : null),
  );
}

export function openTradeDetail(trade, { onChange } = {}) {
  const [outcomeCls, outcomeText] = OUTCOME_BADGE[trade.outcome] || ["open", N_("Open")];
  const reopen = (saved) => {
    onChange?.();
    openTradeDetail(saved, { onChange });
  };

  const body = h(
    "div",
    { class: "stack" },
    h(
      "div",
      { class: "row" },
      h("span", { class: `badge ${trade.direction}` }, trade.direction.toUpperCase()),
      h("span", { class: `badge ${trade.status === "open" ? "open" : outcomeCls}` }, trade.status === "open" ? t("Open") : t(outcomeText)),
      trade.rule_compliant === null ? null : h("span", { class: `badge ${trade.rule_compliant ? "good" : "bad"}` }, trade.rule_compliant ? t("Followed plan") : t("Broke rules")),
      trade.source === "signal" ? h("span", { class: "badge open pm-badge" }, icon("signal"), t("Signal")) : null,
      trade.tags.map((tag) => h("span", { class: "badge" }, `#${tag}`)),
    ),
    trade.signal_text
      ? h("details", { class: "pm-signal" }, h("summary", {}, t("Original signal text")), h("pre", { class: "notes" }, trade.signal_text))
      : null,
    h(
      "div",
      { class: "detail-grid" },
      detailItem(t("Net P&L"), fmt.signedMoney(trade.pnl), pnlClass(trade.pnl)),
      detailItem(t("R-multiple"), fmt.r(trade.r_multiple), pnlClass(trade.r_multiple)),
      detailItem(t("Planned R:R"), fmt.rr(trade.planned_rr)),
      detailItem(t("Executed R:R"), fmt.r(trade.executed_rr)),
      detailItem(t("Entry"), fmt.price(trade.entry_price)),
      detailItem(t("Stop loss"), fmt.price(trade.stop_loss)),
      detailItem(t("Take profit"), fmt.price(trade.take_profit)),
      detailItem(trade.managed ? t("Avg. exit") : t("Exit"), fmt.price(trade.exit_price)),
      detailItem(t("Size"), trade.position_size === null ? "—" : t("{lots} lots", { lots: fmt.lots(trade.position_size) })),
      detailItem(t("Risk"), fmt.money(trade.risk_amount)),
      detailItem(t("Fees"), fmt.money(trade.fees)),
      detailItem(t("P&L source"), pnlSource(trade)),
      detailItem(t("Entry time"), fmt.datetime(trade.entry_time)),
      detailItem(t("Exit time"), fmt.datetime(trade.exit_time)),
      detailItem(t("Holding time"), holdTime(trade)),
      detailItem(t("Session"), trade.session || t("Off-session")),
      accounts().length > 1 ? detailItem(t("Account"), accountTag(trade.account_id)) : null,
      detailItem(t("Strategy"), trade.strategy || "—"),
      detailItem(t("Timeframe"), trade.timeframe || "—"),
      detailItem(t("Emotion"), trade.emotion ? valueLabel(trade.emotion) : "—"),
    ),
    positionSection(trade),
    h(
      "div",
      {},
      h("div", { class: "section-title" }, t("Confluences")),
      trade.confluences.length
        ? h("div", { class: "chips" }, trade.confluences.map((c) => h("span", { class: "chip-toggle on" }, c)))
        : h("p", { class: "muted small" }, t("None recorded.")),
    ),
    trade.mistakes.length
      ? h("div", {}, h("div", { class: "section-title" }, t("Mistakes")), h("div", { class: "pm-mistakes" }, trade.mistakes.map((m) => h("span", { class: "badge warn pm-badge" }, icon("alert"), m))))
      : null,
    trade.violations.length ? h("div", {}, h("div", { class: "section-title" }, t("Rule violations recorded")), violationList(trade.violations)) : null,
    trade.notes ? h("div", {}, h("div", { class: "section-title" }, t("Notes")), h("div", { class: "notes" }, trade.notes)) : null,
    h("div", {}, h("div", { class: "section-title" }, t("Screenshots")), screenshotGallery(trade.screenshots)),
  );

  const ref = modal({
    title: `${trade.symbol} · ${fmt.datetime(trade.entry_time)}`,
    body,
    wide: true,
    footer: [
      h(
        "button",
        {
          class: "btn danger left",
          type: "button",
          onclick: async () => {
            const question = t("Delete trade #{id} ({symbol}) and its screenshots? This cannot be undone.", { id: trade.id, symbol: trade.symbol });
            if (!(await confirmDialog(question, { title: t("Delete trade"), confirmText: t("Delete"), danger: true }))) return;
            try {
              await api.del(`/api/trades/${trade.id}`);
              toast(t("Trade deleted"), "success");
              ref.close();
              loadStatus().catch(() => {});
              onChange?.();
            } catch (error) {
              toast(error.message, "error");
            }
          },
        },
        icon("trash"),
        t("Delete"),
      ),
      trade.status === "open" ? h("a", { class: "btn", href: "#/positions", onclick: () => ref.close() }, icon("layers"), t("Manage")) : null,
      trade.status === "open" && !trade.managed
        ? h("button", { class: "btn", type: "button", onclick: () => (ref.close(), openTradeForm({ trade, closing: true, onSaved: reopen })) }, t("Close trade"))
        : null,
      h("button", { class: "btn primary", type: "button", onclick: () => (ref.close(), openTradeForm({ trade, onSaved: reopen })) }, icon("edit"), t("Edit")),
    ],
  });
}

export async function render(root, { params }) {
  const { plan } = state;
  let page = 0;
  const tableBox = h("div");
  const pager = h("div", { class: "row", style: { marginTop: "12px" } });
  const countLabel = h("span", { class: "muted small" });

  async function load() {
    tableBox.classList.add("refreshing");
    try {
      const data = await api.get(`/api/trades${query({ ...filters, ...sorting, limit: PAGE_SIZE, offset: page * PAGE_SIZE })}`);
      drawTable(data);
    } catch (error) {
      clear(tableBox, h("div", { class: "empty" }, error.message));
    } finally {
      tableBox.classList.remove("refreshing");
    }
  }

  function sortHeader(label, key, numeric = false) {
    const active = sorting.sort === key;
    return h(
      "th",
      {
        class: `sortable${numeric ? " num" : ""}`,
        "aria-sort": active ? (sorting.order === "asc" ? "ascending" : "descending") : "none",
        onclick: () => {
          sorting.order = active && sorting.order === "desc" ? "asc" : "desc";
          sorting.sort = key;
          page = 0;
          load();
        },
      },
      label,
      active ? icon(sorting.order === "asc" ? "up" : "down") : null,
    );
  }

  function drawTable({ items, total }) {
    countLabel.textContent = tn(total, "{n} trade", "{n} trades");
    if (!items.length) {
      clear(
        tableBox,
        h(
          "div",
          { class: "empty" },
          h("strong", {}, total ? t("No trades on this page") : t("No trades found")),
          Object.values(filters).some(Boolean) ? t("Try clearing the filters.") : t("Log your first trade to start building your edge."),
        ),
      );
      clear(pager);
      return;
    }
    // Every account shown and several exist: say which account each trade is on.
    const showAccount = !activeAccount() && accounts().length > 1;
    clear(
      tableBox,
      h(
        "div",
        { class: "table-wrap" },
        h(
          "table",
          {},
          h(
            "thead",
            {},
            h(
              "tr",
              {},
              sortHeader(t("Date"), "entry_time"),
              showAccount ? h("th", {}, t("Account")) : null,
              sortHeader(t("Asset"), "symbol"),
              h("th", {}, t("Side")),
              h("th", {}, t("Strategy")),
              h("th", {}, t("Session")),
              h("th", { class: "num" }, t("Entry")),
              h("th", { class: "num" }, t("Exit")),
              h("th", { class: "num" }, t("Size")),
              sortHeader(t("Plan R:R"), "planned_rr", true),
              sortHeader(t("Result"), "r_multiple", true),
              sortHeader(t("P&L"), "pnl", true),
              h("th", {}, t("Emotion")),
              h("th", {}, t("Rules")),
              h("th", { "aria-label": t("Screenshots") }, icon("image")),
            ),
          ),
          h(
            "tbody",
            {},
            items.map((trade) =>
              h(
                "tr",
                { class: "clickable", tabindex: "0", onclick: () => openTradeDetail(trade, { onChange: load }), onkeydown: (e) => e.key === "Enter" && openTradeDetail(trade, { onChange: load }) },
                h("td", {}, fmt.date(trade.entry_time), h("span", { class: "muted" }, ` ${fmt.time(trade.entry_time)}`)),
                showAccount ? h("td", {}, accountTag(trade.account_id)) : null,
                h("td", {}, h("strong", {}, trade.symbol), trade.source === "signal" ? h("span", { class: "muted pm-src", title: t("From a signal") }, " ", icon("signal")) : null),
                h("td", {}, h("span", { class: `badge ${trade.direction}` }, trade.direction === "long" ? t("Long") : t("Short"))),
                h("td", {}, trade.strategy || h("span", { class: "muted" }, "—")),
                h("td", {}, trade.session || h("span", { class: "muted" }, "—")),
                h("td", { class: "num" }, fmt.price(trade.entry_price)),
                h("td", { class: "num" }, trade.status === "open" ? h("span", { class: "badge open" }, t("Open")) : fmt.price(trade.exit_price)),
                h("td", { class: "num" }, fmt.lots(trade.position_size)),
                h("td", { class: "num" }, fmt.rr(trade.planned_rr)),
                h("td", { class: `num ${pnlClass(trade.r_multiple)}` }, fmt.r(trade.r_multiple)),
                h("td", { class: `num ${pnlClass(trade.pnl)}` }, fmt.signedMoney(trade.pnl)),
                h("td", {}, trade.emotion ? valueLabel(trade.emotion) : h("span", { class: "muted" }, "—")),
                h("td", {}, complianceCell(trade)),
                h("td", { class: "muted" }, trade.screenshots.length || ""),
              ),
            ),
          ),
        ),
      ),
    );
    const pages = Math.ceil(total / PAGE_SIZE);
    clear(
      pager,
      h("span", { class: "muted small" }, t("Showing {from}–{to} of {total}", { from: page * PAGE_SIZE + 1, to: Math.min(total, (page + 1) * PAGE_SIZE), total })),
      h("span", { class: "spacer" }),
      h("button", { class: "btn sm", disabled: page === 0, onclick: () => (page--, load()) }, icon("chevronLeft"), t("Prev")),
      h("button", { class: "btn sm", disabled: page >= pages - 1, onclick: () => (page++, load()) }, t("Next"), icon("chevronRight")),
    );
  }

  const reload = () => {
    page = 0;
    load();
  };
  const bind = (key, options, label) => field(label, select([["", t("All")], ...options], filters[key], { onchange: (e) => ((filters[key] = e.target.value), reload()) }));
  const search = h("input", { type: "search", placeholder: t("Notes, tags, asset, strategy…"), value: filters.q, oninput: debounce((e) => ((filters.q = e.target.value), reload()), 300) });
  const dateInput = (key) => h("input", { type: "date", value: filters[key], onchange: (e) => ((filters[key] = e.target.value), reload()) });

  const newTrade = (prefill = {}) => openTradeForm({ prefill, onSaved: (saved) => (reload(), openTradeDetail(saved, { onChange: load })) });
  const status = state.status;

  clear(
    root,
    h(
      "div",
      { class: "page-head" },
      h("div", {}, h("h1", {}, t("Trade journal")), h("p", {}, countLabel)),
      h(
        "div",
        { class: "row" },
        status.can_trade ? null : h("span", { class: "chip bad" }, icon("lock"), t("Live entries locked - closed trades can still be logged")),
        h("button", { class: "btn primary", type: "button", onclick: () => newTrade() }, icon("plus"), t("New trade")),
      ),
    ),
    h(
      "div",
      { class: "filters" },
      h("div", { class: "field grow" }, h("label", {}, t("Search")), search),
      bind("status", [["open", t("Open")], ["closed", t("Closed")]], t("Status")),
      bind("outcome", [["win", t("Win")], ["loss", t("Loss")], ["breakeven", t("Breakeven")]], t("Outcome")),
      bind("symbol", plan.markets.map((m) => m.symbol), t("Asset")),
      bind("strategy", plan.strategies.map((s) => s.name), t("Strategy")),
      field(t("From"), dateInput("date_from")),
      field(t("To"), dateInput("date_to")),
    ),
    h("div", { class: "card", style: { padding: "8px 14px" } }, tableBox),
    pager,
  );

  await load();

  if (params.get("new")) {
    history.replaceState(null, "", "#/journal");
    newTrade({ symbol: params.get("symbol"), direction: params.get("direction"), entry: params.get("entry"), sl: params.get("sl"), tp: params.get("tp"), size: params.get("size") });
  }
}

/** A trading account's name with its colour dot (journal rows, trade detail). */
export function accountTag(accountId) {
  const account = accounts().find((a) => a.id === accountId);
  if (!account) return h("span", { class: "muted" }, "—");
  return h("span", { class: "acct-tag" }, h("span", { class: "acct-dot", style: { background: account.color }, "aria-hidden": "true" }), account.name);
}
