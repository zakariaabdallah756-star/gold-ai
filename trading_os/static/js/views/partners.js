// Recommended partners page + the owner's referral-link settings (Trading plan -> Partners & referrals).

import { api } from "../api.js";
import { clear, confirmDialog, field, h, icon, modal, select, toast } from "../dom.js";

const KIND_LABEL = { prop_firm: "Prop firm", broker: "Broker" };

function partnerCard(partner) {
  const external = { href: partner.link, target: "_blank", rel: partner.is_referral ? "sponsored noopener noreferrer" : "noopener noreferrer" };
  return h(
    "article",
    { class: `partner-card${partner.featured ? " featured" : ""}` },
    h(
      "div",
      { class: "row", style: { justifyContent: "space-between", flexWrap: "nowrap", alignItems: "flex-start" } },
      h("div", {}, h("h3", {}, partner.name), h("div", { class: "row", style: { gap: "6px", marginTop: "4px" } }, h("span", { class: "badge" }, KIND_LABEL[partner.kind] || partner.kind), partner.featured ? h("span", { class: "badge good" }, icon("star"), "Featured") : null)),
    ),
    partner.description ? h("p", { class: "partner-description" }, partner.description) : null,
    partner.highlights.length ? h("ul", { class: "partner-highlights" }, partner.highlights.map((item) => h("li", {}, icon("check"), h("span", {}, item)))) : null,
    h(
      "div",
      { class: "row partner-actions" },
      h("a", { class: "btn primary", ...external }, `Visit ${partner.name}`, icon("arrowRight")),
      partner.is_referral ? h("span", { class: "muted small", title: "The app owner may earn a commission from this link" }, "Referral link") : null,
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
      h("div", {}, h("h1", {}, "Recommended partners"), h("p", {}, "Brokers and prop firms curated by the app owner")),
      h("a", { class: "btn", href: "#/plan?tab=partners" }, icon("edit"), "Manage partners"),
    ),
    h(
      "div",
      { class: "banner warn partner-disclosure", role: "note" },
      icon("info"),
      h(
        "div",
        {},
        h("strong", {}, "Affiliate disclosure"),
        "Some links on this page may be referral or affiliate links: the app owner may earn a commission if you open an account. Listings are not investment advice or an endorsement. Trading involves substantial risk - compare terms, fees and regulation for your country and do your own due diligence.",
      ),
    ),
    partners.length
      ? h(
          "div",
          { class: "stack", style: { gap: "24px", marginTop: "18px" } },
          section("prop_firm", "Prop firms", "Trade the firm's capital after passing an evaluation. Track your challenge in Prop Firm."),
          section("broker", "Brokers", "Where you hold your own trading account."),
        )
      : h("div", { class: "card empty", style: { marginTop: "18px" } }, h("strong", {}, "No partners listed"), "Add brokers and prop firms in Trading plan → Partners & referrals."),
  );
}

// ------------------------------------------------------------------ settings tab

function partnerEditor(partner, onSaved) {
  const p = partner || { kind: "broker", highlights: [], featured: false, active: true, sort_order: 0 };
  const name = h("input", { value: p.name || "", maxlength: "80" });
  const kind = select([["broker", "Broker"], ["prop_firm", "Prop firm"]], p.kind);
  const website = h("input", { type: "url", value: p.website_url || "", placeholder: "https://" });
  const referral = h("input", { type: "url", value: p.referral_url || "", placeholder: "https://… (your affiliate / IB link)" });
  const description = h("textarea", { rows: 2, value: p.description || "" });
  const highlights = h("input", { value: (p.highlights || []).join(", "), placeholder: "Comma separated" });
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
          referral_url: referral.value.trim(),
          description: description.value,
          highlights: highlights.value.split(",").map((s) => s.trim()).filter(Boolean),
          sort_order: Number(order.value) || 0,
          featured: featured.checked,
          active: active.checked,
        };
        try {
          if (partner) await api.put(`/api/partners/${partner.id}`, body);
          else await api.post("/api/partners", body);
          ref.close();
          toast("Partner saved", "success");
          onSaved();
        } catch (error) {
          toast(error.message, "error");
        }
      },
    },
    "Save",
  );
  const ref = modal({
    title: partner ? `Edit ${partner.name}` : "Add partner",
    wide: true,
    body: h(
      "div",
      { class: "form-grid" },
      field("Name", name),
      field("Type", kind),
      field("Display order", order),
      field("Official website", website, { full: true }),
      field("Referral link", referral, { full: true, help: "Shown instead of the website when set" }),
      field("Description", description, { full: true }),
      field("Highlights", highlights, { full: true }),
      h("label", { class: "check" }, featured, "Featured"),
      h("label", { class: "check" }, active, "Shown on the Partners page"),
    ),
    footer: [h("button", { class: "btn ghost", type: "button", onclick: () => ref.close() }, "Cancel"), save],
  });
}

export async function renderPartnerSettings(container) {
  async function draw() {
    const partners = await api.get("/api/partners/all");
    const rows = partners.map((partner) => {
      const input = h("input", { type: "url", value: partner.referral_url, placeholder: partner.website_url || "https://", "aria-label": `Referral link for ${partner.name}` });
      const saveLink = h(
        "button",
        {
          class: "btn sm",
          type: "button",
          onclick: async () => {
            try {
              await api.put(`/api/partners/${partner.id}/referral`, { referral_url: input.value.trim() });
              toast(`Referral link for ${partner.name} saved`, "success");
            } catch (error) {
              toast(error.message, "error");
            }
          },
        },
        "Save",
      );
      return h(
        "tr",
        {},
        h("td", {}, h("strong", {}, partner.name), h("div", { class: "muted small" }, `${KIND_LABEL[partner.kind]}${partner.active ? "" : " · hidden"}`)),
        h("td", { class: "partner-link-cell" }, h("div", { class: "row", style: { flexWrap: "nowrap", gap: "6px" } }, input, saveLink)),
        h(
          "td",
          { class: "num" },
          h("button", { class: "btn ghost sm icon-only", type: "button", "aria-label": "Edit", onclick: () => partnerEditor(partner, draw) }, icon("edit")),
          h(
            "button",
            {
              class: "btn ghost sm icon-only",
              type: "button",
              "aria-label": "Delete",
              onclick: async () => {
                if (await confirmDialog(`Remove ${partner.name} from the partners list?`, { title: "Delete partner", confirmText: "Delete", danger: true })) {
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
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Partners & referral links"), h("p", {}, "Your affiliate / IB links replace the official website on the Partners page.")), h("button", { class: "btn primary", type: "button", onclick: () => partnerEditor(null, draw) }, icon("plus"), "Add partner")),
        partners.length ? h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Partner"), h("th", {}, "Referral link"), h("th", {}))), h("tbody", {}, rows))) : h("div", { class: "empty" }, "No partners yet."),
      ),
    );
  }
  await draw();
}
