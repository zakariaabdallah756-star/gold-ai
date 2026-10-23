// Trading Accounts (v1.5): register prop firm challenges, live and demo broker accounts; edit, archive or delete
// them and pick the one the dashboard shows. Each card mounts the account's connection & order routing panel
// (views/account-connection.js). Secrets go through that panel, are encrypted on this computer and never exported.

import { accountMoney, colorDot, connectionChip, kindBadge, label, PLATFORM_LABEL } from "../accounts-ui.js";
import { api } from "../api.js";
import { clear, confirmDialog, field, h, icon, modal, select, toast } from "../dom.js";
import { N_, t, tn } from "../i18n.js";
import { loadPlan, loadStatus, reloadAccounts, setActiveAccount, state } from "../state.js";
import { renderConnection } from "./account-connection.js";

const KIND_OPTIONS = [
  ["live", N_("Live (real money)")],
  ["demo", N_("Demo")],
  ["prop", N_("Prop firm challenge")],
  ["funded", N_("Funded (prop firm)")],
];
const PLATFORMS = ["mt5", "mt4", "ctrader", "tradingview", "api", "other"];
const SWATCHES = ["#26a69a", "#3987e5", "#fab219", "#9575cd", "#ef5350", "#ec6fa5", "#66bb6a", "#898781"];
const FIELDS = ["name", "kind", "broker", "platform", "login", "server", "currency", "starting_balance", "leverage", "color", "status", "notes"];
const DEFAULTS = { name: "", kind: "live", broker: "", platform: "mt5", login: "", server: "", starting_balance: 10_000, leverage: 100, color: "#26a69a", status: "active", notes: "" };
const TEMPLATES = [
  {
    key: "prop",
    icon: "shield",
    title: N_("Prop firm challenge"),
    text: N_("A funded-account evaluation with daily loss and drawdown rules"),
    name: N_("Prop Firm Challenge {n}"),
    values: { kind: "prop", platform: "mt5", color: "#3987e5" },
  },
  {
    key: "live",
    icon: "zap",
    title: N_("Live broker account"),
    text: N_("Real money at your broker"),
    name: N_("Live Broker Account {n}"),
    values: { kind: "live", platform: "mt5", color: "#26a69a" },
  },
  {
    key: "demo",
    icon: "play",
    title: N_("Demo account"),
    text: N_("Practice and test strategies without risk"),
    name: N_("Demo Account {n}"),
    values: { kind: "demo", platform: "mt5", color: "#9575cd" },
  },
];

const payloadOf = (account, overrides = {}) => ({ ...Object.fromEntries(FIELDS.map((key) => [key, account[key]])), ...overrides });

/** "Prop Firm Challenge 2" when "Prop Firm Challenge 1" is taken. */
function suggestedName(template, taken) {
  for (let n = 1; n < 100; n += 1) {
    const name = t(template.name, { n });
    if (!taken.has(name.toLowerCase())) return name;
  }
  return "";
}

// ------------------------------------------------------------------ editor

function colorPicker(value) {
  const input = h("input", { type: "color", value, class: "acct-color-input", "aria-label": t("Custom colour") });
  const swatches = SWATCHES.map((color) =>
    h("button", { type: "button", class: "acct-swatch", style: { background: color }, "aria-label": t("Colour {color}", { color }), title: color, onclick: () => set(color) }),
  );
  function set(color) {
    input.value = color;
    for (const swatch of swatches) swatch.setAttribute("aria-pressed", String(swatch.title.toLowerCase() === color.toLowerCase()));
  }
  input.addEventListener("input", () => set(input.value));
  set(value);
  return { el: h("div", { class: "acct-colors", role: "group", "aria-label": t("Colour") }, swatches, input), input, set };
}

