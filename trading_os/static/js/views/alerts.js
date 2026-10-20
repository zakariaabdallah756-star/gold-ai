// Strategy alert engine: rules, scanner status and the alert feed (track S). Placeholder until implemented.

import { clear, h } from "../dom.js";

export async function render(root) {
  clear(root, h("div", { class: "card empty" }, h("strong", {}, "Strategy alerts"), "Coming soon."));
}
