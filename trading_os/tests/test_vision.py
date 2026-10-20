"""Vision AI: chart analysis, Q&A replay, image validation and AI settings (no network)."""

import base64
import copy
import json
import struct
import zlib
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from trading_os.services import ai_client, vision

ANALYSIS = {
    "summary": "Gold is in an H1 uptrend, pulling back into demand above 2640.",
    "trend": {"direction": "bullish", "strength": "moderate", "description": "Higher highs and higher lows."},
    "price_axis": {"visible": True, "min_price": 2620.0, "max_price": 2680.0, "current_price": 2652.4},
    "levels": [
        {"kind": "support", "price": 2640.0, "y_px": 600.0, "strength": "strong", "note": "Prior breakout level"},
        {"kind": "resistance", "price": 2670.0, "y_px": None, "strength": "moderate", "note": "Off-screen high"},
    ],
    "zones": [
        {
            "kind": "demand",
            "price_top": 2645.0,
            "price_bottom": 2638.0,
            "y_top_px": 560.0,
            "y_bottom_px": 620.0,
            "x_start_px": 300.0,
            "x_end_px": None,
            "note": "Origin of the last impulse",
        },
        {
            "kind": "sell_side_liquidity",
            "price_top": None,
            "price_bottom": None,
            "y_top_px": None,
            "y_bottom_px": None,
            "x_start_px": None,
            "x_end_px": None,
            "note": "Equal lows below the range",
        },
    ],
    "patterns": [
        {
            "name": "Bullish engulfing",
            "category": "candlestick",
            "bias": "bullish",
            "confidence": "medium",
            "x_px": 900.0,
            "y_px": 580.0,
            "note": "At the demand zone",
        }
    ],
    "structure": {"description": "Bullish structure intact.", "last_bos": "Break above 2660", "last_choch": None},
    "trade_ideas": [
        {
            "direction": "long",
            "entry_type": "limit",
            "entry": 2645.0,
            "stop_loss": 2636.0,
            "take_profits": [2660.0, 2670.0],
            "trigger": "Bullish reaction in the demand zone",
            "rationale": "Pullback into demand in an uptrend",
            "invalidation": "H1 close below 2636",
            "confidence": "medium",
        }
    ],
    "strategy_fit": {"strategy": None, "fits": None, "missing": [], "notes": "No strategy selected."},
    "risks": ["US CPI later today"],
    "caveats": ["Volume is not visible"],
    "symbol_guess": "XAUUSD",
    "timeframe_guess": "H1",
}

THINKING = {"type": "thinking", "thinking": "", "signature": "sig-1"}


def make_png(width: int, height: int) -> bytes:
    """A valid, solid-colour RGB PNG built without Pillow."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    row = b"\x00" + b"\x16\x17\x1a" * width
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(row * height, 9))
        + chunk(b"IEND", b"")
    )


def make_jpeg(width: int, height: int, orientation: int | None = None) -> bytes:
    """JPEG header bytes up to the SOF0 frame (enough for dimension parsing)."""
    data = b"\xff\xd8"
    if orientation is not None:
        tiff = b"MM\x00\x2a\x00\x00\x00\x08" + b"\x00\x01" + b"\x01\x12\x00\x03\x00\x00\x00\x01"
        tiff += struct.pack(">H", orientation) + b"\x00\x00" + b"\x00\x00\x00\x00"
        payload = b"Exif\x00\x00" + tiff
        data += b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    sof = b"\x08" + struct.pack(">HH", height, width) + b"\x03" + b"\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    return data + b"\xff\xc0" + struct.pack(">H", len(sof) + 2) + sof + b"\xff\xd9"


def text_block(text: str):
    return SimpleNamespace(type="text", text=text)


class FakeFinal:
    def __init__(self, text: str = "", stop_reason: str = "end_turn", model: str | None = None):
        self.stop_reason = stop_reason
        self.model = model
        self.content = [SimpleNamespace(type="thinking", thinking="", signature="sig-1")]
        if text:
            self.content.append(text_block(text))
        self._dict = {"content": [THINKING] + ([{"type": "text", "text": text}] if text else [])}

    def to_dict(self):
        return copy.deepcopy(self._dict)


class FakeStream:
    def __init__(self, outcome):
        self.outcome = outcome

    def __enter__(self):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.outcome


class FakeClient:
    """Stands in for anthropic.Anthropic: records each request and replays scripted outcomes."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        return FakeStream(self.outcomes.pop(0))


