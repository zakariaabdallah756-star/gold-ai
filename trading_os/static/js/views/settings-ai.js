// Trading plan -> AI assistant: the Claude model, effort and API key used by Vision AI.

import { api } from "../api.js";
import { clear, confirmDialog, field, h, icon, select, toast, withNodes } from "../dom.js";
import { N_, t } from "../i18n.js";

const SOURCES = {
  saved: { cls: "ok", icon: "lock", text: N_("Connected - using the API key saved in this journal") },
  environment: { cls: "ok", icon: "checkCircle", text: N_("Connected - using ANTHROPIC_API_KEY or an ant auth login profile") },
  none: { cls: "warn", icon: "alert", text: N_("Not connected - no API key or login found") },
};
const EFFORT_LABELS = { low: N_("Low"), medium: N_("Medium"), high: N_("High (recommended)"), xhigh: N_("Extra high"), max: N_("Max") };

const usd = (value) => (Number.isInteger(value) ? `$${value}` : `$${value.toFixed(2)}`);

export async function renderAiSettings(container) {
  draw(container, await api.get("/api/ai/settings"));
}

function draw(container, settings) {
  const source = SOURCES[settings.key_source] || SOURCES.none;
  const modelSelect = select(
    settings.models.map((m) => [m.id, `${m.name} — ${usd(m.input_per_mtok)} / ${usd(m.output_per_mtok)}`]),
    settings.model,
  );
  const effortSelect = select(
    settings.efforts.map((e) => [e.id, EFFORT_LABELS[e.id] ? t(EFFORT_LABELS[e.id]) : e.id]),
    settings.effort,
  );
  const effortHelp = h("span");
  const describeEffort = () => {
    effortHelp.textContent = settings.efforts.find((e) => e.id === effortSelect.value)?.description || "";
  };
  effortSelect.addEventListener("change", describeEffort);
  describeEffort();

  const keyInput = h("input", {
    type: "password",
    name: "api_key",
    autocomplete: "off",
    spellcheck: false,
    placeholder: settings.key_source === "saved" ? t("A key is saved - paste a new one to replace it") : "sk-ant-…",
  });
  const saveBtn = h("button", { class: "btn primary", type: "submit" }, icon("check"), t("Save"));

  async function save(event) {
    event.preventDefault();
    const body = { model: modelSelect.value, effort: effortSelect.value };
    const key = keyInput.value.trim();
    if (key) body.api_key = key;
    saveBtn.disabled = true;
    try {
      const next = await api.put("/api/ai/settings", body);
      toast(key ? t("AI settings and API key saved") : t("AI settings saved"), "success");
      draw(container, next);
    } catch (error) {
      toast(error.message, "error");
      saveBtn.disabled = false;
    }
  }

  async function removeKey() {
    const ok = await confirmDialog(t("Remove the saved API key? Vision AI then uses ANTHROPIC_API_KEY or an ant auth login profile if one is available."), {
      title: t("Remove API key"),
      confirmText: t("Remove key"),
      danger: true,
    });
    if (!ok) return;
    try {
      const next = await api.put("/api/ai/settings", { clear_key: true });
      toast(t("Saved API key removed"), "success");
      draw(container, next);
    } catch (error) {
      toast(error.message, "error");
    }
  }

  clear(
    container,
    h(
      "form",
      { class: "card ai-settings", autocomplete: "off", onsubmit: save },
      h(
        "div",
        { class: "card-head" },
        h("div", {}, h("h2", {}, t("AI assistant")), h("p", {}, t("Claude reads your chart screenshots in Vision AI and answers follow-up questions about them."))),
        h("a", { class: "btn sm", href: "#/vision" }, icon("brain"), t("Open Vision AI")),
      ),
      h("div", { class: "row ai-status" }, h("span", { class: `chip ${source.cls}` }, icon(source.icon), t(source.text))),
      settings.package_installed
        ? null
        : h(
            "div",
            { class: "vi-callout bad" },
            icon("xCircle"),
            h(
              "div",
              {},
              h("strong", {}, t('The "anthropic" Python package is not installed')),
              withNodes(t("Run {command} and restart Trading OS."), { command: h("code", {}, "pip install anthropic") }),
            ),
          ),
      h(
        "div",
        { class: "form-grid ai-form" },
        field(t("Model"), modelSelect, { help: t("Price per 1M tokens (input / output). A chart image costs up to about 4,800 input tokens.") }),
        field(t("Effort"), effortSelect, { help: effortHelp }),
        h(
          "p",
          { class: "full muted small" },
          t("Higher effort reasons more deeply but is slower and uses more output tokens. Follow-up questions run at medium effort at most to keep chat quick."),
        ),
        field(t("Anthropic API key"), keyInput, {
          full: true,
          help:
            settings.key_source === "saved"
              ? t("A key is saved. It is never shown again - paste a new key to replace it.")
              : t("Optional when ANTHROPIC_API_KEY is set or you ran ant auth login. Get a key at platform.claude.com."),
        }),
      ),
      h(
        "div",
        { class: "row ai-actions" },
        saveBtn,
        settings.key_source === "saved" ? h("button", { class: "btn danger", type: "button", onclick: removeKey }, icon("trash"), t("Remove saved key")) : null,
      ),
      h(
        "div",
        { class: "vi-callout info" },
        icon("shield"),
        h(
          "div",
          {},
          h("strong", {}, t("Privacy")),
          t(
            "When you analyse a chart, the screenshot, your notes and the selected strategy's rules are sent to Anthropic's API. Nothing is sent until you click Analyze chart. The API key stays in this journal's local database, is never displayed and is excluded from backups.",
          ),
        ),
      ),
    ),
  );
}
