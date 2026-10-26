// Trading Accounts -> connection & order routing panel of one account (v1.5): how the account is reached
// (paper, MetaTrader EA bridge, MetaTrader 5 terminal, webhook, OANDA), its write-only secrets, the EA token and
// setup, a connection test, the order-sending switch (typed account name for anything but paper) and the log of
// orders sent. The server enforces every rule; this panel explains them.

import { api } from "../api.js";
import { accordion, clear, confirmDialog, field, h, icon, modal, select, toast } from "../dom.js";
import { fmt } from "../format.js";
import { N_, t } from "../i18n.js";
import { reloadAccounts } from "../state.js";

const CONNECTIONS = [
  ["none", N_("Not connected"), N_("Journal only: no orders are sent from Trading OS.")],
  ["paper", N_("Paper trading (simulated)"), N_("Orders fill at the ticket's price inside Trading OS and never reach a broker - the safe way to try the flow.")],
  ["mt_bridge", N_("MetaTrader EA bridge (MT4 / MT5)"), N_("An Expert Advisor in your MetaTrader terminal fetches the orders - any MT4 or MT5 broker, also on a VPS.")],
  ["mt5_terminal", N_("MetaTrader 5 on this computer (Windows)"), N_("Trading OS drives the MetaTrader 5 terminal installed on this Windows computer (MetaTrader5 Python package).")],
  ["webhook", N_("Webhook bridge"), N_("Each order is POSTed as signed JSON to your own bridge: a trade copier, a script on a VPS.")],
  ["oanda", N_("OANDA (broker API)"), N_("Orders go straight to your OANDA practice or live account through its REST API.")],
];
const STATUS_LABEL = {
  pending: N_("Pending"),
  sent: N_("Sent"),
  filled: N_("Filled"),
  rejected: N_("Rejected"),
  error: N_("Error"),
  cancelled: N_("Cancelled"),
};
const STATUS_CLASS = { pending: "warn", sent: "open", filled: "good", rejected: "bad", error: "bad", cancelled: "" };
const ORDER_TYPE_LABEL = { market: N_("Market"), limit: N_("Limit"), stop: N_("Stop") };
const SECRET_LABEL = {
  password: N_("MetaTrader 5 password"),
  api_token: N_("OANDA API token"),
  webhook_secret: N_("Signing secret (optional)"),
};
const SECRET_HELP = {
  password: N_("Used to log in to the terminal. Stored encrypted on this computer, never shown again."),
  api_token: N_("OANDA > My Account > Manage API Access. Stored encrypted on this computer, never shown again."),
  webhook_secret: N_("Signs every order: header X-TradingOS-Signature: sha256=<HMAC of the body>, like notification webhooks."),
};
const SECRETS_BY_CONNECTION = { webhook: ["webhook_secret"], mt5_terminal: ["password"], oanda: ["api_token"] };
const SEEN_OK_SECONDS = 120;

/** The translated name of a connection type (also used by the Signals page). */
export function connectionLabel(kind) {
  const found = CONNECTIONS.find(([value]) => value === kind);
  return found ? t(found[1]) : kind;
}

/** A translated order status ("Filled", "Pending" ...). */
export function statusLabel(status) {
  return STATUS_LABEL[status] ? t(STATUS_LABEL[status]) : status;
}

export function statusBadge(status) {
  return h("span", { class: `badge ${STATUS_CLASS[status] || ""}` }, statusLabel(status));
}

