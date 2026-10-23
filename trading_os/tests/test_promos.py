"""Dashboard promo carousel: seeding, the carousel feed, the editor API, settings and backups."""

import dataclasses
import io
import json
import re
import zipfile
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from trading_os.app import create_app
from trading_os.config import load_config
from trading_os.services import account as account_service
from trading_os.services import promos as promo_service

from .conftest import update_settings

BUILTIN_KEYS = ["whats_new_v16", "whats_new_v15", "connect_account", "onboarding_guide", "strategy_alerts"]
AFFILIATE_WORDS = re.compile(r"affiliate|referral|commission|sponsored", re.IGNORECASE)


def restart(tmp_path):
    """A second app on the same data folder: start-up seeding runs again."""
    app = create_app(dataclasses.replace(load_config(tmp_path / "data"), background_jobs=False))
    return TestClient(app, base_url="http://localhost")


def editor(client) -> dict:
    response = client.get("/api/promos/all")
    assert response.status_code == 200, response.text
    return response.json()


def shown(client) -> list[dict]:
    response = client.get("/api/promos")
    assert response.status_code == 200, response.text
    return response.json()["promos"]


def partner_id(client, name: str) -> int:
    return next(p["id"] for p in client.get("/api/partners/all").json() if p["name"] == name)


def create(client, **values) -> dict:
    response = client.post("/api/promos", json={"kind": "announcement", "title": "My promo", **values})
    assert response.status_code == 201, response.text
    return response.json()


def freeze(monkeypatch, instant: datetime) -> None:
    """Freeze the clock the account time zone is read from (services.account.local_now)."""

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    monkeypatch.setattr(account_service, "datetime", Frozen)


# --------------------------------------------------------------------------- seeding


def test_seeds_builtin_announcements_and_one_card_per_featured_partner(client):
    data = editor(client)
    promos = data["promos"]
    assert [p["builtin_key"] for p in promos if p["builtin"]] == BUILTIN_KEYS
    assert all(p["kind"] == "announcement" for p in promos if p["builtin"])
    partner_cards = [p for p in promos if p["kind"] == "partner"]
    featured = [p["name"] for p in client.get("/api/partners").json() if p["featured"]]
    assert sorted(p["display"]["partner"]["name"] for p in partner_cards) == sorted(featured) == ["FTMO", "IC Markets"]
    assert [p["sort_order"] for p in promos] == list(range(len(promos)))
    # Nothing is invented: no discount cards, no codes; partner cards use the partner's own description and link.
    assert not [p for p in promos if p["kind"] == "discount" or p["code"]]
    ftmo = next(p for p in shown(client) if p["kind"] == "partner" and p["partner"]["name"] == "FTMO")
    assert ftmo["title"] == "FTMO" and ftmo["cta_url"] == "https://ftmo.com" and ftmo["external"] is True
    assert ftmo["body"] == "Proprietary trading firm offering evaluation programmes for funded trading accounts."
    assert ftmo["cta_label"] == "Visit FTMO" and ftmo["code"] == ""
    assert ftmo["disclaimer"] is True and "substantial risk of loss" in ftmo["disclaimer_text"]
    assert [p["builtin_key"] for p in shown(client)[:2]] == ["whats_new_v16", "whats_new_v15"]
    for whats_new in shown(client)[:2]:
        assert whats_new["disclaimer"] is False
        assert whats_new["cta_url"].startswith("#/") and whats_new["external"] is False
    payload = json.dumps([shown(client), data])
    assert not AFFILIATE_WORDS.search(payload)


