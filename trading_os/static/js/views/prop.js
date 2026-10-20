// Prop firm challenge tracker (track P). Placeholder until the feature is implemented.

import { clear, h } from "../dom.js";

export async function render(root) {
  clear(root, h("div", { class: "card empty" }, h("strong", {}, "Prop firm challenges"), "Coming soon."));
}
