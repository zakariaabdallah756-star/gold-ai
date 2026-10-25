// DOM helpers. User-provided text is always inserted as text nodes, never HTML.

import { t } from "./i18n.js";

const ICONS = {
  dashboard: "M3 3h7v9H3zM14 3h7v5h-7zM14 12h7v9h-7zM3 16h7v5H3z",
  checklist: "M9 11l3 3 8-8M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9",
  calculator: "M6 2h12a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zM8 6h8M8 11h.01M12 11h.01M16 11h.01M8 15h.01M12 15h.01M16 15h.01M8 19h.01M12 19h4",
  journal: "M4 19.5A2.5 2.5 0 0 1 6.5 17H20V2H6.5A2.5 2.5 0 0 0 4 4.5v15zM4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5M9 7h7M9 11h5",
  plan: "M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6",
  backup: "M12 3c4.97 0 9 1.34 9 3s-4.03 3-9 3-9-1.34-9-3 4.03-3 9-3zM21 12c0 1.66-4 3-9 3s-9-1.34-9-3M3 6v12c0 1.66 4 3 9 3s9-1.34 9-3V6",
  lock: "M5 11h14v10H5zM8 11V7a4 4 0 0 1 8 0v4",
  unlock: "M5 11h14v10H5zM8 11V7a4 4 0 0 1 7.75-1.4",
  alert: "M12 9v4M12 17h.01M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z",
  check: "M20 6L9 17l-5-5",
  checkCircle: "M22 11.1V12a10 10 0 1 1-5.9-9.1M22 4L12 14l-3-3",
  x: "M18 6L6 18M6 6l12 12",
  xCircle: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM15 9l-6 6M9 9l6 6",
  plus: "M12 5v14M5 12h14",
  edit: "M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z",
  trash: "M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6",
  image: "M3 3h18v18H3zM8.5 10a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3zM21 15l-5-5L5 21",
  link: "M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7",
  upload: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12",
  download: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3",
  info: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM12 16v-4M12 8h.01",
  arrowRight: "M5 12h14M12 5l7 7-7 7",
  chevronLeft: "M15 18l-6-6 6-6",
  chevronRight: "M9 18l6-6-6-6",
  up: "M18 15l-6-6-6 6",
  down: "M6 9l6 6 6-6",
  copy: "M9 9h11v11H9zM5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1",
  table: "M3 3h18v18H3zM3 9h18M3 15h18M9 3v18",
  chart: "M3 3v18h18M7 15l4-4 3 3 6-6",
  clock: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM12 6v6l4 2",
  signal: "M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2zM8 9h8M8 13h5",
  layers: "M12 2l10 5-10 5L2 7zM2 17l10 5 10-5M2 12l10 5 10-5",
  calendar: "M3 4h18v18H3zM16 2v4M8 2v4M3 10h18",
  review: "M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z",
  power: "M18.4 6.6a9 9 0 1 1-12.8 0M12 2v10",
  bell: "M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0",
  target: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM12 6a6 6 0 1 0 0 12 6 6 0 0 0 0-12zM12 10a2 2 0 1 0 0 4 2 2 0 0 0 0-4z",
  shield: "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z",
  zap: "M13 2L3 14h9l-1 8 10-12h-9z",
  trending: "M23 6l-9.5 9.5-5-5L1 18M17 6h6v6",
  flame: "M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.4-.5-2-1-3-1-2.1-.2-4 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.2.4-2.3 1-3.2.3 1.6 1.4 2.7 2.5 2.7z",
  play: "M5 3l14 9-14 9z",
  refresh: "M23 4v6h-6M1 20v-6h6M3.5 9a9 9 0 0 1 14.9-3.4L23 10M1 14l4.6 4.4A9 9 0 0 0 20.5 15",
  list: "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
  grid: "M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z",
  undo: "M3 7v6h6M21 17a9 9 0 0 0-15-6.7L3 13",
  scissors: "M6 3a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM6 15a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM20 4L8.1 15.9M14.5 14.5L20 20M8.1 8.1L12 12",
  sun: "M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10zM12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4",
  star: "M12 2l3.1 6.3 6.9 1-5 4.9 1.2 6.8-6.2-3.2-6.2 3.2 1.2-6.8-5-4.9 6.9-1z",
  brain: "M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24A2.5 2.5 0 0 1 9.5 2zM14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24A2.5 2.5 0 0 0 14.5 2z",
  // v1.5 app shell: accounts, profile menu, onboarding guide
  user: "M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8z",
  wallet: "M19 7V4a1 1 0 0 0-1-1H5a2 2 0 0 0 0 4h15a1 1 0 0 1 1 1v4h-3a2 2 0 0 0 0 4h3a1 1 0 0 0 1-1v-2a1 1 0 0 0-1-1M3 5v14a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1v-4",
  video: "M23 7l-7 5 7 5zM3 5h11a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z",
  playCircle: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM10 8l6 4-6 4z",
  globe: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z",
  activity: "M22 12h-4l-3 9L9 3l-3 9H2",
  gauge: "M12 14l4-4M3.34 19a10 10 0 1 1 17.32 0",
  archive: "M21 8v13H3V8M1 3h22v5H1zM10 12h4",
  eye: "M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
  plug: "M12 22v-5M9 8V2M15 8V2M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8z",
  key: "M21 2l-2 2M11.4 11.6a5.5 5.5 0 1 1-7.8 7.8 5.5 5.5 0 0 1 7.8-7.8zM11.4 11.6L15.5 7.5M15.5 7.5l3 3L22 7l-3-3M15.5 7.5L19 4",
  book: "M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2zM22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z",
  graduation: "M22 10L12 5 2 10l10 5 10-5zM6 12v5c3 2.7 9 2.7 12 0v-5M22 10v6",
  sparkles: "M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9zM19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8zM5 2l.6 1.4L7 4l-1.4.6L5 6l-.6-1.4L3 4l1.4-.6z",
  message: "M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z",
  send: "M22 2L11 13M22 2l-7 20-4-9-9-4z",
  fileText: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M16 13H8M16 17H8M10 9H8",
  pause: "M6 4h4v16H6zM14 4h4v16h-4z",
  wifi: "M5 12.55a11 11 0 0 1 14 0M8.5 16.1a6 6 0 0 1 7 0M2 8.82a15 15 0 0 1 20 0M12 20h.01",
  wifiOff: "M1 1l22 22M16.72 11.06A10.9 10.9 0 0 1 19 12.55M5 12.55a10.9 10.9 0 0 1 5.17-2.39M10.71 5.05A16 16 0 0 1 22 8.82M1.42 9a15.9 15.9 0 0 1 4.7-2.88M8.53 16.11a6 6 0 0 1 6.95 0M12 20h.01",
};

