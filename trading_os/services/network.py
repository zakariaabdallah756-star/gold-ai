"""Network access: the host names the server answers to, who counts as "this computer", and the optional
remote-access password with its sign-in sessions.

The app has no login by default, so every request passes three checks (``check_request``, called by the
HTTP middleware in ``app.py``):

* **Host name.** Only ``localhost`` (and ``*.localhost``), IP addresses, this computer's own name and the
  names the owner allowed are served: ``TRADING_OS_ALLOWED_HOSTS`` / ``--allow-host`` at start-up, or the
  list saved under Trading Plan > Network & access (no restart). A website therefore cannot reach the app
  through DNS rebinding: its own name is refused. Allowed names may be wildcards such as
  ``*.trycloudflare.com`` for tunnels whose address changes on every run.
* **Cross-origin writes.** A write whose ``Origin`` is another site is refused. Wildcards are never trusted
  as an Origin: another user of the same shared tunnel domain must not pass.
* **Remote-access password** (optional). When one is set, every request that does not come from this
  computer needs a sign-in cookie, except the files the sign-in page itself needs.

"This computer" means: the connection comes from a loopback address, the Host is ``localhost`` (or a
loopback IP) and no proxy or tunnel forwarding headers are present. Only such requests may change the
allowed host names or the password, so the owner can always recover from the machine running the app.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import html
import ipaddress
import logging
import re
import secrets
import socket
import threading
import time
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from math import ceil
from typing import NamedTuple
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

from . import i18n
from .i18n import tr

logger = logging.getLogger("trading_os")

ALL_INTERFACES = ("0.0.0.0", "::", "")
# Single-label suffixes a wildcard may use: home-network names, not public top-level domains.
LAN_SUFFIXES = frozenset({"local", "lan", "home", "internal", "home.arpa", "localdomain"})
MAX_PATTERNS = 50
MAX_NAME_LENGTH = 253
SESSION_DAYS = 30
MIN_PASSWORD = 8
MAX_PASSWORD = 200
LAN_CACHE_SECONDS = 60.0
CANDLES_PATH = "/api/alerts/candles"
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
DEFAULT_PORTS = {"http": 80, "https": 443}
# Headers set by reverse proxies and tunnels (cloudflared, ngrok, Tailscale serve, nginx, ...): a request
# carrying any of them was relayed for someone else and never counts as coming from this computer.
FORWARD_HEADERS = (
    "forwarded",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-forwarded-port",
    "x-forwarded-server",
    "x-real-ip",
    "x-client-ip",
    "true-client-ip",
    "cf-connecting-ip",
    "cf-ray",
    "cf-visitor",
    "fly-client-ip",
    "via",
    "tailscale-user-login",
)
# Paths served without a sign-in: the page shell and what the sign-in form needs.
EXEMPT_PATHS = frozenset({"/", "/sw.js", "/api/access/status", "/api/access/login", "/api/access/logout", "/api/i18n"})
EXEMPT_PREFIXES = ("/static/", "/api/i18n/")
# Tailscale/carrier-grade NAT addresses (100.64.0.0/10) are reachable like LAN addresses.
_SHARED_NETWORK = ipaddress.ip_network("100.64.0.0/10")
_LABEL = re.compile(r"^[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?$")
_SCRYPT = (2**14, 8, 1)  # n, r, p: about 16 MB and a few tens of milliseconds per check
_PBKDF2_ROUNDS = 600_000
_MAX_LOGGED_HOSTS = 200


# --------------------------------------------------------------------------- host names


def hostname(host: str) -> str:
    """The name part of a Host header: lower case, no port, IPv6 unbracketed, one trailing dot removed.

    ``[::1]:8765`` -> ``::1``, ``Localhost.:80`` -> ``localhost``.
    """
    host = (host or "").strip().lower()
    if host.startswith("["):
        name = host[1 : host.find("]")] if "]" in host else host
    elif host.count(":") == 1:
        name = host.rsplit(":", 1)[0]
    else:
        name = host
    return name[:-1] if name.endswith(".") else name


def host_port(host: str) -> int | None:
    """The explicit port of a Host header, if any."""
    host = (host or "").strip()
    if host.startswith("["):
        rest = host[host.find("]") + 1 :] if "]" in host else ""
        port = rest[1:] if rest.startswith(":") else ""
    elif host.count(":") == 1:
        port = host.rsplit(":", 1)[1]
    else:
        port = ""
    return int(port) if port.isdigit() else None


def ip_address(name: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(name)
    except ValueError:
        return None


def _is_loopback_ip(name: str) -> bool:
    ip = ip_address(name)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip is not None and ip.is_loopback


def is_localhost_name(name: str) -> bool:
    """``localhost``, ``*.localhost`` (always this computer in browsers) or a loopback IP address."""
    return name == "localhost" or name.endswith(".localhost") or _is_loopback_ip(name)


def split_entries(text: str) -> list[str]:
    """Entries of a list typed as text: commas, semicolons, spaces and new lines all separate."""
    return [part for part in re.split(r"[\s,;]+", text or "") if part]


def normalize_pattern(text: str) -> str:
    """An allowed-host entry as a bare name: ``https://My.Ngrok-Free.App:443/`` -> ``my.ngrok-free.app``.

    Removes a scheme, user info, path, port and one trailing dot, lower-cases, and encodes international
    names the way browsers send them (``bücher.lan`` -> ``xn--bcher-kva.lan``).
    """
    value = str(text or "").strip().lower()
    if "://" in value:
        value = value.split("://", 1)[1]
    value = re.split(r"[/?#]", value, maxsplit=1)[0]
    value = value.rsplit("@", 1)[-1]
    if value.startswith("["):
        value = value[1 : value.find("]")] if "]" in value else value[1:]
    elif value.count(":") == 1:
        value = value.split(":", 1)[0]
    if value.endswith("."):
        value = value[:-1]
    if not value.isascii():
        try:
            value = ".".join(
                label if label.isascii() else label.encode("idna").decode("ascii") for label in value.split(".")
            )
        except UnicodeError:
            pass  # left as is: pattern_problem() reports it
    return value


def pattern_problem(pattern: str) -> str | None:
    """Why a normalised entry cannot be used as an allowed host name (``None`` when it is fine)."""
    if not pattern:
        return tr("Empty host name")
    if len(pattern) > MAX_NAME_LENGTH:
        return tr("Host names can be at most {max} characters", max=MAX_NAME_LENGTH)
    if ip_address(pattern) is not None:
        return tr("IP addresses are always allowed - no need to add them")
    labels = pattern.split(".")
    if labels[0] == "*":
        labels = labels[1:]
        if not labels:
            return tr("A lone * would allow every host name")
        if len(labels) == 1 and labels[0] not in LAN_SUFFIXES:
            return tr(
                "*.{suffix} is too broad: put at least two names after the *, such as *.example.com",
                suffix=labels[0],
            )
    if any("*" in label for label in labels):
        return tr("A * can only replace the first part of a name, such as *.example.com")
    if not all(_LABEL.match(label) for label in labels):
        return tr("Not a valid host name")
    return None


def parse_patterns(entries: Iterable[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(usable normalised names without duplicates, entries that were ignored as typed)."""
    valid: list[str] = []
    ignored: list[str] = []
    for entry in entries:
        for part in split_entries(entry):
            name = normalize_pattern(part)
            if pattern_problem(name) is not None:
                ignored.append(part)
            elif name not in valid:
                valid.append(name)
    return tuple(valid), tuple(ignored)


