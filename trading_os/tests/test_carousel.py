"""Promo carousel autoplay (v1.6): the 4-second default, the one-time move off the v1.5 default, the v1.6
announcement card, the autoplay timing in static/js/autoplay.js (run with node when it is installed) and the
real carousel in a headless browser (when Playwright and Chromium are installed)."""

import dataclasses
import json
import shutil
import socket
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from trading_os.app import create_app
from trading_os.config import load_config
from trading_os.models import AccountSettings, Promo
from trading_os.services import promos as promo_service

from .conftest import update_settings

STATIC = Path(__file__).resolve().parent.parent / "static"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def restart(tmp_path):
    app = create_app(dataclasses.replace(load_config(tmp_path / "data"), background_jobs=False))
    return TestClient(app, base_url="http://localhost")


# ------------------------------------------------------------------------------ server side


def test_the_carousel_turns_every_four_seconds_by_default(client):
    assert client.get("/api/promos").json()["interval_sec"] == 4
    assert client.get("/api/settings").json()["promo_interval_sec"] == 4
    assert client.get("/api/promos/all").json()["interval_sec"] == 4


def test_journals_on_the_old_default_move_to_four_seconds_once(tmp_path):
    with restart(tmp_path) as first:
        factory = first.app.state.session_factory
        with factory() as db:
            settings = db.get(AccountSettings, 1)
            settings.promo_interval_sec = 7  # a v1.5 journal that never changed it
            settings.seeded_defaults = [g for g in settings.seeded_defaults if g != promo_service.INTERVAL_GROUP]
            db.commit()
    with restart(tmp_path) as second:
        assert second.get("/api/promos").json()["interval_sec"] == 4
        update_settings(second, promo_interval_sec=7)  # chosen again after the move: kept from now on
    with restart(tmp_path) as third:
        assert third.get("/api/promos").json()["interval_sec"] == 7


def test_a_chosen_interval_is_never_changed_by_the_move(tmp_path):
    with restart(tmp_path) as first:
        factory = first.app.state.session_factory
        with factory() as db:
            settings = db.get(AccountSettings, 1)
            settings.promo_interval_sec = 12
            settings.seeded_defaults = [g for g in settings.seeded_defaults if g != promo_service.INTERVAL_GROUP]
            db.commit()
    with restart(tmp_path) as second:
        assert second.get("/api/promos").json()["interval_sec"] == 12


def test_the_v16_announcement_leads_the_carousel(client):
    feed = client.get("/api/promos").json()["promos"]
    assert feed[0]["builtin_key"] == "whats_new_v16" and feed[0]["cta_url"] == "#/academy"


def test_existing_journals_get_the_v16_announcement_first_and_only_once(tmp_path):
    with restart(tmp_path) as first:
        factory = first.app.state.session_factory
        with factory() as db:
            db.delete(db.scalar(select(Promo).where(Promo.builtin_key == "whats_new_v16")))
            settings = db.get(AccountSettings, 1)
            settings.seeded_defaults = [g for g in settings.seeded_defaults if g != "promos:whats_new_v16"]
            db.commit()
        assert first.get("/api/promos").json()["promos"][0]["builtin_key"] == "whats_new_v15"
    with restart(tmp_path) as second:  # an upgraded v1.5 journal
        keys = [p["builtin_key"] for p in second.get("/api/promos").json()["promos"]]
        assert keys[0] == "whats_new_v16" and keys.count("whats_new_v16") == 1
        card = second.get("/api/promos").json()["promos"][0]
        assert second.delete(f"/api/promos/{card['id']}").status_code == 204
    with restart(tmp_path) as third:  # deleted cards never come back
        assert "whats_new_v16" not in [p["builtin_key"] for p in third.get("/api/promos").json()["promos"]]


def test_the_v16_announcement_follows_the_language(client):
    update_settings(client, language="it")
    card = client.get("/api/promos").json()["promos"][0]
    assert card["title"] == "Novità della v1.6: Trading Academy e AI Strategy Learner"
    assert card["cta_label"] == "Apri l'Academy"


def test_the_carousel_scripts_are_wired_to_the_autoplay_module(client):
    carousel = client.get("/static/js/promos.js").text
    assert 'from "./autoplay.js"' in carousel and "createAutoplay(" in carousel
    for reason in ('pause("hover")', 'resume("hover")', 'pause("touch")', 'resumeAfter("touch"', 'pause("focus")'):
        assert reason in carousel
    assert client.get("/static/js/autoplay.js").status_code == 200


