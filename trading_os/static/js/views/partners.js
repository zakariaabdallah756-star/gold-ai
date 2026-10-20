// Recommended partners page + referral-link settings (track PP). Placeholder until the feature is implemented.

import { clear, h } from "../dom.js";

export async function render(root) {
  clear(root, h("div", { class: "card empty" }, h("strong", {}, "Recommended partners"), "Coming soon."));
}

/** Settings tab (Trading plan -> Partners & referrals): owner's referral links. */
export async function renderPartnerSettings(container) {
  clear(container, h("div", { class: "card empty" }, "Partner settings coming soon."));
}
