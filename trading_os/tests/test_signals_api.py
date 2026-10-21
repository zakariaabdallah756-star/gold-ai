"""Signals API: parse -> preview -> execute the ticket through POST /api/trades."""

import pytest

from .conftest import complete_checklist, update_settings

# 1% of 10,000 = 100 risk; XAUUSD pip 0.1, 100 oz -> 10 USD per pip per lot; 50 pip stop -> 0.2 lots.
GOOD_SIGNAL = "XAUUSD BUY NOW @ 2650 SL 2645 TP1 2660 TP2 2670 TP3 2685"
# Same targets with a 100 pip stop: blended R:R 1.875 < the default min R:R of 2.
LOW_RR_SIGNAL = "XAUUSD BUY NOW @ 2650 SL 2640 TP1 2660 TP2 2670 TP3 2685"


def parse(client, text, **extra):
    response = client.post("/api/signals/parse", json={"text": text, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def preview(client, **ticket):
    body = {
        "symbol": "XAUUSD",
        "direction": "long",
        "entry": 2650,
        "stop_loss": 2645,
        "targets": [{"price": 2660, "close_pct": 50}, {"price": 2670, "close_pct": 50}],
        "be_enabled": True,
        "be_offset_pips": 0,
        "trail_enabled": False,
        "trail_distance_pips": None,
        "trail_trigger": "tp1",
        **ticket,
    }
    response = client.post("/api/signals/preview", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def codes(result):
    return {v["code"]: v["severity"] for v in result["check"]["violations"]}


def test_parse_preview_and_execute(client):
    result = parse(client, GOOD_SIGNAL)
    assert result["parsed"]["symbol"] == "XAUUSD"
    assert result["parsed"]["take_profits"] == [2660, 2670, 2685]
    assert result["errors"] == []
    sizing = result["sizing"]
    assert sizing["lots"] == pytest.approx(0.2)
    assert sizing["stop_pips"] == pytest.approx(50)
    assert sizing["pip_value_per_lot"] == pytest.approx(10)
    assert sizing["risk_amount"] == pytest.approx(100)
    assert sizing["max_loss"] == pytest.approx(100)
    assert [(t["level"], t["close_pct"], t["lots"]) for t in result["targets"]] == [
        (1, 50, 0.1),
        (2, 25, 0.05),
        (3, 25, 0.05),
    ]
    assert result["targets"][0]["rr"] == pytest.approx(2)
    assert result["targets"][0]["profit"] == pytest.approx(100)
    assert result["blended_rr"] == pytest.approx(3.75)

    ticket = result["ticket"]
    assert ticket["status"] == "open" and ticket["source"] == "signal"
    assert ticket["signal_text"] == GOOD_SIGNAL
    assert ticket["position_size"] == pytest.approx(0.2)
    assert ticket["targets"] == [
        {"price": 2660, "close_pct": 50},
        {"price": 2670, "close_pct": 25},
        {"price": 2685, "close_pct": 25},
    ]
    assert ticket["be_enabled"] is True  # settings.default_auto_be
    assert ticket["trail_enabled"] is False  # settings.default_trail_pips == 0

    # The pre-market checklist is not done yet: the check blocks and so does the server.
    assert result["check"]["blocked"] is True
    assert codes(result)["CHECKLIST_INCOMPLETE"] == "block"
    refused = client.post("/api/trades", json=ticket)
    assert refused.status_code == 423
    assert "CHECKLIST_INCOMPLETE" in {v["code"] for v in refused.json()["violations"]}

    complete_checklist(client)
    edited = preview(
        client,
        symbol=ticket["symbol"],
        direction=ticket["direction"],
        entry=ticket["entry_price"],
        stop_loss=ticket["stop_loss"],
        targets=ticket["targets"],
        be_enabled=ticket["be_enabled"],
        trail_enabled=ticket["trail_enabled"],
        signal_text=GOOD_SIGNAL,
    )
    assert edited["check"]["blocked"] is False
    assert edited["check"]["planned_rr"] == pytest.approx(edited["blended_rr"])
    created = client.post("/api/trades", json=edited["ticket"])
    assert created.status_code == 201, created.text
    trade = created.json()["trade"]
    assert trade["source"] == "signal"
    assert trade["signal_text"] == GOOD_SIGNAL
    assert trade["planned_rr"] == pytest.approx(edited["blended_rr"])
    assert trade["position_size"] == pytest.approx(0.2)
    assert [(t["level"], t["price"], t["close_pct"]) for t in trade["targets"]] == [
        (1, 2660, 50),
        (2, 2670, 25),
        (3, 2685, 25),
    ]
    assert trade["take_profit"] == 2685


def test_blended_rr_below_minimum_blocks(client):
    complete_checklist(client)
    result = parse(client, LOW_RR_SIGNAL)
    assert result["blended_rr"] == pytest.approx(1.875)
    assert result["check"]["blocked"] is True
    assert codes(result)["RR_BELOW_MIN"] == "block"
    assert client.post("/api/trades", json=result["ticket"]).status_code == 423


def test_lots_override_is_honoured(client):
    result = preview(client, lots=0.13)
    sizing = result["sizing"]
    assert sizing["lots"] == pytest.approx(0.13)
    assert sizing["manual"] is True
    assert sizing["auto_lots"] == pytest.approx(0.2)
    assert sizing["max_loss"] == pytest.approx(65)
    assert sizing["actual_risk_pct"] == pytest.approx(0.65)
    assert result["ticket"]["position_size"] == pytest.approx(0.13)
    assert [t["lots"] for t in result["targets"]] == pytest.approx([0.06, 0.07])

    adjusted = preview(client, lots=0.137)
    assert adjusted["sizing"]["lots"] == pytest.approx(0.13)
    assert any("adjusted to 0.13 lots" in note for note in adjusted["sizing"]["notes"])


def test_strategy_copilot_runs_on_the_ticket(client):
    complete_checklist(client)
    update_settings(client, warn_outside_sessions=False)
    smc = next(s for s in client.get("/api/strategies").json() if s["name"] == "SMC")
    smc.update(required_confluences=["Liquidity sweep", "Break of structure"], allowed_sessions=[])
    assert client.put(f"/api/strategies/{smc['id']}", json=smc).status_code == 200

    context = {"strategy": "SMC", "timeframe": "M15", "confluences": ["Liquidity sweep", "Key level (S/R)"]}
    result = parse(client, GOOD_SIGNAL, **context)
    assert result["draft"]["strategy"] == "SMC"
    assert {key: result["ticket"][key] for key in context} == context
    assert codes(result)["STRATEGY_REQUIRED_CONFLUENCES"] == "block"
    assert result["check"]["blocked"] is True
    assert client.post("/api/trades", json=result["ticket"]).status_code == 423

    ready = preview(client, **{**context, "confluences": ["Liquidity sweep", "Break of structure", "Key level (S/R)"]})
    assert "STRATEGY_REQUIRED_CONFLUENCES" not in codes(ready)
    assert ready["check"]["blocked"] is False
    assert client.post("/api/trades", json=ready["ticket"]).status_code == 201


def test_relative_stop_and_targets_follow_the_entry(client):
    result = parse(client, "XAUUSD Buy  Entry zone: 2648 - 2652  SL: 30 pips  TP: 50 pips")
    assert result["draft"]["entry"] == 2652
    assert result["draft"]["stop_pips"] == 30
    assert result["errors"] == []
    assert result["ticket"]["stop_loss"] == pytest.approx(2649)
    assert result["ticket"]["targets"] == [{"price": pytest.approx(2657), "close_pct": 100}]
    assert result["sizing"]["stop_pips"] == pytest.approx(30)


def test_management_defaults_come_from_settings(client):
    update_settings(client, default_auto_be=False, default_trail_pips=25)
    ticket = parse(client, GOOD_SIGNAL)["ticket"]
    assert ticket["be_enabled"] is False
    assert (ticket["trail_enabled"], ticket["trail_distance_pips"], ticket["trail_trigger"]) == (True, 25, "tp1")


def test_missing_entry_is_reported_not_rejected(client):
    result = parse(client, "BUY GOLD NOW SL 2640 TP 2660")
    assert result["parsed"]["entry"] is None
    assert "No entry price - enter your fill price." in result["errors"]
    assert result["ticket"] is None and result["check"] is None


@pytest.mark.parametrize("text", ["", "hello there", "🚀🚀🚀 1234"])
def test_parse_garbage_returns_errors(client, text):
    result = parse(client, text)
    assert result["errors"]
    assert result["ticket"] is None and result["sizing"] is None


@pytest.mark.parametrize(
    "ticket, message",
    [
        ({"symbol": ""}, "No symbol - choose the instrument."),
        ({"symbol": "DOGEUSD"}, "DOGEUSD is not one of your markets"),
        ({"stop_loss": None}, "No stop loss found - cannot size the position."),
        ({"stop_loss": 2650}, "Stop loss equals the entry price."),
        ({"entry": -5}, "Entry price must be greater than 0."),
        ({"lots": 0}, "Lots must be greater than 0."),
        ({"risk_pct": 150}, "Risk % cannot be more than 100."),
        ({"targets": [{"price": 2660, "close_pct": 120}]}, "TP1 close % must be between 0 and 100."),
        ({"targets": [{"price": 2660, "close_pct": 80}, {"price": 2670, "close_pct": 40}]}, "add up to 100%"),
        ({"targets": [{"price": 2670, "close_pct": 50}, {"price": 2660, "close_pct": 50}]}, "TP2 must be beyond"),
        ({"trail_enabled": True, "trail_distance_pips": None}, "Trailing stop needs a trail distance"),
    ],
)
def test_content_problems_are_200_with_errors(client, ticket, message):
    result = preview(client, **ticket)
    assert any(message in error for error in result["errors"]), result["errors"]
    assert result["ticket"] is None


def test_unconvertible_quote_currency_asks_for_the_rate(client):
    market = next(m for m in client.get("/api/markets").json() if m["symbol"] == "GER40")
    base = {"symbol": "GER40", "entry": 18300, "stop_loss": 18250, "targets": [{"price": 18450, "close_pct": 100}]}
    result = preview(client, **base)
    assert result["needs_fx"] is True
    assert any("EUR→USD rate" in error for error in result["errors"])

    priced = preview(client, **base, fx_rate=1.1)
    assert priced["errors"] == []
    # 100 USD risk / (50 points * 1 contract * 1.1) = 1.818 -> 1.8 lots (lot step 0.1).
    assert priced["sizing"]["lots"] == pytest.approx(1.8)
    assert priced["ticket"]["risk_amount"] == pytest.approx(round(1.8 * 50 * market["contract_size"] * 1.1, 2))


def test_mixed_priced_and_pip_targets_are_put_in_distance_order(client):
    result = parse(client, "XAUUSD BUY 2650 SL 2640 TP1 30 pips TP2 2680")
    assert result["errors"] == [], result["errors"]
    prices = [t["price"] for t in result["ticket"]["targets"]]
    assert prices == sorted(prices) and prices[-1] == 2680.0
