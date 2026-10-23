"""App shell (v1.5): what the top bar, the dashboard hero, the Trading Accounts page and the onboarding guide rely on.

The shell adds no endpoint of its own; these tests pin the API behaviour it is built on and the onboarding video
link parser (static/js/video-embed.js, run with node when it is installed).
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from .conftest import complete_checklist, now_utc, trade_payload, update_settings

STATIC = Path(__file__).resolve().parent.parent / "static"
# The fields the Trading Accounts editor sends back (TradingAccountIn), read from an account as listed.
FORM_FIELDS = ("name", "kind", "broker", "platform", "login", "server", "currency", "starting_balance", "leverage")
FORM_FIELDS += ("color", "status", "notes")


def add_account(client, **fields) -> dict:
    body = {"name": "Prop Firm Challenge 1", "kind": "prop", "platform": "mt5", "starting_balance": 100_000.0, **fields}
    response = client.post("/api/accounts", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def form_payload(account: dict, **overrides) -> dict:
    return {**{key: account[key] for key in FORM_FIELDS}, **overrides}


def test_onboarding_settings_round_trip_without_touching_other_settings(client):
    settings = client.get("/api/settings").json()
    assert settings["onboarding_done"] is False and settings["onboarding_video_url"] == ""

    # "Don't show this again" and the language switch send one field each: nothing else may change.
    response = client.put("/api/settings", json={"onboarding_done": True})
    assert response.status_code == 200 and response.json()["onboarding_done"] is True
    response = client.put("/api/settings", json={"language": "it"})
    after = response.json()
    assert after["language"] == "it" and after["onboarding_done"] is True
    assert {k: after[k] for k in ("account_name", "max_risk_pct", "require_checklist")} == {
        k: settings[k] for k in ("account_name", "max_risk_pct", "require_checklist")
    }

    for url in ("https://youtu.be/dQw4w9WgXcQ", "https://vimeo.com/76979871", "/static/guide.mp4", ""):
        assert client.put("/api/settings", json={"onboarding_video_url": url}).status_code == 200
        assert client.get("/api/plan").json()["settings"]["onboarding_video_url"] == url
    update_settings(client, language="en")
    refused = client.put("/api/settings", json={"onboarding_video_url": "http://example.com/video.mp4"})
    assert refused.status_code == 422  # api.js shows the validator's message, "Value error, " stripped
    assert refused.json()["detail"][0]["msg"].endswith(
        "The video link must start with https:// (or /static/ for a file in the app)"
    )


def test_the_switcher_summary_lists_every_account_and_what_all_accounts_leaves_out(client):
    main = client.get("/api/accounts").json()["accounts"][0]
    prop = add_account(client, color="#3987e5")
    eur = add_account(client, name="EUR broker", kind="live", currency="EUR", starting_balance=5_000.0)
    old = add_account(client, name="Old demo", kind="demo", starting_balance=1_000.0)
    assert client.put(f"/api/accounts/{old['id']}", json=form_payload(old, status="archived")).status_code == 200

    data = client.get("/api/accounts/summary", params={"account": "all"}).json()
    assert data["scope"]["account_id"] is None and data["scope"]["currency"] == "USD"
    assert data["scope"]["accounts"] == [main["id"], prop["id"]]
    assert sorted(data["scope"]["excluded"]) == sorted([eur["id"], old["id"]])
    assert data["totals"]["equity"] == 110_000.0 and data["totals"]["live"] is False
    by_id = {account["id"]: account for account in data["accounts"]}
    assert set(by_id) == {main["id"], prop["id"], eur["id"], old["id"]}
    assert by_id[eur["id"]]["currency"] == "EUR" and by_id[eur["id"]]["metrics"]["equity"] == 5_000.0
    assert by_id[old["id"]]["status"] == "archived" and by_id[prop["id"]]["color"] == "#3987e5"
    for account in data["accounts"]:
        metrics = account["metrics"]
        assert metrics["source"] == "journal" and {"balance", "floating_pnl", "margin", "open_positions"} <= set(
            metrics
        )
        assert "credentials" in account and all(
            set(value) <= {"set", "hint"} for value in account["credentials"].values()
        )


def test_switching_the_account_shown_moves_the_hero_and_the_status_bar(client):
    complete_checklist(client)
    update_settings(client, warn_outside_sessions=False)
    prop = add_account(client)
    trade = client.post("/api/trades", json={**trade_payload(), "account_id": prop["id"]})
    assert trade.status_code == 201, trade.text

    switched = client.put("/api/accounts/active", json={"account_id": prop["id"]}).json()
    assert switched["active_account_id"] == prop["id"] and switched["status"]["account_id"] == prop["id"]
    hero = client.get("/api/accounts/summary").json()
    assert hero["scope"]["account_id"] == prop["id"] and hero["scope"]["name"] == "Prop Firm Challenge 1"
    assert hero["totals"]["open_positions"] == 1 and hero["totals"]["balance"] == 100_000.0
    assert hero["updated_at"] and hero["accounts"][1]["metrics"]["updated_at"]

    back = client.put("/api/accounts/active", json={"account_id": None}).json()
    assert back["active_account_id"] is None
    assert client.get("/api/accounts/summary").json()["scope"]["name"] == "All accounts"
    assert client.get("/api/accounts/summary").json()["totals"]["open_positions"] == 1


def test_the_accounts_page_edits_archives_and_explains_why_an_account_cannot_be_deleted(client):
    complete_checklist(client)
    update_settings(client, warn_outside_sessions=False)
    prop = add_account(client)
    client.put("/api/accounts/active", json={"account_id": prop["id"]})

    edited = client.put(f"/api/accounts/{prop['id']}", json=form_payload(prop, broker="FTMO", leverage=50)).json()
    assert (edited["broker"], edited["leverage"], edited["is_active"]) == ("FTMO", 50, True)

    closed = trade_payload(status="closed", entry_time=now_utc().isoformat(), exit_price=2660.0, account_id=prop["id"])
    assert client.post("/api/trades", json=closed).status_code == 201
    refused = client.delete(f"/api/accounts/{prop['id']}")
    assert refused.status_code == 409 and "archive it instead" in refused.json()["detail"]

    # Archiving the account shown goes back to "All accounts" (the switcher only lists active accounts).
    archived = client.put(f"/api/accounts/{prop['id']}", json=form_payload(edited, status="archived"))
    assert archived.status_code == 200 and client.get("/api/settings").json()["active_account_id"] is None
    reactivated = client.put(f"/api/accounts/{prop['id']}", json=form_payload(edited, status="active")).json()
    assert reactivated["status"] == "active"

    main = client.get("/api/accounts").json()["accounts"][0]
    assert main["is_default"]
    default_refused = client.put(f"/api/accounts/{main['id']}", json=form_payload(main, status="archived"))
    assert default_refused.status_code == 409
    empty = add_account(client, name="Unused demo", kind="demo")
    assert client.delete(f"/api/accounts/{empty['id']}").status_code == 204


def test_the_shell_assets_are_served(client):
    index = client.get("/").text
    for sheet in ("accounts.css", "onboarding.css"):
        assert f"/static/css/{sheet}" in index
    for script in ("topbar.js", "onboarding.js", "accounts-ui.js", "video-embed.js", "views/accounts.js"):
        response = client.get(f"/static/js/{script}")
        assert response.status_code == 200 and "javascript" in response.headers["content-type"]
    # No Content-Security-Policy yet: the YouTube / Vimeo players need no frame-src allowance.
    assert "content-security-policy" not in {key.lower() for key in client.get("/").headers}


VIDEO_CASES = {
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=1m30s": (
        "youtube",
        "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?rel=0&start=90",
    ),
    "https://youtu.be/dQw4w9WgXcQ": ("youtube", "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?rel=0"),
    "https://m.youtube.com/shorts/dQw4w9WgXcQ": ("youtube", "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?rel=0"),
    "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ": (
        "youtube",
        "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?rel=0",
    ),
    "https://vimeo.com/76979871": ("vimeo", "https://player.vimeo.com/video/76979871"),
    "https://vimeo.com/76979871/abc123def4": ("vimeo", "https://player.vimeo.com/video/76979871?h=abc123def4"),
    "https://player.vimeo.com/video/76979871?h=ff00aa11": (
        "vimeo",
        "https://player.vimeo.com/video/76979871?h=ff00aa11",
    ),
    "https://cdn.example.com/guide.MP4": ("file", "https://cdn.example.com/guide.MP4"),
    "/static/videos/guide.webm": ("file", "/static/videos/guide.webm"),
    "https://www.youtube.com/watch?v=short": ("link", "https://www.youtube.com/watch?v=short"),
    "https://example.com/tutorial": ("link", "https://example.com/tutorial"),
    "http://youtu.be/dQw4w9WgXcQ": ("none", ""),
    "javascript:alert(1)": ("none", ""),
    "": ("none", ""),
}


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_onboarding_video_links_become_embeds():
    module = (STATIC / "js" / "video-embed.js").as_uri()
    script = (
        f"import {{ videoEmbed }} from {json.dumps(module)};"
        f"const cases = {json.dumps(list(VIDEO_CASES))};"
        "console.log(JSON.stringify(cases.map((url) => videoEmbed(url))));"
    )
    result = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    got = json.loads(result.stdout)
    for (url, (kind, src)), embed in zip(VIDEO_CASES.items(), got, strict=True):
        assert (embed["kind"], embed["src"]) == (kind, src), url
