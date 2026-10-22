// Recommended partners page + the partner list settings (Trading plan -> Partners).

import { api } from "../api.js";
import { clear, confirmDialog, field, h, icon, modal, select, toast } from "../dom.js";
import { N_, t } from "../i18n.js";

const KIND_LABEL = { prop_firm: N_("Prop firm"), broker: N_("Broker") };

function kindLabel(kind) {
  return KIND_LABEL[kind] ? t(KIND_LABEL[kind]) : kind;
}

function partnerCard(partner) {
  const external = { href: partner.link, target: "_blank", rel: "noopener noreferrer" };
  return h(
    "article",
    { class: `partner-card${partner.featured ? " featured" : ""}` },
    h(
      "div",
      { class: "row", style: { justifyContent: "space-between", flexWrap: "nowrap", alignItems: "flex-start" } },
      h("div", {}, h("h3", {}, partner.name), h("div", { class: "row", style: { gap: "6px", marginTop: "4px" } }, h("span", { class: "badge" }, kindLabel(partner.kind)), partner.featured ? h("span", { class: "badge good" }, icon("star"), t("Featured")) : null)),
    ),
    partner.description ? h("p", { class: "partner-description" }, partner.description) : null,
    partner.highlights.length ? h("ul", { class: "partner-highlights" }, partner.highlights.map((item) => h("li", {}, icon("check"), h("span", {}, item)))) : null,
    h(
      "div",
      { class: "row partner-actions" },
      h("a", { class: "btn primary", ...external }, t("Visit {name}", { name: partner.name }), icon("arrowRight")),
    ),
  );
}

export async function render(root) {
  const partners = await api.get("/api/partners");
  const section = (kind, title, description) => {
    const items = partners.filter((p) => p.kind === kind);
    return items.length
      ? h("section", { class: "stack", style: { gap: "12px" } }, h("div", {}, h("h2", {}, title), h("p", { class: "muted small" }, description)), h("div", { class: "partner-grid" }, items.map(partnerCard)))
      : null;
  };
  clear(
    root,
    h(
      "div",
      { class: "page-head" },
      h("div", {}, h("h1", {}, t("Recommended partners")), h("p", {}, t("Brokers and prop firms for quick reference"))),
      h("a", { class: "btn", href: "#/plan?tab=partners" }, icon("edit"), t("Manage partners")),
    ),
    h(
      "div",
      { class: "banner warn partner-risk", role: "note" },
      icon("alert"),
      h(
        "div",
        {},
        h("strong", {}, t("Risk disclaimer")),
        t("Trading involves substantial risk of loss and is not suitable for everyone. Content and tools are for organizational and educational purposes only and do not constitute financial advice."),
      ),
    ),
    partners.length
      ? h(
          "div",
          { class: "stack", style: { gap: "24px", marginTop: "18px" } },
          section("prop_firm", t("Prop firms"), t("Trade the firm's capital after passing an evaluation. Track your challenge in Prop Firm.")),
          section("broker", t("Brokers"), t("Where you hold your own trading account.")),
        )
      : h("div", { class: "card empty", style: { marginTop: "18px" } }, h("strong", {}, t("No partners listed")), t("Add brokers and prop firms in Trading plan → Partners.")),
  );
}

// ------------------------------------------------------------------ settings tab

