"""Command-line launcher: ``python trading_os/run.py`` / ``python -m trading_os``."""

from __future__ import annotations

import argparse
import dataclasses
import os
import sys
import threading
import webbrowser
from collections.abc import Iterable

from . import APP_NAME, __version__
from .config import DEFAULT_HOST, DEFAULT_PORT, DEMO_DATA_DIR

REQUIRED_MODULES = ("fastapi", "uvicorn", "sqlalchemy", "pydantic", "multipart")
EVERY_NETWORK = ("0.0.0.0", "::")  # bind addresses that listen on all interfaces
# The app's own modules (services.network, ...) are imported only after the dependency check.


def _check_dependencies() -> None:
    missing = []
    for module in REQUIRED_MODULES:
        try:
            __import__(module)
        except ImportError:
            missing.append(module)
    if missing:
        sys.exit(
            f"Missing dependencies: {', '.join(missing)}\n"
            "Install them with:  pip install -r trading_os/requirements.txt"
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="trading_os", description=f"{APP_NAME} & Journal v{__version__}")
    parser.add_argument("--host", help=f"bind address (default {DEFAULT_HOST}; 0.0.0.0 = every network)")
    parser.add_argument(
        "--lan",
        action="store_true",
        help="let phones and other computers on your network open the app (same as --host 0.0.0.0)",
    )
    parser.add_argument(
        "--allow-host",
        action="append",
        default=[],
        metavar="NAME",
        help="also answer to this host name, e.g. trading.lan or *.trycloudflare.com "
        "(repeatable; added to TRADING_OS_ALLOWED_HOSTS)",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port (default {DEFAULT_PORT})")
    parser.add_argument("--data-dir", help="folder for the database, screenshots and backups")
    parser.add_argument(
        "--demo",
        action="store_true",
        help=f"use a separate demo journal pre-filled with sample trades ({DEMO_DATA_DIR.name}/)",
    )
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser automatically")
    args = parser.parse_args(argv)
    if args.lan and args.host not in (None, *EVERY_NETWORK):
        parser.error("--lan already listens on every network: leave out --host")
    args.host = args.host or ("0.0.0.0" if args.lan else DEFAULT_HOST)
    return args


def display_urls(host: str, port: int, lan_ips: Iterable[str] = ()) -> tuple[str, list[str]]:
    """(the address to type on this computer, the addresses other devices can open). IPv6 is bracketed."""
    from .services import network

    return network.local_url(host, port), network.lan_urls(host, port, lan_ips)


def network_lines(
    host: str, port: int, lan_ips: Iterable[str] = (), *, password_set: bool = False, ignored: Iterable[str] = ()
) -> list[str]:
    """Start-up hints about addresses and access, in the journal's language."""
    from .services import network
    from .services.i18n import tr

    local, others = display_urls(host, port, lan_ips)
    lines = [tr("Tip: type {url} in the browser address bar, not the search box.", url=local)]
    if not network.is_loopback_bind(host):
        lines += [tr("On your network: {url}", url=url) for url in others]
        if not others:
            lines.append(tr("No network address found for other devices: check the Wi-Fi or cable connection."))
        lines.append(tr("If Windows Firewall asks, allow Python on Private networks."))
        if not password_set:
            lines.append(
                tr(
                    "Warning: anyone on your network can open and change your journal. "
                    "Set a remote-access password in Trading Plan > Network & access."
                )
            )
    for entry in ignored:
        problem = network.pattern_problem(network.normalize_pattern(entry)) or ""
        lines.append(tr("Ignored allowed host name {entry}: {problem}", entry=repr(entry), problem=problem))
    return lines


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    _check_dependencies()

    import uvicorn

    from .app import create_app
    from .config import LEGACY_DATA_DIR, load_config, migrate_legacy_data_dir
    from .services import i18n, network

    if not args.data_dir and not args.demo and not os.environ.get("TRADING_OS_DATA_DIR"):
        moved = migrate_legacy_data_dir()
        if moved:
            print(f"Moved your journal from {LEGACY_DATA_DIR} to {moved}")
    config = load_config(args.data_dir or (DEMO_DATA_DIR if args.demo else None))
    extra_hosts, ignored_hosts = network.parse_patterns(args.allow_host)
    config = dataclasses.replace(
        config,
        allowed_hosts=tuple(dict.fromkeys((*config.allowed_hosts, *extra_hosts))),
        ignored_hosts=(*config.ignored_hosts, *ignored_hosts),
        bind_host=args.host,
        port=args.port,
    )
    app = create_app(config)

    if args.demo:
        from .services.account import get_settings, local_today
        from .services.demo import seed_demo

        with app.state.session_factory() as db:
            created = seed_demo(db, local_today(get_settings(db)))
        if created:
            print(f"Demo journal created with {created} sample trades.")

    lan_ips = network.lan_addresses() if args.host in network.ALL_INTERFACES else ()
    url, _others = display_urls(args.host, args.port, lan_ips)
    print(f"\n  {APP_NAME} v{__version__}")
    print(f"  Data folder : {config.data_dir}")
    print(f"  Open        : {url}")
    print(f"  API docs    : {url}/api/docs")
    with i18n.using(i18n.stored_language(app.state.session_factory)):
        password_set = app.state.network.policy.password_required
        for line in network_lines(
            args.host, args.port, lan_ips, password_set=password_set, ignored=config.ignored_hosts
        ):
            print(f"  {line}")
    print("  Press Ctrl+C to stop.\n")

    if not args.no_browser:
        threading.Timer(1.2, webbrowser.open, args=(url,)).start()
    # proxy_headers=False: the app reads X-Forwarded-* itself, only from a proxy or tunnel on this computer,
    # and needs the real connection address to tell this computer from other devices.
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", proxy_headers=False)
