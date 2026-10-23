"""A real browser on a real server, for the few tests that need one (skipped without Playwright/Chromium)."""

import dataclasses
import socket
import threading
import time
from contextlib import contextmanager

import pytest

from trading_os.app import create_app
from trading_os.config import load_config


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def live_server(tmp_path):
    import uvicorn

    app = create_app(dataclasses.replace(load_config(tmp_path / "data"), background_jobs=False))
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("the test server did not start")
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture
def browser_page(tmp_path):
    """(base url, page) with the risk disclaimer accepted and the getting-started guide already seen."""
    sync_api = pytest.importorskip("playwright.sync_api")
    with live_server(tmp_path) as base, sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Exception as exc:  # no browser downloaded on this computer
            pytest.skip(f"Chromium is not available: {exc}")
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.request.post(f"{base}/api/disclaimer/accept")
            settings = page.request.get(f"{base}/api/settings").json()
            page.request.put(f"{base}/api/settings", data={**settings, "onboarding_done": True})
            yield base, page
        finally:
            browser.close()