@pytest.fixture
def fake(monkeypatch):
    holder = FakeClient()
    monkeypatch.setattr(ai_client, "get_client", lambda settings: holder)
    return holder


@pytest.fixture
def no_credentials(monkeypatch, tmp_path):
    for name in ai_client.CREDENTIAL_ENV_VARS + ("ANTHROPIC_PROFILE",):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", str(tmp_path / "no-anthropic-config"))


def uploads_dir(tmp_path):
    return tmp_path / "data" / "uploads"


def analyse(client, fake, *outcomes, image: bytes | None = None, **form):
    fake.outcomes.extend(outcomes)
    files = {"file": ("chart.png", image or make_png(1200, 800), "image/png")}
    return client.post("/api/vision/analyses", files=files, data=form)


def rate_limit_error(retry_after: str = "30"):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(429, headers={"retry-after": retry_after}, request=request)
    return anthropic.RateLimitError("rate limited", response=response, body=None)


# ------------------------------------------------------------------ analysis


def test_analysis_request_and_storage(client, fake, tmp_path):
    png = make_png(1200, 800)
    response = analyse(
        client, fake, FakeFinal(json.dumps(ANALYSIS)), image=png, symbol="xauusd", timeframe="H1", notes="NY open"
    )
    assert response.status_code == 201, response.text
    record = response.json()
    assert record["status"] == "done"
    assert record["analysis"] == ANALYSIS
    assert (record["symbol"], record["timeframe"], record["user_notes"]) == ("XAUUSD", "H1", "NY open")
    assert (record["image_width"], record["image_height"]) == (1200, 800)
    assert record["model"] == "claude-opus-5-5"

    params = fake.calls[0]
    assert params["model"] == "claude-opus-5-5"
    assert params["max_tokens"] == 32000
    assert params["thinking"] == {"type": "adaptive"}
    assert params["output_config"]["effort"] == "high"
    assert params["output_config"]["format"] == {"type": "json_schema", "schema": vision.ANALYSIS_SCHEMA}
    assert params["system"] == [{"type": "text", "text": vision.SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]
    assert params["betas"] == ["server-side-fallback-2026-07-01"]
    assert params["fallbacks"] == "default"
    image_block, text = params["messages"][0]["content"]
    assert image_block == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": base64.standard_b64encode(png).decode()},
    }
    assert text["type"] == "text"
    assert "1200x800 pixels" in text["text"] and "XAUUSD" in text["text"] and "NY open" in text["text"]

    # Overlay: pixel coordinates normalised to the image; items without coordinates are left out.
    overlay = record["overlay"]
    assert overlay["levels"] == [{"index": 0, "y": 0.75}]
    assert overlay["zones"] == [{"index": 0, "x0": 0.25, "x1": 1.0, "y0": 0.7, "y1": 0.775}]
    assert overlay["patterns"] == [{"index": 0, "x": 0.75, "y": 0.725}]

    listing = client.get("/api/vision/analyses").json()
    assert listing[0]["id"] == record["id"]
    assert listing[0]["trend"] == "bullish"
    assert listing[0]["image_src"].startswith("/uploads/")
    filename = listing[0]["image_src"].removeprefix("/uploads/")
    assert (uploads_dir(tmp_path) / filename).read_bytes() == png
    assert client.get(listing[0]["image_src"]).status_code == 200


def test_strategy_details_reach_the_prompt(client, fake):
    strategy = client.get("/api/plan").json()["strategies"][0]
    analyse(client, fake, FakeFinal(json.dumps(ANALYSIS)), strategy=strategy["name"])
    text = fake.calls[0]["messages"][0]["content"][1]["text"]
    assert f"Trader's strategy: {strategy['name']}" in text
    assert f"Strategy details for {strategy['name']}:" in text


def test_haiku_gets_no_fallbacks_and_effort_follows_settings(client, fake):
    assert client.put("/api/ai/settings", json={"model": "claude-haiku-5-5", "effort": "low"}).status_code == 200
    analyse(client, fake, FakeFinal(json.dumps(ANALYSIS)))
    params = fake.calls[0]
    assert params["model"] == "claude-haiku-5-5"
    assert params["output_config"]["effort"] == "low"
    assert "betas" not in params and "fallbacks" not in params


