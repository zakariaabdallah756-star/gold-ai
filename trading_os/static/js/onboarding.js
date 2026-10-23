// Getting started guide (v1.5): the onboarding video and an interactive step-by-step tour of Trading OS.
// Opens from the play button next to the profile icon, from the profile menu, and by itself once per launch
// (after the risk disclaimer was accepted) until "Don't show this again" is ticked or the tour is finished
// (settings.onboarding_done). The video link is set under Trading Plan -> Account & risk rules.

import { logoMark } from "./accounts-ui.js";
import { api } from "./api.js";
import { h, icon, toast } from "./dom.js";
import { N_, t } from "./i18n.js";
import { state } from "./state.js";
import { videoEmbed } from "./video-embed.js";

const STEPS = [
  {
    icon: "plan",
    route: "#/plan?tab=rules",
    title: N_("Set up your trading plan and risk rules"),
    text: N_("Start under Trading Plan: account size, risk per trade, minimum reward : risk and daily loss limit. Add the markets, sessions and strategies you trade. Trading OS checks every entry against these rules."),
  },
  {
    icon: "wallet",
    route: "#/accounts",
    title: N_("Add your trading accounts and switch between them"),
    text: N_("Register every account you trade: prop firm challenges, live and demo brokers. Pick one in the account switcher in the top bar, or All accounts, and every page follows it."),
  },
  {
    icon: "checklist",
    route: "#/premarket",
    title: N_("Run the pre-market checklist"),
    text: N_("Before the session, tick your checklist, set your daily bias and check the economic calendar. If your plan requires it, entries stay locked until the checklist is done."),
  },
  {
    icon: "calculator",
    route: "#/signals",
    title: N_("Signals, risk calculator and execution"),
    text: N_("Paste a signal or plan a trade in the Risk Calculator: the lot size follows your risk and every rule is checked before you enter. With a linked account you can send the order straight to your platform."),
  },
  {
    icon: "layers",
    route: "#/positions",
    title: N_("Positions and journal"),
    text: N_("Manage open trades under Positions: partial closes, break-even and trailing stops. Every trade lands in the Journal with your notes, emotions, mistakes and screenshots."),
  },
  {
    icon: "bell",
    route: "#/alerts",
    title: N_("Strategy alerts and Vision AI"),
    text: N_("Strategy Alerts watch the markets for your setups and notify you on your phone. Vision AI reads a chart screenshot and drafts a trade plan for you to check against your rules."),
  },
  {
    icon: "review",
    route: "#/review",
    title: N_("Review, calendar and incubator"),
    text: N_("The Dashboard, Calendar and Review show where your edge comes from: by strategy, session, weekday and hour. Test new strategies in the Incubator before they go live."),
  },
  {
    icon: "shield",
    route: "#/prop",
    title: N_("Prop firm tracking"),
    text: N_("Track a prop firm challenge: daily loss, max drawdown, profit target and trading days, with the room left always in the top bar. Turn on enforce to block entries that would break a rule."),
  },
  {
    icon: "backup",
    route: "#/backup",
    title: N_("Backup and remote access"),
    text: N_("Export your journal or a full backup under Backup & Export: secrets are never included. Under Trading Plan → Network & access you can open Trading OS from your phone, protected by a password."),
  },
];
const SESSION_KEY = "tos.onboarding.autoShown";
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), iframe, video[controls], [tabindex]:not([tabindex="-1"])';

let current = null; // the open guide: {close, dialog}

// ------------------------------------------------------------------ video

function videoPanel(onShowSteps, close) {
  const source = videoEmbed(state.plan?.settings.onboarding_video_url);
  if (source.kind === "youtube" || source.kind === "vimeo") {
    return h(
      "div",
      { class: "onb-video" },
      h("iframe", {
        src: source.src,
        title: t("Trading OS video guide"),
        allow: "accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture; fullscreen",
        allowfullscreen: true,
        referrerpolicy: "strict-origin-when-cross-origin",
        loading: "lazy",
      }),
    );
  }
  if (source.kind === "file") {
    return h("div", { class: "onb-video" }, h("video", { src: source.src, controls: true, preload: "metadata", playsinline: true }, t("Your browser cannot play this video.")));
  }
  const goToPlan = h("a", { class: "btn", href: "#/plan?tab=rules", onclick: () => close() }, icon("plan"), t("Open Trading Plan"));
  if (source.kind === "link") {
    return h(
      "div",
      { class: "onb-empty" },
      icon("video", "icon onb-empty-icon"),
      h("strong", {}, t("This video cannot be played inside Trading OS")),
      h("p", { class: "muted" }, t("Use a YouTube or Vimeo link, or a direct .mp4 / .webm file, to watch it here.")),
      h("div", { class: "row" }, h("a", { class: "btn primary", href: source.src, target: "_blank", rel: "noopener noreferrer" }, icon("link"), t("Open the video in a new tab")), goToPlan),
    );
  }
  return h(
    "div",
    { class: "onb-empty" },
    icon("video", "icon onb-empty-icon"),
    h("strong", {}, t("No video set: add a link under Trading Plan → Account")),
    h("p", { class: "muted" }, t("Paste a YouTube or Vimeo link, or a direct .mp4 / .webm file, in the Onboarding video link field. Meanwhile the step-by-step guide covers everything.")),
    h("div", { class: "row" }, h("button", { class: "btn primary", type: "button", onclick: onShowSteps }, icon("list"), t("Start the step-by-step guide")), goToPlan),
  );
}

