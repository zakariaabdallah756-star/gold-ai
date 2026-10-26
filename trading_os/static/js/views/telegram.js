// Telegram signal listener (v1.7). Bot API only: the listener reads what a Telegram bot is sent (channels and
// groups it is in, forwards, direct messages). Captured signals are shown here; one tap loads a signal into the
// Signals ticket. An optional, guarded auto-execution sends clean signals to a chosen account on its own.

import { api } from "../api.js";
import { clear, confirmDialog, field, h, icon, modal, select, toast } from "../dom.js";
import { fmt } from "../format.js";
import { N_, t, tn } from "../i18n.js";
import { accounts, handoff } from "../state.js";

const SOURCE_LABEL = { channel: N_("Channel"), group: N_("Group"), forward: N_("Forward"), direct: N_("Direct message") };
const AUTO_STATUS = { executed: "good", blocked: "bad", error: "bad", skipped: "" };

export async function render(root) {
  let status = await api.get("/api/telegram/status");
  let signals = (await api.get("/api/telegram/signals")).items;
  let busy = false;

  async function reload() {
    [status, signals] = await Promise.all([api.get("/api/telegram/status"), api.get("/api/telegram/signals").then((r) => r.items)]);
    draw();
  }

  async function pollNow(button) {
    busy = true;
    button.disabled = true;
    try {
      const result = await api.post("/api/telegram/poll");
      toast(result.captured ? tn(result.captured, "Captured {n} signal", "Captured {n} signals") : t("No new messages"), "success");
      await reload();
    } catch (error) {
      toast(error.message, "error");
    } finally {
      busy = false;
    }
  }

  // ---------------------------------------------------------------------- setup card

  function setupCard() {
    const token = h("input", { type: "password", autocomplete: "off", placeholder: status.token_set ? `•••• ${status.token_hint}` : "123456789:AA…", maxlength: "100" });
    const saveToken = h("button", { class: "btn primary sm", type: "button" }, icon("key"), status.token_set ? t("Replace token") : t("Save token"));
    saveToken.addEventListener("click", async () => {
      saveToken.disabled = true;
      try {
        const result = await api.put("/api/telegram/token", { token: token.value.trim() });
        toast(result.bot_username ? t("Connected as @{name}", { name: result.bot_username }) : t("Token removed"), "success");
        token.value = "";
        await reload();
      } catch (error) {
        toast(error.message, "error");
        saveToken.disabled = false;
      }
    });
    const removeToken = status.token_set
      ? h("button", { class: "btn ghost sm", type: "button", onclick: async () => { await api.put("/api/telegram/token", { token: "" }); toast(t("Token removed"), "success"); reload(); } }, t("Remove"))
      : null;

    const enable = h("input", { type: "checkbox", role: "switch", checked: status.enabled, disabled: !status.token_set });
    enable.addEventListener("change", async () => {
      try {
        await api.put("/api/telegram/config", { enabled: enable.checked });
        await reload();
      } catch (error) {
        toast(error.message, "error");
        enable.checked = !enable.checked;
      }
    });

    const chats = h("input", { value: (status.allowed_chats || []).join(", "), placeholder: t("any chat the bot is in"), maxlength: "600" });
    chats.addEventListener("change", async () => {
      const list = chats.value.split(",").map((c) => c.trim()).filter(Boolean);
      await api.put("/api/telegram/config", { allowed_chats: list });
      reload();
    });
    const skipProtected = h("input", { type: "checkbox", checked: status.skip_protected });
    skipProtected.addEventListener("change", () => api.put("/api/telegram/config", { skip_protected: skipProtected.checked }).then(reload));

    const poll = h("button", { class: "btn sm", type: "button", disabled: !status.token_set || busy }, icon("refresh"), t("Check now"));
    poll.addEventListener("click", () => pollNow(poll));

    return h(
      "div",
      { class: "card tg-setup" },
      h("div", { class: "card-head" }, h("div", {}, h("h2", {}, icon("send"), t("Telegram listener")), h("p", {}, t("Reads signals a Telegram bot receives. Create a bot with @BotFather, add it to your channel or forward signals to it. It never signs in as you and never bypasses a channel's content protection.")))),
      h(
        "div",
        { class: "tg-row" },
        field(t("Bot token"), h("div", { class: "row" }, token, saveToken, removeToken)),
      ),
      status.token_set
        ? h(
            "div",
            { class: "form-grid tg-config" },
            h("label", { class: "row tg-check" }, enable, status.bot_username ? t("Listen as @{name}", { name: status.bot_username }) : t("Turn the listener on")),
            h("label", { class: "row tg-check" }, skipProtected, t("Skip messages from protected channels")),
            field(t("Only these chats"), chats, { full: true, help: t("Chat @usernames or ids, comma separated. Empty = any chat the bot is in.") }),
          )
        : h("p", { class: "muted small" }, t("Paste the bot token to begin. The token is encrypted on this computer and never leaves it.")),
      status.token_set ? h("div", { class: "row tg-status-row" }, statusChip(), poll) : null,
    );
  }

  function statusChip() {
    if (status.last_error) return h("span", { class: "badge bad", title: status.last_error }, icon("alert"), t("Error"));
    if (!status.enabled) return h("span", { class: "badge" }, icon("pause"), t("Paused"));
    const when = status.last_ok_at ? fmt.datetime(status.last_ok_at) : t("not yet");
    return h("span", { class: "badge good", title: t("Last check: {when}", { when }) }, icon("wifi"), t("Listening"));
  }

  // ------------------------------------------------------------------- auto card

  function autoCard() {
    const targets = accounts().filter((a) => a.status === "active" && a.connection && a.connection !== "none");
    if (!targets.length) return null;
    const accountSel = select([["", t("Off — capture only")], ...targets.map((a) => [String(a.id), a.name])], status.auto_account_id ? String(status.auto_account_id) : "");
    accountSel.addEventListener("change", () => api.put("/api/telegram/config", { auto_account_id: accountSel.value ? Number(accountSel.value) : null }).then(reload).catch((e) => toast(e.message, "error")));
    const risk = h("input", { type: "number", step: "0.05", min: "0.05", max: "20", value: status.risk_pct });
    risk.addEventListener("change", () => api.put("/api/telegram/config", { risk_pct: Number(risk.value) || 1 }).then(reload));
    const maxTrades = h("input", { type: "number", step: "1", min: "0", max: "50", value: status.max_trades_per_day });
    maxTrades.addEventListener("change", () => api.put("/api/telegram/config", { max_trades_per_day: Math.round(Number(maxTrades.value) || 0) }).then(reload));
    const requireChecklist = h("input", { type: "checkbox", checked: status.require_checklist });
    requireChecklist.addEventListener("change", () => api.put("/api/telegram/config", { require_checklist: requireChecklist.checked }).then(reload));

    const account = targets.find((a) => a.id === status.auto_account_id);
    return h(
      "div",
      { class: "card tg-auto" },
      h("div", { class: "card-head" }, h("div", {}, h("h2", {}, icon("power"), t("Auto-execute captured signals")), h("p", {}, t("Off by default. When armed, a clean signal (symbol, direction, entry and stop) is sent to the chosen account with your risk. Every order passes your guardrails."))), status.auto_execute ? h("span", { class: "badge good" }, t("Armed")) : h("span", { class: "badge" }, t("Off"))),
      h("div", { class: "form-grid tg-config" }, field(t("Account"), accountSel), field(t("Risk per trade (%)"), risk), field(t("Max auto orders a day"), maxTrades), h("label", { class: "row tg-check" }, requireChecklist, t("Only after the pre-market checklist"))),
      account ? armRow(account) : h("p", { class: "muted small" }, t("Choose an account to enable arming.")),
    );
  }

  function armRow(account) {
    if (status.auto_execute) {
      const off = h("button", { class: "btn sm", type: "button" }, icon("pause"), t("Turn off"));
      off.addEventListener("click", async () => { await api.post("/api/telegram/arm", { auto_execute: false }); toast(t("Auto-execution off"), "success"); reload(); });
      return h("div", { class: "bind-auto on" }, h("span", { class: "row" }, icon("power"), t("Sending clean signals to {name} automatically", { name: account.name })), off);
    }
    const on = h("button", { class: "btn primary sm", type: "button" }, icon("power"), t("Arm auto-execution"));
    on.addEventListener("click", () => armDialog(account));
    return h("div", { class: "bind-auto" }, on, h("span", { class: "muted small" }, t("You confirm with the account name. The daily cap and your guardrails still apply.")));
  }

  function armDialog(account) {
    const paper = account.connection === "paper";
    const confirm = h("input", { placeholder: account.name, autocomplete: "off" });
    const go = h("button", { class: "btn primary", type: "button" }, icon("power"), t("Arm"));
    go.addEventListener("click", async () => {
      go.disabled = true;
      try {
        await api.post("/api/telegram/arm", { auto_execute: true, confirm: confirm.value });
        ref.close();
        toast(t("Auto-execution armed"), "success");
        reload();
      } catch (error) {
        toast(error.message, "error");
        go.disabled = false;
      }
    });
    const ref = modal({
      title: t("Arm auto-execution"),
      body: h("div", { class: "stack", style: { gap: "12px" } }, h("div", { class: "banner warn" }, icon("alert"), h("div", {}, paper ? t("Paper account: orders are simulated.") : t("Real orders will be placed on {name} from Telegram signals, without asking. Only use channels you trust.", { name: account.name }))), paper ? null : field(t("Type the account name to confirm"), confirm)),
      footer: [h("button", { class: "btn ghost", type: "button", onclick: () => ref.close() }, t("Cancel")), go],
    });
  }

  // -------------------------------------------------------------------- signals

  function signalRow(sig) {
    const money = (v) => (v == null ? "—" : fmt.price(v));
    const use = h("button", { class: "btn primary sm", type: "button", onclick: () => { handoff.signalText = sig.raw_text; location.hash = "#/signals"; } }, icon("arrowRight"), t("Use"));
    const dismiss = h("button", { class: "btn ghost sm icon-only", type: "button", "aria-label": t("Dismiss"), onclick: async () => { await api.post(`/api/telegram/signals/${sig.id}/dismiss`); reload(); } }, icon("x"));
    const badges = [
      h("span", { class: "badge" }, t(SOURCE_LABEL[sig.source] || sig.source)),
      sig.parse_ok ? h("span", { class: `badge ${sig.direction === "long" ? "long" : "short"}` }, `${sig.symbol} ${sig.direction === "long" ? t("Long") : t("Short")}`) : h("span", { class: "badge warn" }, t("Not parsed")),
      sig.auto_result ? h("span", { class: `badge ${AUTO_STATUS[autoKind(sig)] || ""}`, title: sig.auto_result }, t("Auto: {status}", { status: shortAuto(sig) })) : null,
    ];
    return h(
      "div",
      { class: `tg-signal${sig.status === "dismissed" ? " dim" : ""}` },
      h("div", { class: "tg-signal-head" }, h("div", { class: "row" }, badges), h("span", { class: "muted small" }, `${sig.chat_title} · ${sig.received_at ? fmt.datetime(sig.received_at) : ""}`)),
      sig.parse_ok ? h("div", { class: "tg-signal-nums muted small" }, t("Entry {e} · SL {s} · TP {t}", { e: money(sig.entry_price), s: money(sig.stop_loss), t: sig.take_profits.map(money).join(" / ") || "—" })) : null,
      h("pre", { class: "tg-signal-text" }, sig.raw_text),
      h("div", { class: "row tg-signal-actions" }, sig.status === "new" ? use : h("span", { class: "muted small" }, t(sig.status === "sent" ? "Sent" : "Dismissed")), sig.status !== "dismissed" ? dismiss : null),
    );
  }

  function signalsCard() {
    const clearAll = signals.length ? h("button", { class: "btn ghost sm", type: "button", onclick: async () => { if (await confirmDialog(t("Clear all captured signals?"), { title: t("Clear"), confirmText: t("Clear"), danger: true })) { await api.del("/api/telegram/signals"); reload(); } } }, icon("trash"), t("Clear")) : null;
    return h(
      "div",
      { class: "card tg-signals" },
      h("div", { class: "card-head" }, h("div", {}, h("h2", {}, icon("signal"), t("Captured signals")), h("p", {}, status.new_count ? tn(status.new_count, "{n} new", "{n} new") : t("Signals the bot receives appear here"))), clearAll),
      signals.length ? h("div", { class: "tg-signal-list" }, signals.map(signalRow)) : h("div", { class: "empty" }, h("strong", {}, t("No signals yet")), t("Add the bot to a channel or forward a signal message to it, then Check now.")),
    );
  }

  function draw() {
    clear(
      root,
      h("div", { class: "page-head" }, h("div", {}, h("h1", {}, t("Telegram signals")), h("p", {}, t("Capture trading signals from Telegram and act on them in the Signals page")))),
      setupCard(),
      status.token_set ? autoCard() : null,
      signalsCard(),
    );
  }

  draw();
}

function autoKind(sig) {
  return sig.auto_result.toLowerCase().includes("block") ? "blocked" : sig.execution_id ? "executed" : sig.auto_result ? "error" : "skipped";
}

function shortAuto(sig) {
  if (sig.execution_id) return t("sent");
  if (sig.auto_result.toLowerCase().includes("block")) return t("blocked");
  return t("skipped");
}