def test_seeding_is_idempotent_and_deleted_cards_never_come_back(client, tmp_path):
    promos = editor(client)["promos"]
    guide = next(p for p in promos if p["builtin_key"] == "onboarding_guide")
    ftmo_card = next(p for p in promos if p["kind"] == "partner" and p["display"]["partner"]["name"] == "FTMO")
    assert client.delete(f"/api/promos/{guide['id']}").status_code == 204
    assert client.delete(f"/api/promos/{ftmo_card['id']}").status_code == 204
    assert client.delete(f"/api/promos/{guide['id']}").status_code == 404
    expected = [p["id"] for p in editor(client)["promos"]]

    for _ in range(2):
        with restart(tmp_path) as again:
            assert [p["id"] for p in editor(again)["promos"]] == expected
    settings = client.get("/api/settings").json()
    assert "seeded_defaults" not in settings  # internal bookkeeping stays on the server


def test_an_announcement_added_by_a_later_version_appears_once(client, tmp_path, monkeypatch):
    extra = {"title": "Coming soon", "body": "", "cta_label": "", "cta_url": "#/journal", "theme": "red"}
    monkeypatch.setitem(promo_service.BUILTINS, "later_feature", extra)
    with restart(tmp_path) as again:
        added = [p for p in editor(again)["promos"] if p["builtin_key"] == "later_feature"]
        assert len(added) == 1 and added[0]["sort_order"] == len(editor(again)["promos"]) - 1
        assert again.delete(f"/api/promos/{added[0]['id']}").status_code == 204
    with restart(tmp_path) as third:
        assert not [p for p in editor(third)["promos"] if p["builtin_key"] == "later_feature"]


# ------------------------------------------------------------------------- carousel


def test_carousel_filters_dates_in_the_account_time_zone(client, monkeypatch):
    # 20:00 UTC on 10 March is already 11 March in Kiritimati (UTC+14) and still 10 March in Pago Pago (UTC-11).
    freeze(monkeypatch, datetime(2026, 3, 10, 20, 0, tzinfo=timezone.utc))
    update_settings(client, timezone="Pacific/Kiritimati")
    today = create(client, title="Today only", starts_on="2026-03-11", ends_on="2026-03-11")
    later = create(client, title="Starts tomorrow", starts_on="2026-03-12")
    ended = create(client, title="Ended yesterday", ends_on="2026-03-10")
    off = create(client, title="Switched off", active=False)
    open_ended = create(client, title="Open ended", starts_on="2026-03-01")

    titles = [p["title"] for p in shown(client)]
    assert "Today only" in titles and "Open ended" in titles
    assert not {"Starts tomorrow", "Ended yesterday", "Switched off"} & set(titles)
    status = {p["id"]: p["status"] for p in editor(client)["promos"]}
    assert status[today["id"]] == "live" and status[later["id"]] == "scheduled"
    assert status[ended["id"]] == "ended" and status[off["id"]] == "off" and status[open_ended["id"]] == "live"
    assert editor(client)["today"] == "2026-03-11"

    update_settings(client, timezone="Pacific/Pago_Pago")
    titles = [p["title"] for p in shown(client)]
    assert "Today only" not in titles and "Ended yesterday" in titles
    assert next(p for p in editor(client)["promos"] if p["id"] == today["id"])["status"] == "scheduled"


def test_inactive_or_deleted_partner_hides_its_cards(client):
    ftmo_id = partner_id(client, "FTMO")
    discount = create(client, kind="discount", title="My FTMO code", code="MINE-10", partner_id=ftmo_id)
    names = lambda: [p["partner"]["name"] for p in shown(client) if p["partner"]]  # noqa: E731
    assert names().count("FTMO") == 2

    partner = next(p for p in client.get("/api/partners/all").json() if p["id"] == ftmo_id)
    assert client.put(f"/api/partners/{ftmo_id}", json={**partner, "active": False}).status_code == 200
    assert "FTMO" not in names()
    statuses = {p["kind"]: p["status"] for p in editor(client)["promos"] if p["partner_id"] == ftmo_id}
    assert statuses == {"partner": "partner_hidden", "discount": "partner_hidden"}

    # A deleted partner: its partner card is hidden, the trader's own discount card stays (without the partner).
    assert client.delete(f"/api/partners/{ftmo_id}").status_code == 204
    promos = editor(client)["promos"]
    assert [p["status"] for p in promos if p["kind"] == "partner" and p["partner_id"] is None] == ["no_partner"]
    card = next(p for p in shown(client) if p["id"] == discount["id"])
    assert card["partner"] is None and card["code"] == "MINE-10" and card["disclaimer"] is True


