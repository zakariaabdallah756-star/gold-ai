// Trade entry / edit form with live guardrail checks and screenshot handling.

import { api, ApiError } from "../api.js";
import { clear, debounce, field, h, icon, lightbox, modal, numberOrNull, segmented, select, toast, withNodes } from "../dom.js";
import { fmt, toInputDateTime } from "../format.js";
import { N_, t, tn } from "../i18n.js";
import { valueLabel } from "../labels.js";
import { accountNow, loadStatus, marketBySymbol, state, strategyByName } from "../state.js";
import { violationList } from "./calculator.js";
import { copilotPanel, splitCopilot } from "./copilot.js";

const num = (input) => numberOrNull(input);
const inputValue = (value) => (value === null || value === undefined ? "" : String(value));
const TRAIL_TRIGGER_LABELS = { tp1: N_("After TP1"), immediate: N_("Immediately") };

function screenshotTile(shot, onRemove) {
  const media =
    shot.kind === "file"
      ? h("img", { src: shot.src, alt: shot.caption || t("Chart screenshot"), loading: "lazy", onclick: () => lightbox(shot.src) })
      : h("a", { class: "link-shot", href: shot.src, target: "_blank", rel: "noopener noreferrer" }, icon("link"), shot.src.replace(/^https?:\/\//, "").slice(0, 60));
  return h(
    "div",
    { class: "shot" },
    media,
    h("div", { class: "caption", title: shot.caption }, shot.caption || (shot.kind === "file" ? t("Screenshot") : t("Chart link"))),
    onRemove ? h("button", { type: "button", class: "btn sm icon-only remove", "aria-label": t("Remove screenshot"), onclick: onRemove }, icon("trash")) : null,
  );
}

export function screenshotGallery(shots) {
  return shots.length ? h("div", { class: "shots" }, shots.map((shot) => screenshotTile(shot))) : h("p", { class: "muted small" }, t("No screenshots."));
}

/**
 * Open the trade form.
 * @param {object} options
 * @param {object|null} options.trade  existing trade to edit
 * @param {object} options.prefill      values for a new trade (symbol, direction, entry, sl, tp, size, source, signal_text)
 * @param {boolean} options.closing     preset the status to "closed" (close an unmanaged open trade)
 * @param {Function} options.onSaved    called with the saved trade
 */
export function openTradeForm({ trade = null, prefill = {}, closing = false, onSaved } = {}) {
  const { plan } = state;
  const isEdit = Boolean(trade);
  const rec = trade || {};
  // A managed trade's exits come from the position ledger (Positions page).
  const managed = Boolean(rec.managed);
  const confluences = new Set(rec.confluences || []);
  const mistakes = new Set(rec.mistakes || []);
  const targets = (rec.targets || []).map((tp) => ({ price: tp.price, close_pct: tp.close_pct, hit: tp.hit }));
  let emotion = rec.emotion || "";
  let shots = [...(rec.screenshots || [])];
  const queued = []; // screenshots added before a new trade is saved
  let riskTouched = false;

  // ---------------------------------------------------------------- controls
  const status = segmented(
    [
      ["open", t("Open")],
      ["closed", t("Closed")],
    ],
    closing && !managed ? "closed" : rec.status || "open",
    () => {
      if (status.value === "closed" && !exitTime.value) exitTime.value = toInputDateTime(accountNow());
      showExit();
      check();
    },
  );
  const direction = segmented(
    [
      ["long", t("Long"), "long"],
      ["short", t("Short"), "short"],
    ],
    rec.direction || prefill.direction || "long",
    () => check(),
  );
  const entryTime = h("input", { type: "datetime-local", value: rec.entry_time ? rec.entry_time.slice(0, 16) : toInputDateTime(accountNow()), oninput: () => check() });
  const symbolList = h("datalist", { id: "symbol-options" }, plan.markets.map((m) => h("option", { value: m.symbol }, m.name)));
  const symbol = h("input", {
    value: rec.symbol || prefill.symbol || plan.markets.find((m) => m.active)?.symbol || "",
    list: "symbol-options",
    autocomplete: "off",
    style: { textTransform: "uppercase" },
    oninput: () => (resetRisk(), check()),
  });
  const strategyNames = plan.strategies.filter((s) => s.active || s.name === rec.strategy).map((s) => s.name);
  // A name kept from before a rename (or a deleted strategy) stays selected, so editing never clears it.
  if (rec.strategy && !strategyNames.includes(rec.strategy)) strategyNames.push(rec.strategy);
  const strategy = select([["", "—"], ...strategyNames], rec.strategy || "", { onchange: () => (renderConfluences(), check()) });
  const timeframe = select([["", "—"], ...plan.enums.timeframes], rec.timeframe || "");
  const sessionNames = plan.sessions.map((s) => s.name);
  if (rec.session && !sessionNames.includes(rec.session)) sessionNames.push(rec.session);
  const session = select([["", t("Auto-detect")], ...sessionNames], rec.session || "", { onchange: () => check() });

  const numeric = (value, attrs = {}) => h("input", { type: "number", step: "any", inputmode: "decimal", value: inputValue(value), ...attrs });
  const entry = numeric(rec.entry_price ?? prefill.entry, { oninput: () => (resetRisk(), updateTargets(), check()) });
  const stop = numeric(rec.stop_loss ?? prefill.sl, { oninput: () => (resetRisk(), updateTargets(), check()) });
  const target = numeric(rec.take_profit ?? prefill.tp, { placeholder: t("optional"), oninput: () => check() });
  const targetHelp = h("div", { class: "help" });
  const size = numeric(rec.position_size ?? prefill.size, { placeholder: t("lots"), min: "0", oninput: () => (resetRisk(), check()) });
  const riskAmount = numeric(rec.risk_amount, {
    placeholder: t("auto"),
    min: "0",
    oninput: () => {
      riskTouched = true;
      check();
    },
  });
  const exitPrice = numeric(rec.exit_price);
  const exitTime = h("input", { type: "datetime-local", value: rec.exit_time ? rec.exit_time.slice(0, 16) : "" });
  const fees = numeric(rec.fees || "", { placeholder: "0.00" });
  const pnlIsAuto = rec.pnl !== null && rec.pnl !== undefined && !rec.pnl_manual;
  const pnl = numeric(rec.pnl_manual ? rec.pnl : "", {
    placeholder: pnlIsAuto ? t("auto ({amount})", { amount: fmt.signedMoney(rec.pnl) }) : t("auto from prices"),
  });
  exitPrice.addEventListener("input", () => check());

  const sizeButton = h("button", { type: "button", class: "btn sm", title: t("Size from your default risk %"), onclick: sizeFromRisk }, icon("calculator"), t("Size"));

  const compliance = segmented(
    [
      ["auto", t("Auto")],
      ["yes", t("Followed plan")],
      ["no", t("Broke rules")],
    ],
    rec.rule_compliant === true ? "yes" : rec.rule_compliant === false ? "no" : "auto",
  );
  const tags = h("input", { value: (rec.tags || []).join(", "), placeholder: t("a-plus-setup, news, late-entry") });
  const notes = h("textarea", { rows: 4, value: rec.notes || "", placeholder: t("Why did you take it? What did you see? What would you do differently?") });

  const beEnabled = h("input", { type: "checkbox", checked: isEdit ? rec.be_enabled : plan.settings.default_auto_be, onchange: () => syncManagement() });
  const beOffset = numeric(isEdit && rec.be_offset_pips ? rec.be_offset_pips : "", { placeholder: "0", min: "0", oninput: () => check() });
  const trailEnabled = h("input", { type: "checkbox", checked: isEdit ? rec.trail_enabled : plan.settings.default_trail_pips > 0, onchange: () => syncManagement() });
  const trailDistance = numeric(isEdit ? rec.trail_distance_pips : plan.settings.default_trail_pips || "", { placeholder: t("pips"), min: "0", oninput: () => check() });
  const trailTrigger = select(
    plan.enums.trail_triggers.map((value) => [value, TRAIL_TRIGGER_LABELS[value] ? t(TRAIL_TRIGGER_LABELS[value]) : value]),
    isEdit ? rec.trail_trigger : "tp1",
    { onchange: () => check() },
  );
  const targetRows = h("div", { class: "tf-targets" });
  const targetSum = h("span", { class: "tf-sum" });
  const addTargetButton = h("button", { type: "button", class: "btn sm", onclick: () => addTarget() }, icon("plus"), t("Add target"));

  const confluenceBox = h("div", { class: "chips" });
  const mistakeBox = h("div", { class: "chips" });
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
    confluenceHint.textContent = min
      ? t("{n} / {min} required by {strategy}", { n: confluences.size, min, strategy: strategy.value })
      : t("{n} selected", { n: confluences.size });
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
          valueLabel(name),
        ),
      ),
    );
  }

  function renderMistakes() {
    const names = plan.mistake_types.filter((m) => m.active).map((m) => m.name);
    for (const name of mistakes) if (!names.includes(name)) names.push(name);
    if (!names.length) {
      clear(mistakeBox, h("span", { class: "muted small" }, t("No mistake types yet - add them in the Trading Plan.")));
      return;
    }
    clear(
      mistakeBox,
      names.map((name) =>
        h(
          "button",
          {
            type: "button",
            class: `chip-toggle${mistakes.has(name) ? " on" : ""}`,
            "aria-pressed": String(mistakes.has(name)),
            onclick: () => {
              if (mistakes.has(name)) mistakes.delete(name);
              else mistakes.add(name);
              renderMistakes();
            },
          },
          name,
        ),
      ),
    );
  }

  // ------------------------------------------------------- targets & management
  let targetInfo = []; // per-row R:R cells, refreshed without re-rendering the inputs

  /** Reset close % to the default split for the row count (hit targets keep theirs). */
  function applySplit() {
    if (targets.some((tp) => tp.hit)) return;
    const split = plan.enums.tp_splits[String(targets.length)] || [];
    targets.forEach((tp, index) => (tp.close_pct = split[index] ?? null));
  }

  function addTarget() {
    if (targets.length >= plan.enums.max_targets) return;
    targets.push({ price: targets.length ? null : num(target), close_pct: null, hit: false });
    applySplit();
    renderTargets();
    check();
  }

  function removeTarget(index) {
    targets.splice(index, 1);
    applySplit();
    renderTargets();
    check();
  }

  function renderTargets() {
    const head = targets.length
      ? h("div", { class: "tf-tp-row head" }, h("span", {}, ""), h("span", {}, t("Price")), h("span", {}, t("Close %")), h("span", { class: "tf-tp-rr" }, t("R:R")), h("span", {}, ""))
      : null;
    targetInfo = [];
    const rows = targets.map((tp, index) => {
      const rr = h("span", { class: "tf-tp-rr" });
      targetInfo.push(rr);
      const price = numeric(tp.price, {
        placeholder: t("price"),
        disabled: tp.hit,
        "aria-label": t("TP{n} price", { n: index + 1 }),
        oninput: (e) => {
          tp.price = numberOrNull(e.target);
          updateTargets();
          check();
        },
      });
      const pct = numeric(tp.close_pct, {
        min: "0",
        max: "100",
        disabled: tp.hit,
        "aria-label": t("TP{n} close percent", { n: index + 1 }),
        oninput: (e) => {
          tp.close_pct = numberOrNull(e.target);
          updateTargets();
          check();
        },
      });
      return h(
        "div",
        { class: "tf-tp-row" },
        h("span", { class: "tf-tp-label" }, `TP${index + 1}`, tp.hit ? icon("check") : null),
        price,
        h("div", { class: "tf-pct" }, pct, h("span", {}, "%")),
        rr,
        tp.hit
          ? h("span", { class: "badge good", title: t("Hit targets are locked") }, t("Hit"))
          : h("button", { type: "button", class: "btn sm icon-only ghost", "aria-label": t("Remove TP{n}", { n: index + 1 }), onclick: () => removeTarget(index) }, icon("trash")),
      );
    });
    clear(targetRows, head, rows);
    addTargetButton.disabled = targets.length >= plan.enums.max_targets;
    updateTargets();
  }

  /** Refresh R:R per row, the close % total and the mirrored take profit. */
  function updateTargets() {
    const entryPrice = num(entry);
    const stopPrice = num(stop);
    const risk = entryPrice !== null && stopPrice !== null ? Math.abs(entryPrice - stopPrice) : 0;
    targets.forEach((tp, index) => {
      targetInfo[index].textContent = risk && tp.price !== null ? fmt.rr(Math.abs(tp.price - entryPrice) / risk) : "—";
    });

    if (targets.length) {
      const total = targets.reduce((sum, tp) => sum + (tp.close_pct || 0), 0);
      const ok = Math.abs(total - 100) <= 0.5;
      targetSum.className = `tf-sum ${ok ? "ok" : "warn"}`;
      clear(targetSum, icon(ok ? "check" : "alert"), ok ? t("Total 100%") : t("Total {pct}% - must add up to 100%", { pct: fmt.num(total, 0) }));
      const last = targets.at(-1).price;
      target.readOnly = true;
      target.value = inputValue(last);
      targetHelp.textContent = t("Mirrors TP{n} (the last target)", { n: targets.length });
    } else {
      clear(targetSum);
      target.readOnly = false;
      targetHelp.textContent = t("Or split it into several targets below");
    }
  }

  function targetProblem() {
    const missing = targets.findIndex((tp) => tp.price === null || tp.close_pct === null);
    return missing === -1 ? null : t("Enter a price and close % for TP{n}, or remove it.", { n: missing + 1 });
  }

  function syncManagement() {
    beOffset.disabled = !beEnabled.checked;
    trailDistance.disabled = !trailEnabled.checked;
    trailTrigger.disabled = !trailEnabled.checked;
    check();
  }

  function showExit() {
    exitSection.style.display = managed || status.value === "closed" ? "" : "none";
    saveButton.textContent = isEdit ? t("Save changes") : status.value === "open" ? t("Open trade") : t("Log trade");
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
      " ",
      withNodes(t("Drop chart images here, paste from clipboard (Ctrl+V), or {link}"), {
        link: h("a", { href: "#", onclick: (e) => (e.preventDefault(), fileInput.click()) }, t("browse")),
      }),
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
    const captionInput = h("input", { placeholder: t("Caption (optional)"), maxlength: "200" });
    const addUrl = h("button", { type: "button", class: "btn", onclick: () => addLink(urlInput.value.trim(), captionInput.value.trim()) }, icon("link"), t("Add link"));
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
    if (images.length < files.length) toast(t("Only PNG, JPEG, GIF and WebP images are supported"), "warn");
    for (const file of images) {
      if (isEdit) {
        try {
          shots.push(await uploadFile(rec.id, file));
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
      toast(t("Enter a link starting with http:// or https://"), "warn");
      return;
    }
    if (isEdit) {
      try {
        shots.push(await api.post(`/api/trades/${rec.id}/screenshots/url`, { url, caption }));
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
      toast(t("Pick a configured market and enter entry + stop first"), "warn");
      return;
    }
    try {
      const r = await api.post("/api/risk/position-size", { market_id: market.id, entry_price: num(entry), stop_price: num(stop) });
      size.value = String(r.lots);
      resetRisk();
      check();
      toast(t("{lots} lots risks {risk} ({pct})", { lots: r.lots, risk: fmt.money(r.max_loss), pct: fmt.pct(r.actual_risk_pct, 2) }), "success");
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function payload() {
    const closed = status.value === "closed";
    return {
      status: status.value,
      entry_time: entryTime.value,
      // A managed trade keeps its stored exit (the server derives it from the ledger).
      exit_time: managed ? rec.exit_time : closed && exitTime.value ? exitTime.value : null,
      symbol: symbol.value.trim().toUpperCase(),
      direction: direction.value,
      entry_price: num(entry),
      stop_loss: num(stop),
      take_profit: num(target),
      exit_price: managed ? rec.exit_price : closed ? num(exitPrice) : null,
      position_size: num(size),
      risk_amount: num(riskAmount),
      fees: num(fees) ?? 0,
      pnl: closed && !managed ? num(pnl) : null,
      strategy: strategy.value,
      timeframe: timeframe.value,
      session: session.value,
      confluences: [...confluences],
      emotion,
      rule_compliant: compliance.value === "auto" ? null : compliance.value === "yes",
      tags: tags.value.split(",").map((s) => s.trim()).filter(Boolean),
      mistakes: [...mistakes],
      notes: notes.value,
      source: isEdit ? rec.source : prefill.source || "manual",
      signal_text: isEdit ? rec.signal_text : prefill.signal_text || "",
      targets: targets.map((tp) => ({ price: tp.price, close_pct: tp.close_pct })),
      be_enabled: beEnabled.checked,
      be_offset_pips: num(beOffset) ?? 0,
      trail_enabled: trailEnabled.checked,
      trail_distance_pips: num(trailDistance) || null,
      trail_trigger: trailTrigger.value,
    };
  }

  /** Strategy Co-Pilot first, then the remaining guardrail findings. */
  function guardrailFindings(violations, okText) {
    const { copilot, other } = splitCopilot(violations);
    return h(
      "div",
      { class: "tf-guard" },
      copilotPanel(violations, strategy.value),
      violationList(other, { okText: okText && (copilot.length ? t("All other guardrails pass.") : okText) }),
    );
  }

  function metric(label, value, cls = "") {
    return h("div", {}, h("div", { class: "label" }, label), h("div", { class: `value ${cls}` }, value));
  }

  let checkId = 0;
  const check = debounce(async () => {
    const data = payload();
    if (!data.entry_time || !data.symbol || data.entry_price === null || data.stop_loss === null) {
      clear(live, metric(t("Planned R:R"), "—"), metric(t("Risk"), "—"), metric(t("Session"), "—"));
      clear(checkPanel);
      return;
    }
    const id = ++checkId;
    const problem = targetProblem();
    if (problem) {
      clear(checkPanel, h("div", { class: "violation warn" }, icon("alert"), h("div", {}, problem)));
      return;
    }
    try {
      const r = await api.post(`/api/trades/check${isEdit ? `?trade_id=${rec.id}` : ""}`, { ...data, status: data.status === "closed" && data.exit_price === null ? "open" : data.status });
      if (id !== checkId) return;
      clear(
        live,
        metric(t("Planned R:R"), fmt.rr(r.planned_rr)),
        metric(t("Risk"), r.risk_amount === null ? "—" : fmt.money(r.risk_amount)),
        metric(t("Risk % of equity"), fmt.pct(r.risk_pct, 2)),
        metric(t("Session"), r.session || t("Off-session")),
      );
      clear(
        checkPanel,
        r.blocked ? h("p", { class: "small neg", style: { marginBottom: "8px" } }, t("This entry is blocked by your risk rules.")) : null,
        guardrailFindings(r.violations, t("All guardrails pass.")),
      );
    } catch (error) {
      if (id !== checkId) return;
      clear(checkPanel, h("div", { class: "violation warn" }, icon("alert"), h("div", {}, error.message)));
    }
  }, 350);

  // ------------------------------------------------------------------- save
  const saveButton = h("button", { class: "btn primary", type: "button", onclick: save });

  async function save() {
    const problem = targetProblem();
    if (problem) {
      toast(problem, "warn");
      return;
    }
    saveButton.disabled = true;
    try {
      const data = payload();
      const result = isEdit ? await api.put(`/api/trades/${rec.id}`, data) : await api.post("/api/trades", data);
      let saved = result.trade;
      for (const item of queued) {
        try {
          if (item.file) await uploadFile(saved.id, item.file);
          else await api.post(`/api/trades/${saved.id}/screenshots/url`, { url: item.url, caption: item.caption });
        } catch (error) {
          toast(t("Screenshot not saved: {error}", { error: error.message }), "error");
        }
        if (item.preview.kind === "file") URL.revokeObjectURL(item.preview.src);
      }
      if (queued.length) saved = await api.get(`/api/trades/${saved.id}`);
      const warnings = result.warnings.length;
      let message;
      if (isEdit) message = warnings ? tn(warnings, "Trade updated with {n} rule warning", "Trade updated with {n} rule warnings") : t("Trade updated");
      else message = warnings ? tn(warnings, "Trade logged with {n} rule warning", "Trade logged with {n} rule warnings") : t("Trade logged");
      toast(message, warnings ? "warn" : "success");
      ref.close();
      loadStatus().catch(() => {});
      onSaved?.(saved);
    } catch (error) {
      if (error instanceof ApiError && error.status === 423) {
        clear(checkPanel, h("p", { class: "small neg", style: { marginBottom: "8px" } }, t("Entry refused - fix these first:")), guardrailFindings(error.payload.violations || []));
        checkPanel.scrollIntoView({ behavior: "smooth", block: "center" });
        toast(t("Trade blocked by your risk rules"), "error");
      } else {
        toast(error.message, "error");
      }
    } finally {
      saveButton.disabled = false;
    }
  }

  // ----------------------------------------------------------------- layout
  const feesField = field(t("Fees / swap ({currency})", { currency: plan.settings.currency }), fees, { help: t("Costs, deducted from P&L") });
  if (managed) {
    clear(
      exitSection,
      h("div", { class: "section-title" }, t("Exit")),
      h(
        "div",
        { class: "tf-managed-note" },
        icon("layers"),
        h(
          "div",
          {},
          h("strong", {}, t("Exits are recorded by the position manager - see"), " "),
          h("a", { href: "#/positions", onclick: () => ref.close() }, t("Positions")),
          h("p", { class: "muted small" }, t("Target fills, partial closes and stop-outs come from the position's event ledger; status, exit price and P&L are derived from it.")),
        ),
      ),
      h("div", { class: "form-grid" }, feesField),
    );
  } else {
    clear(
      exitSection,
      h("div", { class: "section-title" }, t("Exit")),
      h(
        "div",
        { class: "form-grid" },
        field(t("Exit price"), exitPrice),
        field(t("Exit time"), exitTime),
        feesField,
        field(t("Net P&L ({currency})", { currency: plan.settings.currency }), pnl, { help: t("Leave blank to calculate from prices") }),
      ),
    );
  }

  const managementSection = [
    h("div", { class: "section-title row" }, t("Targets & management"), h("span", { class: "spacer" }), targetSum),
    targetRows,
    h(
      "div",
      { class: "tf-targets-foot", style: { marginTop: "8px" } },
      addTargetButton,
      h("span", { class: "muted small" }, t("Up to {max} targets. Close % is the share of the original size closed at each target.", { max: plan.enums.max_targets })),
    ),
    h(
      "div",
      { class: "tf-manage" },
      h(
        "div",
        { class: "tf-toggle" },
        h("label", { class: "check" }, beEnabled, t("Move stop to break-even at TP1")),
        h("div", { class: "tf-toggle-fields" }, field(t("Offset (pips)"), beOffset, { help: t("Lock in pips beyond entry") })),
      ),
      h(
        "div",
        { class: "tf-toggle" },
        h("label", { class: "check" }, trailEnabled, t("Trailing stop")),
        h("div", { class: "tf-toggle-fields" }, field(t("Distance (pips)"), trailDistance), field(t("Starts"), trailTrigger)),
      ),
    ),
  ];

  const body = h(
    "div",
    {},
    symbolList,
    h(
      "div",
      { class: "row", style: { justifyContent: "space-between", marginBottom: "14px" } },
      managed
        ? h("span", { class: "badge open pm-badge" }, icon("layers"), rec.status === "open" ? t("Managed position · open") : t("Managed position · closed"))
        : status.el,
      direction.el,
    ),
    h("div", { class: "section-title" }, t("Setup")),
    h(
      "div",
      { class: "form-grid" },
      field(t("Entry time"), entryTime),
      field(t("Asset"), symbol),
      field(t("Strategy"), strategy),
      field(t("Timeframe"), timeframe),
      field(t("Session"), session),
    ),
    h("div", { class: "section-title" }, t("Prices & size")),
    h(
      "div",
      { class: "form-grid" },
      field(t("Entry price"), entry),
      field(t("Stop loss (initial)"), stop),
      h("div", { class: "field" }, h("label", {}, t("Take profit")), target, targetHelp),
      field(t("Position size (lots)"), h("div", { class: "row", style: { flexWrap: "nowrap", gap: "6px" } }, size, sizeButton)),
      field(t("Risk amount ({currency})", { currency: plan.settings.currency }), riskAmount, { help: t("Auto from size & market") }),
    ),
    managementSection,
    live,
    checkPanel,
    exitSection,
    h("div", { class: "section-title row" }, t("Confluences"), h("span", { class: "spacer" }), confluenceHint),
    confluenceBox,
    h("div", { class: "section-title" }, t("Psychology")),
    h(
      "div",
      { class: "stack", style: { gap: "12px" } },
      field(t("Emotional state"), emotionBox),
      field(t("Mistakes"), mistakeBox, { help: t("Execution or psychology mistakes on this trade") }),
      field(t("Rule compliance"), compliance.el, { help: t("Auto = followed plan if no guardrail was violated") }),
      field(t("Tags (comma separated)"), tags),
    ),
    h("div", { class: "section-title" }, t("Notes & screenshots")),
    notes,
    h("div", { style: { marginTop: "12px" } }, shotsBox),
  );

  const ref = modal({
    title: isEdit ? t("Edit trade #{id} · {symbol}", { id: rec.id, symbol: rec.symbol }) : t("New trade"),
    body,
    wide: true,
    footer: [
      h("span", { class: "left muted small" }, isEdit ? "" : t("Open positions dated today are checked against every guardrail.")),
      h("button", { class: "btn ghost", type: "button", onclick: () => ref.close() }, t("Cancel")),
      saveButton,
    ],
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
  renderMistakes();
  renderTargets();
  renderShots();
  showExit();
  syncManagement();
}