function openEditor(account, { taken, template = null, onSaved }) {
  const isNew = !account;
  const values = isNew ? { ...DEFAULTS, currency: state.plan.settings.currency, ...(template?.values || {}) } : payloadOf(account);
  let suggestion = isNew && template ? suggestedName(template, taken) : "";
  if (suggestion) values.name = suggestion;
  const currencies = state.plan.enums.currencies || [];

  const inputs = {
    name: h("input", { type: "text", maxlength: "80", value: values.name, required: true, autocomplete: "off", placeholder: t("e.g. FTMO 100k Challenge") }),
    kind: select(KIND_OPTIONS.map(([value, text]) => [value, t(text)]), values.kind),
    broker: h("input", { type: "text", maxlength: "60", value: values.broker, placeholder: t("e.g. FTMO, IC Markets") }),
    platform: select(PLATFORMS.map((value) => [value, label(PLATFORM_LABEL, value)]), values.platform),
    login: h("input", { type: "text", maxlength: "40", value: values.login, autocomplete: "off", spellcheck: false }),
    server: h("input", { type: "text", maxlength: "80", value: values.server, autocomplete: "off", spellcheck: false, placeholder: t("e.g. FTMO-Server3") }),
    currency: select(currencies.includes(values.currency) ? currencies : [values.currency, ...currencies], values.currency),
    starting_balance: h("input", { type: "number", min: "0", step: "any", value: values.starting_balance, required: true }),
    leverage: h("input", { type: "number", min: "1", max: "3000", step: "1", value: values.leverage }),
    status: select(
      [
        ["active", t("Active")],
        ["archived", t("Archived account")],
      ],
      values.status,
    ),
    notes: h("textarea", { rows: 3, value: values.notes, placeholder: t("Rules, payout dates, anything worth remembering") }),
  };
  const colors = colorPicker(values.color);
  if (account?.is_default) inputs.status.disabled = true;

  const applyTemplate = (next) => {
    for (const [key, value] of Object.entries(next.values)) {
      if (key === "color") colors.set(value);
      else if (inputs[key]) inputs[key].value = value;
    }
    // Replace the name only while it is still a suggestion (never what the trader typed).
    if (!inputs.name.value.trim() || inputs.name.value === suggestion) {
      suggestion = suggestedName(next, taken);
      inputs.name.value = suggestion;
    }
    for (const tile of templateTiles) tile.setAttribute("aria-pressed", String(tile.dataset.key === next.key));
  };
  const templateTiles = isNew
    ? TEMPLATES.map((item) =>
        h(
          "button",
          { type: "button", class: "acct-template mini", dataset: { key: item.key }, "aria-pressed": String(item.key === template?.key), onclick: () => applyTemplate(item) },
          icon(item.icon),
          h("span", {}, t(item.title)),
        ),
      )
    : [];

  const save = h("button", { class: "btn primary", type: "button" }, icon("check"), isNew ? t("Add account") : t("Save"));
  const body = h(
    "div",
    { class: "stack acct-editor" },
    isNew ? h("div", {}, h("div", { class: "section-title" }, t("Start from a template")), h("div", { class: "acct-templates mini", role: "group", "aria-label": t("Templates") }, templateTiles)) : null,
    account?.is_default
      ? h("div", { class: "acct-note" }, icon("info"), h("span", {}, t("This is your default account: its name, currency and starting balance are also your Trading Plan → Account settings. It cannot be archived or deleted.")))
      : null,
    h(
      "div",
      { class: "form-grid" },
      field(t("Name"), inputs.name, { full: true }),
      field(t("Type"), inputs.kind),
      field(t("Platform"), inputs.platform),
      field(t("Broker or prop firm"), inputs.broker),
      field(t("Login / account number"), inputs.login),
      field(t("Server"), inputs.server),
      field(t("Currency"), inputs.currency),
      field(t("Starting balance"), inputs.starting_balance),
      field(t("Leverage (1 : x)"), inputs.leverage),
      field(t("Status"), inputs.status, { help: account?.is_default ? t("The default account stays active") : t("Archived accounts leave the switcher and All accounts; their journal stays") }),
      h("div", { class: "field full" }, h("span", { class: "label" }, t("Colour")), colors.el),
      field(t("Notes"), inputs.notes, { full: true }),
    ),
  );

  save.addEventListener("click", async () => {
    const payload = {
      name: inputs.name.value.trim(),
      kind: inputs.kind.value,
      broker: inputs.broker.value.trim(),
      platform: inputs.platform.value,
      login: inputs.login.value.trim(),
      server: inputs.server.value.trim(),
      currency: inputs.currency.value,
      starting_balance: Number(String(inputs.starting_balance.value).replace(",", ".")),
      leverage: Math.round(Number(inputs.leverage.value)),
      color: colors.input.value,
      status: inputs.status.value,
      notes: inputs.notes.value,
    };
    if (!payload.name) {
      toast(t("Give the account a name"), "error");
      inputs.name.focus();
      return;
    }
    if (!(payload.starting_balance > 0)) {
      toast(t("The starting balance must be above zero"), "error");
      inputs.starting_balance.focus();
      return;
    }
    if (!(payload.leverage >= 1 && payload.leverage <= 3000)) {
      toast(t("Leverage must be between 1 and 3000"), "error");
      inputs.leverage.focus();
      return;
    }
    save.disabled = true;
    try {
      if (isNew) await api.post("/api/accounts", payload);
      else await api.put(`/api/accounts/${account.id}`, payload);
      ref.close();
      toast(isNew ? t("Account added: {name}", { name: payload.name }) : t("Account saved"), "success");
      await onSaved({ plan: Boolean(account?.is_default) });
    } catch (error) {
      toast(error.message, "error");
    } finally {
      save.disabled = false;
    }
  });

  const ref = modal({
    title: isNew ? t("Add a trading account") : t("Edit {name}", { name: account.name }),
    body,
    footer: [h("button", { class: "btn ghost", type: "button", onclick: () => ref.close() }, t("Cancel")), save],
  });
  if (isNew) requestAnimationFrame(() => inputs.name.focus());
}

