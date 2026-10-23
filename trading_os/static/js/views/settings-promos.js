// Trading plan -> Promotions: the dashboard carousel switch and interval, and its cards (order, add, edit, delete).
//
// Partner cards follow their partner (empty title/text/link = the partner's own). Discount cards hold only the
// code and offer text the trader received and typed in: the app never suggests codes or offers.

import { api } from "../api.js";
import { clear, confirmDialog, field, h, icon, modal, segmented, select, toast } from "../dom.js";
import { fmt } from "../format.js";
import { N_, t } from "../i18n.js";
import { hiddenPromoIds, PROMO_THEMES, renderPromoCard, showHiddenPromos } from "../promos.js";

const KIND_LABEL = { announcement: N_("Announcement"), partner: N_("Partner"), discount: N_("Discount") };
const THEME_LABEL = { teal: N_("Teal"), blue: N_("Blue"), gold: N_("Gold"), purple: N_("Purple"), red: N_("Red") };
const STATUS = {
  live: { cls: "good", label: N_("Live") },
  off: { cls: "", label: N_("Off") },
  scheduled: { cls: "warn", label: N_("Scheduled") },
  ended: { cls: "", label: N_("Ended") },
  partner_hidden: { cls: "warn", label: N_("Partner hidden") },
  no_partner: { cls: "bad", label: N_("No partner") },
};
const LIMITS = { title: 80, body: 300, cta_label: 40, cta_url: 500, code: 40 };
const MIN_INTERVAL = 3;
const MAX_INTERVAL = 60;

const isRoute = (url) => /^#\/[\w\-./?=&%]*$/.test(url);
const isHttps = (url) => /^https:\/\/[^\s@/]+[^\s]*$/i.test(url);

