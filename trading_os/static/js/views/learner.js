// AI Strategy Learner (v1.6), shown as a tab of the Strategy incubator.
//
// Left: the conversation. The first message is the source (the trader's rules in plain text, or a pasted video
// transcript / summary); later messages refine the draft or add another source. Right: the draft itself,
// every field editable (saved on its own a moment after a change), with what is still missing, warnings and the
// button that writes it into the incubator as a strategy ready for backtest trades.
//
// Claude answers when it is set up (Trading plan -> AI assistant); otherwise a basic keyword extraction runs on
// this computer. Nothing here places trades or changes the trading plan's risk rules.

import { api } from "../api.js";
import { clear, confirmDialog, field, h, icon, segmented, toast } from "../dom.js";
import { fmt } from "../format.js";
import { N_, t, tn } from "../i18n.js";
import { loadPlan, state } from "../state.js";

const SAVE_IDLE_MS = 700;
const CLEAN_IDLE_MS = 500;
const KIND_LABEL = { rules: N_("Rules"), transcript: N_("Transcript"), chat: N_("Message") };
const ENGINE_LABEL = { ai: N_("Claude"), basic: N_("Basic extraction") };
const DIRECTIONS = [
  ["both", N_("Long and short")],
  ["long", N_("Long only")],
  ["short", N_("Short only")],
];
const EXAMPLE = N_(
  "I trade gold on the 15 minute chart in the London session. The H4 trend must be up. I wait for a sweep of the Asian low, then a break of structure on M5, and enter on the retest of the order block. Stop below the swept low, take profit at 1:3, close half at 1R. Risk 0.5% per trade, max 2 trades a day, no trading before red news.",
);

/** "one\ntwo\n\nthree " -> ["one", "two", "three"] */
export function lines(text) {
  return String(text || "")
    .split("\n")
    .map((line) => line.replace(/^\s*[-•*]\s*/, "").trim())
    .filter(Boolean);
}

function engineBadge(engine, model) {
  const label = engine === "ai" ? (model ? `${t(ENGINE_LABEL.ai)} · ${model}` : t(ENGINE_LABEL.ai)) : t(ENGINE_LABEL.basic);
  return h("span", { class: `badge ${engine === "ai" ? "open" : ""}` }, icon(engine === "ai" ? "sparkles" : "list"), label);
}

/**
 * Render the Learner into ``root``. ``sessionId`` reopens a session; ``onSession(id)`` is told which one is open
 * (for the address bar); ``onOpenStrategy(id)`` shows a created strategy in the incubator.
 */
