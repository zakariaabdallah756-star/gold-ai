"""Web Push: RFC 8291 encryption, RFC 8292 VAPID JWT, subscriptions, service worker and manifest."""

import json
import os
import struct
import time

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from trading_os.config import STATIC_DIR
from trading_os.services import notify, notify_channels, webpush
from trading_os.services.webpush import b64url_decode, b64url_encode

ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc123:def456"


class Browser:
    """A user agent's push subscription: key pair plus auth secret."""

    def __init__(self, endpoint=ENDPOINT):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.auth = os.urandom(16)
        self.endpoint = endpoint

    @property
    def p256dh(self) -> str:
        return b64url_encode(self.key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint))

    def subscription(self) -> dict:
        return {"endpoint": self.endpoint, "keys": {"p256dh": self.p256dh, "auth": b64url_encode(self.auth)}}

    def decrypt(self, body: bytes) -> bytes:
        """Receiver side of RFC 8291 (what the browser does)."""
        salt, record_size, id_len = body[:16], *struct.unpack("!IB", body[16:21])
        as_public = body[21 : 21 + id_len]
        ciphertext = body[21 + id_len :]
        assert record_size == 4096 and id_len == 65
        sender = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public)
        shared = self.key.exchange(ec.ECDH(), sender)
        ua_public = b64url_decode(self.p256dh)

        def hkdf(salt_, ikm, info, length):
            return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt_, info=info).derive(ikm)

        ikm = hkdf(self.auth, shared, b"WebPush: info\x00" + ua_public + as_public, 32)
        cek = hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
        nonce = hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
        plaintext = AESGCM(cek).decrypt(nonce, ciphertext, None)
        assert plaintext.endswith(b"\x02")  # last-record padding delimiter
        return plaintext[:-1]


# ----------------------------------------------------------------- crypto


def test_rfc8291_appendix_a_test_vector():
    """Exact output of the example in RFC 8291 Appendix A."""
    server_key = ec.derive_private_key(
        int.from_bytes(b64url_decode("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"), ec.SECP256R1()
    )
    body = webpush.encrypt(
        b"When I grow up, I want to be a watermelon",
        "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
        "BTBZMqHH6r4Tts7J_aSIgg",
        salt=b64url_decode("DGv6ra1nlYgDCS1FRnbzlw"),
        server_key=server_key,
    )
    assert b64url_encode(body) == (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK"
        "6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"
    )


def test_encrypt_round_trip_with_random_keys():
    browser = Browser()
    payload = json.dumps({"title": "XAUUSD long", "body": "Entry 2650"}).encode()
    first = webpush.encrypt(payload, browser.p256dh, b64url_encode(browser.auth))
    second = webpush.encrypt(payload, browser.p256dh, b64url_encode(browser.auth))
    assert first != second  # fresh salt and ephemeral key every time
    assert browser.decrypt(first) == payload == browser.decrypt(second)


@pytest.mark.parametrize(
    ("p256dh", "auth", "message"),
    [
        (b64url_encode(b"\x04" + b"\x01" * 64), b64url_encode(os.urandom(16)), "valid P-256 point"),
        (b64url_encode(b"\x04" + b"\x01" * 10), b64url_encode(os.urandom(16)), "65-byte"),
        (None, b64url_encode(os.urandom(8)), "16 bytes"),
    ],
)
def test_invalid_subscription_keys(p256dh, auth, message):
    p256dh = p256dh or Browser().p256dh
    with pytest.raises(webpush.WebPushError, match=message):
        webpush.subscription_keys_valid(p256dh, auth)


def test_vapid_jwt_is_signed_with_the_public_key():
    private, public = webpush.generate_vapid_keys()
    assert len(b64url_decode(public)) == 65 and len(b64url_decode(private)) == 32
    before = int(time.time())
    token = webpush.vapid_jwt(ENDPOINT, private, "mailto:me@example.com")
    header_b64, claims_b64, signature_b64 = token.split(".")
    assert json.loads(b64url_decode(header_b64)) == {"typ": "JWT", "alg": "ES256"}
    claims = json.loads(b64url_decode(claims_b64))
    assert claims["aud"] == "https://fcm.googleapis.com"
    assert claims["sub"] == "mailto:me@example.com"
    assert before + 12 * 3600 <= claims["exp"] <= int(time.time()) + 12 * 3600

    raw = b64url_decode(signature_b64)
    assert len(raw) == 64
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b64url_decode(public))
    key.verify(der, f"{header_b64}.{claims_b64}".encode(), ec.ECDSA(hashes.SHA256()))  # raises when invalid

    authorization = webpush.vapid_authorization(ENDPOINT, private, public, "mailto:me@example.com")
    assert authorization.startswith("vapid t=ey") and authorization.endswith(f", k={public}")


