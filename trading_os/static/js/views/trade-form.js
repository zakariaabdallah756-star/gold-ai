// Trade entry / edit form with live guardrail checks and screenshot handling.

import { api, ApiError } from "../api.js";
import { clear, debounce, field, h, icon, lightbox, modal, numberOrNull, segmented, select, toast } from "../dom.js";
import { fmt, toInputDateTime } from "../format.js";
import { accountNow, loadStatus, marketBySymbol, state, strategyByName } from "../state.js";
import { violationList } from "./calculator.js";
import { copilotPanel, splitCopilot } from "./copilot.js";

const num = (input) => numberOrNull(input);
const inputValue = (value) => (value === null || value === undefined ? "" : String(value));
const TRAIL_TRIGGER_LABELS = { tp1: "After TP1", immediate: "Immediately" };

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
 * @param {object} options.prefill      values for a new trade (symbol, direction, entry, sl, tp, size, source, signal_text)
 * @param {boolean} options.closing     preset the status to "closed" (close an unmanaged open trade)
 * @param {Function} options.onSaved    called with the saved trade
 */
export function openTradeForm({ trade = null, prefill = {}, closing = false, onSaved } = {}) {
  const { plan } = state;
  const isEdit = Boolean(trade);
  const t = trade || {};
  // A managed trade's exits come from the position ledger (Positions page).
  const managed = Boolean(t.managed);
  const confluences = new Set(t.confluences || []);
  const mistakes = new Set(t.mistakes || []);
  const targets = (t.targets || []).map((tp) => ({ price: tp.price, close_pct: tp.close_pct, hit: tp.hit }));
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
    closing && !managed ? "closed" : t.status || "open",
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
  const entry = numeric(t.entry_price ?? prefill.entry, { oninput: () => (resetRisk(), updateTargets(), check()) });
  const stop = numeric(t.stop_loss ?? prefill.sl, { oninput: () => (resetRisk(), updateTargets(), check()) });
  const target = numeric(t.take_profit ?? prefill.tp, { placeholder: "optional", oninput: () => check() });
  const targetHelp = h("div", { class: "help" });
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

  const beEnabled = h("input", { type: "checkbox", checked: isEdit ? t.be_enabled : plan.settings.default_auto_be, onchange: () => syncManagement() });
  const beOffset = numeric(isEdit && t.be_offset_pips ? t.be_offset_pips : "", { placeholder: "0", min: "0", oninput: () => check() });
  const trailEnabled = h("input", { type: "checkbox", checked: isEdit ? t.trail_enabled : plan.settings.default_trail_pips > 0, onchange: () => syncManagement() });
  const trailDistance = numeric(isEdit ? t.trail_distance_pips : plan.settings.default_trail_pips || "", { placeholder: "pips", min: "0", oninput: () => check() });
  const trailTrigger = select(
    plan.enums.trail_triggers.map((value) => [value, TRAIL_TRIGGER_LABELS[value] || value]),
    isEdit ? t.trail_trigger : "tp1",
    { onchange: () => check() },
  );
  const targetRows = h("div", { class: "tf-targets" });
  const targetSum = h("span", { class: "tf-sum" });
  const addTargetButton = h("button", { type: "button", class: "btn sm", onclick: () => addTarget() }, icon("plus"), "Add target");

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

  function renderMistakes() {
    const names = plan.mistake_types.filter((m) => m.active).map((m) => m.name);
    for (const name of mistakes) if (!names.includes(name)) names.push(name);
    if (!names.length) {
      clear(mistakeBox, h("span", { class: "muted small" }, "No mistake types yet - add them in the Trading Plan."));
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
      ? h("div", { class: "tf-tp-row head" }, h("span", {}, ""), h("span", {}, "Price"), h("span", {}, "Close %"), h("span", { class: "tf-tp-rr" }, "R:R"), h("span", {}, ""))
      : null;
    targetInfo = [];
    const rows = targets.map((tp, index) => {
      const rr = h("span", { class: "tf-tp-rr" });
      targetInfo.push(rr);
      const price = numeric(tp.price, {
        placeholder: "price",
        disabled: tp.hit,
        "aria-label": `TP${index + 1} price`,
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
        "aria-label": `TP${index + 1} close percent`,
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
          ? h("span", { class: "badge good", title: "Hit targets are locked" }, "Hit")
          : h("button", { type: "button", class: "btn sm icon-only ghost", "aria-label": `Remove TP${index + 1}`, onclick: () => removeTarget(index) }, icon("trash")),
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
      clear(targetSum, icon(ok ? "check" : "alert"), ok ? "Total 100%" : `Total ${fmt.num(total, 0)}% - must add up to 100%`);
      const last = targets.at(-1).price;
      target.readOnly = true;
      target.value = inputValue(last);
      targetHelp.textContent = `Mirrors TP${targets.length} (the last target)`;
    } else {
      clear(targetSum);
      target.readOnly = false;
      targetHelp.textContent = "Or split it into several targets below";
    }
  }

  function targetProblem() {
    const missing = targets.findIndex((tp) => tp.price === null || tp.close_pct === null);
    return missing === -1 ? null : `Enter a price and close % for TP${missing + 1}, or remove it.`;
  }

  function syncManagement() {
    beOffset.disabled = !beEnabled.checked;
    trailDistance.disabled = !trailEnabled.checked;
    trailTrigger.disabled = !trailEnabled.checked;
    check();
  }

  function showExit() {
    exitSection.style.display = managed || status.value === "closed" ? "" : "none";
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
      // A managed trade keeps its stored exit (the server derives it from the ledger).
      exit_time: managed ? t.exit_time : closed && exitTime.value ? exitTime.value : null,
      symbol: symbol.value.trim().toUpperCase(),
      direction: direction.value,
      entry_price: num(entry),
      stop_loss: num(stop),
      take_profit: num(target),
      exit_price: managed ? t.exit_price : closed ? num(exitPrice) : null,
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
      source: isEdit ? t.source : prefill.source || "manual",
      signal_text: isEdit ? t.signal_text : prefill.signal_text || "",
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
      violationList(other, { okText: okText && (copilot.length ? "All other guardrails pass." : okText) }),
    );
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
    const problem = targetProblem();
    if (problem) {
      clear(checkPanel, h("div", { class: "violation warn" }, icon("alert"), h("div", {}, problem)));
      return;
    }
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
        guardrailFindings(r.violations, "All guardrails pass."),
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
        clear(checkPanel, h("p", { class: "small neg", style: { marginBottom: "8px" } }, "Entry refused - fix these first:"), guardrailFindings(error.payload.violations || []));
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
  const feesField = field(`Fees / swap (${plan.settings.currency})`, fees, { help: "Costs, deducted from P&L" });
  if (managed) {
    clear(
      exitSection,
      h("div", { class: "section-title" }, "Exit"),
      h(
        "div",
        { class: "tf-managed-note" },
        icon("layers"),
        h(
          "div",
          {},
          h("strong", {}, "Exits are recorded by the position manager - see "),
          h("a", { href: "#/positions", onclick: () => ref.close() }, "Positions"),
          h("p", { class: "muted small" }, "Target fills, partial closes and stop-outs come from the position's event ledger; status, exit price and P&L are derived from it."),
        ),
      ),
      h("div", { class: "form-grid" }, feesField),
    );
  } else {
    clear(
      exitSection,
      h("div", { class: "section-title" }, "Exit"),
      h(
        "div",
        { class: "form-grid" },
        field("Exit price", exitPrice),
        field("Exit time", exitTime),
        feesField,
        field(`Net P&L (${plan.settings.currency})`, pnl, { help: "Leave blank to calculate from prices" }),
      ),
    );
  }

  const managementSection = [
    h("div", { class: "section-title row" }, "Targets & management", h("span", { class: "spacer" }), targetSum),
    targetRows,
    h(
      "div",
      { class: "tf-targets-foot", style: { marginTop: "8px" } },
      addTargetButton,
      h("span", { class: "muted small" }, `Up to ${plan.enums.max_targets} targets. Close % is the share of the original size closed at each target.`),
    ),
    h(
      "div",
      { class: "tf-manage" },
      h(
        "div",
        { class: "tf-toggle" },
        h("label", { class: "check" }, beEnabled, "Move stop to break-even at TP1"),
        h("div", { class: "tf-toggle-fields" }, field("Offset (pips)", beOffset, { help: "Lock in pips beyond entry" })),
      ),
      h(
        "div",
        { class: "tf-toggle" },
        h("label", { class: "check" }, trailEnabled, "Trailing stop"),
        h("div", { class: "tf-toggle-fields" }, field("Distance (pips)", trailDistance), field("Starts", trailTrigger)),
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
      managed ? h("span", { class: "badge open pm-badge" }, icon("layers"), `Managed position · ${t.status === "open" ? "open" : "closed"}`) : status.el,
      direction.el,
    ),
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
      h("div", { class: "field" }, h("label", {}, "Take profit"), target, targetHelp),
      field("Position size (lots)", h("div", { class: "row", style: { flexWrap: "nowrap", gap: "6px" } }, size, sizeButton)),
      field(`Risk amount (${plan.settings.currency})`, riskAmount, { help: "Auto from size & market" }),
    ),
    managementSection,
    live,
    checkPanel,
    exitSection,
    h("div", { class: "section-title row" }, "Confluences", h("span", { class: "spacer" }), confluenceHint),
    confluenceBox,
    h("div", { class: "section-title" }, "Psychology"),
    h(
      "div",
      { class: "stack", style: { gap: "12px" } },
      field("Emotional state", emotionBox),
      field("Mistakes", mistakeBox, { help: "Execution or psychology mistakes on this trade" }),
      field("Rule compliance", compliance.el, { help: "Auto = followed plan if no guardrail was violated" }),
      field("Tags (comma separated)", tags),
    ),
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
  renderMistakes();
  renderTargets();
  renderShots();
  showExit();
  syncManagement();
}
