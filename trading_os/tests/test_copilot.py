"""Strategy Co-Pilot: unmet strategy parameters are reported and enforced."""

from types import SimpleNamespace

from trading_os.services.copilot import TradeContext, strategy_violations

from .conftest import complete_checklist, trade_payload, update_settings


def strategy(**overrides):
    values = dict(
        name="SMC",
        status="live",
        required_confluences=["Liquidity sweep", "Break of structure"],
        allowed_sessions=["London", "New York"],
        allowed_timeframes=["M5", "M15"],
        allowed_directions="both",
        min_rr=None,
        require_bias_alignment=False,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


BLOCK = SimpleNamespace(trade_rule_mode="block")
WARN = SimpleNamespace(trade_rule_mode="warn")


def codes(violations):
    return {v.code: v.severity for v in violations}


def test_fully_satisfied_strategy_has_no_findings():
    ctx = TradeContext("long", "London", "M15", ["Liquidity sweep", "Break of structure"], 2.5, "bullish")
    assert strategy_violations(BLOCK, strategy(require_bias_alignment=True, min_rr=2), ctx) == []
    assert strategy_violations(BLOCK, None, ctx) == []


def test_missing_parameters_are_named_and_say_wait():
    ctx = TradeContext("short", "Asia", "", ["Liquidity sweep"], 1.5, "")
    found = strategy_violations(BLOCK, strategy(allowed_directions="long", min_rr=2, require_bias_alignment=True), ctx)
    assert codes(found) == {
        "STRATEGY_REQUIRED_CONFLUENCES": "block",
        "STRATEGY_DIRECTION": "block",
        "STRATEGY_SESSION": "block",
        "STRATEGY_TIMEFRAME": "block",
        "STRATEGY_BIAS": "block",
        "STRATEGY_MIN_RR": "block",
    }
    by_code = {v.code: v.message for v in found}
    assert "Missing: Break of structure" in by_code["STRATEGY_REQUIRED_CONFLUENCES"]
    assert "Wait" in by_code["STRATEGY_REQUIRED_CONFLUENCES"]
    assert "select the setup timeframe" in by_code["STRATEGY_TIMEFRAME"]
    assert "not set" in by_code["STRATEGY_BIAS"]
    assert codes(strategy_violations(WARN, strategy(), ctx))["STRATEGY_SESSION"] == "warn"


def test_bias_alignment_and_status_notices():
    ctx = TradeContext("short", "London", "M5", ["Liquidity sweep", "Break of structure"], 3.0, "bullish")
    assert "STRATEGY_BIAS" in codes(strategy_violations(BLOCK, strategy(require_bias_alignment=True), ctx))
    assert codes(strategy_violations(BLOCK, strategy(status="incubator"), ctx)) == {"STRATEGY_INCUBATOR": "warn"}
    assert codes(strategy_violations(BLOCK, strategy(status="retired"), ctx)) == {"STRATEGY_RETIRED": "warn"}


def test_copilot_blocks_live_entry_until_conditions_are_met(client):
    complete_checklist(client)
    update_settings(client, warn_outside_sessions=False)
    smc = next(s for s in client.get("/api/strategies").json() if s["name"] == "SMC")
    smc.update(
        required_confluences=["Liquidity sweep", "Break of structure"],
        allowed_timeframes=["M15"],
        allowed_sessions=[],
    )
    saved = client.put(f"/api/strategies/{smc['id']}", json=smc).json()
    assert saved["min_confluences"] == 3  # never below the number of required confluences

    draft = trade_payload(strategy="SMC", timeframe="M15", confluences=["Liquidity sweep", "Key level (S/R)"])
    check = client.post("/api/trades/check", json=draft).json()
    assert check["blocked"] is True
    assert "STRATEGY_REQUIRED_CONFLUENCES" in {v["code"] for v in check["violations"]}
    assert client.post("/api/trades", json=draft).status_code == 423

    draft["confluences"].append("Break of structure")
    created = client.post("/api/trades", json=draft)
    assert created.status_code == 201, created.text
    assert not [w for w in created.json()["warnings"] if w["code"].startswith("STRATEGY_")]