const SVG_NS = "http://www.w3.org/2000/svg";

export function icon(name, className = "icon") {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", className);
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(SVG_NS, "path");
  path.setAttribute("d", ICONS[name] || ICONS.info);
  svg.append(path);
  return svg;
}

/** Hyperscript: h("div", {class: "x", onclick: fn}, "text", child, [more]) */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "style" && typeof value === "object") Object.assign(el.style, value);
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2), value);
    else if (key === "value") el.value = value;
    else if (key in el && typeof value !== "string") el[key] = value;
    else el.setAttribute(key, value === true ? "" : value);
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

export function clear(el, ...children) {
  el.replaceChildren();
  append(el, children);
  return el;
}

/**
 * A translated template whose {name} placeholders are elements, e.g. the template "Run {command} now" with
 * { command: <code> } gives ["Run ", <code>, " now"] - the words around the element can move in another language.
 */
export function withNodes(template, nodes) {
  return template
    .split(/(\{\w+\})/)
    .map((part) => {
      const name = /^\{(\w+)\}$/.exec(part)?.[1];
      return name && name in nodes ? nodes[name] : part;
    })
    .filter((part) => part !== "");
}

export function debounce(fn, wait = 250) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

// ----------------------------------------------------------------- feedback

const TOAST_ICONS = { success: "checkCircle", error: "xCircle", warn: "alert", info: "info" };

