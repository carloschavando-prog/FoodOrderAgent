"""One-time interactive US Foods authentication bootstrap.

The user completes the provider's current login, secondary-ID, and verification
steps in a local Chrome window. This script captures only the resulting
renewable Panamax token response in memory, proves it can read the configured
ordering list, and writes the refreshed GitHub secrets through stdin.

No passwords, secondary IDs, cookies, screenshots, or token values are logged
or written to local files.
"""

from __future__ import annotations

import json
import os
import subprocess
import time

from browser_auth import (
    BrowserAuthError,
    USF_AUTHORIZE_URL,
    USF_ORDER_LIST_URL,
    USF_TOKEN_PATH,
    _usf_candidate,
)
from scrape_usfoods import get_list_items


DEFAULT_REPOSITORY = "carloschavando-prog/FoodOrderAgent"
DEFAULT_LIST_ID = 1000643297


def _set_github_secret(name, value, repository):
    result = subprocess.run(
        ["gh", "secret", "set", name, "--repo", repository],
        input=value,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise BrowserAuthError(f"Failed to update the {name} GitHub secret.")


def _static_usf_config(candidate, list_id):
    return {
        key: value
        for key, value in {**candidate, "fall_2025_list_id": list_id}.items()
        if key not in {"refresh_token", "bearer"}
    }


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit(
            "Playwright is not installed. Run: python3 -m pip install -r requirements.txt"
        ) from None

    repository = os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPOSITORY)
    list_id = int(os.environ.get("USF_LIST_ID", DEFAULT_LIST_ID))
    capture = {}

    print("Opening a private US Foods login window.")
    print("Complete every provider prompt, including secondary ID or verification.")
    print("The window will close after read-only ordering-list access is verified.")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=False)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()

        def capture_token(response):
            if USF_TOKEN_PATH not in response.url or not response.ok:
                return
            try:
                request_payload = json.loads(response.request.post_data or "{}")
                capture["result"] = _usf_candidate(
                    request_payload,
                    response.request.all_headers(),
                    response.json(),
                )
            except (BrowserAuthError, ValueError, json.JSONDecodeError):
                return

        page.on("response", capture_token)
        page.goto(USF_AUTHORIZE_URL, wait_until="domcontentloaded", timeout=45_000)

        deadline = time.monotonic() + 600
        ordering_opened = False
        try:
            while time.monotonic() < deadline and "result" not in capture:
                if "b2clogin.com" not in page.url and not ordering_opened:
                    page.wait_for_timeout(3_000)
                    page.goto(
                        USF_ORDER_LIST_URL,
                        wait_until="domcontentloaded",
                        timeout=45_000,
                    )
                    ordering_opened = True
                page.wait_for_timeout(500)
        finally:
            browser.close()

    if "result" not in capture:
        raise SystemExit(
            "US Foods login completed without issuing a renewable ordering token."
        )

    bearer, candidate = capture["result"]
    product_numbers = get_list_items(bearer, list_id)
    if not product_numbers:
        raise SystemExit(
            "US Foods authenticated, but the configured ordering list could not be read."
        )

    static_config = _static_usf_config(candidate, list_id)
    _set_github_secret("USF_REFRESH_TOKEN", candidate["refresh_token"], repository)
    _set_github_secret("USF_CONFIG", json.dumps(static_config), repository)
    print(
        "US Foods authentication is bootstrapped and ordering-list access was "
        f"verified ({len(product_numbers)} items)."
    )


if __name__ == "__main__":
    main()
