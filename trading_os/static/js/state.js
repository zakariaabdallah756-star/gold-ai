// Shared app state: the trading plan and the daily discipline status.

import { api } from "./api.js";
import { setCurrency } from "./format.js";

const listeners = new Set();

// One-shot hand-offs between pages (e.g. Vision AI -> Signals). The receiving
// view reads and clears its key on render. ``signalContext`` travels with the
// text: {strategy, timeframe, confluences, risk_pct, alert: {id, symbol}}.
export const handoff = { signalText: null, signalContext: null };

export const state = {
  plan: null,
  status: null,
  clockOffsetMs: 0, // account-time-zone wall clock minus browser wall clock
};

export async function loadPlan() {
  const plan = await api.get("/api/plan");
  state.plan = plan;
  state.clockOffsetMs = new Date(plan.now).getTime() - Date.now();
  setCurrency(displayCurrency());
  return plan;
}

export async function loadStatus() {
  state.status = await api.get("/api/risk/status");
  // Another tab or device switched the account shown: follow it (accounts, currency, the page).
  const shown = state.plan?.settings.active_account_id ?? null;
  if (state.plan && (state.status.account_id ?? null) !== shown) {
    await reloadAccounts().catch(() => {});
    window.dispatchEvent(new CustomEvent("tos:account-changed"));
  }
  for (const listener of listeners) listener(state.status);
  return state.status;
}

export function onStatus(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** "Now" on the account's wall clock, as a local Date for form defaults. */
export function accountNow() {
  return new Date(Date.now() + state.clockOffsetMs);
}

export function marketBySymbol(symbol) {
  return state.plan?.markets.find((market) => market.symbol === symbol) || null;
}

export function strategyByName(name) {
  return state.plan?.strategies.find((strategy) => strategy.name === name) || null;
}

// ------------------------------------------------------------------ trading accounts

/** Every trading account (from /api/plan; no secrets). */
export function accounts() {
  return state.plan?.accounts || [];
}

/** The account the app shows, or null when every account is shown together. */
export function activeAccount() {
  const id = state.plan?.settings.active_account_id;
  return id == null ? null : accounts().find((account) => account.id === id) || null;
}

/** The currency money is shown in: the account shown, else the settings'. */
export function displayCurrency() {
  return activeAccount()?.currency || state.plan?.settings.currency || "USD";
}

/**
 * Show one account (or all with ``null``) across the app. The server remembers it; the status bar is refreshed
 * here and the caller re-renders the page.
 */
export async function setActiveAccount(accountId) {
  const result = await api.put("/api/accounts/active", { account_id: accountId });
  state.plan.settings.active_account_id = result.active_account_id;
  for (const account of accounts()) account.is_active = account.id === result.active_account_id;
  setCurrency(displayCurrency());
  state.status = result.status;
  for (const listener of listeners) listener(state.status);
  return result;
}

/** Reload the accounts list after an account was added, edited or removed. */
export async function reloadAccounts() {
  const data = await api.get("/api/accounts");
  if (state.plan) {
    state.plan.accounts = data.accounts.map(({ metrics, ...account }) => account);
    state.plan.settings.active_account_id = data.active_account_id;
    setCurrency(displayCurrency());
  }
  return data;
}