export function toast(message, kind = "info", timeout = 4200) {
  const node = h("div", { class: `toast ${kind}`, role: kind === "error" ? "alert" : "status" }, icon(TOAST_ICONS[kind]), h("div", {}, message));
  document.getElementById("toasts").append(node);
  setTimeout(() => node.remove(), timeout);
}

export function modal({ title, body, footer, wide = false, onClose }) {
  const previouslyFocused = document.activeElement;
  const close = () => {
    backdrop.remove();
    document.removeEventListener("keydown", onKey);
    previouslyFocused?.focus?.();
    onClose?.();
  };
  const onKey = (event) => {
    if (event.key === "Escape" && !document.querySelector(".lightbox")) close();
  };
  const dialog = h(
    "div",
    { class: `modal${wide ? " wide" : ""}`, role: "dialog", "aria-modal": "true", "aria-label": title },
    h("div", { class: "modal-head" }, h("h2", {}, title), h("button", { class: "btn ghost icon-only", "aria-label": t("Close"), onclick: () => close() }, icon("x"))),
    h("div", { class: "modal-body" }, body),
    footer ? h("div", { class: "modal-foot" }, footer) : null,
  );
  const backdrop = h("div", { class: "modal-backdrop", onmousedown: (event) => event.target === backdrop && close() }, dialog);
  document.body.append(backdrop);
  document.addEventListener("keydown", onKey);
  requestAnimationFrame(() => dialog.querySelector("input, select, textarea, button.primary")?.focus());
  return { close, dialog };
}

export function confirmDialog(message, { title = t("Please confirm"), confirmText = t("Confirm"), danger = false } = {}) {
  return new Promise((resolve) => {
    let answered = false;
    const finish = (value) => {
      answered = true;
      resolve(value);
      ref.close();
    };
    const ref = modal({
      title,
      body: h("p", { style: { color: "var(--text-2)" } }, message),
      footer: [
        h("button", { class: "btn ghost", onclick: () => finish(false) }, t("Cancel")),
        h("button", { class: `btn ${danger ? "danger" : "primary"}`, onclick: () => finish(true) }, confirmText),
      ],
      onClose: () => answered || resolve(false),
    });
  });
}

export function lightbox(src) {
  const box = h("div", { class: "lightbox", onclick: () => box.remove() }, h("img", { src, alt: t("Chart screenshot") }));
  const onKey = (event) => {
    if (event.key === "Escape") {
      box.remove();
      document.removeEventListener("keydown", onKey);
    }
  };
  document.addEventListener("keydown", onKey);
  document.body.append(box);
}

// -------------------------------------------------------------------- forms

export function field(label, control, { help, full = false } = {}) {
  return h("div", { class: `field${full ? " full" : ""}` }, h("label", {}, label), control, help ? h("div", { class: "help" }, help) : null);
}

export function select(options, value, attrs = {}) {
  const el = h("select", attrs);
  for (const option of options) {
    const [optionValue, label] = Array.isArray(option) ? option : [option, option];
    el.append(h("option", { value: optionValue }, label));
  }
  el.value = value ?? "";
  return el;
}

/** Segmented control. Returns {el, get value, set value}. */
export function segmented(options, value, onChange) {
  let current = value;
  const buttons = options.map(([optionValue, label, extraClass = ""]) =>
    h("button", { type: "button", class: extraClass, dataset: { value: optionValue }, onclick: () => set(optionValue, true) }, label),
  );
  const el = h("div", { class: "segmented", role: "group" }, buttons);
  function set(next, notify = false) {
    current = next;
    for (const button of buttons) button.classList.toggle("on", button.dataset.value === String(next));
    if (notify) onChange?.(next);
  }
  set(value);
  return {
    el,
    get value() {
      return current;
    },
    set value(next) {
      set(next);
    },
  };
}

export function numberOrNull(input) {
  const raw = String(input.value).trim().replace(",", ".");
  if (raw === "") return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}