function partnerEditor(partner, onSaved) {
  const p = partner || { kind: "broker", highlights: [], featured: false, active: true, sort_order: 0 };
  const name = h("input", { value: p.name || "", maxlength: "80" });
  const kind = select([["broker", t("Broker")], ["prop_firm", t("Prop firm")]], p.kind);
  const website = h("input", { type: "url", value: p.website_url || "", placeholder: "https://" });
  const customLink = h("input", { type: "url", value: p.link_url || "", placeholder: t("https://… (optional)") });
  const description = h("textarea", { rows: 2, value: p.description || "" });
  const highlights = h("textarea", { rows: 3, value: (p.highlights || []).join("\n"), placeholder: t("One per line") });
  const order = h("input", { type: "number", step: "1", value: p.sort_order ?? 0 });
  const featured = h("input", { type: "checkbox", checked: p.featured });
  const active = h("input", { type: "checkbox", checked: p.active });
  const save = h(
    "button",
    {
      class: "btn primary",
      type: "button",
      onclick: async () => {
        const body = {
          name: name.value,
          kind: kind.value,
          website_url: website.value.trim(),
          link_url: customLink.value.trim(),
          description: description.value,
          highlights: highlights.value.split("\n").map((s) => s.trim()).filter(Boolean),
          sort_order: Number(order.value) || 0,
          featured: featured.checked,
          active: active.checked,
        };
        try {
          if (partner) await api.put(`/api/partners/${partner.id}`, body);
          else await api.post("/api/partners", body);
          ref.close();
          toast(t("Partner saved"), "success");
          onSaved();
        } catch (error) {
          toast(error.message, "error");
        }
      },
    },
    t("Save"),
  );
  const ref = modal({
    title: partner ? t("Edit {name}", { name: partner.name }) : t("Add partner"),
    wide: true,
    body: h(
      "div",
      { class: "form-grid" },
      field(t("Name"), name),
      field(t("Type"), kind),
      field(t("Display order"), order),
      field(t("Official website"), website, { full: true }),
      field(t("Custom link"), customLink, { full: true, help: t("Opened by the Visit button instead of the official website") }),
      field(t("Description"), description, { full: true }),
      field(t("Highlights"), highlights, { full: true }),
      h("label", { class: "check" }, featured, t("Featured")),
      h("label", { class: "check" }, active, t("Shown on the Partners page")),
    ),
    footer: [h("button", { class: "btn ghost", type: "button", onclick: () => ref.close() }, t("Cancel")), save],
  });
}

export async function renderPartnerSettings(container) {
  async function draw() {
    const partners = await api.get("/api/partners/all");
    const rows = partners.map((partner) => {
      const input = h("input", { type: "url", value: partner.link_url, placeholder: partner.website_url || "https://", "aria-label": t("Custom link for {name}", { name: partner.name }) });
      const saveLink = h(
        "button",
        {
          class: "btn sm",
          type: "button",
          onclick: async () => {
            try {
              await api.put(`/api/partners/${partner.id}/link`, { link_url: input.value.trim() });
              toast(t("Link for {name} saved", { name: partner.name }), "success");
            } catch (error) {
              toast(error.message, "error");
            }
          },
        },
        t("Save"),
      );
      return h(
        "tr",
        {},
        h("td", {}, h("strong", {}, partner.name), h("div", { class: "muted small" }, `${kindLabel(partner.kind)}${partner.active ? "" : t(" · hidden")}`)),
        h("td", { class: "partner-link-cell" }, h("div", { class: "row", style: { flexWrap: "nowrap", gap: "6px" } }, input, saveLink)),
        h(
          "td",
          { class: "num" },
          h("button", { class: "btn ghost sm icon-only", type: "button", "aria-label": t("Edit"), onclick: () => partnerEditor(partner, draw) }, icon("edit")),
          h(
            "button",
            {
              class: "btn ghost sm icon-only",
              type: "button",
              "aria-label": t("Delete"),
              onclick: async () => {
                if (await confirmDialog(t("Remove {name} from the partners list?", { name: partner.name }), { title: t("Delete partner"), confirmText: t("Delete"), danger: true })) {
                  await api.del(`/api/partners/${partner.id}`);
                  draw();
                }
              },
            },
            icon("trash"),
          ),
        ),
      );
    });
    clear(
      container,
      h(
        "div",
        { class: "card" },
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, t("Partners")), h("p", {}, t("Brokers and prop firms shown on the Partners page. A custom link replaces the official website link."))), h("button", { class: "btn primary", type: "button", onclick: () => partnerEditor(null, draw) }, icon("plus"), t("Add partner"))),
        partners.length ? h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, t("Partner")), h("th", {}, t("Custom link")), h("th", {}))), h("tbody", {}, rows))) : h("div", { class: "empty" }, t("No partners yet.")),
      ),
    );
  }
  await draw();
}