export async function mountLearner(root, { sessionId = null, lessonId = null, onSession = () => {}, onOpenStrategy = () => {} } = {}) {
  let overview = await api.get("/api/learner");
  let session = null;
  let kind = "rules";
  let engine = "auto";
  let busy = false;
  let alive = true;
  let saveTimer = null;
  let cleanTimer = null;
  let dirtyDraft = null; // edits waiting to be saved
  let addConfluences = true;
  let saving = Promise.resolve();
  let composerText = ""; // kept when the message kind is switched or a request fails
  let lesson = null; // the Academy video whose transcript the trader is about to paste

  const head = h("div", { class: "card ln-head" });
  const chat = h("div", { class: "card ln-chat" });
  const draftCard = h("div", { class: "card ln-draft" });
  clear(root, head, h("div", { class: "ln-layout" }, chat, draftCard));

  const aiReady = () => overview.engine.ai_available;

  // ------------------------------------------------------------------------------- head

  function drawHead() {
    const sessions = overview.sessions;
    const picker = h(
      "select",
      { "aria-label": t("Open a session"), onchange: (event) => openSession(Number(event.target.value) || null) },
      h("option", { value: "" }, t("New session")),
      sessions.map((s) => h("option", { value: String(s.id), selected: session?.id === s.id }, `${s.title}${s.strategy ? " ✓" : ""}`)),
    );
    clear(
      head,
      h(
        "div",
        { class: "ln-head-main" },
        h("div", { class: "ln-head-icon", "aria-hidden": "true" }, icon("sparkles")),
        h(
          "div",
          {},
          h("h2", {}, t("AI Strategy Learner")),
          h("p", { class: "muted" }, t("Describe a strategy in your own words (English or Italian) or paste a video transcript: the Learner extracts confluences, entry and exit rules and risk parameters into a draft ready for backtesting.")),
        ),
      ),
      h(
        "div",
        { class: "ln-head-side" },
        aiReady()
          ? h("span", { class: "badge open" }, icon("sparkles"), t("Claude ready · {model}", { model: overview.engine.model }))
          : h("a", { class: "badge warn", href: "#/plan?tab=ai", title: t("Set up Claude in Trading plan -> AI assistant") }, icon("info"), t("Basic extraction · set up Claude")),
        h("div", { class: "row" }, picker, session ? h("button", { class: "btn ghost sm icon-only", type: "button", "aria-label": t("Delete this session"), title: t("Delete this session"), onclick: deleteSession }, icon("trash")) : null),
      ),
    );
  }

  // ------------------------------------------------------------------------------- chat

  function bubble(message) {
    const mine = message.role === "user";
    const long = message.kind === "transcript" || (message.text || "").length > 600;
    const body = h("div", { class: "ln-msg-text" }, message.text);
    if (long) body.classList.add("clamped");
    return h(
      "div",
      { class: `ln-msg ${mine ? "mine" : "theirs"}` },
      h(
        "div",
        { class: "ln-msg-meta" },
        mine ? h("span", { class: "badge" }, icon(message.kind === "transcript" ? "fileText" : "message"), t(KIND_LABEL[message.kind] || KIND_LABEL.chat)) : engineBadge(message.engine, message.model),
        message.stats?.words ? h("span", { class: "muted small" }, tn(message.stats.words, "{n} word", "{n} words")) : null,
        message.stats?.timestamps ? h("span", { class: "muted small" }, t("{n} timestamps removed", { n: message.stats.timestamps })) : null,
        message.at ? h("span", { class: "muted small" }, fmt.time(message.at)) : null,
      ),
      body,
      long ? h("button", { type: "button", class: "btn ghost sm ln-more", onclick: (event) => (body.classList.toggle("clamped"), (event.currentTarget.textContent = body.classList.contains("clamped") ? t("Show all") : t("Show less"))) }, t("Show all")) : null,
    );
  }

  function drawChat({ error } = {}) {
    const first = !session;
    const limit = kind === "transcript" ? overview.limits.transcript_raw : kind === "rules" ? overview.limits.rules : overview.limits.chat;
    const input = h("textarea", {
      class: "ln-input",
      rows: kind === "transcript" ? 9 : 4,
      maxlength: String(limit),
      placeholder:
        kind === "transcript"
          ? t("Paste the transcript of a YouTube video (… → Show transcript) or a summary of it. Timestamps and [Music] tags are removed for you.")
          : kind === "rules"
            ? t("Describe the strategy: markets, timeframe, setup, entry trigger, stop, targets, risk…")
            : t("Refine the draft: “only London”, “the stop goes below the wick”, “risk 0.5%”…"),
      "aria-label": t("Message to the Learner"),
    });
    const info = h("div", { class: "ln-input-info muted small", "aria-live": "polite" });
    const kinds = segmented(
      (first ? ["rules", "transcript"] : ["chat", "rules", "transcript"]).map((key) => [key, key === "chat" ? t("Refine") : key === "rules" ? t("Describe rules") : t("Paste transcript")]),
      kind,
      (value) => {
        kind = value;
        drawChat();
      },
    );
    const engines = h(
      "select",
      { class: "ln-engine", "aria-label": t("Engine"), onchange: (event) => (engine = event.target.value) },
      [
        ["auto", aiReady() ? t("Claude") : t("Basic extraction")],
        ["ai", t("Claude")],
        ["basic", t("Basic extraction")],
      ]
        .filter(([key]) => key !== "ai" || aiReady())
        .map(([key, label]) => h("option", { value: key, selected: engine === key }, label)),
    );
    const send = h("button", { class: "btn primary", type: "button", disabled: busy, onclick: () => submit(input.value) }, busy ? t("Reading…") : [icon("send"), first ? t("Build the draft") : t("Send")]);
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) submit(input.value);
    });
    input.value = composerText;
    input.addEventListener("input", () => {
      composerText = input.value;
      const n = input.value.length;
      info.textContent = n > limit * 0.8 ? `${n} / ${limit}` : "";
      if (kind === "transcript") {
        clearTimeout(cleanTimer);
        cleanTimer = setTimeout(() => previewClean(input.value, info), CLEAN_IDLE_MS);
      }
    });
    const messages = session?.messages || [];
    clear(
      chat,
      h("div", { class: "card-head" }, h("div", {}, h("h2", {}, icon("message"), t("Conversation")), h("p", {}, first ? t("Start with your rules or a transcript") : tn(messages.length, "{n} message", "{n} messages")))),
      messages.length
        ? h("div", { class: "ln-messages" }, messages.map(bubble))
        : h(
            "div",
            { class: "ln-start" },
            h("p", { class: "muted" }, t("Tip: the more precise the text, the better the draft. Name the market, timeframe, entry trigger, stop, targets and risk.")),
            h("button", { class: "btn ghost sm", type: "button", onclick: () => ((composerText = input.value = t(EXAMPLE)), input.focus()) }, icon("edit"), t("Use an example")),
          ),
      lesson && first
        ? h(
            "div",
            { class: "ln-lesson" },
            icon("video"),
            h(
              "div",
              {},
              h("strong", {}, t("From the Academy: {title}", { title: lesson.title })),
              h("p", { class: "muted small" }, t("On YouTube, open the description and choose “Show transcript”, copy it and paste it below (or paste your own summary of the video).")),
            ),
            h("a", { class: "btn sm", href: lesson.watch_url, target: "_blank", rel: "noopener noreferrer" }, icon("arrowRight"), t("Open on YouTube"), h("span", { class: "sr-only" }, t(" (opens in a new tab)"))),
          )
        : null,
      error
        ? h(
            "div",
            { class: "banner warn ln-error", role: "alert" },
            icon("alert"),
            h("div", {}, h("div", {}, error.message), error.payload?.setup ? h("ul", {}, error.payload.setup.map((step) => h("li", {}, step))) : null),
            error.payload?.basic ? h("button", { class: "btn sm", type: "button", onclick: () => ((engine = "basic"), submit(error.text)) }, t("Use basic extraction instead")) : null,
          )
        : null,
      session?.full
        ? h("div", { class: "banner warn" }, icon("info"), t("This conversation is full: create the strategy, or start a new session."))
        : h("div", { class: "ln-composer" }, h("div", { class: "row ln-composer-top" }, kinds.el, h("span", { class: "spacer" }), engines), input, h("div", { class: "row ln-composer-foot" }, info, h("span", { class: "spacer" }), h("span", { class: "muted small" }, t("Ctrl+Enter to send")), send)),
    );
    const list = chat.querySelector(".ln-messages");
    if (list) list.scrollTop = list.scrollHeight;
  }

  async function previewClean(text, info) {
    if (!text.trim()) return (info.textContent = "");
    try {
      const result = await api.post("/api/learner/clean", { text });
      if (!alive) return;
      const parts = [tn(result.words, "{n} word", "{n} words")];
      if (result.timestamps) parts.push(t("{n} timestamps removed", { n: result.timestamps }));
      if (result.tags) parts.push(t("{n} caption tags removed", { n: result.tags }));
      info.textContent = parts.join(" · ") + (result.too_long ? ` · ${t("too long: paste the part that explains the strategy")}` : "");
      info.classList.toggle("bad", result.too_long);
    } catch {
      info.textContent = "";
    }
  }

  async function submit(text) {
    if (busy || !text.trim()) return;
    await flushDraft();
    busy = true;
    draftCard.inert = true; // edits made now would be saved over the answer's new draft
    draftCard.classList.add("is-busy");
    drawChat();
    try {
      const body = { kind: session || kind !== "chat" ? kind : "rules", text, engine };
      session = session ? await api.post(`/api/learner/sessions/${session.id}/messages`, body) : await api.post("/api/learner/sessions", body);
      overview = await api.get("/api/learner");
      onSession(session.id);
      busy = false;
      draftCard.inert = false;
      draftCard.classList.remove("is-busy");
      kind = "chat";
      composerText = "";
      drawAll();
    } catch (error) {
      busy = false;
      draftCard.inert = false;
      draftCard.classList.remove("is-busy");
      error.text = text;
      composerText = text;
      drawChat({ error });
    }
  }

  // ------------------------------------------------------------------------------ draft

  function scheduleSave(draft, status) {
    dirtyDraft = draft;
    status.textContent = t("Saving…");
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => flushDraft(status), SAVE_IDLE_MS);
  }

  function flushDraft(status) {
    clearTimeout(saveTimer);
    if (!dirtyDraft || !session) return saving;
    const draft = dirtyDraft;
    const id = session.id;
    dirtyDraft = null;
    saving = saving.then(async () => {
      try {
        const saved = await api.put(`/api/learner/sessions/${id}/draft`, draft);
        if (!alive || session?.id !== id) return;
        session = { ...saved, messages: session.messages };
        if (status?.isConnected) status.textContent = t("Saved");
        drawChecks();
      } catch (error) {
        if (!dirtyDraft) dirtyDraft = draft;
        if (status?.isConnected) status.textContent = error.message;
      }
    });
    return saving;
  }

  let checks = null; // the part of the draft card that follows each save (missing, warnings, plan fit)

  function drawChecks() {
    if (!checks || !session) return;
    const draft = session.draft;
    const map = session.mapping;
    const warnings = [...draft.warnings, ...(session.plan_warnings || [])]; // the source's red flags, then the plan checks
    clear(
      checks,
      draft.missing.length ? h("div", { class: "ln-list warn" }, h("strong", {}, icon("info"), t("Still missing")), h("ul", {}, draft.missing.map((x) => h("li", {}, x)))) : null,
      warnings.length ? h("div", { class: "ln-list bad" }, h("strong", {}, icon("alert"), t("Warnings")), h("ul", {}, warnings.map((x) => h("li", {}, x)))) : null,
      map.sessions_unknown.length ? h("p", { class: "muted small" }, t("Not sessions in your plan (kept in the rules text only): {names}", { names: map.sessions_unknown.join(", ") })) : null,
    );
  }

  function chips(options, selected, onToggle) {
    return h(
      "div",
      { class: "ln-chips" },
      options.map((option) =>
        h("button", { type: "button", class: `ln-chip${selected.includes(option) ? " on" : ""}`, "aria-pressed": String(selected.includes(option)), onclick: (event) => (onToggle(option), event.currentTarget.classList.toggle("on"), event.currentTarget.setAttribute("aria-pressed", String(event.currentTarget.classList.contains("on")))) }, option),
      ),
    );
  }

  function drawDraft() {
    if (!session) {
      checks = null;
      clear(
        draftCard,
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, icon("fileText"), t("Strategy draft")), h("p", {}, t("Appears here after the first message")))),
        h(
          "ol",
          { class: "ln-steps" },
          h("li", {}, t("Describe your rules or paste a transcript.")),
          h("li", {}, t("Check and edit the draft: every field can be changed.")),
          h("li", {}, t("Create the incubator strategy, then log backtest trades to prove its edge.")),
        ),
      );
      return;
    }
    const draft = structuredClone(session.draft);
    const status = h("span", { class: "muted small", "aria-live": "polite" });
    const changed = () => scheduleSave(structuredClone(draft), status);
    const text = (value, onInput, attrs = {}) => h("input", { value: value || "", ...attrs, oninput: (event) => (onInput(event.target.value), changed()) });
    const area = (value, onInput, attrs = {}) => h("textarea", { rows: 3, value: value || "", ...attrs, oninput: (event) => (onInput(event.target.value), changed()) });
    const num = (value, onInput, attrs = {}) =>
      h("input", {
        type: "number",
        step: "any",
        inputmode: "decimal",
        value: value ?? "",
        ...attrs,
        oninput: (event) => {
          const raw = event.target.value.trim();
          const parsed = raw === "" ? null : Number(raw);
          onInput(Number.isFinite(parsed) ? parsed : null);
          changed();
        },
      });
    const plan = state.plan;
    const sessionNames = [...new Set([...plan.sessions.map((s) => s.name), ...draft.sessions])];
    const library = new Set(plan.confluences.map((c) => c.name.toLowerCase()));
    const confluenceRows = h("div", { class: "ln-confluences" });
    const drawConfluences = () =>
      clear(
        confluenceRows,
        draft.confluences.map((item, index) =>
          h(
            "div",
            { class: "ln-confluence" },
            text(item.name, (v) => (item.name = v), { maxlength: "80", "aria-label": t("Confluence") }),
            h("label", { class: "ln-required" }, h("input", { type: "checkbox", checked: item.required, onchange: (event) => ((item.required = event.target.checked), changed()) }), t("Required")),
            text(item.detail, (v) => (item.detail = v), { maxlength: "200", placeholder: t("Detail (e.g. EMA 50)"), "aria-label": t("Detail") }),
            library.has(item.name.trim().toLowerCase()) ? null : h("span", { class: "badge", title: t("Not in your confluence library yet") }, t("new")),
            h("button", { type: "button", class: "btn ghost sm icon-only", "aria-label": t("Remove {name}", { name: item.name }), onclick: () => (draft.confluences.splice(index, 1), drawConfluences(), changed()) }, icon("x")),
          ),
        ),
        h("button", { type: "button", class: "btn ghost sm", onclick: () => (draft.confluences.push({ name: "", required: false, detail: "" }), drawConfluences(), confluenceRows.querySelector(".ln-confluence:last-of-type input")?.focus()) }, icon("plus"), t("Add a confluence")),
      );
    drawConfluences();
    const direction = segmented(
      DIRECTIONS.map(([key, label]) => [key, t(label)]),
      draft.direction,
      (value) => ((draft.direction = value), changed()),
    );
    const newOnes = () => draft.confluences.filter((c) => c.name.trim() && !library.has(c.name.trim().toLowerCase())).length;
    const strategy = session.strategy;
    const updating = strategy && strategy.status === "incubator";
    const create = h(
      "button",
      {
        class: "btn primary",
        type: "button",
        onclick: async () => {
          create.disabled = true;
          try {
            await flushDraft(status);
            const result = await api.post(`/api/learner/sessions/${session.id}/strategy`, { add_confluences: addConfluences });
            session = result.session;
            await loadPlan();
            overview = await api.get("/api/learner");
            drawAll();
            const parts = [result.created ? t("{name} is in the incubator, ready for backtest trades", { name: result.strategy.name }) : t("{name} updated in the incubator", { name: result.strategy.name })];
            if (result.confluences_added.length) parts.push(t("New confluences: {names}", { names: result.confluences_added.join(", ") }));
            toast(parts.join(" · "), "success", 6000);
          } catch (error) {
            toast(error.message, "error");
            create.disabled = false;
          }
        },
      },
      icon(updating ? "refresh" : "flame"),
      updating ? t("Update the incubator strategy") : t("Create incubator strategy"),
    );
    checks = h("div", { class: "ln-checks" });
    clear(
      draftCard,
      h("div", { class: "card-head" }, h("div", {}, h("h2", {}, icon("fileText"), t("Strategy draft")), h("p", {}, t("Edit anything: changes are saved on their own"))), status),
      h(
        "div",
        { class: "form-grid ln-form" },
        field(t("Name"), text(draft.name, (v) => (draft.name = v), { maxlength: "60" })),
        field(t("Markets"), text(draft.markets.join(", "), (v) => (draft.markets = v.split(",").map((x) => x.trim()).filter(Boolean)), { placeholder: "XAUUSD, EURUSD" }), { help: t("Comma separated") }),
        field(t("Summary"), area(draft.summary, (v) => (draft.summary = v), { rows: 2, maxlength: "600" }), { full: true }),
        field(t("Timeframes"), chips(plan.enums.timeframes, draft.timeframes, (tf) => ((draft.timeframes = draft.timeframes.includes(tf) ? draft.timeframes.filter((x) => x !== tf) : [...draft.timeframes, tf]), changed())), { full: true }),
        field(t("Sessions"), chips(sessionNames, draft.sessions, (name) => ((draft.sessions = draft.sessions.includes(name) ? draft.sessions.filter((x) => x !== name) : [...draft.sessions, name]), changed())), { full: true }),
        field(t("Direction"), direction.el, { full: true }),
        field(t("Confluences"), confluenceRows, { full: true }),
        field(t("Entry rules"), area(draft.entry_rules.join("\n"), (v) => (draft.entry_rules = lines(v)), { rows: 4 }), { full: true, help: t("One rule per line") }),
        field(t("Stop loss"), text(draft.stop_loss, (v) => (draft.stop_loss = v), { maxlength: "300" }), { full: true }),
        field(t("Exit rules"), area(draft.exit_rules.join("\n"), (v) => (draft.exit_rules = lines(v))), { full: true, help: t("One rule per line") }),
        field(t("Risk per trade (%)"), num(draft.risk.risk_per_trade_pct, (v) => (draft.risk.risk_per_trade_pct = v), { min: "0.01", max: "100" })),
        field(t("Minimum R:R (1:x)"), num(draft.risk.min_rr, (v) => (draft.risk.min_rr = v), { min: "0.1", max: "50" })),
        field(t("Max trades a day"), num(draft.risk.max_trades_per_day, (v) => (draft.risk.max_trades_per_day = v === null ? null : Math.round(v)), { min: "1", max: "100", step: "1" })),
        field(t("Daily loss limit (%)"), num(draft.risk.max_daily_loss_pct, (v) => (draft.risk.max_daily_loss_pct = v), { min: "0.1", max: "100" })),
        field(t("Filters"), area(draft.filters.join("\n"), (v) => (draft.filters = lines(v)), { rows: 2 }), { full: true, help: t("When not to trade, one per line") }),
      ),
      checks,
      h(
        "div",
        { class: "ln-create" },
        h("p", { class: "muted small" }, t("Risk numbers stay in the strategy's rules: your trading plan's limits and guardrails keep applying to every trade.")),
        newOnes() ? h("label", { class: "row small" }, h("input", { type: "checkbox", checked: addConfluences, onchange: (event) => (addConfluences = event.target.checked) }), t("Add new confluences to my library")) : null,
        h(
          "div",
          { class: "row" },
          create,
          strategy ? h("button", { class: "btn", type: "button", onclick: () => onOpenStrategy(strategy.id) }, icon("arrowRight"), t("Open {name}", { name: strategy.name })) : null,
        ),
      ),
    );
    drawChecks();
  }

  // ---------------------------------------------------------------------------- sessions

  async function openSession(id) {
    await flushDraft();
    dirtyDraft = null;
    session = id ? await api.get(`/api/learner/sessions/${id}`) : null;
    kind = session ? "chat" : "rules";
    onSession(session?.id ?? null);
    drawAll();
  }

  async function deleteSession() {
    if (!session) return;
    const ok = await confirmDialog(t("Delete this conversation? A strategy it created stays in the incubator."), { title: t("Delete this session"), confirmText: t("Delete"), danger: true });
    if (!ok) return;
    try {
      clearTimeout(saveTimer);
      dirtyDraft = null;
      await api.del(`/api/learner/sessions/${session.id}`);
      session = null;
      overview = await api.get("/api/learner");
      onSession(null);
      kind = "rules";
      drawAll();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function drawAll() {
    if (!alive) return;
    drawHead();
    drawChat();
    drawDraft();
  }

  if (lessonId && !sessionId) {
    try {
      lesson = (await api.get("/api/academy")).videos.find((v) => v.id === lessonId) || null;
      if (lesson) kind = "transcript";
    } catch {
      lesson = null;
    }
  }
  if (sessionId) {
    try {
      session = await api.get(`/api/learner/sessions/${sessionId}`);
      kind = "chat";
    } catch {
      session = null;
      onSession(null);
    }
  }
  drawAll();

  return () => {
    clearTimeout(cleanTimer);
    flushDraft().finally(() => (alive = false));
  };
}
