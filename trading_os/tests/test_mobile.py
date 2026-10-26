"""Mobile shell (v1.7): bottom navigation, nav drawer, quick actions, and the PWA service worker.

The interactive parts run in a real browser at phone width (skipped without Playwright/Chromium). The service
worker is checked by serving it and asserting its caching contract, which needs no browser.
"""


from .browser import browser_page  # noqa: F401 - pytest fixture


def test_the_service_worker_caches_the_shell_but_never_the_api(client):
    sw = client.get("/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.headers["content-type"]
    body = sw.text
    assert "trading-os-shell" in body  # a versioned app-shell cache
    # The API and uploads are explicitly excluded; navigations fall back to the cached shell offline.
    assert '"/api/"' in body and '"/uploads/"' in body and 'request.mode === "navigate"' in body


def test_the_mobile_stylesheet_is_served(client):
    assert client.get("/static/css/mobile.css").status_code == 200
    assert "/static/css/mobile.css" in client.get("/").text


def test_the_bottom_nav_drawer_and_quick_actions_work(browser_page):  # noqa: F811
    base, page = browser_page
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{base}/#/dashboard")
    page.wait_for_selector("#mobile-nav")
    assert page.is_visible("#mobile-nav") and not page.is_visible(".sidebar")

    # The nav drawer lists every route; tapping one navigates and closes the drawer.
    page.click("#mobile-nav button[aria-haspopup='dialog']")
    page.wait_for_selector(".drawer-left.open")
    assert page.eval_on_selector_all(".nav-drawer a", "els => els.length") >= 16
    page.click(".nav-drawer a[href='#/academy']")
    page.wait_for_function("location.hash === '#/academy'")
    assert page.query_selector(".drawer-left.open") is None  # closed after navigating

    # Quick actions open from the centre button and route on tap.
    page.goto(f"{base}/#/dashboard")
    page.wait_for_selector("#mobile-nav")
    page.click(".mnav-add")
    page.wait_for_selector(".drawer-bottom.open")
    assert page.eval_on_selector_all(".quick-action", "els => els.length") == 4
    page.click(".quick-action[href='#/calculator']")
    page.wait_for_function("location.hash === '#/calculator'")

    # No horizontal overflow at phone width.
    assert page.evaluate("document.documentElement.scrollWidth") <= 390


def test_the_accordion_collapses_a_section(browser_page):  # noqa: F811
    base, page = browser_page
    # The EA setup guide on a MetaTrader account is an accordion.
    acct = page.request.post(f"{base}/api/accounts", data={"name": "MT5 Lab", "kind": "demo", "platform": "mt5"}).json()
    page.request.put(
        f"{base}/api/accounts/{acct['id']}/connection",
        data={"connection": "mt_bridge", "connection_config": {}, "max_lots_per_order": None},
    )
    page.goto(f"{base}/#/accounts")
    page.wait_for_selector(".accordion-toggle")
    toggle = page.query_selector(".accordion-toggle")
    assert toggle.get_attribute("aria-expanded") == "true"  # open by default
    toggle.click()
    page.wait_for_function("document.querySelector('.accordion-toggle').getAttribute('aria-expanded') === 'false'")