// ------------------------------------------------------------------ cards

function metric(text, value, { cls = "", sub = null } = {}) {
  return h("div", { class: "acct-metric" }, h("div", { class: "label" }, text), h("div", { class: `value num ${cls}` }, value), sub ? h("div", { class: "sub" }, sub) : null);
}

function facts(account) {
  const items = [
    [t("Broker"), account.broker],
    [t("Platform"), account.platform === "other" ? "" : label(PLATFORM_LABEL, account.platform)],
    [t("Login"), account.login],
    [t("Server"), account.server],
    [t("Currency"), account.currency],
    [t("Leverage"), `1:${account.leverage}`],
    [t("Starting balance"), accountMoney(account.starting_balance, account.currency)],
    [t("Trades"), String(account.trades)],
  ].filter(([, value]) => value);
  return h("dl", { class: "acct-facts" }, items.map(([name, value]) => h("div", {}, h("dt", {}, name), h("dd", {}, value))));
}

function accountCard(account, actions) {
  const m = account.metrics || {};
  const money = (value, signed = false) => accountMoney(value, account.currency, { signed });
  const archived = account.status !== "active";
  const isDefault = account.is_default;

  const show = account.is_active
    ? h("a", { class: "btn sm", href: "#/dashboard" }, icon("dashboard"), t("Open dashboard"))
    : h("button", { class: "btn sm acct-show", type: "button", onclick: () => actions.show(account) }, icon("eye"), t("Show on dashboard"));
  const edit = h("button", { class: "btn sm", type: "button", onclick: () => actions.edit(account) }, icon("edit"), t("Edit"));
  const archive = h(
    "button",
    {
      class: "btn sm",
      type: "button",
      disabled: isDefault,
      title: isDefault ? t("The default account cannot be archived") : null,
      onclick: () => actions.setStatus(account, archived ? "active" : "archived"),
    },
    icon(archived ? "undo" : "archive"),
    archived ? t("Reactivate") : t("Archive"),
  );
  const remove = h(
    "button",
    {
      class: "btn sm danger icon-only",
      type: "button",
      disabled: isDefault,
      "aria-label": t("Delete {name}", { name: account.name }),
      title: isDefault ? t("The default account cannot be deleted") : t("Delete {name}", { name: account.name }),
      onclick: () => actions.remove(account),
    },
    icon("trash"),
  );

  const badges = [
    kindBadge(account.kind),
    isDefault ? h("span", { class: "badge", title: t("Mirrors Trading Plan → Account: name, currency and starting balance") }, icon("star"), t("Default")) : null,
    account.is_active ? h("span", { class: "badge open" }, icon("eye"), t("Shown")) : null,
    archived ? h("span", { class: "badge warn" }, icon("archive"), t("Archived account")) : null,
    account.prop_challenge_id ? h("a", { class: "badge acct-prop-link", href: "#/prop" }, icon("target"), t("Prop challenge")) : null,
  ];
  const marginSub = m.margin_estimated ? h("span", { class: "badge warn" }, t("estimated")) : m.margin > 0 && m.margin_level ? t("Level {level}", { level: `${Math.round(m.margin_level)}%` }) : null;
  const panel = renderConnection(account, { onSaved: actions.reload });

  return h(
    "article",
    { class: `card acct-card${archived ? " archived" : ""}`, style: { "--acct": account.color || "var(--muted)" }, "aria-labelledby": `acct-name-${account.id}` },
    h(
      "div",
      { class: "acct-card-head" },
      h(
        "div",
        { class: "acct-card-title" },
        colorDot(account.color),
        h("h2", { id: `acct-name-${account.id}` }, account.name),
        h("div", { class: "acct-badges" }, badges),
      ),
      h("div", { class: "acct-card-actions" }, show, edit, archive, remove),
    ),
    facts(account),
    h(
      "div",
      { class: "acct-metrics" },
      metric(t("Balance"), money(m.balance)),
      metric(t("Equity"), money(m.equity), { sub: h("span", { class: m.change > 0 ? "pos" : m.change < 0 ? "neg" : "flat" }, money(m.change, true)) }),
      metric(t("Floating P&L"), money(m.floating_pnl, true), { cls: m.floating_pnl > 0 ? "pos" : m.floating_pnl < 0 ? "neg" : "", sub: tn(m.open_positions || 0, "{n} open position", "{n} open positions") }),
      metric(t("Margin"), money(m.margin), { sub: marginSub }),
    ),
    h(
      "div",
      { class: "acct-status-row" },
      connectionChip(account),
      h("span", { class: `chip${m.source === "bridge" ? " ok" : ""}` }, icon(m.source === "bridge" ? "zap" : "journal"), m.source === "bridge" ? t("Live figures") : t("From journal")),
    ),
    account.notes ? h("p", { class: "acct-notes muted small" }, account.notes) : null,
    panel ? h("div", { class: "acct-connection" }, panel) : null,
  );
}

