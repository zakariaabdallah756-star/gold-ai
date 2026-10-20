// Vision AI chart analysis (track V). Placeholder until the feature is implemented.

import { clear, h } from "../dom.js";

export async function render(root) {
  clear(root, h("div", { class: "card empty" }, h("strong", {}, "Vision AI"), "Coming soon."));
}