def test_served_model_is_recorded_after_a_fallback(client, fake):
    record = analyse(client, fake, FakeFinal(json.dumps(ANALYSIS), model="claude-opus-5")).json()
    assert record["model"] == "claude-opus-5"


@pytest.mark.parametrize(
    ("final", "message"),
    [
        (FakeFinal("", stop_reason="refusal"), "The model declined to analyse this image."),
        (FakeFinal('{"summary": "trunc', stop_reason="max_tokens"), "cut off"),
        (FakeFinal("not json"), "unexpected format"),
        (FakeFinal(json.dumps({**ANALYSIS, "trend": {"direction": "sideways"}})), "unexpected format"),
    ],
)
def test_unusable_responses_end_in_error_status(client, fake, final, message):
    response = analyse(client, fake, final)
    assert response.status_code == 201
    record = response.json()
    assert record["status"] == "error"
    assert message in record["error"]
    assert record["analysis"] is None and record["overlay"] is None


def test_api_errors_are_mapped_and_stored(client, fake):
    response = analyse(client, fake, rate_limit_error("30"))
    assert response.status_code == 429
    body = response.json()
    assert "30 seconds" in body["detail"]
    record = client.get(f"/api/vision/analyses/{body['analysis_id']}").json()
    assert record["status"] == "error" and "rate limit" in record["error"]


def test_retry_reruns_and_clears_questions(client, fake):
    record = analyse(client, fake, FakeFinal("not json")).json()
    assert record["status"] == "error"
    fake.outcomes.append(FakeFinal(json.dumps(ANALYSIS)))
    retried = client.post(f"/api/vision/analyses/{record['id']}/retry")
    assert retried.status_code == 200
    assert retried.json()["status"] == "done"

    fake.outcomes.append(FakeFinal("Below 2636."))
    client.post(f"/api/vision/analyses/{record['id']}/messages", json={"question": "Where is the stop?"})
    fake.outcomes.append(FakeFinal(json.dumps(ANALYSIS)))
    again = client.post(f"/api/vision/analyses/{record['id']}/retry").json()
    assert again["messages"] == []


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (rate_limit_error(), 429),
        (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com")), 503),
        (anthropic.APITimeoutError(request=httpx2.Request("POST", "https://api.anthropic.com")), 503),
        (
            anthropic.AuthenticationError(
                "bad key",
                response=httpx2.Response(401, request=httpx2.Request("POST", "https://api.anthropic.com")),
                body=None,
            ),
            401,
        ),
        (
            anthropic.InternalServerError(
                "overloaded",
                response=httpx2.Response(529, request=httpx2.Request("POST", "https://api.anthropic.com")),
                body=None,
            ),
            502,
        ),
    ],
)
def test_sdk_errors_map_to_http_statuses(error, status):
    with pytest.raises(ai_client.AIRequestError) as caught:
        ai_client.stream_final_message(FakeClient(error), {"model": "claude-opus-5-5"})
    assert caught.value.status_code == status


# ------------------------------------------------------------------ Q&A


def test_questions_replay_the_conversation_unchanged(client, fake):
    record = analyse(client, fake, FakeFinal(json.dumps(ANALYSIS)), symbol="XAUUSD").json()
    url = f"/api/vision/analyses/{record['id']}/messages"

    fake.outcomes.append(FakeFinal("Below the demand zone at 2636."))
    first = client.post(url, json={"question": "Where would you place the stop?"})
    assert first.status_code == 201, first.text
    assert first.json()["answer"]["text"] == "Below the demand zone at 2636."
    assert first.json()["question"]["role"] == "user"

    fake.outcomes.append(FakeFinal("An H1 close below 2636."))
    second = client.post(url, json={"question": "What would invalidate this idea?"})
    assert second.status_code == 201

    q1, q2 = fake.calls[1], fake.calls[2]
    for params in (q1, q2):
        assert params["max_tokens"] == 16000
        assert params["thinking"] == {"type": "adaptive"}
        assert params["output_config"] == {"effort": "medium"}  # chat effort is capped at medium
        assert params["cache_control"] == {"type": "ephemeral"}
        assert params["fallbacks"] == "default"
    opening = q1["messages"][0]
    assert opening["content"][0]["type"] == "image"
    assert json.dumps(ANALYSIS, sort_keys=True) in opening["content"][1]["text"]
    assert opening["content"][1]["text"].endswith("Question: Where would you place the stop?")
    assert q2["messages"][0] == opening  # byte-identical prefix for cache hits
    assert q2["messages"][1] == {
        "role": "assistant",
        "content": [THINKING, {"type": "text", "text": "Below the demand zone at 2636."}],
    }
    assert q2["messages"][2] == {
        "role": "user",
        "content": [{"type": "text", "text": "What would invalidate this idea?"}],
    }

    messages = client.get(f"/api/vision/analyses/{record['id']}").json()["messages"]
    assert [(m["role"], m["text"]) for m in messages] == [
        ("user", "Where would you place the stop?"),
        ("assistant", "Below the demand zone at 2636."),
        ("user", "What would invalidate this idea?"),
        ("assistant", "An H1 close below 2636."),
    ]
    assert all("content" not in m for m in messages)


