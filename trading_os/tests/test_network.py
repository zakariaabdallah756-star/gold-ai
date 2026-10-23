"""Network & access: host names (LAN names, tunnels, wildcards), cross-origin rules, the Network & access API,
the optional remote-access password and the start-up banner. Nothing here touches the real network."""

import dataclasses
import hashlib
import json
import logging

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from trading_os import cli
from trading_os.app import create_app
from trading_os.config import load_config
from trading_os.services import i18n, network

from .conftest import update_settings

LOCAL = ("127.0.0.1", 50000)
LAN_PEER = ("192.168.1.50", 50000)
PASSWORD = "correct horse battery"
BAR = {"time": "2025-10-20T09:00:00", "open": 2650.0, "high": 2652.0, "low": 2649.0, "close": 2651.0}
CANDLES = {"symbol": "XAUUSD", "timeframe": "M15", "bars": [BAR]}


def make_app(tmp_path, monkeypatch, env=None, **config_changes):
    if env is not None:
        monkeypatch.setenv("TRADING_OS_ALLOWED_HOSTS", env)
    config = dataclasses.replace(load_config(tmp_path / "data"), background_jobs=False, **config_changes)
    return create_app(config)


def local_client(app, base_url="http://localhost:8765", **kwargs):
    return TestClient(app, base_url=base_url, client=LOCAL, **kwargs)


def remote_client(app, base_url="http://192.168.1.20:8765", **kwargs):
    return TestClient(app, base_url=base_url, client=LAN_PEER, **kwargs)


def set_password(app, password=PASSWORD):
    response = local_client(app).put("/api/access/password", json={"password": password})
    assert response.status_code == 200, response.text
    return response.json()


def fake_request(client=LOCAL, host="localhost:8765", **headers):
    raw = [(b"host", host.encode())] + [(k.replace("_", "-").encode(), v.encode()) for k, v in headers.items()]
    scope = {"type": "http", "method": "GET", "path": "/", "query_string": b"", "headers": raw, "client": client}
    scope.update({"scheme": "http", "server": ("localhost", 8765), "root_path": ""})
    return Request(scope)


# --------------------------------------------------------------------------- pure helpers


def test_entries_are_normalised_like_browsers_send_them():
    assert network.normalize_pattern(" https://My.Ngrok-Free.App:443/path?q=1 ") == "my.ngrok-free.app"
    assert network.normalize_pattern("Trading.Lan.:8765") == "trading.lan"
    assert network.normalize_pattern("user@mypc.local") == "mypc.local"
    assert network.normalize_pattern("*.TryCloudflare.com") == "*.trycloudflare.com"
    assert network.normalize_pattern("[fe80::1]:8765") == "fe80::1"
    assert network.normalize_pattern("bücher.lan") == "xn--bcher-kva.lan"
    assert network.split_entries("a.lan, b.lan;c.lan\n d.lan") == ["a.lan", "b.lan", "c.lan", "d.lan"]
    assert network.hostname("LOCALHOST.:8765") == "localhost"
    assert network.hostname("[::1]:8765") == "::1" and network.host_port("[::1]:8765") == 8765
    assert network.host_port("example.com") is None and network.host_port("example.com:443") == 443


def test_patterns_are_validated():
    for good in ("*.local", "*.ts.net", "*.trycloudflare.com", "trading.lan", "*.home.arpa", "my_pc", "localhost"):
        assert network.pattern_problem(good) is None, good
    problems = {
        "": "Empty host name",
        "*": "A lone * would allow every host name",
        "*.com": "*.com is too broad",
        "a*.b.com": "A * can only replace",
        "*.*.example.com": "A * can only replace",
        "192.168.1.20": "IP addresses are always allowed",
        "fe80::1": "IP addresses are always allowed",
        "bad name.lan": "Not a valid host name",
        "a..b": "Not a valid host name",
        "-x.lan": "Not a valid host name",
        "x" * 254: "at most 253 characters",
    }
    for pattern, message in problems.items():
        assert message in (network.pattern_problem(pattern) or ""), pattern
    with i18n.using("it"):
        assert network.pattern_problem("*") == "Un * da solo consentirebbe qualsiasi nome host"
    valid, ignored = network.parse_patterns(["Trading.LAN, *.com", "trading.lan", "https://x.ts.net/", "10.0.0.5"])
    assert valid == ("trading.lan", "x.ts.net") and ignored == ("*.com", "10.0.0.5")


def test_wildcards_match_names_below_the_domain_only():
    assert network.matches("abc.trycloudflare.com", "*.trycloudflare.com")
    assert network.matches("a.b.trycloudflare.com", "*.trycloudflare.com")
    assert not network.matches("trycloudflare.com", "*.trycloudflare.com")
    assert not network.matches("evil-trycloudflare.com", "*.trycloudflare.com")
    assert not network.matches("trycloudflare.com.evil.example", "*.trycloudflare.com")
    assert network.matches("trading.lan", "trading.lan") and not network.matches("x.trading.lan", "trading.lan")


