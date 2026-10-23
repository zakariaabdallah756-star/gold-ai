// Dashboard promo carousel (v1.5): partner brokers, prop-firm discounts and platform announcements.
//
// mountPromoCarousel(container) fetches GET /api/promos and renders a hero banner carousel: it rotates on its
// own every interval_sec (4 s by default; timing in autoplay.js), holds while the mouse is over it, a finger is
// on it, the keyboard is inside it, the tab is hidden or the pause button is on, resumes when they end, never
// rotates or animates with prefers-reduced-motion, and supports arrows, dots, keyboard arrows and swipes. Each card can be hidden
// on this device (localStorage). Renders nothing when the carousel is off or has nothing to show, and
// returns a cleanup function that clears its timers and listeners.
//
// renderPromoCard(card) draws one card; the Promotions editor (views/settings-promos.js) uses it for its preview.

import { api } from "./api.js";
import { h, icon } from "./dom.js";
import { createAutoplay, intervalMs } from "./autoplay.js";
import { N_, t } from "./i18n.js";

export const PROMO_THEMES = ["teal", "blue", "gold", "purple", "red"];
const KIND_BADGE = {
  announcement: { label: N_("New"), icon: "zap" },
  partner: { label: N_("Partner"), icon: "star" },
  discount: { label: N_("Discount"), icon: "flame" },
};
const BUILTIN_ART = { whats_new_v16: "graduation", whats_new_v15: "layers", connect_account: "link", onboarding_guide: "play", strategy_alerts: "bell" };
const DISMISSED_KEY = "tos.promos.dismissed";
const SWIPE_MIN_PX = 40;
const TOUCH_RESUME_MS = 5000; // a tap or swipe holds rotation this long after the finger lifts
const COPIED_MS = 1600;

// ------------------------------------------------------------------ hidden cards (this device only)

/** Ids of the cards hidden on this device. Storage can be unavailable (private mode, blocked): never throws. */
export function hiddenPromoIds() {
  try {
    const list = JSON.parse(localStorage.getItem(DISMISSED_KEY) || "[]");
    return new Set(Array.isArray(list) ? list.map(String) : []);
  } catch {
    return new Set();
  }
}

function saveHidden(ids) {
  try {
    if (ids.size) localStorage.setItem(DISMISSED_KEY, JSON.stringify([...ids]));
    else localStorage.removeItem(DISMISSED_KEY);
  } catch {
    // storage blocked: the card is hidden until the page is reloaded
  }
}

/** Show every hidden card again on this device. */
export function showHiddenPromos() {
  saveHidden(new Set());
}

// ------------------------------------------------------------------------------- one card