// ------------------------------------------------------------------ steps

function stepsPanel({ close, finish }) {
  let index = 0;
  const total = STEPS.length;
  const listItems = STEPS.map((step, i) =>
    h("li", {}, h("button", { type: "button", class: "onb-steplink", onclick: () => go(i) }, h("span", { class: "onb-steplink-num" }), h("span", {}, t(step.title)))),
  );
  const list = h("ol", { class: "onb-steplist", "aria-label": t("Steps") }, listItems);
  const stage = h("section", { class: "onb-stage" });
  const back = h("button", { type: "button", class: "btn ghost", onclick: () => go(index - 1) }, icon("chevronLeft"), t("Back"));
  const next = h("button", { type: "button", class: "btn primary onb-next" });
  const dots = STEPS.map((step, i) => h("button", { type: "button", class: "onb-dot", "aria-label": t("Step {n}: {title}", { n: i + 1, title: t(step.title) }), onclick: () => go(i) }));
  const progress = h("div", { class: "onb-dots" }, dots);
  next.addEventListener("click", () => (index === total - 1 ? finish() : go(index + 1)));

  function go(target, focusStage = false) {
    if (target < 0 || target >= total) return;
    index = target;
    const step = STEPS[index];
    stage.replaceChildren(
      h(
        "div",
        { class: "onb-step" },
        h("div", { class: "onb-step-icon" }, icon(step.icon)),
        h(
          "div",
          { class: "onb-step-text", "aria-live": "polite" },
          h("div", { class: "onb-step-count" }, t("Step {n} of {total}", { n: index + 1, total })),
          h("h3", { tabindex: "-1" }, t(step.title)),
          h("p", {}, t(step.text)),
        ),
        h(
          "a",
          {
            class: "btn onb-go",
            href: step.route,
            onclick: () => close(),
          },
          t("Take me there"),
          icon("arrowRight"),
        ),
      ),
    );
    listItems.forEach((item, i) => {
      const link = item.firstChild;
      link.classList.toggle("on", i === index);
      link.classList.toggle("done", i < index);
      link.firstChild.replaceChildren(i < index ? icon("check") : String(i + 1));
      if (i === index) link.setAttribute("aria-current", "step");
      else link.removeAttribute("aria-current");
    });
    dots.forEach((dot, i) => {
      dot.classList.toggle("on", i === index);
      dot.classList.toggle("done", i < index);
      if (i === index) dot.setAttribute("aria-current", "step");
      else dot.removeAttribute("aria-current");
    });
    back.disabled = index === 0;
    next.replaceChildren(...(index === total - 1 ? [icon("check"), t("Finish")] : [t("Next step"), icon("chevronRight")]));
    if (focusStage) stage.querySelector("h3")?.focus();
  }

  const el = h(
    "div",
    { class: "onb-steps" },
    list,
    h("div", { class: "onb-main" }, stage, h("div", { class: "onb-nav" }, back, progress, next)),
  );
  el.addEventListener("keydown", (event) => {
    if (event.target.closest("input, textarea, select")) return;
    if (event.key === "ArrowRight" && index < total - 1) {
      event.preventDefault();
      go(index + 1, !event.target.closest(".onb-nav"));
    } else if (event.key === "ArrowLeft" && index > 0) {
      event.preventDefault();
      go(index - 1, !event.target.closest(".onb-nav"));
    }
  });
  go(0);
  return el;
}

// ------------------------------------------------------------------ dialog

async function saveDone(done, checkbox) {
  try {
    await api.put("/api/settings", { onboarding_done: done });
    if (state.plan) state.plan.settings.onboarding_done = done;
    return true;
  } catch (error) {
    if (checkbox) checkbox.checked = !done;
    toast(error.message, "error");
    return false;
  }
}

function focusables(dialog) {
  return [...dialog.querySelectorAll(FOCUSABLE)].filter((el) => el.offsetParent !== null || el === document.activeElement);
}