def test_policy_rules_and_trusted_origins():
    policy = network.NetworkPolicy(
        env_patterns=("*.trycloudflare.com", "my-tunnel.example"),
        saved_patterns=("trading.lan", "*.ts.net"),
        machine_names=("tradingpc", "tradingpc.local"),
    )
    assert policy.rule_for("localhost:8765") == ("localhost", "localhost")
    assert policy.rule_for("app.localhost") == ("loopback", "*.localhost")
    assert policy.rule_for("[::1]:8765") == ("ip", "::1")
    assert policy.rule_for("TradingPC.local.") == ("machine", "tradingpc.local")
    assert policy.rule_for("abc.trycloudflare.com") == ("env", "*.trycloudflare.com")
    assert policy.rule_for("pc.tail1.ts.net:443") == ("saved", "*.ts.net")
    assert policy.rule_for("") is None and policy.rule_for("evil.localhost.example") is None
    assert policy.trusted_origin_names() == {"my-tunnel.example", "trading.lan"}  # never a wildcard
    assert not policy.password_required


def test_lan_addresses_keep_private_networks_only():
    raw = ["127.0.1.1", "192.168.1.20", "10.0.0.5", "8.8.8.8", "169.254.3.4", "100.101.102.103", "fe80::1%eth0"]
    raw += ["fd12::5", "2001:4860::8888", "192.168.1.20", "::ffff:192.168.1.21", "not-an-ip", "0.0.0.0"]
    assert network.lan_only(raw) == ["192.168.1.20", "10.0.0.5", "100.101.102.103", "192.168.1.21", "fd12::5"]


def test_lan_lookup_is_cached(monkeypatch):
    calls = []
    monkeypatch.undo()  # this test exercises the real cache (with a fake lookup)
    monkeypatch.setattr(network, "_discover_lan_addresses", lambda: calls.append(1) or ("192.168.1.20",))
    monkeypatch.setattr(network, "_lan_cache", None)
    assert network.lan_addresses() == ("192.168.1.20",) and network.lan_addresses() == ("192.168.1.20",)
    assert len(calls) == 1


def test_urls_bracket_ipv6_and_use_localhost_on_this_computer():
    assert cli.display_urls("127.0.0.1", 8765) == ("http://localhost:8765", [])
    assert cli.display_urls("::1", 8765) == ("http://localhost:8765", [])
    assert cli.display_urls("0.0.0.0", 8765, ["192.168.1.20"]) == (
        "http://localhost:8765",
        ["http://192.168.1.20:8765"],
    )
    assert cli.display_urls("::", 9000, ["fd00::5"]) == ("http://localhost:9000", ["http://[fd00::5]:9000"])
    assert cli.display_urls("192.168.1.20", 8765) == ("http://192.168.1.20:8765", ["http://192.168.1.20:8765"])
    assert cli.display_urls("fe80::1", 8765)[0] == "http://[fe80::1]:8765"


def test_this_computer_means_loopback_localhost_and_no_proxy():
    assert network.is_local_request(fake_request())
    assert network.is_local_request(fake_request(client=("::1", 1), host="[::1]:8765"))
    assert network.is_local_request(fake_request(client=("::ffff:127.0.0.1", 1), host="app.localhost"))
    assert not network.is_local_request(fake_request(client=("testclient", 1)))
    assert not network.is_local_request(fake_request(client=LAN_PEER))
    assert not network.is_local_request(fake_request(host="192.168.1.20:8765"))  # LAN IP from this computer
    assert not network.is_local_request(fake_request(host="tradingpc.local"))
    for header in ("x_forwarded_for", "cf_connecting_ip", "tailscale_user_login", "forwarded", "via"):
        assert not network.is_local_request(fake_request(**{header: "1.2.3.4"})), header


