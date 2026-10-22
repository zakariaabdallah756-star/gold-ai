// Sign-in for other devices when a remote-access password is set (Trading Plan -> Network & access).
// The page shell, scripts and translations load without a sign-in; the API answers 401 {code: "login_required"}
// until the session cookie is set. The computer running Trading OS never sees this form.

import { h, icon } from "./dom.js";
import { t, useLanguage } from "./i18n.js";

let shown = false;
let listening = false;

async function readStatus() {
  try {
    const response = await fetch("/api/access/status", { headers: { Accept: "application/json" } });
    return response.ok ? await response.json() : null;
  } catch {
    return null; // server unreachable: let the app show its usual error
  }
}

/** True when the app may load; otherwise shows the sign-in form (the page reloads once signed in). */
export async function ensureAccess() {
  if (!listening) {
    listening = true;
    // A session that ends while the app is open (expired, signed out everywhere, password changed).
    window.addEventListener("tos:login-required", () => showLogin());
  }
  const status = await readStatus();
  if (!status?.needs_login) return true;
  await useLanguage(status.language);
  showLogin();
  return false;
}

export function showLogin() {
  if (shown) return;
  shown = true;
  const input = h("input", { id: "access-password", type: "password", name: "password", autocomplete: "current-password", maxlength: 200, required: true });
  const error = h("p", { class: "access-error", role: "alert" });
  const submit = h("button", { class: "btn primary", type: "submit" }, icon("unlock"), t("Sign in"));

  async function signIn(event) {
    event.preventDefault();
    submit.disabled = true;
    error.textContent = "";
    try {
      const response = await fetch("/api/access/login", {
        method: "POST",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify({ password: input.value }),
      });
      if (response.ok) {
        location.reload();
        return;
      }
      const payload = await response.json().catch(() => ({}));
      error.textContent = typeof payload.detail === "string" ? payload.detail : t("Sign-in failed. Try again.");
    } catch {
      error.textContent = t("Cannot reach Trading OS. Is it still running?");
    }
    submit.disabled = false;
    input.select();
  }

  const form = h(
    "form",
    { class: "card access-card", onsubmit: signIn },
    h("div", { class: "access-brand" }, icon("lock"), h("span", {}, "Trading OS")),
    h("h1", { id: "access-title" }, t("Sign in")),
    h("p", { class: "muted" }, t("A remote-access password protects this journal. Enter it to use Trading OS on this device.")),
    h("div", { class: "field" }, h("label", { for: "access-password" }, t("Password")), input),
    error,
    submit,
    h("p", { class: "muted small access-hint" }, t("Forgot it? Change or remove it on the computer running Trading OS, under Trading Plan > Network & access.")),
  );
  const overlay = h("div", { class: "access-overlay", role: "dialog", "aria-modal": "true", "aria-labelledby": "access-title" }, form);
  document.body.append(overlay);
  input.focus();
}