def test_partner_link_must_be_https_for_the_carousel(client):
    pid = partner_id(client, "IC Markets")
    assert client.put(f"/api/partners/{pid}/link", json={"link_url": "http://insecure.example"}).status_code == 200
    card = next(p for p in shown(client) if p["partner"] and p["partner"]["id"] == pid)
    assert card["cta_url"] == "#/partners" and card["external"] is False


def test_ordering_and_reorder(client):
    created = create(client, title="Last one")
    ids = [p["id"] for p in editor(client)["promos"]]
    assert ids[-1] == created["id"] and [p["id"] for p in shown(client)] == ids

    reversed_ids = list(reversed(ids))
    response = client.put("/api/promos/order", json={"ids": reversed_ids})
    assert response.status_code == 200, response.text
    assert [p["id"] for p in response.json()["promos"]] == reversed_ids
    assert [p["id"] for p in shown(client)] == reversed_ids

    # Listed ids go first; the others keep their order after them.
    assert client.put("/api/promos/order", json={"ids": [ids[2]]}).status_code == 200
    assert [p["id"] for p in shown(client)] == [ids[2]] + [i for i in reversed_ids if i != ids[2]]
    assert client.put("/api/promos/order", json={"ids": [ids[0], ids[0]]}).status_code == 422
    unknown = client.put("/api/promos/order", json={"ids": [99999]})
    assert unknown.status_code == 422 and "99999" in unknown.json()["detail"]


def test_italian_translation_of_builtins_partner_cards_and_disclaimer(client):
    v16 = next(p for p in editor(client)["promos"] if p["builtin_key"] == "whats_new_v16")
    assert client.delete(f"/api/promos/{v16['id']}").status_code == 204  # the v1.5 card leads from here on
    update_settings(client, language="it")
    cards = shown(client)
    first = cards[0]
    assert first["title"] == "Novità della v1.5: più conti di trading"
    assert first["cta_label"] == "Configura i tuoi conti"
    ftmo = next(p for p in cards if p["partner"] and p["partner"]["name"] == "FTMO")
    assert ftmo["body"] == "Prop firm che offre programmi di valutazione per conti di trading finanziati."
    assert ftmo["cta_label"] == "Visita FTMO" and ftmo["title"] == "FTMO"
    assert ftmo["disclaimer_text"].startswith("Il trading comporta un rischio sostanziale")

    # The editor shows the translated text; saving it unchanged keeps the English source (still translated).
    item = editor(client)["promos"][0]
    assert item["title"] == first["title"]
    body = {k: item[k] for k in ("kind", "title", "body", "cta_label", "cta_url", "theme", "active")}
    assert client.put(f"/api/promos/{item['id']}", json=body).status_code == 200
    update_settings(client, language="en")
    assert shown(client)[0]["title"] == "New in v1.5: multiple trading accounts"

    # Edited built-in text is shown as typed in every language.
    edited = {**body, "title": "Accounts are here"}
    assert client.put(f"/api/promos/{item['id']}", json=edited).status_code == 200
    update_settings(client, language="it")
    assert shown(client)[0]["title"] == "Accounts are here"
    assert shown(client)[0]["body"].startswith("Segui fianco a fianco")  # untouched texts still follow the language
    assert client.post("/api/promos", json={"kind": "partner"}).json()["detail"] == "Scegli il partner di questa scheda"


# ----------------------------------------------------------------------------- CRUD


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        "http://example.com",
        "data:text/html,hi",
        "//evil.example",
        "ftp://example.com",
        "https://",
        "https://user@evil.example",
        "https://exa mple.com",
        "#javascript:alert(1)",
        "#/journal<script>",
        "journal",
    ],
)
def test_refuses_unsafe_or_unsupported_links(client, url):
    response = client.post("/api/promos", json={"title": "Bad link", "cta_url": url})
    assert response.status_code == 422, url