export async function renderPromoSettings(container) {
  let data = await api.get("/api/promos/all");

  async function reload(focus) {
    data = await api.get("/api/promos/all");
    draw(focus);
  }

  function draw(focus) {
    clear(container, h("div", { class: "stack" }, settingsCard(), listCard()));
    if (focus) container.querySelector(focus)?.focus();
  }

  // -------------------------------------------------------------- carousel settings

  function settingsCard() {
    const enabled = h("input", { type: "checkbox", role: "switch", checked: data.enabled, onchange: () => save({ enabled: enabled.checked }) });
    const interval = h("input", { type: "number", min: String(MIN_INTERVAL), max: String(MAX_INTERVAL), step: "1", value: String(data.interval_sec), inputmode: "numeric" });
    interval.addEventListener("change", () => {
      const value = Math.round(Number(interval.value));
      if (!Number.isFinite(value) || value < MIN_INTERVAL || value > MAX_INTERVAL) {
        toast(t("Choose between {min} and {max} seconds", { min: MIN_INTERVAL, max: MAX_INTERVAL }), "error");
        interval.value = String(data.interval_sec);
        return;
      }
      save({ interval_sec: value });
    });
    const hiddenCount = data.promos.filter((p) => hiddenPromoIds().has(String(p.id))).length;
    return h(
      "div",
      { class: "card" },
      h("div", { class: "card-head" }, h("div", {}, h("h2", {}, t("Dashboard carousel")), h("p", {}, t("Rotating banners at the top of the dashboard: platform news, featured partners and your own discount codes.")))),
      h(
        "div",
        { class: "pe-settings" },
        h("label", { class: "pe-switch" }, enabled, h("span", { class: "pe-switch-track", "aria-hidden": "true" }), h("span", {}, t("Show the carousel on the dashboard"))),
        field(t("Rotate every (seconds)"), interval, { help: t("{min}–{max} seconds", { min: MIN_INTERVAL, max: MAX_INTERVAL }) }),
      ),
      hiddenCount
        ? h(
            "div",
            { class: "row", style: { marginTop: "14px" } },
            h("span", { class: "muted small" }, t("Cards you hide on the dashboard stay hidden on this device.")),
            h("button", { type: "button", class: "btn sm", onclick: () => (showHiddenPromos(), toast(t("Hidden cards are shown again on this device"), "success"), draw()) }, icon("undo"), t("Show hidden cards ({n})", { n: hiddenCount })),
          )
        : null,
    );
  }

  async function save(changes) {
    try {
      const result = await api.put("/api/promos/settings", changes);
      data.enabled = result.enabled;
      data.interval_sec = result.interval_sec;
      toast(t("Carousel settings saved"), "success");
    } catch (error) {
      toast(error.message, "error");
      draw();
    }
  }

  // ----------------------------------------------------------------------- list

  function listCard() {
    const hidden = hiddenPromoIds();
    const rows = data.promos.map((promo, i) => promoRow(promo, i, hidden));
    return h(
      "div",
      { class: "card" },
      h(
        "div",
        { class: "card-head" },
        h("div", {}, h("h2", {}, t("Promotions")), h("p", {}, t("Shown in this order. Partner and discount cards carry the risk disclaimer."))),
        h("button", { class: "btn primary", type: "button", onclick: () => openEditor(null) }, icon("plus"), t("Add promotion")),
      ),
      rows.length ? h("ul", { class: "pe-list" }, rows) : h("div", { class: "empty" }, h("strong", {}, t("No promotions yet")), t("Add an announcement, a partner card or a discount code you received.")),
    );
  }

  function promoRow(promo, i, hidden) {
    const status = STATUS[promo.status] || STATUS.off;
    const title = promo.display.title || t(KIND_LABEL[promo.kind]);
    const dates = dateRange(promo);
    const last = data.promos.length - 1;
    const button = (name, label, onclick, extra = {}) => h("button", { type: "button", class: "btn ghost sm icon-only", "aria-label": label, title: label, onclick, ...extra }, icon(name));
    return h(
      "li",
      { class: `pe-row${promo.status === "live" ? "" : " is-off"}` },
      h("span", { class: `pe-swatch promo-${promo.theme}`, "aria-hidden": "true" }),
      h(
        "div",
        { class: "pe-text" },
        h("strong", {}, title),
        h(
          "div",
          { class: "pe-meta" },
          h("span", { class: "badge" }, t(KIND_LABEL[promo.kind] || KIND_LABEL.announcement)),
          h("span", { class: `badge ${status.cls}` }, t(status.label)),
          promo.builtin ? h("span", { class: "badge open" }, t("Built-in")) : null,
          promo.display.partner && promo.display.partner.name !== title ? h("span", {}, promo.display.partner.name) : null,
          promo.code ? h("span", { class: "chip", style: { padding: "1px 8px", fontFamily: "var(--mono)" } }, promo.code) : null,
          dates ? h("span", {}, dates) : null,
          hidden.has(String(promo.id)) ? h("span", {}, icon("info"), " ", t("Hidden on this device")) : null,
        ),
      ),
      h(
        "div",
        { class: "pe-actions" },
        button("up", t("Move {title} up", { title }), () => move(i, -1), { disabled: i === 0, dataset: { move: "up", id: String(promo.id) } }),
        button("down", t("Move {title} down", { title }), () => move(i, 1), { disabled: i === last, dataset: { move: "down", id: String(promo.id) } }),
        button("edit", t("Edit {title}", { title }), () => openEditor(promo)),
        button("trash", t("Delete {title}", { title }), () => remove(promo, title)),
      ),
    );
  }

  function dateRange(promo) {
    if (promo.starts_on && promo.ends_on) return t("{start} – {end}", { start: fmt.date(promo.starts_on), end: fmt.date(promo.ends_on) });
    if (promo.starts_on) return t("From {date}", { date: fmt.date(promo.starts_on) });
    if (promo.ends_on) return t("Until {date}", { date: fmt.date(promo.ends_on) });
    return "";
  }

  async function move(i, step) {
    const ids = data.promos.map((p) => p.id);
    const j = i + step;
    if (j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    const id = ids[j];
    try {
      data = await api.put("/api/promos/order", { ids });
      const direction = step < 0 ? "up" : "down";
      const other = step < 0 ? "down" : "up";
      draw();
      const target = container.querySelector(`[data-move="${direction}"][data-id="${id}"]`);
      (target && !target.disabled ? target : container.querySelector(`[data-move="${other}"][data-id="${id}"]`))?.focus();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  async function remove(promo, title) {
    const message = promo.builtin
      ? t("Delete the built-in card “{title}”? It will not come back. To hide it for a while, switch it off instead.", { title })
      : t("Delete the promotion “{title}”?", { title });
    if (!(await confirmDialog(message, { title: t("Delete promotion"), confirmText: t("Delete"), danger: true }))) return;
    try {
      await api.del(`/api/promos/${promo.id}`);
      toast(t("Promotion deleted"), "success");
      await reload();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  // --------------------------------------------------------------------- editor

  function openEditor(promo) {
    const v = promo
      ? { ...promo }
      : { kind: "announcement", title: "", body: "", cta_label: "", cta_url: "", code: "", partner_id: null, theme: "teal", starts_on: null, ends_on: null, active: true, builtin_key: "" };
    const partners = data.partners;

    const counter = (input, limit) => {
      const out = h("div", { class: "pe-counter", "aria-hidden": "true" });
      const update = () => (out.textContent = `${input.value.length}/${limit}`);
      input.addEventListener("input", update);
      update();
      return out;
    };
    const titleInput = h("input", { value: v.title, maxlength: String(LIMITS.title) });
    const bodyInput = h("textarea", { rows: 3, value: v.body, maxlength: String(LIMITS.body) });
    const codeInput = h("input", { value: v.code, maxlength: String(LIMITS.code), autocomplete: "off", spellcheck: "false", style: { fontFamily: "var(--mono)" } });
    const labelInput = h("input", { value: v.cta_label, maxlength: String(LIMITS.cta_label) });
    const urlInput = h("input", { value: v.cta_url, maxlength: String(LIMITS.cta_url), placeholder: t("#/journal or https://…"), spellcheck: "false", autocomplete: "off" });
    const urlError = h("div", { class: "help", style: { color: "var(--critical-text)" }, role: "alert" });
    const startsInput = h("input", { type: "date", value: v.starts_on || "" });
    const endsInput = h("input", { type: "date", value: v.ends_on || "" });
    const activeBox = h("input", { type: "checkbox", checked: v.active });
    const partnerSelect = select([], "");
    const kind = segmented(
      Object.entries(KIND_LABEL).map(([value, label]) => [value, t(label)]),
      v.kind,
      () => refresh(),
    );
    kind.el.setAttribute("aria-label", t("Type"));
    if (v.builtin_key) for (const b of kind.el.querySelectorAll("button")) b.disabled = true;

    let theme = PROMO_THEMES.includes(v.theme) ? v.theme : "teal";
    const themeButtons = PROMO_THEMES.map((name) =>
      h("button", { type: "button", role: "radio", class: `pe-theme promo-${name}`, "aria-label": t(THEME_LABEL[name]), title: t(THEME_LABEL[name]), dataset: { theme: name }, onclick: () => setTheme(name, true) }),
    );
    const themes = h("div", { class: "pe-themes", role: "radiogroup", "aria-label": t("Colour"), onkeydown: onThemeKey }, themeButtons);
    function setTheme(name, focus = false) {
      theme = name;
      for (const b of themeButtons) {
        const on = b.dataset.theme === name;
        b.setAttribute("aria-checked", String(on));
        b.tabIndex = on ? 0 : -1;
        if (on && focus) b.focus();
      }
      updatePreview();
    }
    function onThemeKey(event) {
      const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
      if (!step) return;
      event.preventDefault();
      const i = PROMO_THEMES.indexOf(theme);
      setTheme(PROMO_THEMES[(i + step + PROMO_THEMES.length) % PROMO_THEMES.length], true);
    }

    const partnerField = field(t("Partner"), partnerSelect);
    const codeField = field(t("Discount code"), codeInput, { help: t("Only a code you received yourself. Check the current terms on the firm's website.") });
    const discountNote = h("div", { class: "pe-note warn full" }, icon("alert"), h("span", {}, t("Type the code and the offer exactly as the firm gave them to you. Trading OS never suggests codes or offers.")));
    const builtinNote = v.builtin_key ? h("div", { class: "pe-note full" }, icon("info"), h("span", {}, t("Built-in announcement: its texts follow the app language until you change them."))) : null;
    const titleField = field(t("Title"), h("div", { class: "stack", style: { gap: "2px" } }, titleInput, counter(titleInput, LIMITS.title)), { full: true });
    const bodyField = field(t("Text"), h("div", { class: "stack", style: { gap: "2px" } }, bodyInput, counter(bodyInput, LIMITS.body)), { full: true });
    const urlField = field(t("Button link"), h("div", { class: "stack", style: { gap: "4px" } }, urlInput, urlError), { full: true });
    const urlHelp = h("div", { class: "help" });
    urlField.append(urlHelp);

    const previewSlot = h("div");
    const preview = h(
      "div",
      { class: "pe-preview", onclick: (event) => event.target.closest("a") && event.preventDefault() },
      h("span", { class: "label" }, t("Preview")),
      previewSlot,
    );

    function selectedPartner() {
      const id = Number(partnerSelect.value);
      return kind.value === "announcement" || !id ? null : partners.find((p) => p.id === id) || null;
    }

    function fillPartners() {
      const current = partnerSelect.value ? Number(partnerSelect.value) : v.partner_id;
      const options = partners.map((p) => [String(p.id), p.active ? p.name : t("{name} (hidden)", { name: p.name })]);
      if (kind.value === "discount") options.unshift(["", t("No partner")]);
      clear(partnerSelect, options.map(([value, label]) => h("option", { value }, label)));
      const wanted = current != null && partners.some((p) => p.id === current) ? String(current) : kind.value === "partner" && partners.length ? String(partners[0].id) : "";
      partnerSelect.value = wanted;
    }

    function refresh() {
      const k = kind.value;
      fillPartners();
      partnerField.hidden = k === "announcement";
      codeField.hidden = k !== "discount";
      discountNote.hidden = k !== "discount";
      updatePlaceholders();
      updatePreview();
    }

    function updatePlaceholders() {
      const partner = selectedPartner();
      titleInput.placeholder = partner ? partner.name : "";
      labelInput.placeholder = partner && !urlInput.value.trim() ? t("Visit {name}", { name: partner.name }) : t("Learn more");
      bodyInput.placeholder = partner && kind.value === "partner" ? partner.description : "";
      urlHelp.textContent = partner
        ? t("An app page such as #/journal or an https:// link. Leave empty to use the partner's link.")
        : t("An app page such as #/journal or an https:// link. External links open in a new tab.");
    }

    function urlProblem() {
      const url = urlInput.value.trim();
      return url && !isRoute(url) && !isHttps(url) ? t("Links must be an app page such as #/journal or start with https://") : "";
    }

    function values() {
      return {
        kind: kind.value,
        title: titleInput.value.trim(),
        body: bodyInput.value.trim(),
        cta_label: labelInput.value.trim(),
        cta_url: urlInput.value.trim(),
        code: kind.value === "discount" ? codeInput.value.trim() : "",
        partner_id: kind.value === "announcement" ? null : Number(partnerSelect.value) || null,
        theme,
        starts_on: startsInput.value || null,
        ends_on: endsInput.value || null,
        active: activeBox.checked,
      };
    }

    function previewCard() {
      const value = values();
      const partner = selectedPartner();
      let url = urlProblem() ? "" : value.cta_url;
      if (!url && partner && !value.cta_url) url = isHttps(partner.link) ? partner.link : "#/partners";
      let label = value.cta_label;
      if (url && !label) label = partner && !value.cta_url ? t("Visit {name}", { name: partner.name }) : t("Learn more");
      const disclaimer = value.kind !== "announcement";
      return {
        id: promo?.id ?? 0,
        kind: value.kind,
        theme,
        builtin_key: v.builtin_key || "",
        title: value.title || partner?.name || t("Your title"),
        body: value.body || (value.kind === "partner" && partner ? partner.description : ""),
        cta_label: url ? label : "",
        cta_url: url,
        code: value.code,
        partner: partner ? { id: partner.id, name: partner.name, kind: partner.kind } : null,
        disclaimer,
        disclaimer_text: disclaimer ? data.disclaimer_text : "",
      };
    }

    function updatePreview() {
      urlError.textContent = urlProblem();
      clear(previewSlot, renderPromoCard(previewCard()));
    }

    for (const input of [titleInput, bodyInput, codeInput, labelInput, urlInput, partnerSelect]) {
      input.addEventListener("input", () => (updatePlaceholders(), updatePreview()));
      input.addEventListener("change", () => (updatePlaceholders(), updatePreview()));
    }

    const saveBtn = h("button", { class: "btn primary", type: "button", onclick: submit }, icon("check"), t("Save"));
    async function submit() {
      const body = values();
      if (urlProblem()) {
        toast(urlProblem(), "error");
        urlInput.focus();
        return;
      }
      if (body.kind === "partner" && !body.partner_id) {
        toast(t("Choose the partner this card is about"), "error");
        partnerSelect.focus();
        return;
      }
      if (!body.title && !body.partner_id) {
        toast(t("A title is required"), "error");
        titleInput.focus();
        return;
      }
      if (body.starts_on && body.ends_on && body.ends_on < body.starts_on) {
        toast(t("The end date must be on or after the start date"), "error");
        endsInput.focus();
        return;
      }
      saveBtn.disabled = true;
      try {
        const saved = promo ? await api.put(`/api/promos/${promo.id}`, body) : await api.post("/api/promos", body);
        ref.close();
        toast(t("Promotion saved"), "success");
        await reload(`[data-move][data-id="${saved.id}"]:not([disabled])`);
      } catch (error) {
        toast(error.message, "error");
        saveBtn.disabled = false;
      }
    }

    const form = h(
      "div",
      { class: "form-grid" },
      builtinNote,
      h("div", { class: "field full" }, h("span", { class: "label" }, t("Type")), kind.el),
      partnerField,
      titleField,
      bodyField,
      codeField,
      discountNote,
      field(t("Button label"), labelInput),
      urlField,
      h("div", { class: "field full" }, h("span", { class: "label" }, t("Colour")), themes),
      field(t("Show from"), startsInput),
      field(t("Show until"), endsInput),
      h("label", { class: "check full" }, activeBox, t("Shown in the carousel")),
    );
    partnerField.classList.add("full");
    const ref = modal({
      title: promo ? t("Edit promotion") : t("Add promotion"),
      wide: true,
      body: h("div", { class: "pe-editor" }, form, preview),
      footer: [h("button", { class: "btn ghost", type: "button", onclick: () => ref.close() }, t("Cancel")), saveBtn],
    });
    setTheme(theme);
    refresh();
    requestAnimationFrame(() => (partnerField.hidden ? titleInput : partnerSelect).focus());
  }

  draw();
}