def matches(name: str, pattern: str) -> bool:
    """Exact match, or for ``*.example.com`` any name below ``example.com`` (not ``example.com`` itself)."""
    if pattern.startswith("*."):
        return len(name) > len(pattern) - 1 and name.endswith(pattern[1:])
    return name == pattern


@lru_cache(maxsize=1)
def machine_names() -> tuple[str, ...]:
    """This computer's own names (``mypc``, ``mypc.local`` for Bonjour/mDNS), never a DNS lookup."""
    try:
        name = normalize_pattern(socket.gethostname())
    except (OSError, UnicodeError):
        return ()
    if not name or is_localhost_name(name) or pattern_problem(name) is not None:
        return ()
    short = name.split(".", 1)[0]
    names = [name, short, f"{short}.local"]
    return tuple(dict.fromkeys(n for n in names if pattern_problem(n) is None))


class HostRule(NamedTuple):
    """Why a host name is allowed: ``source`` is localhost | loopback | ip | machine | env | saved."""

    source: str
    pattern: str


@dataclass(frozen=True)
class NetworkPolicy:
    """Who may reach the app. Built at start-up and after every change, so requests never read the database."""

    env_patterns: tuple[str, ...] = ()  # TRADING_OS_ALLOWED_HOSTS and --allow-host
    saved_patterns: tuple[str, ...] = ()  # Trading Plan > Network & access
    machine_names: tuple[str, ...] = ()
    password_hash: str = ""
    session_secret: str = ""

    @property
    def password_required(self) -> bool:
        return bool(self.password_hash)

    def rule_for(self, host: str) -> HostRule | None:
        name = hostname(host)
        if not name:
            return None
        if name == "localhost":
            return HostRule("localhost", "localhost")
        if name.endswith(".localhost"):
            return HostRule("loopback", "*.localhost")
        if ip_address(name) is not None:  # LAN access by IP; DNS rebinding needs a name
            return HostRule("ip", name)
        if name in self.machine_names:
            return HostRule("machine", name)
        for source, patterns in (("env", self.env_patterns), ("saved", self.saved_patterns)):
            for pattern in patterns:
                if matches(name, pattern):
                    return HostRule(source, pattern)
        return None

    def allowed(self, host: str) -> bool:
        return self.rule_for(host) is not None

    def trusted_origin_names(self) -> frozenset[str]:
        """Exact configured names a reverse proxy may present as Origin while rewriting Host. Never wildcards."""
        return frozenset(p for p in (*self.env_patterns, *self.saved_patterns) if not p.startswith("*."))


