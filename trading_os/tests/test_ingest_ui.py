"""External signal API panel (v1.8) in a real browser on the Telegram signals page.

Skipped without Playwright/Chromium. Confirms the card generates a token (shown once), shows the endpoint and
the token hint, and that the Italian interface renders the panel too.
"""

from .browser import browser_page  # noqa: F401 - pytest fixture


def test_the_external_api_panel_generates_a_token_and_enables(browser_page):  # noqa: F811
    base, page = browser_page
    page.goto(f"{base}/#/telegram")
    page.wait_for_selector(".tg-setup")

    # The External signal API card shows the full endpoint URL.
    assert page.get_by_text("External signal API").is_visible()
    assert "/api/v1/signals/incoming" in page.content()

    # Generate a token: a one-time dialog shows it, and the card then reports it is set with a hint.
    page.get_by_role("button", name="Generate token").click()
    page.wait_for_selector(".modal")
    assert page.get_by_text("Your API token").is_visible()
    page.locator(".modal button.primary").click()  # the footer "Close" (not the header X)

    # The status endpoint confirms it is enabled with a token set, and the UI shows the hint, not the token.
    status = page.request.get(f"{base}/api/ingest/status").json()
    assert status["enabled"] is True and status["token_set"] is True and status["token_hint"]
    assert "Regenerate token" in page.content()


def test_the_panel_renders_in_italian(browser_page):  # noqa: F811
    base, page = browser_page
    settings = page.request.get(f"{base}/api/settings").json()
    page.request.put(f"{base}/api/settings", data={**settings, "language": "it"})
    page.goto(f"{base}/#/telegram")
    page.wait_for_selector(".tg-setup")
    assert page.get_by_text("API segnali esterni").is_visible()
    assert page.get_by_role("button", name="Genera token").is_visible()
