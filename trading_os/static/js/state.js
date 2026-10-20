// Shared app state: the trading plan and the daily discipline status.

import { api } from "./api.js";
import { setCurrency } from "./format.js";

const listeners = new Set();

// One-shot hand-offs between pages (e.g. Vision AI -> Signals). The receiving
// view reads and clears its key on render.
export const handoff = { signalText: null };

export const state = {
  plan: null,
  status: null,
  clockOffsetMs: 0, // account-time-zone wall clock minus browser wall clock
};

export async function loadPlan() {
  const plan = await api.get("/api/plan");
  state.plan = plan;
  state.clockOffsetMs = new Date(plan.now).getTime() - Date.now();
  setCurrency(plan.settings.currency);
  return plan;
}

export async function loadStatus() {
  state.status = await api.get("/api/risk/status");
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
