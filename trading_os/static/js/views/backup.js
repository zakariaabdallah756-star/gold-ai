// Data export (CSV / JSON / ZIP) and restore.

import { api } from "../api.js";
import { clear, confirmDialog, h, icon, toast } from "../dom.js";
import { loadPlan, loadStatus, state } from "../state.js";

const EXPORTS = [
  ["Trades as CSV", "One row per trade for Excel, Google Sheets, Python or R. Lists are joined with '; '.", "/api/export/trades.csv"],
  ["Full backup (JSON)", "Every table - settings, plan, checklist history and trades. Restorable.", "/api/export/backup.json"],
  ["Full backup with screenshots (ZIP)", "The JSON backup plus every uploaded chart image. Restorable.", "/api/export/backup.zip"],
];

export async function render(root) {
  const fileInput = h("input", { type: "file", accept: ".json,.zip,application/json,application/zip" });
  const result = h("div");

  const restore = h(
    "button",
    {
      class: "btn danger",
      type: "button",
      onclick: async () => {
        const file = fileInput.files[0];
        if (!file) {
          toast("Choose a .json or .zip backup first", "warn");
          return;
        }
        const ok = await confirmDialog(
          `Restoring "${file.name}" replaces ALL current data (trades, plan, settings, checklist history). A safety snapshot of the current data is saved first.`,
          { title: "Restore backup", confirmText: "Replace my data", danger: true },
        );
        if (!ok) return;
        restore.disabled = true;
        try {
          const form = new FormData();
          form.append("file", file);
          const response = await api.upload("/api/import", form);
          await loadPlan();
          await loadStatus();
          const counts = response.restored;
          clear(
            result,
            h(
              "div",
              { class: "violation ok", style: { marginTop: "12px" } },
              icon("checkCircle"),
              h("div", {}, `Restored ${counts.trades ?? 0} trades, ${counts.markets ?? 0} markets and ${counts.checklist_days ?? 0} checklist days${counts.files !== undefined ? `, ${counts.files} screenshot files` : ""}. Previous data saved as backups/${response.safety_snapshot}.`),
            ),
          );
          toast("Backup restored", "success");
        } catch (error) {
          toast(error.message, "error");
        } finally {
          restore.disabled = false;
        }
      },
    },
    icon("upload"),
    "Restore…",
  );

  clear(
    root,
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Backup & export"), h("p", {}, "Your journal is stored locally. Export it often."))),
    h(
      "div",
      { class: "grid cols-2" },
      h(
        "div",
        { class: "card" },
        h("div", { class: "card-head" }, h("h2", {}, "Export")),
        h(
          "div",
          { class: "stack", style: { gap: "10px" } },
          EXPORTS.map(([title, description, href]) =>
            h(
              "div",
              { class: "row", style: { justifyContent: "space-between", flexWrap: "nowrap", padding: "10px 0", borderBottom: "1px solid var(--border)" } },
              h("div", {}, h("strong", {}, title), h("p", { class: "muted small" }, description)),
              h("a", { class: "btn", href, download: "" }, icon("download"), "Download"),
            ),
          ),
        ),
      ),
      h(
        "div",
        { class: "stack" },
        h(
          "div",
          { class: "card" },
          h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Restore"), h("p", {}, "From a JSON or ZIP backup made by Trading OS"))),
          h("div", { class: "row", style: { flexWrap: "nowrap" } }, fileInput, restore),
          result,
        ),
        h(
          "div",
          { class: "card" },
          h("div", { class: "card-head" }, h("h2", {}, "Where your data lives")),
          h("p", { class: "muted small" }, "Everything - database, screenshots and automatic pre-restore snapshots - is in this folder. Copying it is a complete backup:"),
          h("pre", { class: "notes", style: { marginTop: "8px", fontFamily: "var(--mono)", fontSize: "12px", overflowX: "auto" } }, state.plan.app.data_dir),
        ),
      ),
    ),
  );
}
