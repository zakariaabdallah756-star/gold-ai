// Strategy Co-Pilot panel: shows every unmet strategy parameter (STRATEGY_* guardrail
// findings) with explicit "wait" advice. Used by the trade form and the Signals page.

import { h, icon } from "../dom.js";

export const COPILOT_CODES = new Set([
  "STRATEGY_REQUIRED_CONFLUENCES",
  "CONFLUENCES_BELOW_MIN",
  "STRATEGY_DIRECTION",
  "STRATEGY_SESSION",
  "STRATEGY_TIMEFRAME",
  "STRATEGY_BIAS",
  "STRATEGY_MIN_RR",
  "STRATEGY_INCUBATOR",
  "STRATEGY_RETIRED",
]);

const NOTICE_CODES = new Set(["STRATEGY_INCUBATOR", "STRATEGY_RETIRED"]);

/** Split guardrail violations into Co-Pilot findings and everything else. */
export function splitCopilot(violations) {
  const copilot = [];
  const other = [];
  for (const violation of violations || []) (COPILOT_CODES.has(violation.code) ? copilot : other).push(violation);
  return { copilot, other };
}

/** The Co-Pilot panel for a draft trade checked by /api/trades/check or a signal preview. */
export function copilotPanel(violations, strategyName) {
  if (!strategyName) {
    return h(
      "div",
      { class: "copilot neutral" },
      icon("brain"),
      h(
        "div",
        {},
        h("strong", {}, "Strategy Co-Pilot"),
        h("p", { class: "muted small" }, "Select a strategy so the Co-Pilot can check its required confluences, sessions, timeframes and bias."),
      ),
    );
  }
  const { copilot } = splitCopilot(violations);
  const unmet = copilot.filter((v) => !NOTICE_CODES.has(v.code));
  const ready = unmet.length === 0;
  const title = ready
    ? `Co-Pilot: every ${strategyName} condition is met`
    : `Co-Pilot: wait - ${unmet.length} ${strategyName} condition${unmet.length === 1 ? "" : "s"} not met`;
  return h(
    "div",
    { class: `copilot ${ready ? "ok" : "wait"}`, role: "status" },
    icon(ready ? "checkCircle" : "alert"),
    h(
      "div",
      {},
      h("strong", {}, title),
      copilot.length ? h("ul", {}, copilot.map((v) => h("li", {}, v.message.replace(/^Co-Pilot:\s*/, "")))) : null,
      ready ? null : h("p", { class: "small" }, "Do not enter yet. Wait until every condition above is satisfied, then re-check."),
    ),
  );
}