def test_crud_and_validation(client):
    ftmo_id = partner_id(client, "FTMO")
    ok = create(client, title="Review time", cta_url="#/review?tab=week", cta_label="Open review", theme="blue")
    assert ok["display"]["cta_url"] == "#/review?tab=week" and ok["display"]["external"] is False
    https = create(client, title="Docs", cta_url="https://example.com/docs")
    assert https["display"]["external"] is True and https["display"]["cta_label"] == "Learn more"

    too_long = {"title": "x" * 81}, {"body": "x" * 301}, {"cta_label": "x" * 41}, {"code": "x" * 41}
    for extra in too_long:
        payload = {"kind": "discount", "title": "Fine", **extra}
        assert client.post("/api/promos", json=payload).status_code == 422, extra
    bad = [
        {"kind": "banner", "title": "X"},
        {"theme": "green", "title": "X"},
        {"title": ""},
        {"kind": "discount", "title": "X", "code": "TWO WORDS"},
        {"kind": "discount", "title": "X", "partner_id": 99999},
        {"kind": "partner"},
        {"title": "X", "starts_on": "2026-05-02", "ends_on": "2026-05-01"},
        {"title": "X", "starts_on": "not a date"},
    ]
    for payload in bad:
        assert client.post("/api/promos", json=payload).status_code == 422, payload
    assert client.post("/api/promos", json={"kind": "partner", "partner_id": 99999}).json()["detail"] == (
        "Unknown partner 99999"
    )

    same_day = create(client, title="One day", starts_on="2026-05-01", ends_on="2026-05-01")
    assert same_day["starts_on"] == same_day["ends_on"] == "2026-05-01"
    # A partner card may leave its texts empty: it shows the partner's own.
    partner_card = create(client, kind="partner", title="", partner_id=ftmo_id, theme="purple")
    assert partner_card["display"]["title"] == "FTMO" and partner_card["display"]["cta_url"] == "https://ftmo.com"
    # Announcements have no partner and only discount cards keep a code.
    announcement = create(client, title="News", partner_id=ftmo_id, code="IGNORED")
    assert announcement["partner_id"] is None and announcement["code"] == ""
    discount = create(client, kind="discount", title="  My code  ", code=" MINE-5 ", partner_id=ftmo_id)
    assert discount["title"] == "My code" and discount["code"] == "MINE-5"
    assert discount["display"]["code"] == "MINE-5" and discount["display"]["disclaimer"] is True
    assert discount["display"]["body"] == ""  # a discount card's text is the trader's own, never filled in

    body = {"kind": "discount", "title": "My code v2", "code": "MINE-6", "partner_id": ftmo_id, "theme": "gold"}
    updated = client.put(f"/api/promos/{discount['id']}", json=body)
    assert updated.status_code == 200 and updated.json()["code"] == "MINE-6" and updated.json()["theme"] == "gold"
    assert client.put(f"/api/promos/{discount['id']}", json={**body, "cta_url": "http://x.example"}).status_code == 422
    assert client.put("/api/promos/99999", json=body).status_code == 404
    assert client.delete(f"/api/promos/{discount['id']}").status_code == 204
    assert discount["id"] not in [p["id"] for p in editor(client)["promos"]]


# ------------------------------------------------------------------------- settings