def build_policy(env_patterns: Iterable[str], settings) -> NetworkPolicy:
    saved, _ignored = parse_patterns(settings.allowed_hosts or []) if settings is not None else ((), ())
    return NetworkPolicy(
        env_patterns=tuple(env_patterns),
        saved_patterns=saved,
        machine_names=machine_names(),
        password_hash=(settings.remote_password_hash or "") if settings is not None else "",
        session_secret=(settings.remote_session_secret or "") if settings is not None else "",
    )


# --------------------------------------------------------------------------- this computer / LAN


def peer_host(request: Request) -> str:
    return request.client.host if request.client else ""


def forwarding_headers(request: Request) -> list[str]:
    return [name for name in FORWARD_HEADERS if name in request.headers]


def _from_loopback(request: Request) -> bool:
    return _is_loopback_ip(peer_host(request))


def _unmapped(name: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    ip = ip_address(name)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def _from_own_bind_address(request: Request) -> bool:
    """Bound to one address of this computer (``--host 192.168.1.5``), so nothing listens on loopback: the browser
    here connects from that same address, typed as the Host. No other device can connect from it."""
    state = getattr(getattr(request.scope.get("app"), "state", None), "network", None)
    bind = _unmapped(getattr(state, "bind_host", "") or "")
    if bind is None or bind.is_unspecified or bind.is_loopback:
        return False
    return _unmapped(peer_host(request)) == bind and _unmapped(hostname(request.headers.get("host", ""))) == bind


def is_local_request(request: Request) -> bool:
    """Sent from this computer: loopback connection with a ``localhost`` Host (or, with a specific bind address,
    from that address to that address) and no proxy/tunnel headers."""
    from_here = (
        _from_loopback(request) and is_localhost_name(hostname(request.headers.get("host", "")))
    ) or _from_own_bind_address(request)
    return from_here and not forwarding_headers(request)


def effective_host(request: Request) -> str:
    """The name the visitor typed: ``X-Forwarded-Host`` from a proxy on this computer, otherwise ``Host``."""
    host = request.headers.get("host", "")
    if _from_loopback(request):
        forwarded = request.headers.get("x-forwarded-host", "").split(",")[0].strip()
        if forwarded:
            return forwarded
    return host


def request_scheme(request: Request) -> str:
    """``https`` when the visitor used HTTPS (a tunnel on this computer says so in ``X-Forwarded-Proto``)."""
    if _from_loopback(request):
        proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
        if proto in DEFAULT_PORTS:
            return proto
    return request.url.scheme


def client_ip(request: Request) -> str:
    """The visitor's address for the sign-in rate limit (the tunnel's own address is 127.0.0.1)."""
    if _from_loopback(request):
        for header in ("cf-connecting-ip", "true-client-ip", "x-real-ip"):
            value = request.headers.get(header, "").strip()
            if value:
                return value[:64]
        forwarded_for = request.headers.get("x-forwarded-for", "")
        if forwarded_for.strip():
            return forwarded_for.split(",")[-1].strip()[:64]
    return peer_host(request)


def lan_only(addresses: Iterable[str]) -> list[str]:
    """Private network addresses other devices can use (IPv4 first); loopback and link-local are dropped."""
    v4: list[str] = []
    v6: list[str] = []
    for raw in addresses:
        ip = ip_address(str(raw).split("%", 1)[0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if ip is None or ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast:
            continue
        if not (ip.is_private or (ip.version == 4 and ip in _SHARED_NETWORK)):
            continue
        bucket = v4 if ip.version == 4 else v6
        if str(ip) not in bucket:
            bucket.append(str(ip))
    return v4 + v6


def _discover_lan_addresses() -> tuple[str, ...]:
    candidates: list[str] = []
    try:
        candidates += [str(info[4][0]) for info in socket.getaddrinfo(socket.gethostname(), None)]
    except (OSError, UnicodeError):
        pass
    for family, probe in ((socket.AF_INET, ("192.0.2.1", 80)), (socket.AF_INET6, ("2001:db8::1", 80))):
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as sock:
                sock.connect(probe)  # a UDP "connect" only picks the outgoing interface: nothing is sent
                candidates.append(str(sock.getsockname()[0]))
        except OSError:
            pass
    return tuple(lan_only(candidates))


_lan_cache: tuple[float, tuple[str, ...]] | None = None
_lan_lock = threading.Lock()


def lan_addresses() -> tuple[str, ...]:
    """This computer's LAN addresses (cached for a minute; the lookup can be slow on a broken DNS)."""
    global _lan_cache
    with _lan_lock:
        if _lan_cache is not None and time.monotonic() - _lan_cache[0] < LAN_CACHE_SECONDS:
            return _lan_cache[1]
    found = _discover_lan_addresses()
    with _lan_lock:
        _lan_cache = (time.monotonic(), found)
    return found


def url_for(host: str, port: int, scheme: str = "http") -> str:
    ip = ip_address(host)
    shown = f"[{host}]" if ip is not None and ip.version == 6 else host
    return f"{scheme}://{shown}:{port}"


def is_loopback_bind(bind_host: str) -> bool:
    return is_localhost_name(hostname(bind_host)) if bind_host not in ALL_INTERFACES else False


def local_url(bind_host: str, port: int) -> str:
    """The address to type on this computer."""
    if bind_host in ALL_INTERFACES or is_loopback_bind(bind_host):
        return url_for("localhost", port)
    return url_for(bind_host, port)


def lan_urls(bind_host: str, port: int, addresses: Iterable[str] = ()) -> list[str]:
    """Addresses other devices can open: every LAN address when listening on all interfaces."""
    if bind_host in ALL_INTERFACES:
        return [url_for(ip, port) for ip in addresses]
    if is_loopback_bind(bind_host):
        return []
    return [url_for(bind_host, port)]


# --------------------------------------------------------------------------- password and sessions


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    """``scrypt$n$r$p$salt$hash`` (PBKDF2-SHA256 where this Python has no scrypt)."""
    salt = secrets.token_bytes(16)
    secret = password.encode("utf-8")
    n, r, p = _SCRYPT
    try:
        digest = hashlib.scrypt(secret, salt=salt, n=n, r=r, p=p, dklen=32)
    except (AttributeError, ValueError):
        digest = hashlib.pbkdf2_hmac("sha256", secret, salt, _PBKDF2_ROUNDS)
        return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${_b64(salt)}${_b64(digest)}"
    return f"scrypt${n}${r}${p}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        kind, *parts = (stored or "").split("$")
        secret = password.encode("utf-8")
        if kind == "scrypt" and len(parts) == 5:
            n, r, p = (int(value) for value in parts[:3])
            if n > 2**20 or r > 32 or p > 16:
                return False
            expected = _unb64(parts[4])
            candidate = hashlib.scrypt(
                secret, salt=_unb64(parts[3]), n=n, r=r, p=p, dklen=len(expected), maxmem=256 * 1024 * 1024
            )
        elif kind == "pbkdf2_sha256" and len(parts) == 3:
            rounds = int(parts[0])
            if rounds > 10_000_000:
                return False
            expected = _unb64(parts[2])
            candidate = hashlib.pbkdf2_hmac("sha256", secret, _unb64(parts[1]), rounds)
        else:
            return False
    except (AttributeError, ValueError, TypeError, binascii.Error):
        return False
    return hmac.compare_digest(candidate, expected)


def new_session_secret() -> str:
    return secrets.token_hex(32)


def session_cookie_name(port: int) -> str:
    """Per port, so two copies of the app on one computer (journal and demo) keep separate sign-ins."""
    return f"tos_session_{port}"


def _now() -> float:
    return time.time()


def _sign(secret: str, payload: str) -> str:
    return _b64(hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest())


def make_session_token(secret: str, days: float = SESSION_DAYS) -> str:
    """``expiry.nonce.signature``: valid until ``expiry`` while ``secret`` (rotated to sign out all) is unchanged."""
    payload = f"{int(_now() + days * 86400)}.{secrets.token_urlsafe(12)}"
    return f"{payload}.{_sign(secret, payload)}"


def read_session_token(secret: str, token: str | None) -> tuple[int, str] | None:
    """(expiry, nonce) of a valid, unexpired token; ``None`` otherwise."""
    if not secret or not token or len(token) > 200:
        return None
    parts = token.split(".")
    if len(parts) != 3 or not parts[0].isdigit():
        return None
    expiry, nonce, signature = parts
    if not hmac.compare_digest(signature.encode("utf-8"), _sign(secret, f"{expiry}.{nonce}").encode("utf-8")):
        return None
    if int(expiry) <= _now():
        return None
    return int(expiry), nonce


class LoginLimiter:
    """Failed sign-ins allowed per visitor address and in total within a sliding window."""

    def __init__(self, per_ip: int = 5, total: int = 30, window: float = 300.0, clock=time.monotonic):
        self.per_ip, self.total, self.window, self.clock = per_ip, total, window, clock
        self._failures: dict[str, deque[float]] = {}
        self._all: deque[float] = deque()
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        cutoff = now - self.window
        while self._all and self._all[0] <= cutoff:
            self._all.popleft()
        for ip in list(self._failures):
            queue = self._failures[ip]
            while queue and queue[0] <= cutoff:
                queue.popleft()
            if not queue:
                del self._failures[ip]

    def retry_after(self, ip: str) -> int:
        """Seconds to wait before the next attempt (0: allowed now)."""
        with self._lock:
            now = self.clock()
            self._prune(now)
            waits = []
            queue = self._failures.get(ip)
            if queue and len(queue) >= self.per_ip:
                waits.append(queue[0] + self.window - now)
            if len(self._all) >= self.total:
                waits.append(self._all[0] + self.window - now)
            return max(1, ceil(max(waits))) if waits else 0

    def failed(self, ip: str) -> None:
        with self._lock:
            now = self.clock()
            self._failures.setdefault(ip, deque()).append(now)
            self._all.append(now)

    def succeeded(self, ip: str) -> None:
        with self._lock:
            self._failures.pop(ip, None)


class NetworkState:
    """Per-app holder: the current policy plus in-memory sign-in bookkeeping (``app.state.network``)."""

    def __init__(self, policy: NetworkPolicy, port: int, bind_host: str = ""):
        self.policy = policy
        self.port = port
        self.bind_host = bind_host
        self.cookie_name = session_cookie_name(port)
        self.limiter = LoginLimiter()
        self._revoked: dict[str, int] = {}  # signed-out session nonce -> its expiry
        self._logged_hosts: set[str] = set()
        self._lock = threading.Lock()

    def session(self, request: Request) -> tuple[int, str] | None:
        data = read_session_token(self.policy.session_secret, request.cookies.get(self.cookie_name))
        if data is None:
            return None
        with self._lock:
            return None if data[1] in self._revoked else data

    def signed_in(self, request: Request) -> bool:
        return self.session(request) is not None

    def revoke(self, expiry: int, nonce: str) -> None:
        with self._lock:
            now = _now()
            self._revoked = {key: until for key, until in self._revoked.items() if until > now}
            self._revoked[nonce] = expiry

    def clear_revoked(self) -> None:
        with self._lock:
            self._revoked.clear()

    def log_refused(self, name: str) -> None:
        """One console line per refused name, so the owner sees what to allow (never a flood)."""
        with self._lock:
            if name in self._logged_hosts or len(self._logged_hosts) >= _MAX_LOGGED_HOSTS:
                return
            self._logged_hosts.add(name)
        logger.warning(
            "Refused a request for the host name %s. Allow it under Trading Plan > Network & access "
            "(open %s on this computer) or in TRADING_OS_ALLOWED_HOSTS.",
            repr(name)[:100],
            url_for("localhost", self.port),
        )


def install(app, config, session_factory) -> NetworkState:
    """Create ``app.state.network`` from the start-up config and the saved settings."""
    state = NetworkState(NetworkPolicy(env_patterns=tuple(config.allowed_hosts)), config.port, config.bind_host)
    app.state.network = state
    with session_factory() as db:
        reload_policy(app, db)
    return state


def reload_policy(app, db) -> NetworkPolicy:
    """Rebuild the policy after the allowed host names or the password changed (takes effect at once)."""
    from .account import get_settings

    settings = get_settings(db)
    if settings.remote_password_hash and not settings.remote_session_secret:
        settings.remote_session_secret = new_session_secret()
        db.commit()
    state: NetworkState = app.state.network
    state.policy = build_policy(state.policy.env_patterns, settings)
    return state.policy


# --------------------------------------------------------------------------- the request check


def _origin_parts(origin: str) -> tuple[str, str, int] | None:
    try:
        parts = urlsplit(origin.strip())
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in DEFAULT_PORTS or not parts.hostname:
        return None
    name = parts.hostname.lower()
    return parts.scheme, name[:-1] if name.endswith(".") else name, port or DEFAULT_PORTS[parts.scheme]


def cross_origin(headers, host: str, trusted: frozenset[str] = frozenset()) -> bool:
    """A write sent by another website.

    Same origin: the Origin's name and port equal the Host's (a Host without a port means the Origin
    scheme's default port, so ``https://x`` matches ``x`` and ``x:443``). ``trusted`` exact names (a reverse
    proxy or tunnel that rewrites Host) count as this app; wildcards are never passed here.
    """
    origin = headers.get("origin")
    parts = _origin_parts(origin) if origin is not None else None
    if parts is not None and parts[1] in trusted:
        return False
    if headers.get("sec-fetch-site") == "cross-site":
        return True
    if origin is None:  # scripts, curl, platform webhooks
        return False
    if parts is None:  # "null" (sandboxed pages, file://) or another scheme
        return True
    scheme, name, port = parts
    explicit = host_port(host)
    return name != hostname(host) or (explicit if explicit is not None else DEFAULT_PORTS[scheme]) != port


def is_exempt(path: str) -> bool:
    return path in EXEMPT_PATHS or path.startswith(EXEMPT_PREFIXES)


def _push_token_set(session_factory) -> bool:
    from ..models import AccountSettings

    try:
        with session_factory() as db:
            settings = db.get(AccountSettings, 1)
            return bool(settings and (settings.market_api_keys or {}).get("push_token"))
    except Exception:  # an unreadable database must not open the door
        return False


def check_request(request: Request, state: NetworkState, port: int, session_factory) -> Response | None:
    """The response that refuses ``request``, or ``None`` to let it through."""
    policy = state.policy
    host = request.headers.get("host", "")
    effective = effective_host(request)
    for candidate in dict.fromkeys((host, effective)):
        if not policy.allowed(candidate):
            return refused_host_response(request, state, hostname(candidate), port, session_factory)
    if request.method not in SAFE_METHODS and cross_origin(request.headers, effective, policy.trusted_origin_names()):
        return JSONResponse(status_code=403, content={"detail": "Cross-origin requests are not allowed"})
    if not policy.password_required or is_exempt(request.url.path) or is_local_request(request):
        return None
    if state.signed_in(request):
        return None
    if request.url.path == CANDLES_PATH:
        if _push_token_set(session_factory):
            return None  # the endpoint checks the token itself
        detail = tr(
            "A remote-access password is set: candles sent from outside this computer need a push token. "
            "Set one on Strategy Alerts > Data providers."
        )
        return JSONResponse(status_code=401, content={"detail": detail, "code": "push_token_required"})
    detail = tr("Sign in with the remote-access password to use Trading OS from this device.")
    return JSONResponse(status_code=401, content={"detail": detail, "code": "login_required"})


def refused_host_response(request: Request, state: NetworkState, name: str, port: int, session_factory) -> Response:
    shown = name[:100]
    state.log_refused(shown)
    if request.method in ("GET", "HEAD") and "text/html" in request.headers.get("accept", ""):
        first = i18n.stored_language(session_factory)
        return HTMLResponse(refused_host_page(shown, port, first), status_code=400)
    detail = tr(
        "Unknown host name '{host}'. Allow it under Trading Plan > Network & access on the computer running "
        "Trading OS, or add it to TRADING_OS_ALLOWED_HOSTS.",
        host=shown,
    )
    return JSONResponse(status_code=400, content={"detail": detail, "host": shown, "code": "host_not_allowed"})


def _page_section(language: str, name: str, port: int) -> str:
    esc = html.escape
    entry = name if name and pattern_problem(normalize_pattern(name)) is None else "my-name.example"
    local = url_for("localhost", port)
    with i18n.using(language):
        texts = {
            "title": tr("This address is not allowed yet"),
            "intro": tr("Trading OS only answers to the names you allow, so other websites cannot reach your journal."),
            "refused": tr("Refused name:"),
            "steps": tr("To allow it, on the computer running Trading OS:"),
            "open": tr("Open {url} there - type it in the browser address bar, not the search box.", url=local),
            "add": tr(
                "Go to Trading Plan > Network & access and add the name under Allowed host names. "
                "It works at once, without a restart."
            ),
            "env": tr("Or allow it when starting Trading OS (restart needed):"),
            "ip": tr("Addresses by IP, such as {example}, are always allowed.", example=url_for("192.168.1.20", port)),
            "wildcard": tr(
                "For a tunnel whose address changes on every run, allow a wildcard such as {pattern}.",
                pattern="*.trycloudflare.com",
            ),
        }
    commands = [
        ("Windows (cmd)", f"set TRADING_OS_ALLOWED_HOSTS={entry}\ntrading_os\\run.bat"),
        ("Windows (PowerShell)", f'$env:TRADING_OS_ALLOWED_HOSTS="{entry}"\ntrading_os\\run.bat'),
        ("macOS / Linux", f"TRADING_OS_ALLOWED_HOSTS={entry} trading_os/run.sh"),
        ("--allow-host", f"python trading_os/run.py --allow-host {entry}"),
    ]
    blocks = "".join(f"<p class='os'>{esc(label)}</p><pre>{esc(command)}</pre>" for label, command in commands)
    return (
        f"<section lang='{language}'>"
        f"<h1>{esc(texts['title'])}</h1>"
        f"<p>{esc(texts['intro'])}</p>"
        f"<p>{esc(texts['refused'])} <code>{esc(name or '-')}</code></p>"
        f"<p><strong>{esc(texts['steps'])}</strong></p>"
        f"<ol><li>{esc(texts['open'])}</li><li>{esc(texts['add'])}</li></ol>"
        f"<p><strong>{esc(texts['env'])}</strong></p>{blocks}"
        f"<p class='muted'>{esc(texts['ip'])} {esc(texts['wildcard'])}</p>"
        "</section>"
    )


def refused_host_page(name: str, port: int, first_language: str = "en") -> str:
    """The page a browser shows for a refused host name: English and Italian, no buttons, no scripts.

    There is deliberately no "allow" button: a page reached through an unknown name must not be able to
    change what the app trusts (that is the whole point of the check).
    """
    order = [first_language] + [code for code in ("en", "it") if code != first_language]
    sections = "<hr>".join(_page_section(code, name, port) for code in order if code in i18n.LANGUAGES)
    style = (
        "body{margin:0;background:#0d0e10;color:#e6e7ea;font:15px/1.55 system-ui,-apple-system,'Segoe UI',sans-serif}"
        "main{max-width:720px;margin:0 auto;padding:32px 20px}h1{font-size:22px;margin:0 0 10px}"
        "code,pre{background:#1d1f23;border:1px solid #2c2f35;border-radius:6px;font:13px ui-monospace,monospace}"
        "code{padding:1px 6px;overflow-wrap:anywhere}pre{padding:10px 12px;margin:4px 0 12px;white-space:pre-wrap;"
        "overflow-wrap:anywhere}.os{margin:10px 0 0;color:#a9adb5;font-size:13px}.muted{color:#8b9099;font-size:13px}"
        "hr{border:0;border-top:1px solid #2c2f35;margin:28px 0}ol{padding-left:22px}"
    )
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'><meta name='robots' content='noindex'>"
        f"<title>Trading OS</title><style>{style}</style></head><body><main>{sections}</main></body></html>"
    )


def describe_rule(rule: HostRule | None) -> dict | None:
    return None if rule is None else {"source": rule.source, "pattern": rule.pattern}


def request_summary(request: Request, state: NetworkState) -> dict:
    """What the server sees of this connection (for Trading Plan > Network & access)."""
    host = request.headers.get("host", "")
    effective = effective_host(request)
    scheme = request_scheme(request)
    name = hostname(effective)
    local = is_local_request(request)
    return {
        "host": host[:255],
        "hostname": name[:255],
        "allowed_by": describe_rule(state.policy.rule_for(effective)),
        "local": local,
        "local_reasons": {
            "peer": peer_host(request)[:64],
            "peer_loopback": _from_loopback(request),
            "host_local": is_localhost_name(hostname(host)),
            "forwarding_headers": forwarding_headers(request),
        },
        "scheme": scheme,
        "secure_context": scheme == "https" or is_localhost_name(name),
        "authenticated": state.signed_in(request),
    }