// ------------------------------------------------------------------ page

export async function render(root) {
  const list = h("div", { class: "acct-list" });
  const overview = h("div", { class: "acct-overview" });
  let data = null;

  const taken = () => new Set((data?.accounts || []).map((account) => account.name.toLowerCase()));

  async function load() {
    data = await reloadAccounts();
    draw();
  }

  /** After any change: the accounts list here, the plan when the default account changed, the top bar. */
  async function changed({ plan = false } = {}) {
    try {
      if (plan) await loadPlan();
      await load();
    } catch (error) {
      toast(error.message, "error");
    }
    loadStatus().catch(() => {}); // status strip, switcher equities and profile initials follow
  }

  const actions = {
    reload: () => changed(),
    edit: (account) => openEditor(account, { taken: taken(), onSaved: changed }),
    async show(account) {
      try {
        await setActiveAccount(account.id);
        location.hash = "#/dashboard";
      } catch (error) {
        toast(error.message, "error");
      }
    },
    async setStatus(account, status) {
      if (status === "archived") {
        const ok = await confirmDialog(t("Archive {name}? It leaves the account switcher and All accounts. Its journal history stays and you can reactivate it any time.", { name: account.name }), {
          title: t("Archive account"),
          confirmText: t("Archive"),
        });
        if (!ok) return;
      }
      try {
        await api.put(`/api/accounts/${account.id}`, payloadOf(account, { status }));
        toast(status === "archived" ? t("{name} archived", { name: account.name }) : t("{name} reactivated", { name: account.name }), "success");
        await changed({ plan: account.is_default });
      } catch (error) {
        toast(error.message, "error");
      }
    },
    async remove(account) {
      const ok = await confirmDialog(t("Delete {name}? Its connection settings and stored secrets are removed too. This cannot be undone.", { name: account.name }), {
        title: t("Delete account"),
        confirmText: t("Delete"),
        danger: true,
      });
      if (!ok) return;
      try {
        await api.del(`/api/accounts/${account.id}`);
        toast(t("{name} deleted", { name: account.name }), "success");
        await changed();
      } catch (error) {
        // 409: trades are logged on it (journal history is never deleted) - offer to archive it instead.
        if (error.status === 409 && account.trades && account.status === "active" && !account.is_default) {
          const archive = await confirmDialog(error.message, { title: t("This account has journal history"), confirmText: t("Archive instead") });
          if (archive) {
            try {
              await api.put(`/api/accounts/${account.id}`, payloadOf(account, { status: "archived" }));
              toast(t("{name} archived", { name: account.name }), "success");
              await changed();
            } catch (inner) {
              toast(inner.message, "error");
            }
          }
        } else {
          toast(error.message, "error", 7000);
        }
      }
    },
  };

  const add = (template = null) => openEditor(null, { taken: taken(), template, onSaved: changed });

  function draw() {
    const all = data.accounts;
    const active = all.filter((account) => account.status === "active");
    const archived = all.filter((account) => account.status !== "active");
    const shown = all.find((account) => account.id === data.active_account_id);
    clear(
      overview,
      h(
        "div",
        { class: "acct-overview-text" },
        h("span", { class: "muted" }, t("Dashboard shows")),
        shown ? [colorDot(shown.color), h("strong", {}, shown.name)] : [icon("layers"), h("strong", {}, t("All accounts"))],
        h("span", { class: "muted" }, "·"),
        h("span", { class: "muted" }, [tn(active.length, "{n} active account", "{n} active accounts"), archived.length ? ` · ${tn(archived.length, "{n} archived", "{n} archived")}` : ""]),
      ),
      shown ? h("button", { class: "btn sm ghost", type: "button", onclick: () => actions.showAll() }, icon("layers"), t("Show all accounts")) : null,
    );
    clear(
      list,
      active.map((account) => accountCard(account, actions)),
      archived.length ? [h("h2", { class: "acct-section" }, icon("archive"), tn(archived.length, "Archived account ({n})", "Archived accounts ({n})")), archived.map((account) => accountCard(account, actions))] : null,
    );
  }

  actions.showAll = async () => {
    try {
      await setActiveAccount(null);
      await load();
    } catch (error) {
      toast(error.message, "error");
    }
  };

  const templates = h(
    "div",
    { class: "acct-templates", role: "group", "aria-label": t("Quick add") },
    TEMPLATES.map((template) =>
      h(
        "button",
        { type: "button", class: "acct-template", onclick: () => add(template) },
        h("span", { class: `acct-template-icon kind-${template.values.kind}` }, icon(template.icon)),
        h("span", { class: "acct-template-text" }, h("strong", {}, t(template.title)), h("span", { class: "muted small" }, t(template.text))),
        icon("plus", "icon acct-template-plus"),
      ),
    ),
  );

  clear(
    root,
    h(
      "div",
      { class: "page-head" },
      h("div", {}, h("h1", {}, t("Trading accounts")), h("p", {}, t("Register every account you trade and switch between them from the top bar. Each account keeps its own equity, daily loss limit and trade count."))),
      h("button", { class: "btn primary", type: "button", onclick: () => add() }, icon("plus"), t("Add account")),
    ),
    h(
      "div",
      { class: "stack" },
      secretsNote(),
      overview,
      list,
      h("section", { class: "acct-add", "aria-labelledby": "acct-add-title" }, h("h2", { class: "acct-section", id: "acct-add-title" }, icon("plus"), t("Quick add")), templates),
    ),
  );
  await load();
}

function secretsNote() {
  return h(
    "div",
    { class: "acct-secure" },
    icon("key"),
    h(
      "div",
      {},
      h("strong", {}, t("Your credentials stay on this computer")),
      h("p", { class: "muted small" }, t("Passwords, API tokens and bridge secrets are stored encrypted on this computer and are never exported: backups and CSV exports leave them out, and the app never shows them again after you save them.")),
    ),
  );
}
