// Risk disclaimer: compact footer on every page + the first-launch agreement modal.

import { api } from "./api.js";
import { clear, h, icon, modal, toast } from "./dom.js";
import { t } from "./i18n.js";

function sections(disclaimer) {
  return disclaimer.sections.map((s) => h("section", { class: "disclaimer-section" }, h("h3", {}, s.title), h("p", {}, s.text)));
}

function showFull(disclaimer) {
  modal({ title: t("Risk disclaimer"), body: h("div", { class: "disclaimer-body" }, sections(disclaimer)), wide: true });
}

// The agreement can't be dismissed: no close button, Escape and backdrop clicks are ignored.
function requireAcceptance(disclaimer) {
  const agree = h("input", { type: "checkbox", id: "disclaimer-agree" });
  const accept = h("button", { class: "btn primary", type: "button", disabled: true }, t("Accept & continue"));
  agree.addEventListener("change", () => (accept.disabled = !agree.checked));
  const dialog = h(
    "div",
    { class: "modal wide", role: "dialog", "aria-modal": "true", "aria-labelledby": "disclaimer-title" },
    h("div", { class: "modal-head" }, h("h2", { id: "disclaimer-title" }, icon("shield"), " ", t("Before you start: risk disclaimer"))),
    h("div", { class: "modal-body disclaimer-body" }, sections(disclaimer)),
    h("div", { class: "modal-foot" }, h("label", { class: "check left", for: "disclaimer-agree" }, agree, t("I have read and understand the risks and accept these terms")), accept),
  );
  const backdrop = h("div", { class: "modal-backdrop disclaimer-backdrop" }, dialog);
  const trapFocus = (event) => {
    if (event.key === "Escape") event.stopImmediatePropagation();
    if (event.key !== "Tab") return;
    const focusable = [agree, accept].filter((el) => !el.disabled);
    const index = focusable.indexOf(document.activeElement);
    event.preventDefault();
    focusable[(index + (event.shiftKey ? focusable.length - 1 : 1)) % focusable.length].focus();
  };
  document.addEventListener("keydown", trapFocus, true);
  accept.addEventListener("click", async () => {
    accept.disabled = true;
    try {
      await api.post("/api/disclaimer/accept");
      document.removeEventListener("keydown", trapFocus, true);
      backdrop.remove();
    } catch (error) {
      accept.disabled = false;
      toast(error.message, "error");
    }
  });
  document.body.append(backdrop);
  agree.focus();
}

export async function mountDisclaimer() {
  const footer = document.getElementById("app-footer");
  let disclaimer;
  try {
    disclaimer = await api.get("/api/disclaimer");
  } catch {
    clear(footer, t("Risk warning: trading involves substantial risk of loss. Nothing in this app is financial advice."));
    return;
  }
  clear(
    footer,
    h("span", {}, disclaimer.footer, " "),
    h("button", { class: "link-button", type: "button", onclick: () => showFull(disclaimer) }, t("Read the full disclaimer")),
  );
  if (!disclaimer.accepted) requireAcceptance(disclaimer);
}
