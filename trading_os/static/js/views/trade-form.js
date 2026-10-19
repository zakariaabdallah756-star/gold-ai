// Trade entry / edit form with live guardrail checks and screenshot handling.

import { api, ApiError } from "../api.js";
import { clear, debounce, field, h, icon, lightbox, modal, numberOrNull, segmented, select, toast } from "../dom.js";
import { fmt, toInputDateTime } from "../format.js";
import { accountNow, loadStatus, marketBySymbol, state, strategyByName } from "../state.js";
import { violationList } from "./calculator.js";

const num = (input) => numberOrNull(input);
const inputValue = (value) => (value === null || value === undefined ? "" : String(value));

function screenshotTile(shot, onRemove) {
  const media =
    shot.kind === "file"
      ? h("img", { src: shot.src, alt: shot.caption || "Chart screenshot", loading: "lazy", onclick: () => lightbox(shot.src) })
      : h("a", { class: "link-shot", href: shot.src, target: "_blank", rel: "noopener noreferrer" }, icon("link"), shot.src.replace(/^https?:\/\//, "").slice(0, 60));
  return h(
    "div",
    { class: "shot" },
    media,
    h("div", { class: "caption", title: shot.caption }, shot.caption || (shot.kind === "file" ? "Screenshot" : "Chart link")),
    onRemove ? h("button", { type: "button", class: "btn sm icon-only remove", "aria-label": "Remove screenshot", onclick: onRemove }, icon("trash")) : null,
  );
}

export function screenshotGallery(shots) {
  return shots.length ? h("div", { class: "shots" }, shots.map((shot) => screenshotTile(shot))) : h("p", { class: "muted small" }, "No screenshots.");
}

/**
 * Open the trade form.
 * @param {object} options
 * @param {object|null} options.trade  existing trade to edit
 * @param {object} options.prefill      values for a new trade (symbol, direction, entry, sl, tp, size)
 * @param {boolean} options.closing     preset the status to "closed" (close an open trade)
 * @param {Function} options.onSaved    called with the saved trade
 */
export function openTradeForm({ trade = null, prefill = {}, closing = false, onSaved } = {}) {
  const { plan } = state;
  const isEdit = Boolean(trade);
  const t = trade || {};
  const confluences = new Set(t.confluences || []);
  let emotion = t.emotion || "";
  let shots = [...(t.screenshots || [])];
  const queued = []; // screenshots added before a new trade is saved
  let riskTouched = false;

  // ---------------------------------------------------------------- controls
  const status = segmented(
    [
      ["open", "Open"],
      ["closed", "Closed"],
    ],
    closing ? "closed" : t.status || "open",
    () => {
      if (status.value === "closed" && !exitTime.value) exitTime.value = toInputDateTime(accountNow());
      showExit();
      check();
    },
  );
  const direction = segmented(
    [
      ["long", "Long", "long"],
      ["short", "Short", "short"],
    ],
    t.direction || prefill.direction || "long",
    () => check(),
  );
  const entryTime = h("input", { type: "datetime-local", value: t.entry_time ? t.entry_time.slice(0, 16) : toInputDateTime(accountNow()), oninput: () => check() });
  const symbolList = h("datalist", { id: "symbol-options" }, plan.markets.map((m) => h("option", { value: m.symbol }, m.name)));
  const symbol = h("input", {
    value: t.symbol || prefill.symbol || plan.markets.find((m) => m.active)?.symbol || "",
    list: "symbol-options",
    autocomplete: "off",
    style: { textTransform: "uppercase" },
    oninput: () => (resetRisk(), check()),
  });
  const strategyNames = plan.strategies.filter((s) => s.active || s.name === t.strategy).map((s) => s.name);
  const strategy = select([["", "—"], ...strategyNames], t.strategy || "", { onchange: () => (renderConfluences(), check()) });
  const timeframe = select([["", "—"], ...plan.enums.timeframes], t.timeframe || "");
  const sessionNames = plan.sessions.map((s) => s.name);
  if (t.session && !sessionNames.includes(t.session)) sessionNames.push(t.session);
  const session = select([["", "Auto-detect"], ...sessionNames], t.session || "", { onchange: () => check() });

  const numeric = (value, attrs = {}) => h("input", { type: "number", step: "any", inputmode: "decimal", value: inputValue(value), ...attrs });
  const entry = numeric(t.entry_price ?? prefill.entry, { oninput: () => (resetRisk(), check()) });
  const stop = numeric(t.stop_loss ?? prefill.sl, { oninput: () => (resetRisk(), check()) });
  const target = numeric(t.take_profit ?? prefill.tp, { placeholder: "optional", oninput: () => check() });
  const size = numeric(t.position_size ?? prefill.size, { placeholder: "lots", min: "0", oninput: () => (resetRisk(), check()) });
  const riskAmount = numeric(t.risk_amount, {
    placeholder: "auto",
    min: "0",
    oninput: () => {
      riskTouched = true;
      check();
    },
  });
  const exitPrice = numeric(t.exit_price);
  const exitTime = h("input", { type: "datetime-local", value: t.exit_time ? t.exit_time.slice(0, 16) : "" });
  const fees = numeric(t.fees || "", { placeholder: "0.00" });
  const pnl = numeric(t.pnl_manual ? t.pnl : "", { placeholder: t.pnl !== null && t.pnl !== undefined && !t.pnl_manual ? `auto (${fmt.signedMoney(t.pnl)})` : "auto from prices" });
  exitPrice.addEventListener("input", () => check());

  const sizeButton = h("button", { type: "button", class: "btn sm", title: "Size from your default risk %", onclick: sizeFromRisk }, icon("calculator"), "Size");

  const compliance = segmented(
    [
      ["auto", "Auto"],
      ["yes", "Followed plan"],
      ["no", "Broke rules"],
    ],
    t.rule_compliant === true ? "yes" : t.rule_compliant === false ? "no" : "auto",
  );
  const tags = h("input", { value: (t.tags || []).join(", "), placeholder: "a-plus-setup, news, late-entry" });
  const notes = h("textarea", { rows: 4, value: t.notes || "", placeholder: "Why did you take it? What did you see? What would you do differently?" });

  const confluenceBox = h("div", { class: "chips" });
  const confluenceHint = h("span", { class: "muted small" });
  const emotionBox = h("div", { class: "chips" });
  const exitSection = h("div");
  const live = h("div", { class: "live-metrics" });
  const checkPanel = h("div", { style: { marginTop: "12px" } });
  const shotsBox = h("div");

  function resetRisk() {
    // Let the server recompute risk from size/prices unless the user typed it.
    if (!riskTouched && marketBySymbol(symbol.value.trim().toUpperCase())) riskAmount.value = "";
  }

  function renderConfluences() {
    const names = plan.confluences.filter((c) => c.active).map((c) => c.name);
    for (const name of confluences) if (!names.includes(name)) names.push(name);
    clear(
      confluenceBox,
      names.map((name) =>
        h(
          "button",
          {
            type: "button",
            class: `chip-toggle${confluences.has(name) ? " on" : ""}`,
            "aria-pressed": String(confluences.has(name)),
            onclick: () => {
              if (confluences.has(name)) confluences.delete(name);
              else confluences.add(name);
              renderConfluences();
              check();
            },
          },
          name,
        ),
      ),
    );
    const min = strategyByName(strategy.value)?.min_confluences || 0;
    confluenceHint.textContent = min ? `${confluences.size} / ${min} required by ${strategy.value}` : `${confluences.size} selected`;
    confluenceHint.className = min && confluences.size < min ? "small neg" : "muted small";
  }

  function renderEmotions() {
    clear(
      emotionBox,
      plan.enums.emotions.map((name) =>
        h(
          "button",
          {
            type: "button",
            class: `chip-toggle${emotion === name ? " on" : ""}`,
            "aria-pressed": String(emotion === name),
            onclick: () => {
              emotion = emotion === name ? "" : name;
              renderEmotions();
            },
          },
          name,
        ),
      ),
    );
  }

  function showExit() {
    exitSection.style.display = status.value === "closed" ? "" : "none";
    saveButton.textContent = isEdit ? "Save changes" : status.value === "open" ? "Open trade" : "Log trade";
  }

  function renderShots() {
    const removeExisting = (shot) => async () => {
      try {
        await api.del(`/api/screenshots/${shot.id}`);
        shots = shots.filter((s) => s.id !== shot.id);
        renderShots();
      } catch (error) {
        toast(error.message, "error");
      }
    };
    const tiles = [
      ...shots.map((shot) => screenshotTile(shot, removeExisting(shot))),
      ...queued.map((item, index) =>
        screenshotTile(item.preview, () => {
          if (item.preview.kind === "file") URL.revokeObjectURL(item.preview.src);
          queued.splice(index, 1);
          renderShots();
        }),
      ),
    ];
    const fileInput = h("input", { type: "file", accept: "image/png,image/jpeg,image/gif,image/webp", multiple: true, style: { display: "none" }, onchange: (e) => addFiles([...e.target.files]) });
    const dropzone = h(
      "div",
      { class: "dropzone" },
      icon("upload"),
      " Drop chart images here, paste from clipboard (Ctrl+V), or ",
      h("a", { href: "#", onclick: (e) => (e.preventDefault(), fileInput.click()) }, "browse"),
      fileInput,
    );
    dropzone.addEventListener("dragover", (e) => (e.preventDefault(), dropzone.classList.add("over")));
    dropzone.addEventListener("dragleave", () => dropzone.classList.remove("over"));
    dropzone.addEventListener("drop", (e) => {
      e.preventDefault();
      dropzone.classList.remove("over");
      addFiles([...e.dataTransfer.files]);
    });
    const urlInput = h("input", { type: "url", placeholder: "https://www.tradingview.com/x/…" });
    const captionInput = h("input", { placeholder: "Caption (optional)", maxlength: "200" });
    const addUrl = h("button", { type: "button", class: "btn", onclick: () => addLink(urlInput.value.trim(), captionInput.value.trim()) }, icon("link"), "Add link");
    clear(shotsBox, tiles.length ? h("div", { class: "shots", style: { marginBottom: "10px" } }, tiles) : null, dropzone, h("div", { class: "row", style: { marginTop: "10px" } }, h("div", { style: { flex: "2", minWidth: "200px" } }, urlInput), h("div", { style: { flex: "1", minWidth: "140px" } }, captionInput), addUrl));
  }

  async function uploadFile(tradeId, file, caption = "") {
    const form = new FormData();
    form.append("file", file);
    form.append("caption", caption);
    return api.upload(`/api/trades/${tradeId}/screenshots`, form);
  }

  async function addFiles(files) {
    const images = files.filter((f) => /^image\/(png|jpeg|gif|webp)$/.test(f.type));
    if (images.length < files.length) toast("Only PNG, JPEG, GIF and WebP images are supported", "warn");
    for (const file of images) {
      if (isEdit) {
        try {
          shots.push(await uploadFile(t.id, file));
        } catch (error) {
          toast(error.message, "error");
        }
      } else {
        queued.push({ file, preview: { kind: "file", src: URL.createObjectURL(file), caption: file.name } });
      }
    }
    renderShots();
  }

  async function addLink(url, caption) {
    if (!/^https?:\/\//i.test(url)) {
      toast("Enter a link starting with http:// or https://", "warn");
      return;
    }
    if (isEdit) {
      try {
        shots.push(await api.post(`/api/trades/${t.id}/screenshots/url`, { url, caption }));
      } catch (error) {
        toast(error.message, "error");
        return;
      }
    } else {
      queued.push({ url, caption, preview: { kind: "url", src: url, caption } });
    }
    renderShots();
  }

  async function sizeFromRisk() {
    const market = marketBySymbol(symbol.value.trim().toUpperCase());
    if (!market || num(entry) === null || num(stop) === null) {
      toast("Pick a configured market and enter entry + stop first", "warn");
      return;
    }
    try {
      const r = await api.post("/api/risk/position-size", { market_id: market.id, entry_price: num(entry), stop_price: num(stop) });
      size.value = String(r.lots);
      resetRisk();
      check();
      toast(`${r.lots} lots risks ${fmt.money(r.max_loss)} (${fmt.pct(r.actual_risk_pct, 2)})`, "success");
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function payload() {
    const closed = status.value === "closed";
    return {
      status: status.value,
      entry_time: entryTime.value,
      exit_time: closed && exitTime.value ? exitTime.value : null,
      symbol: symbol.value.trim().toUpperCase(),
      direction: direction.value,
      entry_price: num(entry),
      stop_loss: num(stop),
      take_profit: num(target),
      exit_price: closed ? num(exitPrice) : null,
      position_size: num(size),
      risk_amount: num(riskAmount),
      fees: num(fees) ?? 0,
      pnl: closed ? num(pnl) : null,
      strategy: strategy.value,
      timeframe: timeframe.value,
      session: session.value,
      confluences: [...confluences],
      emotion,
      rule_compliant: compliance.value === "auto" ? null : compliance.value === "yes",
      tags: tags.value.split(",").map((s) => s.trim()).filter(Boolean),
      notes: notes.value,
    };
  }

  function metric(label, value, cls = "") {
    return h("div", {}, h("div", { class: "label" }, label), h("div", { class: `value ${cls}` }, value));
  }

  let checkId = 0;
  const check = debounce(async () => {
    const data = payload();
    if (!data.entry_time || !data.symbol || data.entry_price === null || data.stop_loss === null) {
      clear(live, metric("Planned R:R", "—"), metric("Risk", "—"), metric("Session", "—"));
      clear(checkPanel);
      return;
    }
    const id = ++checkId;
    try {
      const r = await api.post(`/api/trades/check${isEdit ? `?trade_id=${t.id}` : ""}`, { ...data, status: data.status === "closed" && data.exit_price === null ? "open" : data.status });
      if (id !== checkId) return;
      clear(
        live,
        metric("Planned R:R", fmt.rr(r.planned_rr)),
        metric("Risk", r.risk_amount === null ? "—" : fmt.money(r.risk_amount)),
        metric("Risk % of equity", fmt.pct(r.risk_pct, 2)),
        metric("Session", r.session || "Off-session"),
      );
      clear(
        checkPanel,
        r.blocked ? h("p", { class: "small neg", style: { marginBottom: "8px" } }, "This entry is blocked by your risk rules.") : null,
        violationList(r.violations, { okText: "All guardrails pass." }),
      );
    } catch (error) {
      if (id !== checkId) return;
      clear(checkPanel, h("div", { class: "violation warn" }, icon("alert"), h("div", {}, error.message)));
    }
  }, 350);

  // ------------------------------------------------------------------- save
  const saveButton = h("button", { class: "btn primary", type: "button", onclick: save });

  async function save() {
    saveButton.disabled = true;
    try {
      const data = payload();
      const result = isEdit ? await api.put(`/api/trades/${t.id}`, data) : await api.post("/api/trades", data);
      let saved = result.trade;
      for (const item of queued) {
        try {
          if (item.file) await uploadFile(saved.id, item.file);
          else await api.post(`/api/trades/${saved.id}/screenshots/url`, { url: item.url, caption: item.caption });
        } catch (error) {
          toast(`Screenshot not saved: ${error.message}`, "error");
        }
        if (item.preview.kind === "file") URL.revokeObjectURL(item.preview.src);
      }
      if (queued.length) saved = await api.get(`/api/trades/${saved.id}`);
      const warnings = result.warnings.length;
      toast(`${isEdit ? "Trade updated" : "Trade logged"}${warnings ? ` with ${warnings} rule warning${warnings > 1 ? "s" : ""}` : ""}`, warnings ? "warn" : "success");
      ref.close();
      loadStatus().catch(() => {});
      onSaved?.(saved);
    } catch (error) {
      if (error instanceof ApiError && error.status === 423) {
        clear(checkPanel, h("p", { class: "small neg", style: { marginBottom: "8px" } }, "Entry refused - fix these first:"), violationList(error.payload.violations));
        checkPanel.scrollIntoView({ behavior: "smooth", block: "center" });
        toast("Trade blocked by your risk rules", "error");
      } else {
        toast(error.message, "error");
      }
    } finally {
      saveButton.disabled = false;
    }
  }

  // ----------------------------------------------------------------- layout
  clear(
    exitSection,
    h("div", { class: "section-title" }, "Exit"),
    h(
      "div",
      { class: "form-grid" },
      field("Exit price", exitPrice),
      field("Exit time", exitTime),
      field(`Fees / swap (${plan.settings.currency})`, fees, { help: "Costs, deducted from P&L" }),
      field(`Net P&L (${plan.settings.currency})`, pnl, { help: "Leave blank to calculate from prices" }),
    ),
  );

  const body = h(
    "div",
    {},
    symbolList,
    h("div", { class: "row", style: { justifyContent: "space-between", marginBottom: "14px" } }, status.el, direction.el),
    h("div", { class: "section-title" }, "Setup"),
    h(
      "div",
      { class: "form-grid" },
      field("Entry time", entryTime),
      field("Asset", symbol),
      field("Strategy", strategy),
      field("Timeframe", timeframe),
      field("Session", session),
    ),
    h("div", { class: "section-title" }, "Prices & size"),
    h(
      "div",
      { class: "form-grid" },
      field("Entry price", entry),
      field("Stop loss (initial)", stop),
      field("Take profit", target),
      field("Position size (lots)", h("div", { class: "row", style: { flexWrap: "nowrap", gap: "6px" } }, size, sizeButton)),
      field(`Risk amount (${plan.settings.currency})`, riskAmount, { help: "Auto from size & market" }),
    ),
    live,
    checkPanel,
    exitSection,
    h("div", { class: "section-title row" }, "Confluences", h("span", { class: "spacer" }), confluenceHint),
    confluenceBox,
    h("div", { class: "section-title" }, "Psychology"),
    h("div", { class: "stack", style: { gap: "12px" } }, field("Emotional state", emotionBox), field("Rule compliance", compliance.el, { help: "Auto = followed plan if no guardrail was violated" }), field("Tags (comma separated)", tags)),
    h("div", { class: "section-title" }, "Notes & screenshots"),
    notes,
    h("div", { style: { marginTop: "12px" } }, shotsBox),
  );

  const ref = modal({
    title: isEdit ? `Edit trade #${t.id} · ${t.symbol}` : "New trade",
    body,
    wide: true,
    footer: [h("span", { class: "left muted small" }, isEdit ? "" : "Open positions dated today are checked against every guardrail."), h("button", { class: "btn ghost", type: "button", onclick: () => ref.close() }, "Cancel"), saveButton],
    onClose: () => queued.forEach((item) => item.preview.kind === "file" && URL.revokeObjectURL(item.preview.src)),
  });
  ref.dialog.addEventListener("paste", (event) => {
    const files = [...(event.clipboardData?.files || [])].filter((f) => f.type.startsWith("image/"));
    if (files.length) {
      event.preventDefault();
      addFiles(files);
    }
  });

  renderConfluences();
  renderEmotions();
  renderShots();
  showExit();
  check();
}