# --------------------------------------------------------- subscriptions


@pytest.fixture
def http(monkeypatch):
    calls = []
    responses = {}

    def fake(url, body, headers):
        calls.append({"url": url, "body": body, "headers": headers})
        return responses.get(url, httpx.Response(201))

    monkeypatch.setattr(notify_channels, "_http_post", fake)
    fake.calls, fake.responses = calls, responses
    return fake


def subscribe(client, browser, **extra):
    response = client.post(
        "/api/notifications/webpush/subscribe",
        json={"subscription": browser.subscription(), **extra},
        headers={
            "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/129.0 Mobile Safari/537.36"
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_vapid_keys_are_generated_once(client):
    first = client.get("/api/notifications/webpush/key").json()
    assert first["available"] is True and len(b64url_decode(first["public_key"])) == 65
    assert client.get("/api/notifications/webpush/key").json() == first
    assert client.get("/api/notifications/settings").json()["vapid_public_key"] == first["public_key"]


def test_subscribe_upserts_by_endpoint_and_masks_the_endpoint(client):
    browser = Browser()
    channel = subscribe(client, browser)
    assert channel["kind"] == "webpush" and channel["name"] == "Chrome on Android"
    assert channel["config"] == {"push_service": "fcm.googleapis.com"}
    assert channel["endpoint_id"] == notify_channels.endpoint_id(ENDPOINT)
    assert channel["summary"] == "Browser push via fcm.googleapis.com"

    again = subscribe(client, browser, name="Work phone")
    assert again["id"] == channel["id"] and again["name"] == "Work phone"
    listing = client.get("/api/notifications/channels").text
    assert ENDPOINT not in listing and browser.p256dh not in listing

    removed = client.post("/api/notifications/webpush/unsubscribe", json={"endpoint": ENDPOINT}).json()
    assert removed == {"removed": 1}
    assert client.get("/api/notifications/channels").json() == []


def test_rotated_subscription_keeps_the_channel_settings(client):
    old = Browser()
    channel = subscribe(client, old, name="iPhone")
    client.put(f"/api/notifications/channels/{channel['id']}", json={"events": ["risk"]})
    new = Browser(endpoint="https://web.push.apple.com/QGx2")
    rotated = subscribe(client, new, old_endpoint=ENDPOINT)
    assert rotated["id"] == channel["id"] and rotated["events"] == ["risk"]
    assert rotated["config"] == {"push_service": "web.push.apple.com"}


def test_invalid_subscriptions_are_rejected(client):
    browser = Browser()
    bad = browser.subscription()
    bad["endpoint"] = "http://insecure.example.com/push"
    response = client.post("/api/notifications/webpush/subscribe", json={"subscription": bad})
    assert response.status_code == 422 and "https://" in response.json()["detail"]
    bad = browser.subscription()
    bad["keys"]["auth"] = b64url_encode(b"short")
    assert client.post("/api/notifications/webpush/subscribe", json={"subscription": bad}).status_code == 422


def test_webpush_delivery_is_encrypted_and_authorised(client, http):
    browser = Browser()
    public_key = client.get("/api/notifications/webpush/key").json()["public_key"]
    channel = subscribe(client, browser)
    client.put("/api/notifications/settings", json={"enabled": True, "app_url": "https://journal.example.com"})
    with client.app.state.session_factory() as db:
        settings = notify.account.get_settings(db)
        note = notify.Note("strategy_alert", "XAUUSD long", "Entry 2650 · SL 2645 · TP 2665", "/#/alerts?id=7", "high")
        row = notify.send(db, settings, note)
        assert row.status == "sent"

    call = http.calls[0]
    assert call["url"] == ENDPOINT
    headers = call["headers"]
    assert headers["Content-Encoding"] == "aes128gcm"
    assert headers["Content-Type"] == "application/octet-stream"
    assert headers["Urgency"] == "high" and int(headers["TTL"]) > 0
    assert headers["Authorization"].startswith("vapid t=") and headers["Authorization"].endswith(f"k={public_key}")
    payload = json.loads(browser.decrypt(call["body"]))
    assert payload["title"] == "XAUUSD long"
    assert payload["url"] == "/#/alerts?id=7"  # in-app path: the click opens the subscribing origin
    assert payload["priority"] == "high" and payload["event"] == "strategy_alert"
    listed = client.get("/api/notifications/channels").json()[0]
    assert listed["id"] == channel["id"] and listed["last_sent_at"]


def test_webpush_payload_stays_small():
    message = notify_channels.Message(event="news", title="t" * 500, body="é" * 5000, path="/#/premarket")
    payload = notify_channels.webpush_payload(message)
    assert len(payload) <= webpush.MAX_PAYLOAD_BYTES
    assert json.loads(payload)["title"].endswith("…")


@pytest.mark.parametrize("status", [404, 410])
def test_expired_subscription_disables_the_channel(client, http, status):
    browser = Browser()
    client.get("/api/notifications/webpush/key")
    channel = subscribe(client, browser)
    http.responses[ENDPOINT] = httpx.Response(status)
    result = client.post("/api/notifications/test").json()
    assert result["status"] == "failed" and result["results"][0]["error"] == "Subscription expired"
    listed = client.get("/api/notifications/channels").json()[0]
    assert listed["id"] == channel["id"]
    assert listed["enabled"] is False and listed["last_error"] == "Subscription expired"
    assert client.post("/api/notifications/test").json()["status"] == "skipped"


def test_other_push_errors_keep_the_channel(client, http):
    browser = Browser()
    client.get("/api/notifications/webpush/key")
    subscribe(client, browser)
    http.responses[ENDPOINT] = httpx.Response(429)
    result = client.post("/api/notifications/test").json()
    assert result["results"][0]["error"].startswith("HTTP 429")
    assert client.get("/api/notifications/channels").json()[0]["enabled"] is True


def test_device_labels():
    assert (
        notify.device_label(
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) Version/17.4 Mobile/15E148 Safari/604.1"
        )
        == "Safari on iPhone"
    )
    assert (
        notify.device_label("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/129.0 Safari/537.36 Edg/129.0")
        == "Edge on Windows"
    )
    assert (
        notify.device_label("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5; rv:131.0) Gecko/20100101 Firefox/131.0")
        == "Firefox on macOS"
    )
    assert notify.device_label("") == "Browser"


# ---------------------------------------------------- service worker, manifest


def test_service_worker_is_served_from_the_root(client):
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert response.headers["Service-Worker-Allowed"] == "/"
    assert "javascript" in response.headers["content-type"]
    for event in ("push", "notificationclick", "pushsubscriptionchange"):
        assert f'addEventListener("{event}"' in response.text


def test_manifest_and_icons(client):
    manifest = client.get("/static/manifest.webmanifest").json()
    assert manifest["start_url"] == "/" and manifest["scope"] == "/" and manifest["display"] == "standalone"
    sizes = {icon["sizes"] for icon in manifest["icons"]}
    assert {"192x192", "512x512"} <= sizes
    for icon in manifest["icons"]:
        response = client.get(icon["src"])
        assert response.status_code == 200 and response.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert (STATIC_DIR / "icons" / "badge-72.png").is_file()