function ago(seconds, iso) {
  if (seconds === null || seconds === undefined) return t("never");
  if (seconds < 60) return t("{n} s ago", { n: seconds });
  if (seconds < 3600) return t("{n} min ago", { n: Math.floor(seconds / 60) });
  return fmt.datetime(iso);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    // Plain http on the LAN has no clipboard API: fall back to a temporary selection.
    const area = h("textarea", { value: text, readonly: true, style: { position: "fixed", opacity: "0" } });
    document.body.append(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
  toast(t("Copied"), "success", 1800);
}

function codeLine(text, label) {
  return h(
    "div",
    { class: "exec-code" },
    h("code", {}, text),
    h("button", { type: "button", class: "btn sm", title: t("Copy"), "aria-label": label || t("Copy"), onclick: () => copyText(text) }, icon("copy")),
  );
}

function mapToText(map) {
  return Object.entries(map || {})
    .map(([journal, broker]) => `${journal}=${broker}`)
    .join("\n");
}

function textToMap(text) {
  const map = {};
  for (const line of String(text).split(/[\n,;]+/)) {
    const [journal, ...rest] = line.split("=");
    if (!journal.trim() && !rest.join("=").trim()) continue;
    map[journal.trim().toUpperCase()] = rest.join("=").trim();
  }
  return map;
}

/** The connection / credentials / execution panel of ``account`` (null without an account). */
export function renderConnection(account, { onSaved } = {}) {
  if (!account || account.id === undefined || account.id === null) return null;
  const root = h("section", { class: "exec-panel", "aria-label": t("Connection & order sending") }, h("p", { class: "muted small" }, t("Loading…")));
  let lastTest = null; // {ok, message} of the latest connection test, shown until the next one
  let revealed = null; // the bridge token, once revealed or created on this computer

  async function load() {
    try {
      draw(await api.get(`/api/accounts/${account.id}/connection`));
    } catch (error) {
      clear(root, h("div", { class: "exec-note bad", role: "alert" }, icon("alert"), error.message));
    }
  }

  function changed(panel, message, kind = "success") {
    draw(panel);
    if (message) toast(message, kind);
    reloadAccounts().catch(() => {});
    onSaved?.(panel.account);
  }

  function draw(panel) {
    clear(
      root,
      headSection(panel),
      panel.last_error ? h("div", { class: "exec-note warn", role: "status" }, icon("alert"), h("div", {}, h("strong", {}, t("Last problem")), " ", panel.last_error)) : null,
      connectionForm(panel),
      secretsSection(panel),
      panel.connection === "mt_bridge" ? bridgeSection(panel) : null,
      panel.connection !== "none" ? testSection(panel) : null,
      panel.connection !== "none" ? sendingSection(panel) : null,
      logSection(panel),
    );
  }

  // ------------------------------------------------------------- header
  function headSection(panel) {
    return h(
      "div",
      { class: "exec-head" },
      h("div", {}, h("h3", {}, icon("zap"), t("Connection & order sending")), h("p", { class: "muted small" }, t("Send confirmed signals from Trading OS straight to this account."))),
      h(
        "div",
        { class: "chips" },
        h("span", { class: "chip" }, icon("link"), connectionLabel(panel.connection)),
        panel.connection === "none"
          ? null
          : panel.execution_enabled
            ? h("span", { class: "chip ok" }, icon("check"), t("Order sending on"))
            : h("span", { class: "chip" }, icon("lock"), t("Order sending off")),
      ),
    );
  }

  // ------------------------------------------------------------- connection type and settings
  function connectionForm(panel) {
    const config = panel.connection_config || {};
    const state = { connection: panel.connection };
    const typeHelp = h("div", { class: "help" });
    const fieldsBox = h("div", { class: "form-grid" });
    const switchNote = h("p", { class: "small muted exec-switch-note", "aria-live": "polite" });
    const inputs = {};

    const typeSelect = select(
      CONNECTIONS.map(([value, label]) => [value, t(label)]),
      panel.connection,
      {
        id: `exec-type-${panel.account_id}`,
        onchange: (event) => {
          state.connection = event.target.value;
          renderFields();
        },
      },
    );

    function text(key, value, attrs = {}) {
      inputs[key] = h("input", { type: "text", value: value ?? "", autocomplete: "off", spellcheck: false, ...attrs });
      return inputs[key];
    }

    function renderFields() {
      const kind = state.connection;
      const description = CONNECTIONS.find(([value]) => value === kind);
      typeHelp.textContent = description ? t(description[2]) : "";
      for (const key of Object.keys(inputs)) delete inputs[key];
      const same = kind === panel.connection;
      const rows = [];
      if (kind === "webhook") {
        rows.push(field(t("Webhook URL"), text("url", same ? config.url : "", { type: "url", placeholder: "https://", inputmode: "url" }), { full: true, help: t("https:// address of your bridge (plain http only for localhost or a LAN address).") }));
      } else if (kind === "oanda") {
        rows.push(field(t("OANDA account ID"), text("account_id", same ? config.account_id : "", { placeholder: "101-004-1234567-001" })));
        inputs.environment = select(
          [
            ["practice", t("Practice (demo)")],
            ["live", t("Live (real money)")],
          ],
          (same && config.environment) || "practice",
        );
        rows.push(field(t("Environment"), inputs.environment));
      } else if (kind === "mt5_terminal") {
        rows.push(
          h(
            "div",
            { class: "field full" },
            h("span", { class: "label" }, t("Login and server")),
            h("div", { class: "small" }, panel.login || panel.server ? `${panel.login || "—"} · ${panel.server || "—"}` : t("Not set yet")),
            h("div", { class: "help" }, t("From the account details above (MT5 login number and server).")),
          ),
        );
        rows.push(field(t("Magic number"), text("magic", same ? config.magic : "", { inputmode: "numeric", placeholder: "20250" })));
        rows.push(field(t("Max slippage (points)"), text("max_slippage_points", same ? config.max_slippage_points : "", { inputmode: "numeric", placeholder: "20" })));
      } else if (kind === "mt_bridge") {
        rows.push(
          field(t("Order lifetime (seconds)"), text("order_ttl_sec", same ? config.order_ttl_sec : "", { inputmode: "numeric", placeholder: "120" }), {
            help: t("An order the EA has not picked up by then expires, so an old signal never fires late."),
          }),
        );
      }
      if (kind !== "none") {
        rows.push(field(t("Symbol suffix"), text("symbol_suffix", same ? config.symbol_suffix : "", { placeholder: ".m" }), { help: t("Added to every symbol, e.g. XAUUSD.m") }));
        inputs.symbol_map = h("textarea", { rows: 2, spellcheck: false, value: same ? mapToText(config.symbol_map) : "", placeholder: "XAUUSD=GOLD" });
        rows.push(field(t("Symbol map"), inputs.symbol_map, { full: true, help: t("One per line: journal symbol=broker symbol. Wins over the suffix.") }));
        inputs.max_lots = h("input", { type: "number", min: "0", step: "any", inputmode: "decimal", value: panel.max_lots_per_order ?? "", placeholder: t("No limit") });
        rows.push(field(t("Max lots per order"), inputs.max_lots, { help: t("Larger orders are refused before anything is sent.") }));
      }
      clear(fieldsBox, rows);
      switchNote.textContent = panel.execution_enabled && kind !== panel.connection ? t("Changing the connection type switches order sending off.") : "";
    }

    function collect() {
      const kind = state.connection;
      const config = {};
      const value = (key) => (inputs[key] ? String(inputs[key].value).trim() : "");
      if (kind === "webhook") config.url = value("url");
      if (kind === "oanda") Object.assign(config, { account_id: value("account_id"), environment: inputs.environment.value });
      if (kind === "mt5_terminal") {
        if (value("magic")) config.magic = Number(value("magic"));
        if (value("max_slippage_points")) config.max_slippage_points = Number(value("max_slippage_points"));
      }
      if (kind === "mt_bridge" && value("order_ttl_sec")) config.order_ttl_sec = Number(value("order_ttl_sec"));
      if (kind !== "none") {
        config.symbol_suffix = value("symbol_suffix");
        config.symbol_map = textToMap(inputs.symbol_map.value);
      }
      const lots = inputs.max_lots ? String(inputs.max_lots.value).trim().replace(",", ".") : "";
      return { connection: kind, connection_config: config, max_lots_per_order: lots === "" ? null : Number(lots) };
    }

    const saveButton = h("button", { type: "submit", class: "btn primary sm" }, icon("check"), t("Save connection"));
    const form = h(
      "form",
      {
        class: "exec-section stack",
        onsubmit: async (event) => {
          event.preventDefault();
          saveButton.disabled = true;
          try {
            const next = await api.put(`/api/accounts/${panel.account_id}/connection`, collect());
            lastTest = null;
            changed(next, next.switched_off ? t("Connection saved - order sending is now off") : t("Connection saved"), next.switched_off ? "warn" : "success");
          } catch (error) {
            toast(error.message, "error");
          } finally {
            saveButton.disabled = false;
          }
        },
      },
      h("div", { class: "field" }, h("label", { for: `exec-type-${panel.account_id}` }, t("How Trading OS reaches this account")), typeSelect, typeHelp),
      fieldsBox,
      h("div", { class: "row" }, saveButton, switchNote),
    );
    renderFields();
    return form;
  }

  // ------------------------------------------------------------- secrets (write-only)
  function secretsSection(panel) {
    const kinds = SECRETS_BY_CONNECTION[panel.connection] || [];
    if (!kinds.length) return null;
    return h("div", { class: "exec-section stack" }, h("h4", {}, icon("lock"), t("Credentials")), kinds.map((kind) => secretRow(panel, kind)));
  }

  function secretRow(panel, kind) {
    const info = panel.credentials?.[kind];
    const box = h("div", { class: "exec-secret" });
    const id = `exec-secret-${panel.account_id}-${kind}`;

    async function store(value) {
      try {
        const result = await api.put(`/api/accounts/${panel.account_id}/credentials`, { [kind]: value });
        panel.credentials = result.credentials;
        changed(await api.get(`/api/accounts/${panel.account_id}/connection`), value ? t("Saved") : t("Removed"));
      } catch (error) {
        toast(error.message, "error");
      }
    }

    function showInput(focus = true) {
      const input = h("input", { id, type: "password", autocomplete: "new-password", spellcheck: false, placeholder: info?.set ? t("New value") : "" });
      const save = h("button", { type: "button", class: "btn sm primary", onclick: () => input.value.trim() && store(input.value.trim()) }, t("Save"));
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          save.click();
        }
      });
      clear(
        box,
        h("label", { for: id }, t(SECRET_LABEL[kind])),
        h("div", { class: "exec-secret-row" }, input, save, info?.set ? h("button", { type: "button", class: "btn sm ghost", onclick: showSet }, t("Cancel")) : null),
        h("div", { class: "help" }, t(SECRET_HELP[kind])),
      );
      if (focus) input.focus();
    }

    function showSet() {
      clear(
        box,
        h("span", { class: "label" }, t(SECRET_LABEL[kind])),
        h(
          "div",
          { class: "exec-secret-row" },
          h("span", { class: "chip ok" }, icon("check"), info.hint ? t("set ····{hint}", { hint: info.hint }) : t("set")),
          h("button", { type: "button", class: "btn sm", onclick: () => showInput() }, icon("edit"), t("Replace")),
          h("button", { type: "button", class: "btn sm ghost danger", onclick: () => store("") }, icon("trash"), t("Remove")),
        ),
        h("div", { class: "help" }, t(SECRET_HELP[kind])),
      );
    }

    if (info?.set) showSet();
    else showInput(false);
    return box;
  }

  // ------------------------------------------------------------- MetaTrader EA bridge
  function bridgeSection(panel) {
    const tokenInfo = panel.credentials?.bridge_token;
    const seen = panel.last_seen_seconds;
    const online = seen !== null && seen !== undefined && seen <= SEEN_OK_SECONDS;
    // MetaTrader's WebRequest list wants 127.0.0.1 rather than localhost.
    const here = (panel.server_urls?.this || "").replace(/^(https?:\/\/)localhost(?=[:/]|$)/, (_match, scheme) => `${scheme}127.0.0.1`);
    const servers = [here, ...(panel.server_urls?.lan || [])].filter((url, i, all) => url && all.indexOf(url) === i);
    const server = servers[0] || location.origin;
    const tokenBox = h("div", { class: "exec-token stack" });

    async function reveal() {
      try {
        revealed = (await api.get(`/api/accounts/${panel.account_id}/bridge-token`)).token;
        renderToken();
      } catch (error) {
        toast(error.message, "error");
      }
    }

    async function rotate() {
      if (tokenInfo?.set) {
        const ok = await confirmDialog(t("Create a new bridge token? The EA stops working until you paste the new token in its inputs."), { confirmText: t("Create new token") });
        if (!ok) return;
      }
      try {
        const result = await api.post(`/api/accounts/${panel.account_id}/bridge-token`);
        revealed = result.token;
        panel.credentials = result.credentials;
        changed(await api.get(`/api/accounts/${panel.account_id}/connection`), t("New bridge token created"));
      } catch (error) {
        toast(error.message, "error");
      }
    }

    function renderToken() {
      if (!panel.can_manage_token) {
        clear(
          tokenBox,
          h("p", { class: "small muted" }, icon("lock"), " ", tokenInfo?.set ? t("A token is set (····{hint}).", { hint: tokenInfo.hint || "" }) : t("No token yet."), " ", t("Show or create the token on the computer running Trading OS.")),
        );
        return;
      }
      clear(
        tokenBox,
        revealed ? codeLine(revealed, t("Copy the bridge token")) : h("p", { class: "small" }, tokenInfo?.set ? t("A token is set (····{hint}).", { hint: tokenInfo.hint || "" }) : t("No token yet - create one for the EA.")),
        h(
          "div",
          { class: "row" },
          tokenInfo?.set && !revealed ? h("button", { type: "button", class: "btn sm", onclick: reveal }, icon("unlock"), t("Show token")) : null,
          h("button", { type: "button", class: "btn sm", onclick: rotate }, icon("refresh"), tokenInfo?.set ? t("Create new token") : t("Create token")),
        ),
      );
    }
    renderToken();

    return h(
      "div",
      { class: "exec-section stack" },
      h(
        "div",
        { class: "row exec-row-head" },
        h("h4", {}, icon("signal"), t("MetaTrader Expert Advisor")),
        h("span", { class: "spacer" }),
        h("span", { class: `chip ${online ? "ok" : "warn"}` }, icon(online ? "check" : "clock"), t("Last seen: {when}", { when: ago(seen, panel.last_seen_at) })),
      ),
      h("div", { class: "field" }, h("span", { class: "label" }, t("Bridge token")), tokenBox),
      accordion([
        {
          title: t("Step-by-step setup guide"),
          open: true,
          body: h(
        "ol",
        { class: "exec-steps small" },
        h(
          "li",
          {},
          t("Download the EA:"),
          " ",
          h("a", { href: "/api/execution/ea/mq5", download: "TradingOS_Bridge.mq5" }, "TradingOS_Bridge.mq5"),
          " (MetaTrader 5) · ",
          h("a", { href: "/api/execution/ea/mq4", download: "TradingOS_Bridge.mq4" }, "TradingOS_Bridge.mq4"),
          " (MetaTrader 4)",
        ),
        h("li", {}, t("In MetaTrader: File > Open Data Folder > MQL5 (or MQL4) > Experts, copy the file there, open it in MetaEditor and press Compile.")),
        h("li", {}, t("Tools > Options > Expert Advisors: tick Allow WebRequest for listed URL and add this address (from a VPS, the address of this computer):"), servers.map((url) => codeLine(url))),
        h("li", {}, t("Drag the EA onto one chart, paste the server address and the token in its inputs and switch Algo Trading on.")),
        h("li", {}, h("strong", {}, t("Test on a demo account first.")), " ", t("Then press Test connection here: the EA polls every few seconds.")),
      ),
        },
      ]),
      h("div", { class: "small muted" }, t("Poll URL (the EA sends the token in the X-Bridge-Token header):"), codeLine(`${server}/api/bridge/orders`)),
    );
  }

  // ------------------------------------------------------------- test
  function testSection(panel) {
    const result = h("div", { class: "exec-test-result", "aria-live": "polite" });
    const button = h(
      "button",
      {
        type: "button",
        class: "btn sm",
        onclick: async () => {
          button.disabled = true;
          clear(result, h("span", { class: "muted small" }, t("Testing…")));
          try {
            const outcome = await api.post(`/api/accounts/${panel.account_id}/test`);
            lastTest = { ok: outcome.ok, message: outcome.message };
            changed(outcome.panel, null);
          } catch (error) {
            lastTest = { ok: false, message: error.message };
            showResult();
          } finally {
            button.disabled = false;
          }
        },
      },
      icon("refresh"),
      t("Test connection"),
    );
    function showResult() {
      clear(result, lastTest ? h("div", { class: `exec-note ${lastTest.ok ? "ok" : "bad"}` }, icon(lastTest.ok ? "checkCircle" : "xCircle"), lastTest.message) : null);
    }
    showResult();
    const snapshot = panel.snapshot || {};
    return h(
      "div",
      { class: "exec-section stack" },
      h("div", { class: "row" }, button, panel.snapshot_at && snapshot.equity !== undefined ? h("span", { class: "small muted" }, t("Broker equity {equity} · {when}", { equity: fmt.num(snapshot.equity, 2), when: fmt.datetime(panel.snapshot_at) })) : null),
      result,
    );
  }

  // ------------------------------------------------------------- the order-sending switch
  function sendingSection(panel) {
    const live = panel.connection !== "paper";
    async function put(enabled, confirm = "") {
      try {
        const next = await api.put(`/api/accounts/${panel.account_id}/execution`, { enabled, confirm });
        changed(next, enabled ? t("Order sending is on for {name}", { name: next.name }) : t("Order sending is off for {name}", { name: next.name }), enabled ? "success" : "info");
      } catch (error) {
        toast(error.message, "error");
      }
    }
    async function turnOn() {
      if (!live) return put(true);
      const typed = await typedConfirmation(panel);
      if (typed !== null) await put(true, typed);
    }
    return h(
      "div",
      { class: `exec-section stack exec-switch${panel.execution_enabled ? " on" : ""}` },
      h("h4", {}, icon("power"), t("Order sending")),
      h("p", { class: "small muted" }, t("When it is on, Execute on {name} on the Signals page sends the order to this account after you confirm it. Your guardrails run first: a blocked trade is never sent.", { name: panel.name })),
      panel.missing ? h("div", { class: "exec-note warn" }, icon("info"), panel.missing) : null,
      h(
        "div",
        { class: "row" },
        panel.execution_enabled
          ? h("button", { type: "button", class: "btn sm", onclick: () => put(false) }, icon("lock"), t("Turn order sending off"))
          : h("button", { type: "button", class: `btn sm ${live ? "danger" : "primary"}`, disabled: !panel.ready, onclick: turnOn }, icon("zap"), t("Turn order sending on")),
        live && !panel.execution_enabled ? h("span", { class: "small muted" }, t("You will type the account name to confirm.")) : null,
      ),
    );
  }

  // ------------------------------------------------------------- the execution log
  function logSection(panel) {
    const rows = panel.executions || [];
    const body = rows.length
      ? h(
          "div",
          { class: "table-wrap" },
          h(
            "table",
            { class: "exec-log" },
            h("thead", {}, h("tr", {}, [t("Time"), t("Order"), t("Lots"), t("Price"), t("Status"), t("Ticket"), ""].map((label, i) => h("th", { class: i === 2 || i === 3 ? "num" : "" }, label)))),
            h(
              "tbody",
              {},
              rows.map((row) =>
                h(
                  "tr",
                  {},
                  h("td", {}, fmt.datetime(row.created_at)),
                  h("td", {}, h("span", { class: `badge ${row.direction}` }, row.direction === "long" ? t("Buy") : t("Sell")), " ", row.broker_symbol || row.symbol, h("span", { class: "muted small" }, ` · ${t(ORDER_TYPE_LABEL[row.order_type] || row.order_type)}`)),
                  h("td", { class: "num" }, fmt.lots(row.lots)),
                  h("td", { class: "num" }, row.fill_price !== null ? fmt.price(row.fill_price) : fmt.price(row.price)),
                  h("td", { class: "exec-status" }, statusBadge(row.status), row.message ? h("div", { class: "muted small exec-message" }, row.message) : null),
                  h("td", {}, row.broker_ref || "—"),
                  h(
                    "td",
                    {},
                    row.cancellable
                      ? h(
                          "button",
                          {
                            type: "button",
                            class: "btn sm ghost",
                            onclick: async () => {
                              try {
                                await api.post(`/api/executions/${row.id}/cancel`);
                                toast(t("Order cancelled"), "success");
                                load();
                              } catch (error) {
                                toast(error.message, "error");
                              }
                            },
                          },
                          icon("x"),
                          t("Cancel order"),
                        )
                      : null,
                  ),
                ),
              ),
            ),
          ),
        )
      : h("p", { class: "muted small" }, t("No orders sent from this account yet."));
    return h(
      "div",
      { class: "exec-section stack" },
      h("div", { class: "row exec-row-head" }, h("h4", {}, icon("list"), t("Execution log")), h("span", { class: "spacer" }), h("button", { type: "button", class: "btn ghost sm", onclick: load }, icon("refresh"), t("Refresh"))),
      body,
    );
  }

  load();
  return root;
}

