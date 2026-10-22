"""Network & access: allowed host names, this connection, LAN addresses and the remote-access password.

Changes are accepted only from the computer running Trading OS (see ``services/network.is_local_request``),
so a remote visitor - even a signed-in one - can never lock the owner out or widen access.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StringConstraints

from ..services import network
from ..services.i18n import tr
from .deps import ConfigDep, DbDep, SettingsDep

router = APIRouter(prefix="/api", tags=["network"])

Entry = Annotated[str, StringConstraints(max_length=300)]


class NetworkIn(BaseModel):
    allowed_hosts: list[Entry] = Field(default_factory=list, max_length=200)


class PasswordIn(BaseModel):
    password: Annotated[str, StringConstraints(max_length=network.MAX_PASSWORD)] = ""


class LoginIn(BaseModel):
    password: Annotated[str, StringConstraints(max_length=network.MAX_PASSWORD)] = ""


def _state(request: Request) -> network.NetworkState:
    return request.app.state.network


def _local_only(request: Request, config) -> JSONResponse | None:
    if network.is_local_request(request):
        return None
    detail = tr(
        "Change remote-access settings on the computer running Trading OS (open {url} there).",
        url=network.local_url(config.bind_host, config.port),
    )
    return JSONResponse(status_code=403, content={"detail": detail, "code": "local_only"})


def _status(request: Request, config, settings) -> dict:
    state = _state(request)
    policy = state.policy
    listening_on_lan = not network.is_loopback_bind(config.bind_host)
    addresses = network.lan_addresses() if config.bind_host in network.ALL_INTERFACES else ()
    return {
        "this_request": network.request_summary(request, state),
        "server": {
            "bind_host": config.bind_host,
            "port": config.port,
            "listening_on_lan": listening_on_lan,
            "local_url": network.local_url(config.bind_host, config.port),
            "lan_urls": network.lan_urls(config.bind_host, config.port, addresses),
        },
        "allowed_hosts": {
            "builtin": ["localhost", "*.localhost"],
            "machine": list(policy.machine_names),
            "env": list(policy.env_patterns),
            "env_ignored": [
                {"entry": entry, "problem": network.pattern_problem(network.normalize_pattern(entry)) or ""}
                for entry in config.ignored_hosts
            ],
            "saved": list(policy.saved_patterns),
            "max": network.MAX_PATTERNS,
        },
        "access": {
            "password_set": policy.password_required,
            "session_days": network.SESSION_DAYS,
            "can_manage": network.is_local_request(request),
        },
        "push_token_set": bool((settings.market_api_keys or {}).get("push_token")),
        "app_url": settings.app_url,
    }


@router.get("/network")
def get_network(request: Request, config: ConfigDep, settings: SettingsDep) -> dict:
    return _status(request, config, settings)


@router.put("/network")
def save_network(body: NetworkIn, request: Request, db: DbDep, config: ConfigDep, settings: SettingsDep):
    refused = _local_only(request, config)
    if refused is not None:
        return refused
    names: list[str] = []
    problems: list[str] = []
    for entry in body.allowed_hosts:
        for part in network.split_entries(entry):
            name = network.normalize_pattern(part)
            problem = network.pattern_problem(name)
            if problem is not None:
                problems.append(f"{part[:100]}: {problem}")
            elif name not in names:
                names.append(name)
    if problems:
        return JSONResponse(status_code=422, content={"detail": " · ".join(problems)})
    if len(names) > network.MAX_PATTERNS:
        detail = tr("At most {max} host names can be saved", max=network.MAX_PATTERNS)
        return JSONResponse(status_code=422, content={"detail": detail})
    settings.allowed_hosts = names
    db.commit()
    network.reload_policy(request.app, db)
    return _status(request, config, settings)


# --------------------------------------------------------------------------- remote-access password


def _access_status(request: Request, settings) -> dict:
    state = _state(request)
    local = network.is_local_request(request)
    authenticated = state.signed_in(request)
    password_set = state.policy.password_required
    return {
        "password_required": password_set,
        "authenticated": authenticated,
        "local": local,
        "needs_login": password_set and not local and not authenticated,
        "language": settings.language,
    }


@router.get("/access/status")
def access_status(request: Request, settings: SettingsDep) -> dict:
    """Whether this device must sign in first (served without a sign-in)."""
    return _access_status(request, settings)


def _wait_message(seconds: int) -> str:
    minutes = max(1, -(-seconds // 60))
    return tr("Too many wrong passwords. Try again in {minutes} min.", minutes=minutes)


@router.post("/access/login", status_code=204)
def login(body: LoginIn, request: Request):
    state = _state(request)
    policy = state.policy
    if not policy.password_required:
        return Response(status_code=204)
    ip = network.client_ip(request)
    wait = state.limiter.retry_after(ip)
    if wait:
        content = {"detail": _wait_message(wait), "code": "too_many_attempts"}
        return JSONResponse(status_code=429, content=content, headers={"Retry-After": str(wait)})
    if not network.verify_password(body.password, policy.password_hash):
        state.limiter.failed(ip)
        return JSONResponse(status_code=401, content={"detail": tr("Wrong password"), "code": "wrong_password"})
    state.limiter.succeeded(ip)
    response = Response(status_code=204)
    response.set_cookie(
        state.cookie_name,
        network.make_session_token(policy.session_secret),
        max_age=network.SESSION_DAYS * 86400,
        path="/",
        httponly=True,
        samesite="strict",
        secure=network.request_scheme(request) == "https",
    )
    return response


@router.post("/access/logout", status_code=204)
def logout(request: Request):
    state = _state(request)
    session = state.session(request)
    if session is not None:
        state.revoke(*session)
    response = Response(status_code=204)
    response.delete_cookie(state.cookie_name, path="/", httponly=True, samesite="strict")
    return response


@router.put("/access/password")
def set_password(body: PasswordIn, request: Request, db: DbDep, config: ConfigDep, settings: SettingsDep):
    """Set, change or (with an empty password) remove the remote-access password. Signs out every device."""
    refused = _local_only(request, config)
    if refused is not None:
        return refused
    password = body.password
    if password and len(password) < network.MIN_PASSWORD:
        detail = tr("The password needs at least {min} characters", min=network.MIN_PASSWORD)
        return JSONResponse(status_code=422, content={"detail": detail})
    settings.remote_password_hash = network.hash_password(password) if password else ""
    settings.remote_session_secret = network.new_session_secret()
    db.commit()
    network.reload_policy(request.app, db)
    _state(request).clear_revoked()
    return _status(request, config, settings)


@router.post("/access/signout-all")
def sign_out_all(request: Request, db: DbDep, config: ConfigDep, settings: SettingsDep):
    """Invalidate every sign-in cookie (each device must enter the password again)."""
    refused = _local_only(request, config)
    if refused is not None:
        return refused
    settings.remote_session_secret = network.new_session_secret()
    db.commit()
    network.reload_policy(request.app, db)
    _state(request).clear_revoked()
    return _status(request, config, settings)