def test_carousel_settings(client):
    assert client.get("/api/promos").json()["interval_sec"] == 4
    response = client.put("/api/promos/settings", json={"enabled": False, "interval_sec": 12})
    assert response.status_code == 200 and response.json() == {"enabled": False, "interval_sec": 12}
    feed = client.get("/api/promos").json()
    assert feed == {"enabled": False, "interval_sec": 12, "promos": []}
    assert editor(client)["enabled"] is False and len(editor(client)["promos"]) >= 4  # the editor still lists them
    settings = client.get("/api/settings").json()
    assert settings["promos_enabled"] is False and settings["promo_interval_sec"] == 12

    for interval in (2, 61):
        assert client.put("/api/promos/settings", json={"interval_sec": interval}).status_code == 422
    assert client.put("/api/promos/settings", json={"enabled": True}).json() == {"enabled": True, "interval_sec": 12}

    update_settings(client, promos_enabled=False, promo_interval_sec=30)
    assert client.get("/api/promos").json()["enabled"] is False
    update_settings(client, promos_enabled=True)
    feed = client.get("/api/promos").json()
    assert feed["enabled"] is True and feed["interval_sec"] == 30 and feed["promos"]


# -------------------------------------------------------------------------- backups


def test_promos_round_trip_through_backups(client):
    ftmo_id = partner_id(client, "FTMO")
    mine = create(
        client,
        kind="discount",
        title="Evaluation code",
        body="Typed from my email",
        code="MINE-20",
        partner_id=ftmo_id,
        cta_url="https://ftmo.com",
        theme="red",
        starts_on="2026-01-01",
        ends_on="2026-12-31",
    )
    ids = [p["id"] for p in editor(client)["promos"]]
    client.put("/api/promos/order", json={"ids": [mine["id"]]})
    client.put("/api/promos/settings", json={"interval_sec": 9})
    before = editor(client)["promos"]

    backup = client.get("/api/export/backup.json").json()
    row = next(r for r in backup["tables"]["promos"] if r["id"] == mine["id"])
    assert row["code"] == "MINE-20" and row["starts_on"] == "2026-01-01" and row["partner_id"] == ftmo_id
    for promo_id in ids:
        client.delete(f"/api/promos/{promo_id}")
    client.put("/api/promos/settings", json={"interval_sec": 20})
    assert editor(client)["promos"] == []

    response = client.post("/api/import", files={"file": ("backup.json", json.dumps(backup), "application/json")})
    assert response.status_code == 200, response.text
    assert editor(client)["promos"] == before
    assert editor(client)["interval_sec"] == 9

    archive = zipfile.ZipFile(io.BytesIO(client.get("/api/export/backup.zip").content))
    tables = json.loads(archive.read("backup.json"))["tables"]
    assert {r["id"] for r in tables["promos"]} == {p["id"] for p in before}


def test_a_backup_from_before_promos_gets_the_builtin_cards_on_next_start(client, tmp_path):
    backup = client.get("/api/export/backup.json").json()
    backup["schema_version"] = 4
    del backup["tables"]["promos"]
    settings_row = backup["tables"]["account_settings"][0]
    settings_row["seeded_defaults"] = [g for g in settings_row["seeded_defaults"] if not g.startswith("promos:")]
    for key in ("promos_enabled", "promo_interval_sec"):
        settings_row.pop(key)
    response = client.post("/api/import", files={"file": ("backup.json", json.dumps(backup), "application/json")})
    assert response.status_code == 200, response.text
    assert editor(client)["promos"] == [] and editor(client)["enabled"] is True

    with restart(tmp_path) as again:
        promos = editor(again)["promos"]
        assert [p["builtin_key"] for p in promos if p["builtin"]] == BUILTIN_KEYS
        assert len([p for p in promos if p["kind"] == "partner"]) == 2


def test_validation_helpers():
    assert promo_service.validate_cta_url("  #/journal  ") == "#/journal"
    assert promo_service.validate_cta_url("https://Example.com/a?b=c#d") == "https://Example.com/a?b=c#d"
    assert promo_service.validate_cta_url("") == ""
    with pytest.raises(promo_service.PromoError):
        promo_service.validate_cta_url("https://example.com/" + "x" * 500)
    with pytest.raises(promo_service.PromoError):
        promo_service.check_dates(date(2026, 2, 2), date(2026, 2, 1))