# ------------------------------------------------------------------------- autoplay timing

HARNESS = """
import { createAutoplay, intervalMs, DEFAULT_INTERVAL_MS } from MODULE;

class FakeClock {
  constructor() { this.t = 0; this.seq = 1; this.timers = new Map(); }
  now() { return this.t; }
  setTimeout(fn, ms) { const id = this.seq++; this.timers.set(id, { at: this.t + Math.max(0, ms), fn }); return id; }
  clearTimeout(id) { this.timers.delete(id); }
  advance(ms) {
    const end = this.t + ms;
    for (;;) {
      let best = null;
      for (const [id, timer] of this.timers) {
        if (timer.at <= end && (!best || timer.at < best[1].at || (timer.at === best[1].at && id < best[0]))) best = [id, timer];
      }
      if (!best) break;
      this.timers.delete(best[0]);
      this.t = best[1].at;
      best[1].fn();
    }
    this.t = end;
  }
}

const clock = new FakeClock();
let slides = 0;
const changes = [];
const play = createAutoplay({
  intervalMs: 4000,
  clock,
  onAdvance: () => { slides += 1; play.restart(); },
  onChange: (state) => changes.push(state.running),
});
const out = {};
"""


def run_autoplay(script: str) -> dict:
    module = json.dumps((STATIC / "js" / "autoplay.js").as_uri())
    source = HARNESS.replace("MODULE", module) + script + "\nconsole.log(JSON.stringify(out));"
    result = subprocess.run(["node", "--input-type=module", "-e", source], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@needs_node
def test_autoplay_advances_every_interval():
    out = run_autoplay(
        """
        clock.advance(3999); out.before = slides;
        clock.advance(1); out.first = slides;
        clock.advance(8000); out.later = slides;
        out.default = DEFAULT_INTERVAL_MS;
        """
    )
    assert out == {"before": 0, "first": 1, "later": 3, "default": 4000}


@needs_node
def test_hover_holds_rotation_and_leaving_resumes_with_the_time_left():
    out = run_autoplay(
        """
        clock.advance(2500);
        play.pause("hover");
        clock.advance(60000); out.held = slides; out.left = play.state().remainingMs; out.paused = play.isPaused();
        play.resume("hover");
        clock.advance(1499); out.almost = slides;
        clock.advance(1); out.resumed = slides;
        clock.advance(4000); out.next = slides;
        """
    )
    assert out == {"held": 0, "left": 1500, "paused": True, "almost": 0, "resumed": 1, "next": 2}


@needs_node
def test_rotation_waits_until_every_reason_is_lifted():
    out = run_autoplay(
        """
        play.pause("hover"); play.pause("focus");
        play.resume("hover");
        clock.advance(10000); out.stillHeld = slides; out.reasons = play.state().reasons;
        play.resume("focus");
        clock.advance(4000); out.running = slides;
        """
    )
    assert out == {"stillHeld": 0, "reasons": ["focus"], "running": 1}


@needs_node
def test_a_tap_holds_rotation_until_a_while_after_the_finger_lifts():
    out = run_autoplay(
        """
        clock.advance(1000);
        play.pause("touch");
        clock.advance(500);
        play.resumeAfter("touch", 5000);
        clock.advance(4999); out.held = slides; out.paused = play.isPaused("touch");
        clock.advance(1); out.liftedAt = play.isPaused("touch");
        clock.advance(2999); out.almost = slides;
        clock.advance(1); out.turned = slides;
        """
    )
    assert out == {"held": 0, "paused": True, "liftedAt": False, "almost": 0, "turned": 1}


@needs_node
def test_touching_again_cancels_the_delayed_resume():
    out = run_autoplay(
        """
        play.pause("touch");
        play.resumeAfter("touch", 5000);
        clock.advance(2000);
        play.pause("touch");
        clock.advance(30000); out.held = slides; out.timers = clock.timers.size;
        play.resumeAfter("touch", 5000);
        clock.advance(5000 + 4000); out.turned = slides;
        """
    )
    assert out == {"held": 0, "timers": 0, "turned": 1}


@needs_node
def test_resume_after_ignores_reasons_that_are_not_set():
    out = run_autoplay(
        """
        play.resumeAfter("touch", 5000);
        out.timers = clock.timers.size;  // only the rotation timer
        clock.advance(4000); out.turned = slides;
        """
    )
    assert out == {"timers": 1, "turned": 1}


@needs_node
def test_a_new_slide_gets_its_full_interval():
    out = run_autoplay(
        """
        clock.advance(3000);
        play.restart();  // the trader clicked an arrow or a dot
        clock.advance(3999); out.before = slides;
        clock.advance(1); out.after = slides;
        play.pause("hover"); clock.advance(1000); play.restart(); out.restartedWhilePaused = play.state();
        """
    )
    assert out["before"] == 0 and out["after"] == 1
    assert out["restartedWhilePaused"]["running"] is False and out["restartedWhilePaused"]["remainingMs"] == 4000


@needs_node
def test_the_pause_button_toggles_rotation():
    out = run_autoplay(
        """
        out.paused = play.toggle("user");
        clock.advance(20000); out.held = slides;
        out.playing = play.toggle("user");
        clock.advance(4000); out.turned = slides;
        """
    )
    assert out == {"paused": True, "held": 0, "playing": False, "turned": 1}


@needs_node
def test_disabled_autoplay_never_turns_and_keeps_its_pauses():
    out = run_autoplay(
        """
        play.setEnabled(false);  // one card left, or reduced motion
        clock.advance(20000); out.off = slides; out.enabled = play.state().enabled;
        play.pause("hover");
        play.setEnabled(true);
        clock.advance(20000); out.stillHovered = slides;
        play.resume("hover");
        clock.advance(4000); out.turned = slides;
        """
    )
    assert out == {"off": 0, "enabled": False, "stillHovered": 0, "turned": 1}


@needs_node
def test_dispose_clears_every_timer():
    out = run_autoplay(
        """
        play.pause("touch"); play.resumeAfter("touch", 5000); play.resume("touch");
        play.pause("x"); play.resumeAfter("x", 9000);
        play.dispose();
        out.timers = clock.timers.size;
        play.resume("x"); play.restart(); play.setEnabled(true); play.pause("hover");
        clock.advance(100000);
        out.slides = slides; out.timersAfter = clock.timers.size;
        """
    )
    assert out == {"timers": 0, "slides": 0, "timersAfter": 0}


@needs_node
def test_rotation_continues_when_the_page_does_not_restart_it():
    out = run_autoplay(
        """
        let count = 0;
        const plain = createAutoplay({ intervalMs: 4000, clock, onAdvance: () => { count += 1; } });
        play.dispose();
        clock.advance(12000); out.count = count;
        """
    )
    assert out == {"count": 3}


@needs_node
def test_change_events_report_running_and_paused():
    out = run_autoplay(
        """
        play.pause("hover"); play.resume("hover"); play.toggle("user");
        out.changes = changes;
        """
    )
    assert out == {"changes": [False, True, False]}


@needs_node
def test_interval_seconds_are_clamped_to_the_allowed_range():
    out = run_autoplay(
        """
        out.values = [4, undefined, null, "abc", 0, -5, 1, 2.5, 7, 999, "12"].map((s) => intervalMs(s));
        """
    )
    assert out == {"values": [4000, 4000, 4000, 4000, 4000, 4000, 3000, 3000, 7000, 60000, 12000]}


# ---------------------------------------------------------------------------- real browser


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
        if time.monotonic() > deadline:
            raise RuntimeError("the test server did not start")
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture
def browser_page(tmp_path):
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


def _current_slide(page) -> int:
    return page.evaluate("[...document.querySelectorAll('.promo-dot')].findIndex((d) => d.classList.contains('on'))")


def test_the_dashboard_carousel_turns_by_itself_and_holds_while_hovered(browser_page):
    base, page = browser_page
    page.clock.install()
    page.goto(f"{base}/#/dashboard")
    page.wait_for_selector(".promo-dot.on")
    page.mouse.move(5, 890)  # away from the carousel
    assert _current_slide(page) == 0
    page.clock.run_for(4100)
    assert _current_slide(page) == 1

    page.hover(".promo-viewport")
    assert page.eval_on_selector(".promo-carousel", "el => el.classList.contains('is-paused')")
    page.clock.run_for(20000)
    assert _current_slide(page) == 1  # held while the mouse is over a card

    page.mouse.move(5, 890)
    page.clock.run_for(4100)
    assert _current_slide(page) == 2  # resumed when the mouse left

    page.click(".promo-toggle")  # the pause button holds it even after the mouse leaves
    page.mouse.move(5, 890)
    page.clock.run_for(20000)
    assert _current_slide(page) == 2