/** Open the getting-started guide (focuses it when it is already open). */
export function openOnboarding({ tab } = {}) {
  if (current) {
    current.dialog.focus();
    return current;
  }
  const previouslyFocused = document.activeElement;
  const app = document.querySelector(".app");
  const hasVideo = videoEmbed(state.plan?.settings.onboarding_video_url).kind !== "none";
  let activeTab = tab || (hasVideo ? "video" : "steps");

  const dontShow = h("input", { type: "checkbox", id: "onb-dont-show", checked: Boolean(state.plan?.settings.onboarding_done) });
  dontShow.addEventListener("change", () => saveDone(dontShow.checked, dontShow));

  const tabs = [
    ["steps", "list", t("Step-by-step")],
    ["video", "video", t("Video")],
  ].map(([key, iconName, text]) =>
    h("button", { type: "button", role: "tab", id: `onb-tab-${key}`, "aria-controls": "onb-panel", dataset: { tab: key }, onclick: () => showTab(key, true) }, icon(iconName), text),
  );
  const tablist = h("div", { class: "tabs onb-tabs", role: "tablist", "aria-label": t("Getting started guide") }, tabs);
  const panel = h("div", { class: "onb-panel", role: "tabpanel", id: "onb-panel", tabindex: "-1" });

  const closeButton = h("button", { type: "button", class: "btn ghost icon-only", "aria-label": t("Close"), onclick: () => close() }, icon("x"));
  const dialog = h(
    "div",
    { class: "modal wide onb", role: "dialog", "aria-modal": "true", "aria-labelledby": "onb-title", "aria-describedby": "onb-subtitle", tabindex: "-1" },
    h(
      "div",
      { class: "onb-head" },
      h("div", { class: "onb-brand" }, logoMark("logo-mark onb-logo")),
      h("div", { class: "onb-title" }, h("h2", { id: "onb-title" }, t("Getting started with Trading OS")), h("p", { id: "onb-subtitle", class: "muted small" }, t("A quick tour of the essentials: watch the video or follow the steps."))),
      closeButton,
    ),
    tablist,
    panel,
    h(
      "div",
      { class: "onb-foot" },
      h("label", { class: "check", for: "onb-dont-show" }, dontShow, t("Don't show this again")),
      h("span", { class: "muted small onb-foot-hint" }, t("Reopen it any time with the play button in the top bar.")),
    ),
  );
  const backdrop = h("div", { class: "modal-backdrop onb-backdrop", onmousedown: (event) => event.target === backdrop && close() }, dialog);

  function showTab(key, focusTab = false) {
    activeTab = key;
    for (const button of tabs) {
      const on = button.dataset.tab === key;
      button.classList.toggle("on", on);
      button.setAttribute("aria-selected", String(on));
      button.tabIndex = on ? 0 : -1;
      if (on) panel.setAttribute("aria-labelledby", button.id);
      if (on && focusTab) button.focus();
    }
    // Leaving the video tab removes the player, which also stops playback.
    panel.replaceChildren(key === "video" ? videoPanel(() => showTab("steps", true), close) : stepsPanel({ close, finish }));
  }

  async function finish() {
    if (!dontShow.checked) {
      dontShow.checked = true;
      await saveDone(true, dontShow);
    }
    close();
    toast(t("You're all set. Reopen the guide any time with the play button in the top bar."), "success");
  }

  tablist.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight" && event.key !== "Home" && event.key !== "End") return;
    event.preventDefault();
    event.stopPropagation();
    const keys = tabs.map((button) => button.dataset.tab);
    const at = keys.indexOf(activeTab);
    const target = event.key === "Home" ? 0 : event.key === "End" ? keys.length - 1 : (at + (event.key === "ArrowRight" ? 1 : -1) + keys.length) % keys.length;
    showTab(keys[target], true);
  });

  function onKey(event) {
    if (event.key === "Escape") {
      if (document.querySelector(".lightbox")) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      close();
      return;
    }
    if (event.key !== "Tab") return;
    const items = focusables(dialog);
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) {
      event.preventDefault();
      first.focus();
    }
  }

  function close() {
    if (!current) return;
    current = null;
    document.removeEventListener("keydown", onKey, true);
    backdrop.remove();
    if (app) app.inert = false;
    if (previouslyFocused?.isConnected) previouslyFocused.focus?.();
  }

  document.body.append(backdrop);
  if (app) app.inert = true; // the page behind stays out of reach for keyboard and screen readers
  document.addEventListener("keydown", onKey, true);
  showTab(activeTab);
  requestAnimationFrame(() => (tabs.find((button) => button.dataset.tab === activeTab) || dialog).focus());
  current = { close, dialog };
  return current;
}

/** First launch: open the guide once per browser session until the trader turned it off. */
export function autoOpenOnboarding() {
  if (!state.plan || state.plan.settings.onboarding_done) return;
  try {
    if (sessionStorage.getItem(SESSION_KEY)) return;
    sessionStorage.setItem(SESSION_KEY, "1");
  } catch {
    // storage unavailable (private mode): still show it once now
  }
  openOnboarding();
}