function safeLink(url) {
  // The server only stores app routes and https:// links; checked again here before it becomes an href.
  if (typeof url !== "string") return null;
  if (/^#\/[\w\-./?=&%]*$/.test(url)) return { href: url, external: false };
  if (/^https:\/\/[^\s]+$/i.test(url)) return { href: url, external: true };
  return null;
}

function artIcon(card) {
  if (BUILTIN_ART[card.builtin_key]) return BUILTIN_ART[card.builtin_key];
  if (card.kind === "partner") return card.partner?.kind === "prop_firm" ? "shield" : "trending";
  if (card.kind === "discount") return "flame";
  return "zap";
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // no clipboard API (plain http on another device) or permission refused: fall back to a selection
  }
  try {
    const area = h("textarea", { readonly: true, style: { position: "fixed", top: "0", left: "0", opacity: "0" } });
    area.value = text;
    document.body.append(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    return ok;
  } catch {
    return false;
  }
}

function codeChip(code, timers) {
  const value = h("code", { class: "promo-code-value" }, code);
  const label = h("span", {}, t("Copy"));
  const status = h("span", { class: "sr-only", "aria-live": "polite" });
  const button = h(
    "button",
    {
      type: "button",
      class: "promo-copy",
      "aria-label": t("Copy code {code}", { code }),
      onclick: async () => {
        const ok = await copyText(code);
        if (!ok) {
          const range = document.createRange(); // let the trader copy it by hand
          range.selectNodeContents(value);
          const selection = window.getSelection();
          selection?.removeAllRanges();
          selection?.addRange(range);
        }
        button.classList.toggle("copied", ok);
        button.replaceChildren(icon(ok ? "check" : "copy"), label);
        label.textContent = ok ? t("Copied") : t("Copy failed");
        status.textContent = ok ? t("Code {code} copied", { code }) : t("Copy failed");
        const timer = setTimeout(() => {
          timers?.delete(timer);
          button.classList.remove("copied");
          button.replaceChildren(icon("copy"), label);
          label.textContent = t("Copy");
          status.textContent = "";
        }, COPIED_MS);
        timers?.add(timer);
      },
    },
    icon("copy"),
    label,
  );
  return h("div", { class: "promo-code" }, h("span", { class: "promo-code-label" }, t("Code")), value, button, status);
}

/**
 * One promo card as returned by GET /api/promos ({kind, theme, title, body, cta_label, cta_url, code, partner,
 * disclaimer, disclaimer_text, builtin_key}). ``onDismiss`` adds the hide button; ``attrs`` go on the article.
 */
export function renderPromoCard(card, { onDismiss, attrs = {}, timers } = {}) {
  const theme = PROMO_THEMES.includes(card.theme) ? card.theme : "teal";
  const badge = KIND_BADGE[card.kind] || KIND_BADGE.announcement;
  const link = card.cta_label ? safeLink(card.cta_url) : null;
  const cta = link
    ? h(
        "a",
        { class: "promo-cta", href: link.href, ...(link.external ? { target: "_blank", rel: "noopener noreferrer" } : {}) },
        card.cta_label,
        link.external ? h("span", { class: "sr-only" }, t(" (opens in a new tab)")) : null,
        icon(link.external ? "arrowRight" : "chevronRight"),
      )
    : null;
  const code = card.kind === "discount" && card.code ? codeChip(card.code, timers) : null;
  const partnerName = card.partner?.name && card.partner.name !== card.title ? h("span", { class: "promo-partner" }, card.partner.name) : null;
  return h(
    "article",
    { class: `promo-card promo-${theme}`, dataset: { kind: card.kind }, ...attrs },
    h("div", { class: "promo-art", "aria-hidden": "true" }, icon(artIcon(card), "icon promo-art-icon")),
    h(
      "div",
      { class: "promo-main" },
      h("div", { class: "promo-top" }, h("span", { class: "promo-badge" }, icon(badge.icon), t(badge.label)), partnerName),
      h("h3", { class: "promo-title" }, card.title),
      card.body ? h("p", { class: "promo-body" }, card.body) : null,
      cta || code ? h("div", { class: "promo-actions" }, cta, code) : null,
      card.disclaimer && card.disclaimer_text ? h("p", { class: "promo-disclaimer" }, card.disclaimer_text) : null,
    ),
    onDismiss
      ? h("button", { type: "button", class: "promo-dismiss", "aria-label": t("Hide this promotion"), title: t("Hide this promotion"), onclick: onDismiss }, icon("x"))
      : null,
  );
}

// ------------------------------------------------------------------------------- carousel

/** Fetch the active promos and render the carousel into ``container`` (empty when off or none). */
export async function mountPromoCarousel(container) {
  let disposed = false;
  let teardown = () => {};
  const cleanup = () => {
    disposed = true;
    teardown();
  };
  container.replaceChildren();
  let data;
  try {
    data = await api.get("/api/promos");
  } catch {
    return cleanup; // the carousel is optional: never break the dashboard
  }
  if (disposed || !data?.enabled || !Array.isArray(data.promos) || !data.promos.length) return cleanup;
  teardown = buildCarousel(container, data);
  return cleanup;
}

function buildCarousel(container, data) {
  const all = data.promos;
  const interval = intervalMs(data.interval_sec);
  let hidden = hiddenPromoIds();
  let promos = [];
  let index = 0;
  let touch = null;
  const timers = new Set(); // short-lived timeouts (copy feedback)
  const motion = window.matchMedia?.("(prefers-reduced-motion: reduce)");
  let reduced = Boolean(motion?.matches);
  // Pause reasons: hover | focus | touch | hidden | user. Each one holds rotation until it is lifted.
  const autoplay = createAutoplay({ intervalMs: interval, enabled: false, onAdvance: tick, onChange: showState });

  const track = h("div", { class: "promo-track" });
  const viewport = h("div", { class: "promo-viewport", "aria-live": "off" }, track);
  const toggleGlyph = h("span", { class: "promo-toggle-glyph" });
  const toggle = h("button", { type: "button", class: "promo-ctl promo-toggle", onclick: () => autoplay.toggle("user") }, toggleGlyph);
  const dots = h("div", { class: "promo-dots", role: "group", "aria-label": t("Choose a promotion") });
  const prev = h("button", { type: "button", class: "promo-ctl", "aria-label": t("Previous promotion"), title: t("Previous promotion"), onclick: () => go(index - 1) }, icon("chevronLeft"));
  const next = h("button", { type: "button", class: "promo-ctl", "aria-label": t("Next promotion"), title: t("Next promotion"), onclick: () => go(index + 1) }, icon("chevronRight"));
  const restore = h("button", { type: "button", class: "promo-restore", onclick: () => showAll() });
  const controls = h("div", { class: "promo-controls" }, toggle, dots, h("span", { class: "promo-spacer" }), restore, prev, next);
  const section = h(
    "section",
    {
      class: "promo-carousel",
      "aria-roledescription": t("carousel"),
      "aria-label": t("Promotions"),
      tabindex: "-1",
      onkeydown: onKey,
      onfocusin: (event) => {
        if (isKeyboardFocus(event.target)) autoplay.pause("focus");
      },
      onfocusout: (event) => {
        if (!section.contains(event.relatedTarget)) autoplay.resume("focus");
      },
      // Mouse and pen: rotation holds while the pointer is over the carousel (reading, clicking a card).
      onpointerenter: (event) => event.pointerType !== "touch" && autoplay.pause("hover"),
      onpointerleave: (event) => event.pointerType !== "touch" && autoplay.resume("hover"),
    },
    viewport,
    controls,
  );
  section.style.setProperty("--promo-interval", `${interval}ms`);
  // Touch: a finger on the carousel holds rotation; it resumes a little after the finger lifts.
  section.addEventListener("touchstart", onTouchStart, { passive: true });
  section.addEventListener("touchend", onTouchEnd, { passive: true });
  section.addEventListener("touchcancel", onTouchCancel, { passive: true });
  document.addEventListener("visibilitychange", onVisibility);
  motion?.addEventListener?.("change", onMotion);
  if (document.hidden) autoplay.pause("hidden");

  let alive = true;
  function teardown() {
    if (!alive) return;
    alive = false;
    autoplay.dispose();
    for (const pending of timers) clearTimeout(pending);
    timers.clear();
    document.removeEventListener("visibilitychange", onVisibility);
    motion?.removeEventListener?.("change", onMotion);
  }

  function isKeyboardFocus(target) {
    try {
      return target.matches(":focus-visible");
    } catch {
      return true;
    }
  }

  function canRotate() {
    return !reduced && promos.length > 1;
  }

  function showState({ running, enabled }) {
    section.classList.toggle("is-paused", !running);
    section.classList.toggle("is-static", !enabled);
    section.classList.toggle("reduced-motion", reduced);
    viewport.setAttribute("aria-live", running ? "off" : "polite");
    const userPaused = autoplay.isPaused("user");
    toggle.hidden = !enabled;
    toggle.setAttribute("aria-label", userPaused ? t("Start automatic rotation") : t("Pause automatic rotation"));
    toggle.title = toggle.getAttribute("aria-label");
    toggle.classList.toggle("is-play", userPaused);
    toggleGlyph.replaceChildren(userPaused ? icon("play") : "");
  }

  function tick() {
    if (!section.isConnected) {
      teardown(); // the page was replaced without calling the cleanup
      return;
    }
    go(index + 1);
  }

  function onVisibility() {
    if (document.hidden) autoplay.pause("hidden");
    else autoplay.resume("hidden");
  }

  function onMotion() {
    reduced = Boolean(motion?.matches);
    autoplay.setEnabled(canRotate());
  }

  function onKey(event) {
    if (event.target.closest("input, textarea, select")) return;
    if (section.contains(document.activeElement)) autoplay.pause("focus"); // keyboard use inside: stop rotating
    const moves = { ArrowLeft: index - 1, ArrowRight: index + 1, Home: 0, End: promos.length - 1 };
    if (!(event.key in moves) || promos.length < 2) return;
    event.preventDefault();
    go(moves[event.key]);
  }

  function onTouchStart(event) {
    autoplay.pause("touch");
    const point = event.touches[0];
    touch = event.touches.length === 1 && viewport.contains(event.target) ? { x: point.clientX, y: point.clientY } : null;
  }

  function onTouchEnd(event) {
    if (event.touches.length === 0) autoplay.resumeAfter("touch", TOUCH_RESUME_MS);
    if (!touch) return;
    const point = event.changedTouches[0];
    const dx = point.clientX - touch.x;
    const dy = point.clientY - touch.y;
    touch = null;
    if (Math.abs(dx) >= SWIPE_MIN_PX && Math.abs(dx) > Math.abs(dy) * 1.2) go(index + (dx < 0 ? 1 : -1));
  }

  function onTouchCancel() {
    touch = null;
    autoplay.resumeAfter("touch", TOUCH_RESUME_MS);
  }

  function restartProgress() {
    for (const [i, dot] of [...dots.children].entries()) {
      const on = i === index;
      dot.classList.toggle("on", on);
      if (on) dot.setAttribute("aria-current", "true");
      else dot.removeAttribute("aria-current");
      dot.replaceChildren(on ? h("span", { class: "promo-dot-fill" }) : "");
    }
  }

  function go(target) {
    if (!promos.length) return;
    index = (target + promos.length) % promos.length;
    const focusInSlide = track.contains(document.activeElement);
    track.style.transform = `translateX(${-100 * index}%)`;
    for (const [i, slide] of [...track.children].entries()) {
      const current = i === index;
      slide.inert = !current; // no tabbing into cards that are off screen
      if (current) slide.removeAttribute("aria-hidden");
      else slide.setAttribute("aria-hidden", "true");
    }
    if (focusInSlide) track.children[index]?.focus({ preventScroll: true }); // keep focus out of inert cards
    restartProgress();
    autoplay.restart(); // every slide gets its full interval, whether it came by timer, arrow, dot or swipe
  }

  function dismiss(id) {
    hidden.add(String(id));
    saveHidden(hidden);
    const focusInside = section.contains(document.activeElement);
    render();
    if (focusInside && promos.length) track.children[index]?.focus();
  }

  function showAll() {
    showHiddenPromos();
    hidden = new Set();
    render();
    section.focus?.();
  }

  function render() {
    promos = all.filter((promo) => !hidden.has(String(promo.id)));
    if (!promos.length) {
      teardown();
      container.replaceChildren();
      return;
    }
    if (!section.isConnected) container.replaceChildren(section);
    index = Math.min(index, promos.length - 1);
    const total = promos.length;
    track.replaceChildren(
      ...promos.map((promo, i) =>
        renderPromoCard(promo, {
          timers,
          onDismiss: () => dismiss(promo.id),
          attrs: { role: "group", "aria-roledescription": t("slide"), "aria-label": t("{n} of {total}", { n: i + 1, total }), tabindex: "-1" },
        }),
      ),
    );
    dots.replaceChildren(
      ...promos.map((promo, i) =>
        h("button", { type: "button", class: "promo-dot", "aria-label": t("Show promotion {n} of {total}: {title}", { n: i + 1, total, title: promo.title }), onclick: () => go(i) }),
      ),
    );
    const multiple = total > 1;
    dots.hidden = !multiple;
    prev.hidden = !multiple;
    next.hidden = !multiple;
    const hiddenCount = all.length - total;
    restore.hidden = hiddenCount === 0;
    restore.textContent = t("Show hidden ({n})", { n: hiddenCount });
    controls.hidden = !multiple && hiddenCount === 0;
    autoplay.setEnabled(canRotate());
    go(index);
  }

  render();
  return teardown;
}