def test_low_effort_setting_is_kept_for_questions(client, fake):
    client.put("/api/ai/settings", json={"effort": "low"})
    record = analyse(client, fake, FakeFinal(json.dumps(ANALYSIS))).json()
    fake.outcomes.append(FakeFinal("Answer"))
    client.post(f"/api/vision/analyses/{record['id']}/messages", json={"question": "Trend?"})
    assert fake.calls[1]["output_config"] == {"effort": "low"}


def test_question_rules(client, fake):
    failed = analyse(client, fake, FakeFinal("not json")).json()
    url = f"/api/vision/analyses/{failed['id']}/messages"
    assert client.post(url, json={"question": "Why?"}).status_code == 409
    assert client.post(url, json={"question": "   "}).status_code == 422
    assert client.post(url, json={"question": "x" * 2001}).status_code == 422

    done = analyse(client, fake, FakeFinal(json.dumps(ANALYSIS))).json()
    url = f"/api/vision/analyses/{done['id']}/messages"
    fake.outcomes.append(FakeFinal("", stop_reason="refusal"))
    refused = client.post(url, json={"question": "Why?"})
    assert refused.status_code == 422
    assert client.get(f"/api/vision/analyses/{done['id']}").json()["messages"] == []


# ------------------------------------------------------------------ availability & images


def test_unconfigured_ai_returns_setup_steps(client, no_credentials, tmp_path):
    response = client.post("/api/vision/analyses", files={"file": ("c.png", make_png(64, 64), "image/png")})
    assert response.status_code == 503
    body = response.json()
    assert "not configured" in body["detail"]
    assert any("ANTHROPIC_API_KEY" in step for step in body["setup"])
    assert any("ant auth login" in step for step in body["setup"])
    assert list(uploads_dir(tmp_path).iterdir()) == []
    assert client.get("/api/vision/analyses").json() == []


def test_missing_package_returns_503(client, monkeypatch):
    monkeypatch.setattr(ai_client, "package_installed", lambda: False)
    response = client.post("/api/vision/analyses", files={"file": ("c.png", make_png(64, 64), "image/png")})
    assert response.status_code == 503
    assert "pip install anthropic" in response.json()["detail"]
    assert client.get("/api/ai/settings").json()["package_installed"] is False


def test_image_validation(client, fake):
    not_image = client.post("/api/vision/analyses", files={"file": ("c.txt", b"hello world" * 10, "text/plain")})
    assert not_image.status_code == 400
    assert "PNG, JPEG, GIF and WebP" in not_image.json()["detail"]

    huge = make_png(100, 100) + b"\x00" * (vision.MAX_IMAGE_BYTES + 1)
    too_big = client.post("/api/vision/analyses", files={"file": ("c.png", huge, "image/png")})
    assert too_big.status_code == 400
    assert "5 MB" in too_big.json()["detail"]

    bad_tf = client.post(
        "/api/vision/analyses", files={"file": ("c.png", make_png(10, 10), "image/png")}, data={"timeframe": "H2"}
    )
    assert bad_tf.status_code == 400
    assert fake.calls == []


