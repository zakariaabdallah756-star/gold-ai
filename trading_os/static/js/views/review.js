// Post-market review (track D). Placeholder until the feature is implemented.

import { clear, h } from "../dom.js";

export async function render(root) {
  clear(root, h("div", { class: "card empty" }, h("strong", {}, "Post-market review"), "Coming soon."));
}
