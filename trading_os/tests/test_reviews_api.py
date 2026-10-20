from datetime import date, timedelta

from .conftest import trade_payload, yesterday_iso


def today(client) -> date:
    return date.fromisoformat(client.get("/api/plan").json()["today"])


def closed_trade(client, pnl, **extra):
    payload = trade_payload(status="closed", entry_time=yesterday_iso(), exit_price=2660.0, pnl=pnl, **extra)
    response = client.post("/api/trades", json=payload)
    assert response.status_code == 201, response.text


def review(**overrides) -> dict:
    payload = {
        "discipline_rating": 4,
        "mood": "Calm",
        "followed_plan": True,
        "went_well": "Waited for my A+ setup.",
        "went_wrong": "Moved my stop once.",
        "lessons": "Trust the plan.",
        "tomorrow_focus": "Only London session.",
        "mistakes": ["Moved stop loss"],
    }
    payload.update(overrides)
    return payload


def test_unreviewed_day_returns_blank_review(client):
    day = (today(client) - timedelta(days=1)).isoformat()
    data = client.get(f"/api/reviews/{day}").json()
    assert data["day"] == day and data["exists"] is False
    assert data["discipline_rating"] is None and data["followed_plan"] is None
    assert data["mood"] == "" and data["mistakes"] == []
    assert data["stats"]["trades"] == 0 and data["stats"]["net_pnl"] == 0.0
    assert data["stats"]["win_rate"] is None and data["stats"]["compliance_rate"] is None


def test_review_upsert_and_day_stats(client):
    closed_trade(client, 200.0, emotion="Calm", rule_compliant=True)
    closed_trade(client, -100.0, emotion="FOMO", rule_compliant=False, mistakes=["FOMO entry", "Oversized"])
    closed_trade(client, -40.0, emotion="FOMO", rule_compliant=False, mistakes=["FOMO entry"])
    day = (today(client) - timedelta(days=1)).isoformat()

    created = client.put(f"/api/reviews/{day}", json=review())
    assert created.status_code == 200, created.text
    data = created.json()
    assert data["exists"] is True
    assert data["discipline_rating"] == 4 and data["mood"] == "Calm" and data["followed_plan"] is True
    assert data["mistakes"] == ["Moved stop loss"]

    stats = data["stats"]
    assert (stats["trades"], stats["wins"], stats["losses"], stats["opened"]) == (3, 1, 2, 3)
    assert stats["net_pnl"] == 60.0
    assert stats["win_rate"] == 33.33
    assert stats["compliance_rate"] == 33.33
    assert stats["mistakes"] == [{"name": "FOMO entry", "count": 2}, {"name": "Oversized", "count": 1}]
    assert stats["emotions"] == [{"name": "FOMO", "count": 2}, {"name": "Calm", "count": 1}]

    updated = client.put(
        f"/api/reviews/{day}",
        json=review(discipline_rating=2, mood="", followed_plan=None, mistakes=[" FOMO entry ", "FOMO entry"]),
    ).json()
    assert updated["discipline_rating"] == 2 and updated["mood"] == "" and updated["followed_plan"] is None
    assert updated["mistakes"] == ["FOMO entry"]
    assert client.get(f"/api/reviews/{day}").json()["discipline_rating"] == 2


def test_review_validation(client):
    day = today(client)
    assert client.put(f"/api/reviews/{day}", json=review(discipline_rating=6)).status_code == 422
    assert client.put(f"/api/reviews/{day}", json=review(discipline_rating=0)).status_code == 422
    assert client.put(f"/api/reviews/{day}", json=review(mood="Happy")).status_code == 422
    assert client.put(f"/api/reviews/{day}", json=review(lessons="x" * 10_001)).status_code == 422
    future = client.put(f"/api/reviews/{day + timedelta(days=1)}", json=review())
    assert future.status_code == 422 and "past day" in future.json()["detail"]
    assert client.put(f"/api/reviews/{day}", json={}).json()["exists"] is True


def test_review_history(client):
    closed_trade(client, 75.0)
    now = today(client)
    yesterday = now - timedelta(days=1)
    client.put(f"/api/reviews/{yesterday}", json=review(discipline_rating=5, mood="Satisfied"))

    rows = client.get("/api/reviews", params={"days": 7}).json()
    assert [row["day"] for row in rows] == [(now - timedelta(days=i)).isoformat() for i in range(7)]
    assert rows[0]["exists"] is False and rows[0]["trades"] == 0
    assert rows[1] == {
        "day": yesterday.isoformat(),
        "exists": True,
        "discipline_rating": 5,
        "mood": "Satisfied",
        "followed_plan": True,
        "pnl": 75.0,
        "trades": 1,
    }
    assert len(client.get("/api/reviews").json()) == 30
    assert client.get("/api/reviews", params={"days": 0}).status_code == 422