def test_image_header_parsing():
    assert vision.image_info(make_png(1920, 1080)) == ("png", 1920, 1080)
    assert vision.image_info(make_jpeg(1280, 720)) == ("jpg", 1280, 720)
    assert vision.image_info(make_jpeg(1280, 720, orientation=6)) == ("jpg", 720, 1280)
    assert vision.image_info(b"GIF89a" + struct.pack("<HH", 640, 480) + b"\x00" * 8) == ("gif", 640, 480)
    vp8x = b"RIFF\x00\x00\x00\x00WEBPVP8X" + b"\x0a\x00\x00\x00" + b"\x00" * 4
    vp8x += (1599).to_bytes(3, "little") + (899).to_bytes(3, "little")
    assert vision.image_info(vp8x) == ("webp", 1600, 900)
    with pytest.raises(vision.VisionError):
        vision.image_info(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4)
    with pytest.raises(vision.VisionError):
        vision.image_info(make_png(9000, 100)[:40])


def test_resized_size_matches_the_high_resolution_tier():
    assert vision.resized_size(1920, 1080) == (1920, 1080)
    assert vision.resized_size(3840, 2160) == (2576, 1449)
    assert vision.resized_size(2160, 3840) == (1449, 2576)
    overlay = vision.overlay({**ANALYSIS, "levels": [{**ANALYSIS["levels"][0], "y_px": 724.5}]}, 3840, 2160)
    assert overlay["levels"] == [{"index": 0, "y": 0.5}]


def test_schema_is_valid_for_structured_outputs():
    forbidden = {"minimum", "maximum", "minLength", "maxLength", "exclusiveMinimum", "exclusiveMaximum"}

    def walk(node):
        if isinstance(node, dict):
            assert not forbidden & node.keys()
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert node["required"] == list(node["properties"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(vision.ANALYSIS_SCHEMA)
    assert vision.ChartAnalysis.model_validate(ANALYSIS).model_dump(mode="json") == ANALYSIS


def test_delete_removes_record_and_image(client, fake, tmp_path):
    record = analyse(client, fake, FakeFinal(json.dumps(ANALYSIS))).json()
    filename = record["image_src"].removeprefix("/uploads/")
    assert (uploads_dir(tmp_path) / filename).exists()
    assert client.delete(f"/api/vision/analyses/{record['id']}").status_code == 204
    assert not (uploads_dir(tmp_path) / filename).exists()
    assert client.get(f"/api/vision/analyses/{record['id']}").status_code == 404


def test_missing_image_blocks_retry(client, fake, tmp_path):
    record = analyse(client, fake, FakeFinal("not json")).json()
    (uploads_dir(tmp_path) / record["image_src"].removeprefix("/uploads/")).unlink()
    response = client.post(f"/api/vision/analyses/{record['id']}/retry")
    assert response.status_code == 409
    assert "missing" in response.json()["detail"]


# ------------------------------------------------------------------ settings


def test_ai_settings_never_return_the_key(client, no_credentials):
    initial = client.get("/api/ai/settings").json()
    assert initial["model"] == "claude-opus-5-5" and initial["effort"] == "high"
    assert initial["key_source"] == "none"
    assert [m["id"] for m in initial["models"]] == list(ai_client.MODEL_INFO)
    assert {e["id"] for e in initial["efforts"]} == {"low", "medium", "high", "xhigh", "max"}

    secret = "sk-ant-test-0123456789"
    saved = client.put("/api/ai/settings", json={"api_key": secret, "model": "claude-sonnet-5-5"})
    assert saved.status_code == 200
    assert saved.json()["key_source"] == "saved" and saved.json()["model"] == "claude-sonnet-5-5"
    for path in ("/api/ai/settings", "/api/settings", "/api/plan"):
        assert secret not in client.get(path).text
    assert secret not in saved.text

    cleared = client.put("/api/ai/settings", json={"clear_key": True}).json()
    assert cleared["key_source"] == "none"


def test_environment_credentials_are_detected(client, no_credentials, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
    assert client.get("/api/ai/settings").json()["key_source"] == "environment"


@pytest.mark.parametrize(
    "payload",
    [
        {"model": "gpt-4o"},
        {"effort": "extreme"},
        {"api_key": "sk ant"},
        {"api_key": "sk-ant-x", "clear_key": True},
    ],
)
def test_ai_settings_validation(client, payload):
    assert client.put("/api/ai/settings", json=payload).status_code == 422
