// "Bind to Account" card for an incubator/live strategy (v1.7): link the strategy to a trading account so its
// alerts can be sent there automatically. Creating or editing the binding never sends an order; automation is
// armed separately (the typed account name for a real account) and still needs the account's order sending on.

import { api } from "../api.js";
import { clear, confirmDialog, field, h, icon, modal, select, toast } from "../dom.js";
import { fmt } from "../format.js";
import { N_, t } from "../i18n.js";

const ORDER_TYPES = [
  ["market", N_("Market")],
  ["limit", N_("Limit")],
];

/** Mount the binding card for ``strategyId`` into ``container``. Returns a reload function. */
export async function mountBindingCard(container, strategyId) {
  async function load() {
    const [accountsData, bindingData] = await Promise.all([api.get("/api/accounts"), api.get(`/api/bindings/strategy/${strategyId}`)]);
    draw(accountsData.accounts, bindingData.binding);
  }

  function draw(allAccounts, binding) {
    // Only accounts that can send orders (connected, active) are useful targets; demo/paper included.
    const targets = allAccounts.filter((a) => a.status === "active" && a.connection && a.connection !== "none");
    const body = h("div", { class: "stack", style: { gap: "12px" } });
    const card = h(
      "div",
      { class: "card bind-card" },
      h(
        "div",
        { class: "card-head", style: { flexWrap: "wrap" } },
        h("div", {}, h("h2", {}, icon("plug"), t("Bind to account")), h("p", {}, t("Send this strategy's alerts to a linked trading account, automatically, with its own risk."))),
        binding ? statusBadge(binding) : null,
      ),
      body,
    );

    if (!targets.length) {
      clear(
        body,
        h("div", { class: "empty small" }, t("No linked account yet.")),
        h("a", { class: "btn sm", href: "#/accounts" }, icon("wallet"), t("Set up a trading account")),
      );
      clear(container, card);
      return;
    }

    const current = binding || {};
    const accountSel = select(targets.map((a) => [String(a.id), accountLabel(a)]), String(current.account_id || targets[0].id));
    const risk = h("input", { type: "number", step: "0.05", min: "0.05", max: "20", value: current.risk_pct ?? 1.0, inputmode: "decimal" });
    const maxLots = h("input", { type: "number", step: "any", min: "0", value: current.max_lots_per_trade ?? "", placeholder: t("no cap") });
    const orderType = select(ORDER_TYPES.map(([v, l]) => [v, t(l)]), current.order_type || "market");
    const maxTrades = h("input", { type: "number", step: "1", min: "0", max: "50", value: current.max_trades_per_day ?? 3, inputmode: "numeric" });
    const requireChecklist = h("input", { type: "checkbox", checked: current.require_checklist ?? true });
    const notify = h("input", { type: "checkbox", checked: current.notify ?? true });

    const save = h(
      "button",
      { class: "btn primary", type: "button" },
      icon(binding ? "check" : "plug"),
      binding ? t("Save binding") : t("Bind strategy"),
    );
    save.addEventListener("click", async () => {
      save.disabled = true;
      try {
        await api.put(`/api/bindings/strategy/${strategyId}`, {
          account_id: Number(accountSel.value),
          risk_pct: Number(risk.value) || 1.0,
          max_lots_per_trade: maxLots.value === "" ? null : Number(maxLots.value),
          order_type: orderType.value,
          max_trades_per_day: Math.round(Number(maxTrades.value) || 0),
          require_checklist: requireChecklist.checked,
          notify: notify.checked,
          enabled: true,
        });
        toast(t("Binding saved"), "success");
        await load();
      } catch (error) {
        toast(error.message, "error");
      } finally {
        save.disabled = false;
      }
    });

    const controls = h(
      "div",
      { class: "form-grid bind-form" },
      field(t("Account"), accountSel),
      field(t("Risk per trade (%)"), risk, { help: t("Each auto order risks this share of the account's equity") }),
      field(t("Max lots per order"), maxLots),
      field(t("Order type"), orderType),
      field(t("Max auto orders a day"), maxTrades, { help: t("0 = only the trading plan's limits apply") }),
      h("label", { class: "row bind-check" }, requireChecklist, t("Only after the pre-market checklist")),
      h("label", { class: "row bind-check" }, notify, t("Notify on each auto order")),
    );

    clear(body, controls, h("div", { class: "row bind-actions" }, save, binding ? autoRow(binding, load) : null, binding ? removeButton(binding, load) : null));
    clear(container, card);
  }

  await load();
  return load;
}

