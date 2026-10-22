// Trading plan -> Network & access: this connection, LAN addresses, allowed host names, the optional
// remote-access password and copyable tunnel instructions. Changes are accepted only from the computer
// running Trading OS (the server enforces it; the form explains it).

import { api } from "../api.js";
import { clear, confirmDialog, field, h, icon, toast } from "../dom.js";
import { N_, t } from "../i18n.js";

const RULES = {
  localhost: N_("Built in: localhost"),
  loopback: N_("Built in: *.localhost (always this computer)"),
  ip: N_("IP address (always allowed)"),
  machine: N_("This computer's name"),
  env: N_("Start-up setting (TRADING_OS_ALLOWED_HOSTS or --allow-host): {pattern}"),
  saved: N_("Saved on this page: {pattern}"),
};

export async function renderNetworkSettings(container) {
  draw(container, await api.get("/api/network"));
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    // Plain http on the LAN has no clipboard API: fall back to a temporary selection.
    const area = h("textarea", { value: text, readonly: true, style: { position: "fixed", opacity: "0" } });
    document.body.append(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
  toast(t("Copied"), "success", 1800);
}

function command(text, label) {
  return h(
    "div",
    { class: "net-cmd" },
    label ? h("span", { class: "net-cmd-label muted small" }, label) : null,
    h("div", { class: "net-cmd-row" }, h("code", {}, text), h("button", { class: "btn sm", type: "button", title: t("Copy"), "aria-label": t("Copy"), onclick: () => copyText(text) }, icon("copy"))),
  );
}

function kv(rows) {
  return h(
    "dl",
    { class: "net-kv" },
    rows.filter(Boolean).map(([label, value]) => [h("dt", {}, label), h("dd", {}, value)]),
  );
}

function draw(container, data) {
  const redraw = (next) => draw(container, next);
  clear(
    container,
    h("div", { class: "stack net-settings" }, connectionCard(data), devicesCard(data, redraw), hostsCard(data, redraw), passwordCard(data, redraw), tunnelsCard(data)),
  );
}

// ------------------------------------------------------------- this connection

function connectionCard(data) {
  const me = data.this_request;
  const source = me.allowed_by?.source;
  const rule = RULES[source] ? t(RULES[source], { pattern: me.allowed_by.pattern }) : me.allowed_by?.pattern || "—";
  const remoteOpen = !me.local && !data.access.password_set;

  async function signOut() {
    try {
      await api.post("/api/access/logout");
      location.reload();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  return h(
    "div",
    { class: "card" },
    h(
      "div",
      { class: "card-head" },
      h("div", {}, h("h2", {}, t("Network & access")), h("p", {}, t("Open Trading OS from your phone or another computer, and control who can reach it."))),
      h("span", { class: `chip ${me.local ? "ok" : "warn"}` }, icon(me.local ? "check" : "signal"), me.local ? t("This computer") : t("Another device")),
    ),
    remoteOpen
      ? h(
          "div",
          { class: "banner bad net-banner" },
          icon("alert"),
          h("div", {}, h("strong", {}, t("Anyone with this address can use your journal")), t("No remote-access password is set: whoever opens this address can see and change your journal and use your AI key. Set a password on the computer running Trading OS.")),
        )
      : null,
    kv([
      [t("Address"), h("code", {}, me.host || "—")],
      [t("Allowed by"), rule],
      [
        t("Connection"),
        me.secure_context
          ? h("span", { class: "chip ok" }, icon("lock"), t("HTTPS or localhost - Web Push works here"))
          : h("span", { class: "chip warn" }, icon("unlock"), t("Plain http - phones cannot use Web Push on this address")),
      ],
      me.local ? null : [t("Seen as"), me.local_reasons.forwarding_headers.length ? t("Through a proxy or tunnel") : t("Directly from {peer}", { peer: me.local_reasons.peer })],
    ]),
    me.authenticated ? h("div", { class: "row" }, h("button", { class: "btn sm", type: "button", onclick: signOut }, icon("power"), t("Sign out on this device"))) : null,
  );
}

// ------------------------------------------------------------- other devices

function devicesCard(data, redraw) {
  const { server } = data;
  const body = [];
  if (!server.listening_on_lan) {
    body.push(
      h("p", {}, t("Trading OS only listens on this computer right now. To open it from your phone or another computer on the same Wi-Fi, restart it with --lan:")),
      command("trading_os\\run.bat --lan", "Windows"),
      command("trading_os/run.sh --lan", "macOS / Linux"),
    );
  } else if (server.lan_urls.length) {
    body.push(
      h("p", {}, t("Open one of these addresses on your phone or another computer on the same network:")),
      h(
        "ul",
        { class: "net-urls" },
        server.lan_urls.map((url) =>
          h(
            "li",
            {},
            h("code", {}, url),
            h("button", { class: "btn sm", type: "button", onclick: () => copyText(url) }, icon("copy"), t("Copy")),
            data.app_url === url
              ? h("span", { class: "chip ok" }, icon("bell"), t("Notification link"))
              : h("button", { class: "btn sm", type: "button", title: t("Notifications open the app at this address"), onclick: () => useAsAppLink(url) }, icon("link"), t("Use for notifications")),
          ),
        ),
      ),
    );
  } else {
    body.push(h("p", { class: "muted" }, t("Trading OS listens on every network, but no LAN address was found. Check the Wi-Fi or cable connection.")));
  }
  if (server.listening_on_lan) {
    body.push(
      h("p", { class: "muted small" }, t("Plain http works on your network, but phones only allow Web Push over HTTPS: use a tunnel below, or an ntfy, Telegram or Pushover channel.")),
      h("p", { class: "muted small" }, t("If Windows Firewall asks, allow Python on Private networks.")),
    );
  }
  const tip = data.this_request.local
    ? t("On this computer, type {url} in the browser address bar - not in the search box.", { url: server.local_url })
    : t("On the computer running Trading OS, type {url} in the browser address bar - not in the search box.", { url: server.local_url });
  body.push(h("p", { class: "muted small" }, icon("info"), " ", tip));

  async function useAsAppLink(url) {
    try {
      await api.put("/api/notifications/settings", { app_url: url });
      toast(t("Notifications now link to {url}", { url }), "success");
      redraw(await api.get("/api/network"));
    } catch (error) {
      toast(error.message, "error");
    }
  }

  return h(
    "div",
    { class: "card" },
    h("div", { class: "card-head" }, h("div", {}, h("h2", {}, t("Open from other devices")), h("p", {}, t("Phones, tablets and other computers on the same network.")))),
    body,
  );
}

// ------------------------------------------------------------- allowed host names

function hostsCard(data, redraw) {
  const hosts = data.allowed_hosts;
  const canManage = data.access.can_manage;
  const editor = h("textarea", { rows: 4, value: hosts.saved.join("\n"), spellcheck: false, disabled: !canManage, placeholder: "trading.lan\n*.trycloudflare.com" });
  const save = h("button", { class: "btn primary", type: "submit", disabled: !canManage }, icon("check"), t("Save"));

  async function submit(event) {
    event.preventDefault();
    save.disabled = true;
    try {
      const next = await api.put("/api/network", { allowed_hosts: editor.value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean) });
      toast(t("Allowed host names saved"), "success");
      redraw(next);
    } catch (error) {
      toast(error.message, "error");
      save.disabled = false;
    }
  }

  const builtin = [...hosts.builtin, ...hosts.machine];
  return h(
    "form",
    { class: "card", onsubmit: submit },
    h("div", { class: "card-head" }, h("div", {}, h("h2", {}, t("Allowed host names")), h("p", {}, t("Trading OS answers only to these names, so other websites cannot reach it. Addresses by IP always work.")))),
    h("div", { class: "net-label" }, t("Always allowed")),
    h("div", { class: "net-chips" }, builtin.map((name) => h("span", { class: "chip" }, name)), h("span", { class: "chip net-note" }, t("IP addresses"))),
    hosts.env.length || hosts.env_ignored.length
      ? [
          h("div", { class: "net-label" }, t("From TRADING_OS_ALLOWED_HOSTS or --allow-host (change them where Trading OS is started)")),
          h("div", { class: "net-chips" }, hosts.env.map((name) => h("span", { class: "chip" }, name))),
          hosts.env_ignored.map((item) => h("p", { class: "small net-ignored" }, icon("alert"), " ", t("Ignored {entry}: {problem}", { entry: item.entry, problem: item.problem }))),
        ]
      : null,
    h(
      "div",
      { class: "form-grid net-form" },
      field(t("Your extra names"), editor, {
        full: true,
        help: t("One per line, e.g. trading.lan or a tunnel address. *.example.com allows every name below example.com - handy for tunnels whose address changes, such as *.trycloudflare.com. Changes apply at once, up to {max} names.", { max: hosts.max }),
      }),
    ),
    h("div", { class: "row" }, save, canManage ? null : h("span", { class: "muted small" }, t("Only the computer running Trading OS can change this (open {url} there).", { url: data.server.local_url }))),
  );
}

// ------------------------------------------------------------- remote-access password

function passwordCard(data, redraw) {
  const { access } = data;
  const status = access.password_set
    ? h("span", { class: "chip ok" }, icon("lock"), t("Password set - other devices must sign in"))
    : h("span", { class: "chip warn" }, icon("unlock"), t("No password - anyone who can reach this address can use the app"));
  const intro = h("p", { class: "muted small" }, t("Optional. When set, phones and other computers sign in once every {days} days; this computer never needs it. On plain http (your home network) the password travels unencrypted - outside it, use a tunnel with HTTPS.", { days: access.session_days }));

  if (!access.can_manage) {
    return h(
      "div",
      { class: "card" },
      h("div", { class: "card-head" }, h("div", {}, h("h2", {}, t("Remote-access password"))), status),
      intro,
      h("p", { class: "muted small" }, t("Only the computer running Trading OS can change this (open {url} there).", { url: data.server.local_url })),
    );
  }

  const password = h("input", { type: "password", autocomplete: "new-password", minlength: 8, maxlength: 200, required: true });
  const repeat = h("input", { type: "password", autocomplete: "new-password", minlength: 8, maxlength: 200, required: true });
  const save = h("button", { class: "btn primary", type: "submit" }, icon("lock"), access.password_set ? t("Change password") : t("Set password"));

  async function send(value, message) {
    const next = await api.put("/api/access/password", { password: value });
    toast(message, "success");
    redraw(next);
  }

  async function submit(event) {
    event.preventDefault();
    if (password.value !== repeat.value) {
      toast(t("The two passwords do not match."), "error");
      repeat.focus();
      return;
    }
    save.disabled = true;
    try {
      await send(password.value, access.password_set ? t("Password changed - other devices must sign in again") : t("Password set - other devices must sign in"));
    } catch (error) {
      toast(error.message, "error");
      save.disabled = false;
    }
  }

  async function remove() {
    const ok = await confirmDialog(t("Remove the remote-access password? Anyone who can reach this address will be able to use Trading OS without signing in."), {
      title: t("Remove password"),
      confirmText: t("Remove password"),
      danger: true,
    });
    if (!ok) return;
    try {
      await send("", t("Password removed"));
    } catch (error) {
      toast(error.message, "error");
    }
  }

  async function signOutAll() {
    try {
      redraw(await api.post("/api/access/signout-all"));
      toast(t("Every device must sign in again"), "success");
    } catch (error) {
      toast(error.message, "error");
    }
  }

  return h(
    "form",
    { class: "card", autocomplete: "off", onsubmit: submit },
    h("div", { class: "card-head" }, h("div", {}, h("h2", {}, t("Remote-access password"))), status),
    intro,
    h(
      "div",
      { class: "form-grid net-form" },
      field(access.password_set ? t("New password") : t("Password"), password, { help: t("At least 8 characters.") }),
      field(t("Repeat password"), repeat),
    ),
    h(
      "div",
      { class: "row" },
      save,
      access.password_set ? h("button", { class: "btn", type: "button", onclick: signOutAll }, icon("power"), t("Sign out all devices")) : null,
      access.password_set ? h("button", { class: "btn danger", type: "button", onclick: remove }, icon("trash"), t("Remove password")) : null,
    ),
  );
}

// ------------------------------------------------------------- tunnels

function tunnel(name, commandText, note, extra) {
  return h("article", { class: "net-tunnel" }, h("h3", {}, name), command(commandText), h("p", { class: "muted small" }, note), extra || null);
}

function tunnelsCard(data) {
  const port = data.server.port;
  return h(
    "div",
    { class: "card" },
    h(
      "div",
      { class: "card-head" },
      h("div", {}, h("h2", {}, t("Tunnels and remote access")), h("p", {}, t("Reach Trading OS from outside your home network. Keep it on this computer and run the tunnel here too."))),
    ),
    data.push_token_set
      ? null
      : h(
          "div",
          { class: "banner warn net-banner" },
          icon("alert"),
          h(
            "div",
            {},
            h("strong", {}, t("Before you open a tunnel")),
            t("Set a remote-access password above, and a push token on Strategy Alerts so only your platform can send candles."),
            " ",
            h("a", { href: "#/alerts" }, t("Open Strategy Alerts")),
          ),
        ),
    h(
      "div",
      { class: "net-tunnels" },
      tunnel("Tailscale", `tailscale serve --bg ${port}`, t("Recommended: private to the devices in your tailnet, with HTTPS. Add {pattern} above (or this computer's full tailnet name).", { pattern: "*.ts.net" })),
      tunnel(
        "Cloudflare Tunnel",
        `cloudflared tunnel --url http://localhost:${port}`,
        t("Gives a random https address that changes on every run: add {pattern} above. Web Push and the notification link stop working when it changes - a named Cloudflare tunnel keeps a fixed name.", { pattern: "*.trycloudflare.com" }),
      ),
      tunnel("ngrok", `ngrok http ${port}`, t("Add {pattern} above, or your static ngrok domain. Free plans show an ngrok warning page on the first visit.", { pattern: "*.ngrok-free.app" })),
      tunnel("SSH", `ssh -L ${port}:localhost:${port} you@this-computer`, t("Then open http://localhost:{port} on the other computer. It counts as this computer: no host name or password needed.", { port })),
    ),
    h("p", { class: "muted small" }, t("Your own reverse proxy (nginx, Caddy, ...) must send X-Forwarded-For, otherwise its visitors look like this computer and skip the password.")),
  );
}