def test_a_specific_bind_address_counts_as_this_computer_from_itself(tmp_path, monkeypatch):
    # --host 192.168.1.5: nothing listens on loopback, the browser here connects from that address to it.
    app = make_app(tmp_path, monkeypatch, bind_host="192.168.1.5")
    here = TestClient(app, base_url="http://192.168.1.5:8765", client=("192.168.1.5", 50000))
    assert here.get("/api/network").json()["access"]["can_manage"] is True
    assert here.put("/api/access/password", json={"password": PASSWORD}).status_code == 200
    assert here.get("/api/settings").status_code == 200  # no sign-in needed here
    other = TestClient(app, base_url="http://192.168.1.5:8765", client=LAN_PEER)
    assert other.get("/api/settings").status_code == 401
    proxied = TestClient(app, base_url="http://192.168.1.5:8765", client=("192.168.1.5", 50000))
    assert proxied.get("/api/settings", headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 401
    # Listening everywhere, the own LAN address is not "this computer": localhost is.
    request = fake_request(client=("192.168.1.5", 1), host="192.168.1.5:8765")
    assert not network.is_local_request(request)


def test_forwarding_headers_are_read_only_from_a_proxy_on_this_computer():
    proxied = fake_request(
        host="localhost",
        x_forwarded_host="victim.trycloudflare.com",
        x_forwarded_proto="https",
        x_forwarded_for="9.9.9.9, 1.2.3.4",
    )
    assert network.effective_host(proxied) == "victim.trycloudflare.com"
    assert network.request_scheme(proxied) == "https" and network.client_ip(proxied) == "1.2.3.4"
    assert network.client_ip(fake_request(cf_connecting_ip="5.6.7.8", x_forwarded_for="1.2.3.4")) == "5.6.7.8"
    spoofed = fake_request(
        client=LAN_PEER, host="192.168.1.20", x_forwarded_host="x.example", x_forwarded_proto="https"
    )
    assert network.effective_host(spoofed) == "192.168.1.20"
    assert network.request_scheme(spoofed) == "http" and network.client_ip(spoofed) == "192.168.1.50"


def test_cross_origin_rules():
    trusted = frozenset({"my-tunnel.example"})
    same = [
        ("http://localhost:8765", "localhost:8765"),
        ("http://localhost", "localhost"),
        ("http://localhost:80", "localhost"),
        ("https://abc.example", "abc.example"),
        ("https://abc.example", "abc.example:443"),
        ("https://abc.example:443", "abc.example"),
        ("http://[::1]:8765", "[::1]:8765"),
        ("http://Localhost.:8765", "localhost:8765"),
    ]
    for origin, host in same:
        assert not network.cross_origin({"origin": origin}, host), (origin, host)
    cross = [
        ("http://localhost:3000", "localhost:8765"),
        ("https://abc.example", "abc.example:80"),
        ("http://abc.example:8443", "abc.example"),
        ("https://attacker.trycloudflare.com", "victim.trycloudflare.com"),
        ("null", "localhost:8765"),
        ("chrome-extension://abc", "abc"),
    ]
    for origin, host in cross:
        assert network.cross_origin({"origin": origin}, host), (origin, host)
    assert not network.cross_origin({}, "localhost")  # scripts and webhooks send no Origin
    assert network.cross_origin({"sec-fetch-site": "cross-site"}, "localhost")
    assert not network.cross_origin(
        {"origin": "https://my-tunnel.example", "sec-fetch-site": "cross-site"}, "localhost", trusted
    )


def test_passwords_are_hashed_with_scrypt_and_verified(monkeypatch):
    stored = network.hash_password(PASSWORD)
    assert stored.startswith("scrypt$16384$8$1$") and PASSWORD not in stored
    assert network.verify_password(PASSWORD, stored) and not network.verify_password("wrong", stored)
    assert network.hash_password(PASSWORD) != stored  # salted
    for broken in ("", "scrypt$1$2", "md5$x$y", "scrypt$99999999$8$1$AA$AA", "pbkdf2_sha256$x$AA$AA"):
        assert not network.verify_password(PASSWORD, broken)
    monkeypatch.delattr(hashlib, "scrypt")  # a Python built without scrypt falls back to PBKDF2
    fallback = network.hash_password("pässwörd-123")
    assert fallback.startswith("pbkdf2_sha256$") and network.verify_password("pässwörd-123", fallback)
    assert not network.verify_password(PASSWORD, stored)  # scrypt hashes cannot be checked without scrypt


def test_session_tokens_expire_and_cannot_be_forged(monkeypatch):
    token = network.make_session_token("s" * 64)
    expiry, _nonce = network.read_session_token("s" * 64, token)
    assert expiry > network._now() + 29 * 86400
    assert network.read_session_token("t" * 64, token) is None
    assert network.read_session_token("", token) is None
    tampered = str(int(token.split(".")[0]) + 999) + token[token.index(".") :]
    assert network.read_session_token("s" * 64, tampered) is None
    for junk in (None, "", "a.b", "x.y.z", "ü.ü.ü", "1" * 300):
        assert network.read_session_token("s" * 64, junk) is None
    later = network._now() + 31 * 86400
    monkeypatch.setattr(network, "_now", lambda: later)
    assert network.read_session_token("s" * 64, token) is None


def test_login_limiter_per_address_and_in_total():
    clock = [1000.0]
    limiter = network.LoginLimiter(per_ip=5, total=8, window=300, clock=lambda: clock[0])
    for _ in range(5):
        assert limiter.retry_after("1.1.1.1") == 0
        limiter.failed("1.1.1.1")
    assert limiter.retry_after("1.1.1.1") == 300 and limiter.retry_after("2.2.2.2") == 0
    clock[0] += 100
    assert limiter.retry_after("1.1.1.1") == 200
    for _ in range(3):
        limiter.failed("2.2.2.2")
    assert limiter.retry_after("3.3.3.3") == 200  # the total limit holds for every address
    clock[0] += 201
    assert limiter.retry_after("1.1.1.1") == 0 and limiter.retry_after("3.3.3.3") == 0
    limiter.failed("4.4.4.4")
    limiter.succeeded("4.4.4.4")
    assert "4.4.4.4" not in limiter._failures


# --------------------------------------------------------------------------- host names through the app


def test_local_names_machine_name_and_trailing_dots_are_served(client):
    for host in (
        "localhost.",
        "LOCALHOST.:8765",
        "app.localhost",
        "app.localhost:8765",
        "tradingpc",
        "TradingPC.local.",
    ):
        assert client.get("/api/plan", headers={"host": host}).status_code == 200, host
    for host in ("evil.localhost.example", "localhost..", "tradingpc.evil.example", ""):
        assert client.get("/api/plan", headers={"host": host}).status_code == 400, host


def test_wildcard_and_normalised_env_entries(tmp_path, monkeypatch):
    env = "*.trycloudflare.com, https://My.Ngrok-Free.App/, trading.lan:8765 *.com;10.0.0.5"
    app = make_app(tmp_path, monkeypatch, env=env)
    assert app.state.config.allowed_hosts == ("*.trycloudflare.com", "my.ngrok-free.app", "trading.lan")
    assert app.state.config.ignored_hosts == ("*.com", "10.0.0.5")
    client = TestClient(app, base_url="http://localhost")
    for host, status in {
        "abc.trycloudflare.com": 200,
        "a.b.trycloudflare.com": 200,
        "trycloudflare.com": 400,
        "evil-trycloudflare.com": 400,
        "my.ngrok-free.app": 200,
        "trading.lan:8765": 200,
        "x.com": 400,
    }.items():
        assert client.get("/api/plan", headers={"host": host}).status_code == status, host


def test_wildcards_are_never_trusted_as_origins(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, env="*.trycloudflare.com")
    client = TestClient(app, base_url="https://victim.trycloudflare.com")
    accept = "/api/disclaimer/accept"
    assert client.post(accept, headers={"origin": "https://attacker.trycloudflare.com"}).status_code == 403
    attacker = {
        "origin": "https://attacker.trycloudflare.com",
        "host": "localhost:8765",
        "sec-fetch-site": "cross-site",
    }
    assert client.post(accept, headers=attacker).status_code == 403
    assert client.post(accept, headers={"origin": "https://victim.trycloudflare.com"}).status_code == 200


def test_a_local_proxy_that_rewrites_host_keeps_the_visitors_name(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, env="*.trycloudflare.com")
    proxy = TestClient(app, base_url="http://localhost:8765", client=LOCAL)
    tunnel = {"x-forwarded-host": "victim.trycloudflare.com", "x-forwarded-for": "1.2.3.4"}
    origin = {"origin": "https://victim.trycloudflare.com"}
    assert proxy.post("/api/disclaimer/accept", headers={**tunnel, **origin}).status_code == 200
    assert proxy.post("/api/disclaimer/accept", headers=origin).status_code == 403  # no X-Forwarded-Host
    unknown = proxy.get("/api/plan", headers={"x-forwarded-host": "evil.example"})
    assert unknown.status_code == 400 and unknown.json()["host"] == "evil.example"
    # A device on the network cannot pretend to be a proxy.
    lan = remote_client(app)
    assert lan.post("/api/disclaimer/accept", headers={**tunnel, **origin}).status_code == 403
    assert lan.get("/api/plan", headers={"x-forwarded-host": "evil.example"}).status_code == 200


def test_default_ports_count_as_the_same_origin(client):
    lan = {"host": "192.168.1.20"}
    assert client.post("/api/disclaimer/accept", headers={**lan, "origin": "http://192.168.1.20:80"}).status_code == 200
    assert client.post("/api/disclaimer/accept", headers={**lan, "origin": "https://192.168.1.20"}).status_code == 200
    on_80 = {"host": "192.168.1.20:80", "origin": "https://192.168.1.20"}
    assert client.post("/api/disclaimer/accept", headers=on_80).status_code == 403


def test_refused_host_page_for_browsers_and_json_for_the_api(client, caplog):
    caplog.set_level(logging.WARNING, logger="trading_os")
    page = client.get("/", headers={"host": "evil.example", "accept": "text/html,application/xhtml+xml"})
    assert page.status_code == 400 and page.headers["content-type"].startswith("text/html")
    text = page.text
    for expected in ("evil.example", "TRADING_OS_ALLOWED_HOSTS", "Network &amp; access", "http://localhost:8765"):
        assert expected in text
    assert "address bar, not the search box" in text and "barra degli indirizzi" in text  # English and Italian
    assert "set TRADING_OS_ALLOWED_HOSTS=evil.example" in text and "--allow-host evil.example" in text
    assert "<script" not in text and "<form" not in text and "<button" not in text
    assert text.index("This address is not allowed yet") < text.index("Questo indirizzo non è ancora consentito")

    api = client.get("/api/plan", headers={"host": "evil.example:8765"})
    assert api.status_code == 400 and api.headers["content-type"] == "application/json"
    body = api.json()
    assert body["code"] == "host_not_allowed" and body["host"] == "evil.example"
    assert "TRADING_OS_ALLOWED_HOSTS" in body["detail"] and "'evil.example'" in body["detail"]
    posted = client.post("/api/killswitch", json={"hours": 4}, headers={"host": "evil.example", "accept": "text/html"})
    assert posted.json()["code"] == "host_not_allowed"  # only GET navigations get the page
    client.get("/api/plan", headers={"host": "other.example"})
    refused = [r.getMessage() for r in caplog.records if r.name == "trading_os"]
    assert len(refused) == 2 and "'evil.example'" in refused[0] and "'other.example'" in refused[1]
    assert "http://localhost:8765" in refused[0]


def test_refused_host_texts_follow_the_language(client):
    update_settings(client, language="it")
    body = client.get("/api/plan", headers={"host": "evil.example"}).json()
    assert body["detail"].startswith("Nome host sconosciuto 'evil.example'")
    page = client.get("/", headers={"host": "<b>x</b>.example", "accept": "text/html"}).text
    assert page.index("Questo indirizzo non è ancora consentito") < page.index("This address is not allowed yet")
    assert "&lt;b&gt;x&lt;/b&gt;.example" in page and "<b>x</b>" not in page
    assert "my-name.example" in page  # an invalid name is never pasted into the commands


# --------------------------------------------------------------------------- Network & access API


def test_saved_host_names_are_local_only_and_apply_at_once(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    local, lan = local_client(app), remote_client(app)
    assert lan.get("/api/plan", headers={"host": "trading.lan"}).status_code == 400

    refused = lan.put("/api/network", json={"allowed_hosts": ["trading.lan"]})
    assert refused.status_code == 403 and refused.json()["code"] == "local_only"
    assert "http://localhost:8765" in refused.json()["detail"]
    via_proxy = local.put(
        "/api/network", json={"allowed_hosts": ["trading.lan"]}, headers={"x-forwarded-for": "1.2.3.4"}
    )
    assert via_proxy.status_code == 403
    assert (
        TestClient(app, base_url="http://localhost").put("/api/network", json={"allowed_hosts": []}).status_code == 403
    )

    saved = local.put("/api/network", json={"allowed_hosts": ["Trading.Lan.", "https://*.ts.net/", "trading.lan"]})
    assert saved.status_code == 200, saved.text
    assert saved.json()["allowed_hosts"]["saved"] == ["trading.lan", "*.ts.net"]
    assert lan.get("/api/plan", headers={"host": "trading.lan"}).status_code == 200
    status = lan.get("/api/network", headers={"host": "pc.tail1234.ts.net"}).json()
    assert status["this_request"]["allowed_by"] == {"source": "saved", "pattern": "*.ts.net"}
    # Exact saved names are trusted origins for proxies that rewrite Host (wildcards are not).
    proxied = {"host": "localhost:8765", "origin": "https://trading.lan", "sec-fetch-site": "cross-site"}
    assert lan.post("/api/disclaimer/accept", headers=proxied).status_code == 200
    proxied["origin"] = "https://pc.tail1234.ts.net"
    assert lan.post("/api/disclaimer/accept", headers=proxied).status_code == 403

    bad = local.put("/api/network", json={"allowed_hosts": ["ok.lan", "*.com", "a*.b.com", "10.0.0.1"]})
    assert bad.status_code == 422
    for part in ("*.com:", "a*.b.com:", "10.0.0.1: IP addresses are always allowed"):
        assert part in bad.json()["detail"]
    too_many = local.put("/api/network", json={"allowed_hosts": [f"pc{i}.lan" for i in range(51)]})
    assert too_many.status_code == 422 and "At most 50" in too_many.json()["detail"]
    assert local.get("/api/network").json()["allowed_hosts"]["saved"] == ["trading.lan", "*.ts.net"]

    assert local.put("/api/network", json={"allowed_hosts": []}).status_code == 200
    assert lan.get("/api/plan", headers={"host": "trading.lan"}).status_code == 400


def test_saved_host_names_survive_a_restart(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    assert local_client(app).put("/api/network", json={"allowed_hosts": ["trading.lan"]}).status_code == 200
    again = make_app(tmp_path, monkeypatch)
    assert TestClient(again).get("/api/plan", headers={"host": "trading.lan"}).status_code == 200


def test_network_status_reports_this_connection_and_lan_urls(tmp_path, monkeypatch):
    monkeypatch.setattr(network, "lan_addresses", lambda: ("192.168.1.20", "fd00::5"))
    app = make_app(tmp_path, monkeypatch, env="trading.lan, *.com", bind_host="0.0.0.0", port=9000)
    status = local_client(app, base_url="http://localhost:9000").get("/api/network").json()
    assert status["server"] == {
        "bind_host": "0.0.0.0",
        "port": 9000,
        "listening_on_lan": True,
        "local_url": "http://localhost:9000",
        "lan_urls": ["http://192.168.1.20:9000", "http://[fd00::5]:9000"],
    }
    me = status["this_request"]
    assert me["local"] and me["allowed_by"]["source"] == "localhost" and me["secure_context"]
    assert me["local_reasons"] == {
        "peer": "127.0.0.1",
        "peer_loopback": True,
        "host_local": True,
        "forwarding_headers": [],
    }
    hosts = status["allowed_hosts"]
    assert hosts["builtin"] == ["localhost", "*.localhost"] and hosts["machine"] == ["tradingpc", "tradingpc.local"]
    assert hosts["env"] == ["trading.lan"] and hosts["env_ignored"][0]["entry"] == "*.com"
    assert "too broad" in hosts["env_ignored"][0]["problem"] and hosts["saved"] == [] and hosts["max"] == 50
    assert status["access"] == {"password_set": False, "session_days": 30, "can_manage": True}
    assert status["push_token_set"] is False and status["app_url"] == ""

    remote = remote_client(app, base_url="http://192.168.1.20:9000").get("/api/network").json()["this_request"]
    assert remote["allowed_by"] == {"source": "ip", "pattern": "192.168.1.20"} and not remote["local"]
    assert remote["secure_context"] is False and remote["local_reasons"]["peer"] == "192.168.1.50"
    tunnel = (
        local_client(app)
        .get("/api/network", headers={"host": "trading.lan", "x-forwarded-proto": "https", "cf-ray": "1"})
        .json()["this_request"]
    )
    assert tunnel["scheme"] == "https" and tunnel["secure_context"] and not tunnel["local"]
    assert tunnel["local_reasons"]["forwarding_headers"] == ["x-forwarded-proto", "cf-ray"]


def test_loopback_server_does_not_look_up_lan_addresses(client, monkeypatch):
    def boom():
        raise AssertionError("LAN lookup while listening on localhost only")

    monkeypatch.setattr(network, "lan_addresses", boom)
    server = client.get("/api/network").json()["server"]
    assert server["listening_on_lan"] is False and server["lan_urls"] == [] and server["port"] == 8765


# --------------------------------------------------------------------------- remote-access password


def test_without_a_password_nothing_changes(tmp_path, monkeypatch):
    lan = remote_client(make_app(tmp_path, monkeypatch))
    assert lan.get("/api/plan").status_code == 200
    assert lan.get("/api/docs").status_code == 200
    assert lan.get("/api/access/status").json() == {
        "password_required": False,
        "authenticated": False,
        "local": False,
        "needs_login": False,
        "language": "en",
    }
    assert lan.post("/api/access/login", json={"password": "anything"}).status_code == 204


def test_password_gate_for_other_devices(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    local, lan = local_client(app), remote_client(app)
    short = local.put("/api/access/password", json={"password": "short"})
    assert short.status_code == 422 and "at least 8" in short.json()["detail"]
    assert set_password(app)["access"]["password_set"] is True
    with app.state.session_factory() as db:
        from trading_os.models import AccountSettings

        row = db.get(AccountSettings, 1)
        assert row.remote_password_hash.startswith("scrypt$") and len(row.remote_session_secret) == 64

    for path in ("/api/plan", "/uploads/x.png", "/api/docs", "/api/openapi.json", "/api/network", "/api/nothing"):
        refused = lan.get(path)
        assert refused.status_code == 401 and refused.json()["code"] == "login_required", path
    assert lan.post("/api/killswitch", json={"hours": 4}).json()["code"] == "login_required"
    for path in ("/", "/static/js/main.js", "/sw.js", "/api/i18n", "/api/i18n/it", "/static/manifest.webmanifest"):
        assert lan.get(path).status_code == 200, path
    assert lan.get("/api/access/status").json()["needs_login"] is True
    # The computer running the app never needs the password.
    assert local.get("/api/plan").status_code == 200
    mine = local.get("/api/access/status").json()
    assert mine["local"] is True and mine["needs_login"] is False and mine["password_required"] is True

    wrong = lan.post("/api/access/login", json={"password": "nope nope"})
    assert wrong.status_code == 401 and wrong.json()["code"] == "wrong_password"
    ok = lan.post("/api/access/login", json={"password": PASSWORD})
    assert ok.status_code == 204
    cookie = ok.headers["set-cookie"]
    for part in ("tos_session_8765=", "HttpOnly", "SameSite=strict", "Path=/", "Max-Age=2592000"):
        assert part.lower() in cookie.lower(), part
    assert "secure" not in cookie.lower()
    assert lan.get("/api/plan").status_code == 200
    assert lan.get("/api/access/status").json()["authenticated"] is True
    # Signed in is not "this computer": remote-access settings stay local-only.
    for method, path, body in (
        ("put", "/api/access/password", {"password": ""}),
        ("post", "/api/access/signout-all", None),
        ("put", "/api/network", {"allowed_hosts": ["x.lan"]}),
    ):
        response = getattr(lan, method)(path, json=body) if body is not None else getattr(lan, method)(path)
        assert response.status_code == 403 and response.json()["code"] == "local_only", path


def test_sign_out_password_change_and_sign_out_all_end_sessions(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    set_password(app)
    local = local_client(app)
    cookie_name = network.session_cookie_name(8765)

    def sign_in():
        device = remote_client(app)
        assert device.post("/api/access/login", json={"password": PASSWORD}).status_code == 204
        return device, device.cookies[cookie_name]

    def works(token):
        return remote_client(app, cookies={cookie_name: token}).get("/api/plan").status_code == 200

    device, token = sign_in()
    assert works(token)
    assert device.post("/api/access/logout").status_code == 204
    assert cookie_name not in device.cookies and not works(token)

    _device, token = sign_in()
    other, other_token = sign_in()
    assert local.post("/api/access/signout-all").status_code == 200
    assert not works(token) and not works(other_token)

    _device, token = sign_in()
    set_password(app, "a brand new password")
    assert not works(token)
    assert remote_client(app).post("/api/access/login", json={"password": PASSWORD}).status_code == 401

    device = remote_client(app)
    assert device.post("/api/access/login", json={"password": "a brand new password"}).status_code == 204
    token = device.cookies[cookie_name]
    assert works(token) and not works(token[:-2] + "xx")
    real_now = network._now
    monkeypatch.setattr(network, "_now", lambda: real_now() + 31 * 86400)
    assert not works(token)

    monkeypatch.setattr(network, "_now", real_now)
    assert local.put("/api/access/password", json={"password": ""}).json()["access"]["password_set"] is False
    assert remote_client(app).get("/api/plan").status_code == 200


def test_secure_cookie_only_over_https(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, env="*.trycloudflare.com")
    set_password(app)
    https = remote_client(app, base_url="https://192.168.1.20")
    assert "secure" in https.post("/api/access/login", json={"password": PASSWORD}).headers["set-cookie"].lower()
    tunnel = {"host": "abc.trycloudflare.com", "x-forwarded-proto": "https", "x-forwarded-for": "1.2.3.4"}
    through_tunnel = local_client(app).post("/api/access/login", json={"password": PASSWORD}, headers=tunnel)
    assert "secure" in through_tunnel.headers["set-cookie"].lower()
    spoofed = remote_client(app).post(
        "/api/access/login", json={"password": PASSWORD}, headers={"x-forwarded-proto": "https"}
    )
    assert "secure" not in spoofed.headers["set-cookie"].lower()
    # A tunnel is never "this computer", even though the connection comes from 127.0.0.1.
    assert local_client(app).get("/api/plan", headers=tunnel).json()["code"] == "login_required"


def test_wrong_passwords_are_rate_limited(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    set_password(app)
    lan = remote_client(app)
    for _ in range(5):
        assert lan.post("/api/access/login", json={"password": "guess guess"}).status_code == 401
    blocked = lan.post("/api/access/login", json={"password": PASSWORD})
    assert blocked.status_code == 429 and blocked.json()["code"] == "too_many_attempts"
    assert int(blocked.headers["retry-after"]) > 0 and "Try again in 5 min" in blocked.json()["detail"]
    # Visitors behind a tunnel are counted by their own address, not the tunnel's 127.0.0.1.
    proxied = local_client(app, headers={"x-forwarded-for": "8.8.4.4"})
    assert proxied.post("/api/access/login", json={"password": PASSWORD}).status_code == 204


def test_cross_site_sign_in_is_refused(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    set_password(app)
    response = remote_client(app).post(
        "/api/access/login", json={"password": PASSWORD}, headers={"origin": "http://evil.example"}
    )
    assert response.status_code == 403 and "set-cookie" not in response.headers


def test_candles_from_other_devices_need_a_push_token_when_a_password_is_set(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    set_password(app)
    lan = remote_client(app)
    missing = lan.post("/api/alerts/candles", json=CANDLES)
    assert missing.status_code == 401 and missing.json()["code"] == "push_token_required"
    assert "push token" in missing.json()["detail"]
    assert local_client(app).post("/api/alerts/candles", json=CANDLES).status_code == 200
    local_client(app).put("/api/alerts/providers/push/key", json={"api_key": "tok-123"})
    assert lan.post("/api/alerts/candles", json={**CANDLES, "token": "tok-123"}).status_code == 200
    wrong = lan.post("/api/alerts/candles", json={**CANDLES, "token": "nope"})
    assert wrong.status_code == 401 and wrong.json()["detail"] == "Invalid or missing push token"
    assert local_client(app).get("/api/network").json()["push_token_set"] is True


def test_network_settings_never_leave_the_machine_and_survive_a_restore(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    local = local_client(app)
    assert local.put("/api/network", json={"allowed_hosts": ["trading.lan"]}).status_code == 200
    set_password(app)
    exported = local.get("/api/export/backup.json")
    for secret in ("allowed_hosts", "remote_password_hash", "remote_session_secret", "trading.lan", "scrypt$"):
        assert secret not in exported.text
    restored = local.post("/api/import", files={"file": ("b.json", exported.content, "application/json")})
    assert restored.status_code == 200, restored.text
    status = local.get("/api/network").json()
    assert status["allowed_hosts"]["saved"] == ["trading.lan"] and status["access"]["password_set"] is True
    assert remote_client(app).get("/api/plan").json()["code"] == "login_required"
    assert remote_client(app).post("/api/access/login", json={"password": PASSWORD}).status_code == 204


def test_new_settings_columns_default_to_open_access(client):
    with client.app.state.session_factory() as db:
        from trading_os.models import AccountSettings

        row = db.get(AccountSettings, 1)
        assert (row.allowed_hosts, row.remote_password_hash, row.remote_session_secret) == ([], "", "")


def test_access_status_and_gate_texts_follow_the_language(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    local = local_client(app)
    update_settings(local, language="it")
    set_password(app)
    lan = remote_client(app)
    assert lan.get("/api/access/status").json()["language"] == "it"
    assert lan.get("/api/plan").json()["detail"].startswith("Accedi con la password")
    assert lan.post("/api/access/login", json={"password": "x"}).json()["detail"] == "Password errata"


# --------------------------------------------------------------------------- command line


def test_cli_flags():
    assert cli.parse_args([]).host == "127.0.0.1" and cli.parse_args(["--lan"]).host == "0.0.0.0"
    assert cli.parse_args(["--lan", "--host", "::"]).host == "::"
    assert cli.parse_args(["--host", "192.168.1.20"]).host == "192.168.1.20"
    args = cli.parse_args(["--allow-host", "trading.lan", "--allow-host", "*.trycloudflare.com"])
    assert args.allow_host == ["trading.lan", "*.trycloudflare.com"]
    with pytest.raises(SystemExit):
        cli.parse_args(["--lan", "--host", "127.0.0.1"])


def test_banner_hints():
    assert cli.network_lines("127.0.0.1", 8765) == [
        "Tip: type http://localhost:8765 in the browser address bar, not the search box."
    ]
    lines = cli.network_lines("0.0.0.0", 8765, ["192.168.1.20"], ignored=["*.com"])
    assert "On your network: http://192.168.1.20:8765" in lines
    assert "If Windows Firewall asks, allow Python on Private networks." in lines
    assert any(line.startswith("Warning: anyone on your network") for line in lines)
    assert any(line.startswith("Ignored allowed host name '*.com': *.com is too broad") for line in lines)
    protected = cli.network_lines("0.0.0.0", 8765, [], password_set=True)
    assert not any(line.startswith("Warning") for line in protected)
    assert any(line.startswith("No network address found") for line in protected)
    with i18n.using("it"):
        assert cli.network_lines("::1", 8765)[0].startswith("Suggerimento: scrivi http://localhost:8765")


@pytest.mark.filterwarnings("ignore::PendingDeprecationWarning")  # python-multipart's import name
def test_cli_passes_bind_address_port_and_extra_hosts_to_the_app(tmp_path, monkeypatch, capsys):
    import uvicorn

    started = {}
    monkeypatch.setenv("TRADING_OS_BACKGROUND_JOBS", "0")
    monkeypatch.setenv("TRADING_OS_ALLOWED_HOSTS", "trading.lan")
    monkeypatch.setattr(network, "lan_addresses", lambda: ("192.168.1.20",))
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: started.update(app=app, **kwargs))
    argv = ["--data-dir", str(tmp_path / "d"), "--lan", "--port", "9123", "--no-browser"]
    cli.main(argv + ["--allow-host", "*.TryCloudflare.com", "--allow-host", "trading.lan,*.com"])
    config = started["app"].state.config
    assert (config.bind_host, config.port) == ("0.0.0.0", 9123)
    assert config.allowed_hosts == ("trading.lan", "*.trycloudflare.com") and config.ignored_hosts == ("*.com",)
    assert started["host"] == "0.0.0.0" and started["port"] == 9123 and started["proxy_headers"] is False
    out = capsys.readouterr().out
    assert "Open        : http://localhost:9123" in out and "On your network: http://192.168.1.20:9123" in out
    assert "Ignored allowed host name '*.com'" in out and "Warning: anyone on your network" in out
    assert json.dumps(config.allowed_hosts)  # plain strings, ready for the API