function accountLabel(account) {
  const bits = [account.name];
  if (account.connection === "paper") bits.push(t("paper"));
  else if (account.connection) bits.push(account.connection.replace("_", " "));
  return `${bits.join(" · ")}${account.execution_enabled ? "" : " — " + t("sending off")}`;
}

function statusBadge(binding) {
  if (binding.auto_execute) return h("span", { class: "badge good" }, icon("power"), t("Automatic"));
  return h("span", { class: "badge" }, icon("pause"), t("Manual only"));
}

function autoRow(binding, reload) {
  if (binding.auto_execute) {
    const off = h("button", { class: "btn sm", type: "button" }, icon("pause"), t("Turn automatic off"));
    off.addEventListener("click", async () => {
      await api.post(`/api/bindings/${binding.id}/arm`, { auto_execute: false });
      toast(t("Automatic execution off"), "success");
      await reload();
    });
    return h(
      "div",
      { class: "bind-auto on" },
      h("span", { class: "row" }, icon("power"), t("Auto-executes validated setups on {name}", { name: binding.account_name })),
      off,
      binding.last_result ? h("div", { class: "muted small" }, t("Last: {msg}", { msg: binding.last_result }) + (binding.last_auto_at ? ` · ${fmt.datetime(binding.last_auto_at)}` : "")) : null,
    );
  }
  const on = h("button", { class: "btn primary sm", type: "button", disabled: !binding.can_arm }, icon("power"), t("Turn automatic on"));
  on.addEventListener("click", () => armDialog(binding, reload));
  return h(
    "div",
    { class: "bind-auto" },
    on,
    binding.can_arm
      ? h("span", { class: "muted small" }, t("Orders are sent only when a scanner alert for this strategy validates. Every order passes your guardrails."))
      : h("a", { class: "muted small", href: "#/accounts" }, binding.arming_problem || t("Switch the account's order sending on first.")),
  );
}

function armDialog(binding, reload) {
  const paper = binding.account_paper;
  const confirm = h("input", { placeholder: binding.account_name, autocomplete: "off", spellcheck: "false" });
  const go = h("button", { class: "btn primary", type: "button" }, icon("power"), t("Arm automatic execution"));
  go.addEventListener("click", async () => {
    go.disabled = true;
    try {
      await api.post(`/api/bindings/${binding.id}/arm`, { auto_execute: true, confirm: confirm.value });
      ref.close();
      toast(t("Automatic execution armed"), "success");
      await reload();
    } catch (error) {
      toast(error.message, "error");
      go.disabled = false;
    }
  });
  const ref = modal({
    title: t("Arm automatic execution"),
    body: h(
      "div",
      { class: "stack", style: { gap: "12px" } },
      h("div", { class: "banner warn", role: "note" }, icon("alert"), h("div", {}, paper ? t("Paper account: orders are simulated, nothing reaches a broker.") : t("Real orders will be placed on {name} without asking, whenever a setup for this strategy validates. Your guardrails and the daily cap still apply.", { name: binding.account_name }))),
      paper ? null : field(t("Type the account name to confirm"), confirm),
    ),
    footer: [h("button", { class: "btn ghost", type: "button", onclick: () => ref.close() }, t("Cancel")), go],
  });
}

function removeButton(binding, reload) {
  const remove = h("button", { class: "btn ghost sm", type: "button" }, icon("trash"), t("Unbind"));
  remove.addEventListener("click", async () => {
    if (!(await confirmDialog(t("Remove this binding? Automatic execution stops."), { title: t("Unbind"), confirmText: t("Unbind"), danger: true }))) return;
    await api.del(`/api/bindings/${binding.id}`);
    toast(t("Binding removed"), "success");
    await reload();
  });
  return remove;
}
