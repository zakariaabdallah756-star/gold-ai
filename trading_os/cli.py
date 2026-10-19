"""Command-line launcher: ``python run_trading_os.py`` / ``python -m trading_os``."""

from __future__ import annotations

import argparse
import sys
import threading
import webbrowser

from . import APP_NAME, __version__
from .config import DEFAULT_HOST, DEFAULT_PORT, DEMO_DATA_DIR

REQUIRED_MODULES = ("fastapi", "uvicorn", "sqlalchemy", "pydantic", "multipart")


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
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"bind address (default {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port (default {DEFAULT_PORT})")
    parser.add_argument("--data-dir", help="folder for the database, screenshots and backups")
    parser.add_argument(
        "--demo",
        action="store_true",
        help=f"use a separate demo journal pre-filled with sample trades ({DEMO_DATA_DIR.name}/)",
    )
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser automatically")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    _check_dependencies()

    import uvicorn

    from .app import create_app
    from .config import load_config

    config = load_config(args.data_dir or (DEMO_DATA_DIR if args.demo else None))
    app = create_app(config)

    if args.demo:
        from .services.account import get_settings, local_today
        from .services.demo import seed_demo

        with app.state.session_factory() as db:
            created = seed_demo(db, local_today(get_settings(db)))
        if created:
            print(f"Demo journal created with {created} sample trades.")

    url = f"http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}"
    print(f"\n  {APP_NAME} v{__version__}")
    print(f"  Data folder : {config.data_dir}")
    print(f"  Open        : {url}")
    print(f"  API docs    : {url}/api/docs")
    print("  Press Ctrl+C to stop.\n")

    if not args.no_browser:
        threading.Timer(1.2, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
