// Trading Academy (v1.6): the trader's own library of YouTube lessons by topic (psychology, risk management,
// strategies, discipline), played with YouTube's privacy-enhanced embed, marked as completed, with private notes.
//
// One player at a time: the selected lesson plays on the left, its topic's list is on the right. The topic and
// lesson are kept in the address (#/academy?topic=risk&video=12) without re-rendering the page. Notes save on
// their own a moment after typing stops, when the field loses focus and when the page is left.

import { api } from "../api.js";
import { clear, confirmDialog, field, h, icon, modal, select, toast } from "../dom.js";
import { fmt } from "../format.js";
import { N_, t, tn } from "../i18n.js";

const ALL = "all";
const NOTES_IDLE_MS = 800;
const PREVIEW_IDLE_MS = 450;
const SAVED = { idle: "", saving: N_("Saving…"), saved: N_("Saved"), failed: N_("Not saved - check the connection") };

/** "1:05:09" / "4:30" from seconds. */
export function clock(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  const hours = Math.floor(s / 3600);
  const minutes = Math.floor((s % 3600) / 60);
  const rest = String(s % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${rest}` : `${minutes}:${rest}`;
}

function progressBar(pct, label) {
  return h(
    "div",
    { class: "ac-progress", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(Math.round(pct)), "aria-label": label },
    h("span", { style: { width: `${Math.min(100, Math.max(0, pct))}%` } }),
  );
}

/** The YouTube thumbnail, or an icon when there is none or it cannot load (offline). */
function thumbnail(item) {
  const fallback = () => h("span", { class: "ac-thumb-fallback" }, icon(item.kind === "playlist" ? "list" : "playCircle"));
  if (!item.thumbnail_url) return fallback();
  const img = h("img", { src: item.thumbnail_url, alt: "", loading: "lazy", referrerpolicy: "no-referrer" });
  img.addEventListener("error", () => img.replaceWith(fallback()), { once: true });
  return img;
}

function kindBadge(video) {
  if (video.kind === "playlist") return h("span", { class: "badge" }, icon("list"), t("Playlist"));
  return video.start_sec ? h("span", { class: "badge" }, icon("clock"), t("from {time}", { time: clock(video.start_sec) })) : null;
}

// --------------------------------------------------------------------------- add / edit

function videoDialog({ video, topics, topic, onSaved }) {
  const editing = Boolean(video);
  const url = h("input", { type: "url", inputmode: "url", maxlength: "500", placeholder: "https://www.youtube.com/watch?v=…", value: video?.watch_url || "", autocomplete: "off", spellcheck: "false" });
  const topicSelect = select(topics.map((x) => [x.key, x.label]), video?.topic || (topic === ALL ? topics[0].key : topic));
  const title = h("input", { maxlength: "200", value: video?.title || "", placeholder: t("Filled in from YouTube when left blank") });
  const channel = h("input", { maxlength: "120", value: video?.channel || "" });
  const description = h("textarea", { rows: 2, maxlength: "2000", value: video?.description || "", placeholder: t("Why is this lesson worth watching?") });
  const preview = h("div", { class: "ac-preview", "aria-live": "polite" });
  let timer = null;
  let lastUrl = null;
  let seq = 0;
  let suggested = { title: "", channel: "" };

  async function lookUp() {
    const value = url.value.trim();
    if (value === lastUrl) return;
    lastUrl = value;
    const mine = ++seq;
    if (!value) return clear(preview);
    clear(preview, h("div", { class: "muted small" }, t("Checking the link…")));
    try {
      const found = await api.post("/api/academy/preview", { url: value });
      if (mine !== seq) return;
      // Titles typed by the trader stay; the ones suggested for the previous link follow the new one.
      if (!title.value.trim() || title.value === suggested.title) title.value = found.title || "";
      if (!channel.value.trim() || channel.value === suggested.channel) channel.value = found.channel || "";
      suggested = { title: found.title || "", channel: found.channel || "" };
      const duplicate = found.duplicate && found.duplicate.id !== video?.id ? found.duplicate : null;
      clear(
        preview,
        h(
          "div",
          { class: "ac-preview-card" },
          thumbnail(found),
          h(
            "div",
            {},
            h("div", { class: "row small" }, h("span", { class: "badge good" }, icon("check"), found.kind === "playlist" ? t("YouTube playlist") : t("YouTube video")), found.start_sec ? h("span", { class: "badge" }, t("from {time}", { time: clock(found.start_sec) })) : null),
            h("div", { class: "ac-preview-title" }, found.title || t("Title not found - type one below")),
            found.channel ? h("div", { class: "muted small" }, found.channel) : null,
          ),
        ),
        duplicate ? h("div", { class: "banner warn ac-dup" }, icon("alert"), t("Already in the Academy: {title}", { title: duplicate.title })) : null,
      );
    } catch (error) {
      if (mine === seq) clear(preview, h("div", { class: "ac-preview-error" }, icon("alert"), error.message));
    }
  }

  url.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(lookUp, PREVIEW_IDLE_MS);
  });
  url.addEventListener("change", () => {
    clearTimeout(timer);
    lookUp();
  });

  const save = h(
    "button",
    {
      class: "btn primary",
      type: "button",
      onclick: async () => {
        save.disabled = true;
        const body = { url: url.value.trim(), topic: topicSelect.value, title: title.value.trim(), channel: channel.value.trim(), description: description.value.trim() };
        try {
          const saved = editing ? await api.put(`/api/academy/videos/${video.id}`, body) : await api.post("/api/academy/videos", body);
          clearTimeout(timer);
          ref.close();
          toast(editing ? t("Lesson updated") : t("{title} added to the Academy", { title: saved.title }), "success");
          onSaved(saved);
        } catch (error) {
          toast(error.message, "error");
        } finally {
          save.disabled = false;
        }
      },
    },
    editing ? t("Save") : t("Add to the Academy"),
  );
  const ref = modal({
    title: editing ? t("Edit lesson") : t("Add a YouTube lesson"),
    onClose: () => clearTimeout(timer),
    body: h(
      "div",
      { class: "stack", style: { gap: "12px" } },
      h("p", { class: "muted small" }, t("Paste the link of a YouTube video or playlist. It plays here with YouTube's privacy-enhanced player.")),
      field(t("YouTube link"), url),
      preview,
      h("div", { class: "form-grid" }, field(t("Topic"), topicSelect), field(t("Channel"), channel), field(t("Title"), title, { full: true }), field(t("Description"), description, { full: true })),
    ),
    footer: [h("button", { class: "btn ghost", type: "button", onclick: () => ref.close() }, t("Cancel")), save],
  });
  if (video) lookUp();
  else url.focus();
}

// ------------------------------------------------------------------------------- page

export async function render(root, { params }) {
  let data = await api.get("/api/academy");
  let topic = data.topics.some((x) => x.key === params.get("topic")) ? params.get("topic") : ALL;
  let selectedId = Number(params.get("video")) || null;
  let notesTimer = null;
  let pendingNotes = null; // {id, text} not saved yet
  let alive = true;

  const summary = h("div", {});
  const tabs = h("div", { class: "tabs ac-tabs", role: "tablist", "aria-label": t("Topics") });
  const player = h("div", { class: "stack ac-player" });
  const list = h("div", { class: "card ac-list" });

  const byId = (id) => data.videos.find((v) => v.id === id) || null;
  const inTopic = () => data.videos.filter((v) => topic === ALL || v.topic === topic);
  const topicOf = (key) => data.topics.find((x) => x.key === key);

  function remember() {
    const search = new URLSearchParams();
    if (topic !== ALL) search.set("topic", topic);
    if (selectedId) search.set("video", String(selectedId));
    const text = search.toString();
    history.replaceState(null, "", `#/academy${text ? `?${text}` : ""}`);
  }

  async function reload(selectId = selectedId) {
    data = await api.get("/api/academy");
    selectedId = selectId;
    draw();
  }

  async function flushNotes() {
    clearTimeout(notesTimer);
    notesTimer = null;
    const pending = pendingNotes;
    if (!pending) return true;
    pendingNotes = null;
    try {
      await api.put(`/api/academy/videos/${pending.id}/notes`, { notes: pending.text });
      const video = byId(pending.id);
      if (video) video.notes = pending.text;
      if (alive) pending.status?.(pendingNotes ? "saving" : "saved");
      return true;
    } catch {
      if (!pendingNotes) pendingNotes = pending; // keep it for the next try
      if (alive) pending.status?.("failed");
      return false;
    }
  }

  // ------------------------------------------------------------------- summary & tabs

  function drawSummary() {
    const p = data.progress;
    clear(
      summary,
      h(
        "div",
        { class: "card ac-summary" },
        h("div", { class: "ac-summary-icon", "aria-hidden": "true" }, icon("graduation")),
        h(
          "div",
          { class: "ac-summary-main" },
          h("div", { class: "ac-summary-line" }, h("strong", {}, p.total ? t("{done} of {total} lessons completed", { done: p.completed, total: p.total }) : t("Your library is empty")), p.total ? h("span", { class: "muted" }, fmt.pct(p.pct, 0)) : null),
          progressBar(p.pct, t("Academy progress")),
        ),
        h(
          "div",
          { class: "ac-topic-meters" },
          data.topics.map((x) =>
            h("button", { type: "button", class: `ac-topic-meter${topic === x.key ? " on" : ""}`, onclick: () => pickTopic(x.key) }, h("span", {}, x.label), h("span", { class: "muted num" }, `${x.completed}/${x.total}`), progressBar(x.pct, x.label)),
          ),
        ),
      ),
    );
  }

  function drawTabs() {
    const entries = [[ALL, t("All topics"), data.progress.total], ...data.topics.map((x) => [x.key, x.label, x.total])];
    clear(
      tabs,
      entries.map(([key, label, count]) =>
        h("button", { type: "button", role: "tab", class: topic === key ? "on" : "", "aria-selected": String(topic === key), onclick: () => pickTopic(key) }, label, h("span", { class: "ac-count" }, String(count))),
      ),
    );
  }

  function pickTopic(key) {
    topic = key;
    const visible = inTopic();
    if (!visible.some((v) => v.id === selectedId)) selectedId = (visible.find((v) => !v.completed) || visible[0])?.id ?? null;
    draw();
  }

  // ------------------------------------------------------------------------ player

  function nextLesson(current) {
    const visible = inTopic();
    const start = visible.findIndex((v) => v.id === current.id);
    const after = [...visible.slice(start + 1), ...visible.slice(0, start)];
    return after.find((v) => !v.completed) || null;
  }

  function notesCard(video) {
    const status = h("span", { class: "ac-notes-status muted small", "aria-live": "polite" });
    const count = h("span", { class: "muted small num" });
    const area = h("textarea", { class: "ac-notes", rows: 8, maxlength: "20000", value: video.notes || "", placeholder: t("Key ideas, rules to apply, questions… Only you can see these notes."), "aria-label": t("Private notes") });
    const show = (key) => {
      status.textContent = t(SAVED[key] || "");
      status.classList.toggle("bad", key === "failed");
    };
    const counter = () => (count.textContent = area.value.length > 15000 ? `${area.value.length} / 20000` : "");
    area.addEventListener("input", () => {
      pendingNotes = { id: video.id, text: area.value, status: show };
      show("saving");
      counter();
      clearTimeout(notesTimer);
      notesTimer = setTimeout(flushNotes, NOTES_IDLE_MS);
    });
    area.addEventListener("blur", () => flushNotes());
    counter();
    return h(
      "div",
      { class: "card ac-notes-card" },
      h("div", { class: "card-head" }, h("div", {}, h("h2", {}, icon("edit"), t("Private notes")), h("p", {}, t("Saved automatically on this computer and included in your backups."))), h("div", { class: "row" }, count, status)),
      area,
    );
  }

  // The stage is built once: the player frame is replaced only when another lesson is chosen, so marking a
  // lesson as completed or editing it never restarts the video (moving an iframe in the page reloads it).
  const frameBox = h("div", { class: "ac-frame" });
  const stageInfo = h("div", { class: "ac-stage-body" });
  const stage = h("div", { class: "card ac-stage" }, frameBox, stageInfo);
  const notesSlot = h("div", {});
  const emptySlot = h("div", {});
  player.append(emptySlot, stage, notesSlot);
  let frameKey = null;
  let notesFor = null;

  function showFrame(video) {
    const key = `${video.id}|${video.embed_url}`;
    if (key === frameKey) {
      frameBox.querySelector("iframe")?.setAttribute("title", video.title); // renamed: same player, new label
      return;
    }
    frameKey = key;
    clear(
      frameBox,
      h("iframe", {
        src: video.embed_url,
        title: video.title,
        allow: "accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture; fullscreen",
        referrerpolicy: "strict-origin-when-cross-origin",
      }),
    );
  }

  function drawEmpty() {
    const current = topicOf(topic);
    frameKey = null;
    notesFor = null;
    clear(frameBox);
    clear(notesSlot);
    stage.hidden = true;
    clear(
      emptySlot,
      h(
        "div",
        { class: "card ac-empty" },
        h("div", { class: "ac-empty-icon", "aria-hidden": "true" }, icon("graduation")),
        h("strong", {}, current ? t("No {topic} lessons yet", { topic: current.label }) : t("Build your own trading academy")),
        h("p", { class: "muted" }, t("Add YouTube videos or whole playlists you want to study, mark them as completed and keep notes on what to apply.")),
        h(
          "div",
          { class: "row", style: { justifyContent: "center" } },
          h("button", { class: "btn primary", type: "button", onclick: () => addLesson() }, icon("plus"), t("Add a lesson")),
          current ? h("a", { class: "btn", href: current.search_url, target: "_blank", rel: "noopener noreferrer" }, icon("arrowRight"), t("Find videos on YouTube"), h("span", { class: "sr-only" }, t(" (opens in a new tab)"))) : null,
        ),
      ),
    );
  }

  function drawPlayer() {
    const video = byId(selectedId);
    if (!video || (topic !== ALL && video.topic !== topic)) return drawEmpty();
    clear(emptySlot);
    stage.hidden = false;
    showFrame(video);
    if (notesFor !== video.id) {
      notesFor = video.id;
      clear(notesSlot, notesCard(video));
    }
    const next = nextLesson(video);
    const done = h(
      "button",
      {
        type: "button",
        class: `btn ${video.completed ? "good-outline" : "primary"}`,
        "aria-pressed": String(video.completed),
        onclick: async () => {
          done.disabled = true;
          try {
            await flushNotes();
            const saved = await api.put(`/api/academy/videos/${video.id}/progress`, { completed: !video.completed });
            if (saved.completed) toast(t("Lesson completed"), "success");
            await reload(video.id);
            stageInfo.querySelector("[aria-pressed]")?.focus();
          } catch (error) {
            toast(error.message, "error");
            done.disabled = false;
          }
        },
      },
      icon(video.completed ? "checkCircle" : "check"),
      video.completed ? t("Completed") : t("Mark as completed"),
    );
    clear(
      stageInfo,
      h("div", { class: "row ac-stage-tags" }, h("span", { class: `badge ac-topic-${video.topic}` }, topicOf(video.topic)?.label || video.topic), kindBadge(video), video.completed && video.completed_at ? h("span", { class: "badge good" }, icon("check"), t("Completed {date}", { date: fmt.date(video.completed_at) })) : null),
      h("h2", { class: "ac-stage-title" }, video.title),
      video.channel ? h("div", { class: "muted" }, video.channel) : null,
      video.description ? h("p", { class: "ac-stage-desc" }, video.description) : null,
      h(
        "div",
        { class: "row ac-stage-actions" },
        done,
        next ? h("button", { class: "btn", type: "button", onclick: () => select_(next.id) }, t("Next lesson"), icon("chevronRight")) : null,
        h("a", { class: "btn ghost", href: `#/incubator?tab=learner&lesson=${video.id}`, title: t("Paste this video's transcript into the AI Strategy Learner") }, icon("sparkles"), t("Turn into a strategy")),
        h("span", { class: "spacer" }),
        h("a", { class: "btn ghost sm", href: video.watch_url, target: "_blank", rel: "noopener noreferrer" }, icon("arrowRight"), t("Open on YouTube"), h("span", { class: "sr-only" }, t(" (opens in a new tab)"))),
        h("button", { class: "btn ghost sm icon-only", type: "button", "aria-label": t("Edit lesson"), title: t("Edit lesson"), onclick: () => videoDialog({ video, topics: data.topics, topic, onSaved: (saved) => reload(saved.id) }) }, icon("edit")),
        h(
          "button",
          {
            class: "btn ghost sm icon-only",
            type: "button",
            "aria-label": t("Remove lesson"),
            title: t("Remove lesson"),
            onclick: async () => {
              const ok = await confirmDialog(t("Remove {title} from the Academy? Its notes are deleted too.", { title: video.title }), { title: t("Remove lesson"), confirmText: t("Remove"), danger: true });
              if (!ok) return;
              try {
                if (pendingNotes?.id === video.id) {
                  clearTimeout(notesTimer);
                  pendingNotes = null;
                }
                await api.del(`/api/academy/videos/${video.id}`);
                await reload(null);
              } catch (error) {
                toast(error.message, "error");
              }
            },
          },
          icon("trash"),
        ),
      ),
    );
  }

  // -------------------------------------------------------------------------- list

  async function move(video, delta) {
    const ids = data.videos.filter((v) => v.topic === video.topic).map((v) => v.id);
    const from = ids.indexOf(video.id);
    const to = from + delta;
    if (to < 0 || to >= ids.length) return;
    [ids[from], ids[to]] = [ids[to], ids[from]];
    try {
      data = await api.put("/api/academy/order", { topic: video.topic, ids });
      draw();
      list.querySelector(`[data-id="${video.id}"] .ac-item-main`)?.focus();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function drawList() {
    const visible = inTopic();
    const current = topicOf(topic);
    const sameTopic = (video) => data.videos.filter((v) => v.topic === video.topic);
    clear(
      list,
      h(
        "div",
        { class: "card-head" },
        h("div", {}, h("h2", {}, current ? current.label : t("All lessons")), h("p", {}, tn(visible.length, "{n} lesson", "{n} lessons"))),
        h("button", { class: "btn sm", type: "button", onclick: () => addLesson() }, icon("plus"), t("Add")),
      ),
      visible.length
        ? h(
            "ol",
            { class: "ac-items" },
            visible.map((video) => {
              const siblings = sameTopic(video);
              const index = siblings.findIndex((v) => v.id === video.id);
              return h(
                "li",
                { class: `ac-item${video.id === selectedId ? " on" : ""}${video.completed ? " done" : ""}`, dataset: { id: String(video.id) } },
                h(
                  "button",
                  { type: "button", class: "ac-item-main", "aria-current": video.id === selectedId ? "true" : null, onclick: () => select_(video.id) },
                  h(
                    "span",
                    { class: "ac-thumb" },
                    thumbnail(video),
                    video.completed ? h("span", { class: "ac-thumb-done", "aria-hidden": "true" }, icon("check")) : null,
                  ),
                  h(
                    "span",
                    { class: "ac-item-text" },
                    h("span", { class: "ac-item-title" }, video.title),
                    h("span", { class: "ac-item-meta" }, [topic === ALL ? topicOf(video.topic)?.label : null, video.channel, video.kind === "playlist" ? t("Playlist") : null, video.notes ? t("notes") : null].filter(Boolean).join(" · ")),
                    video.completed ? h("span", { class: "sr-only" }, t("Completed")) : null,
                  ),
                ),
                topic !== ALL && siblings.length > 1
                  ? h(
                      "span",
                      { class: "ac-item-move" },
                      h("button", { type: "button", class: "btn ghost sm icon-only", disabled: index === 0, "aria-label": t("Move {title} up", { title: video.title }), onclick: () => move(video, -1) }, icon("up")),
                      h("button", { type: "button", class: "btn ghost sm icon-only", disabled: index === siblings.length - 1, "aria-label": t("Move {title} down", { title: video.title }), onclick: () => move(video, 1) }, icon("down")),
                    )
                  : null,
              );
            }),
          )
        : h("div", { class: "empty small" }, t("Nothing here yet.")),
    );
  }

  async function select_(id) {
    await flushNotes();
    selectedId = id;
    draw();
    if (window.matchMedia?.("(max-width: 1000px)").matches) player.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function addLesson() {
    videoDialog({
      video: null,
      topics: data.topics,
      topic,
      onSaved: async (saved) => {
        if (topic !== ALL && saved.topic !== topic) topic = saved.topic;
        await reload(saved.id);
      },
    });
  }

  function draw() {
    if (!alive) return;
    if (selectedId && !byId(selectedId)) selectedId = null;
    if (!selectedId) selectedId = (inTopic().find((v) => !v.completed) || inTopic()[0])?.id ?? null;
    drawSummary();
    drawTabs();
    drawPlayer();
    drawList();
    remember();
  }

  clear(
    root,
    h(
      "div",
      { class: "page-head" },
      h("div", {}, h("h1", {}, t("Trading Academy")), h("p", {}, t("Your own library of YouTube lessons by topic: watch, mark what you finished and keep private notes"))),
      h("button", { class: "btn primary", type: "button", onclick: () => addLesson() }, icon("plus"), t("Add a lesson")),
    ),
    summary,
    tabs,
    h("div", { class: "ac-layout" }, player, list),
  );
  draw();

  const onLeave = () => {
    if (pendingNotes) flushNotes();
  };
  window.addEventListener("beforeunload", onLeave);
  return () => {
    window.removeEventListener("beforeunload", onLeave);
    flushNotes().finally(() => (alive = false));
  };
}