/** Ask for the account's exact name before real orders can be sent; resolves to the typed text or null. */
function typedConfirmation(panel) {
  return new Promise((resolve) => {
    let answered = false;
    const id = `exec-confirm-${panel.account_id}`;
    const input = h("input", { id, type: "text", autocomplete: "off", spellcheck: false });
    const confirm = h("button", { type: "button", class: "btn danger", disabled: true }, icon("zap"), t("Turn order sending on"));
    input.addEventListener("input", () => (confirm.disabled = input.value.trim() !== panel.name));
    const ref = modal({
      title: t("Send real orders to {name}?", { name: panel.name }),
      body: h(
        "div",
        { class: "stack" },
        h("div", { class: "exec-note warn" }, icon("alert"), t("Confirmed signals will be sent as orders through {connection}. Test on a demo account first.", { connection: connectionLabel(panel.connection) })),
        h("label", { for: id }, t("Type the account name to confirm: {name}", { name: panel.name })),
        input,
      ),
      footer: [
        h("button", { type: "button", class: "btn ghost", onclick: () => finish(null) }, t("Cancel")),
        confirm,
      ],
      onClose: () => answered || resolve(null),
    });
    confirm.addEventListener("click", () => finish(input.value.trim()));
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !confirm.disabled) finish(input.value.trim());
    });
    function finish(value) {
      answered = true;
      resolve(value);
      ref.close();
    }
  });
}
