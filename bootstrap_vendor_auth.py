"""Secure, interactive reconnect for PFG and GFS.

The user completes the provider login in a temporary local browser. Renewable
session material stays in memory, is validated against a read-only order guide,
and is then sent to GitHub Secrets through stdin. This script never saves a
browser profile, takes screenshots, reads a cart, or places an order.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.parse

import scrape_gfs
import scrape_pfg
from github_secrets import SecretPromotionError, set_repository_secret


DEFAULT_REPOSITORY = "carloschavando-prog/FoodOrderAgent"
PFG_PORTAL_URL = "https://www.customerfirstsolutions.com/"
PFG_TOKEN_HOST = "pfgcustomerfirst.b2clogin.com"
PFG_TOKEN_PATH = "/oauth2/v2.0/token"
PFG_CUSTOMER_ID = "ccbddeae-bc43-4287-a4e0-8d5bee2b913c"
PFG_LIST_ID = "13e8ce85-8f4e-4cfe-a6dd-cac49a88dc60"
GFS_PORTAL_URL = "https://order.gfs.com/home"
GFS_COOKIE_NAMES = {
    "GOR": "gor",
    "GCLB": "gclb",
    "XSRF-TOKEN": "xsrf",
    "__Secure-GORDONORDERING2": "session",
}


class VendorReconnectError(RuntimeError):
    """A sanitized reconnect failure safe to show in CI or a terminal."""


def _pfg_token_candidate(payload):
    access = payload.get("access_token") or payload.get("id_token")
    refresh = payload.get("refresh_token")
    if not access or not refresh:
        raise VendorReconnectError(
            "PFG login did not issue both catalog and renewable credentials."
        )
    return f"Bearer {access}", refresh


def _gfs_cookie_payload(cookies):
    selected = {
        target: cookie.get("value", "")
        for cookie in cookies
        for source, target in GFS_COOKIE_NAMES.items()
        if cookie.get("name") == source
    }
    selected.setdefault("gor", "us-central1")
    selected.setdefault("gclb", "")
    if not selected.get("xsrf") or not selected.get("session"):
        raise VendorReconnectError(
            "GFS login did not establish a complete ordering session."
        )
    return selected


def _gfs_material_count(guide):
    materials = {
        material
        for category in guide.get("guideCategories", [])
        for material in category.get("materialNumbers", [])
    }
    return len(materials)


def _launch_browser(playwright):
    try:
        return playwright.chromium.launch(channel="chrome", headless=False)
    except Exception:
        return playwright.chromium.launch(headless=False)


def reconnect_pfg(playwright, repository, *, timeout_seconds=600):
    capture = {}
    browser = _launch_browser(playwright)
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()

    def capture_token(response):
        parsed = urllib.parse.urlsplit(response.url)
        if (
            parsed.hostname != PFG_TOKEN_HOST
            or PFG_TOKEN_PATH not in parsed.path
            or not response.ok
        ):
            return
        try:
            capture["candidate"] = _pfg_token_candidate(response.json())
        except (VendorReconnectError, ValueError, json.JSONDecodeError):
            return

    print("Opening a private PFG login window.")
    print("Complete the provider prompts; the window closes after validation.")
    try:
        page.on("response", capture_token)
        page.goto(PFG_PORTAL_URL, wait_until="domcontentloaded", timeout=45_000)
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline and "candidate" not in capture:
            page.wait_for_timeout(500)
    finally:
        browser.close()

    if "candidate" not in capture:
        raise VendorReconnectError(
            "PFG login completed without issuing a renewable catalog session."
        )
    bearer, refresh_token = capture["candidate"]
    products = scrape_pfg.get_products(bearer, PFG_CUSTOMER_ID, PFG_LIST_ID)
    if not products:
        raise VendorReconnectError(
            "PFG authenticated, but its configured product list could not be read."
        )
    set_repository_secret("PFG_REFRESH_TOKEN", refresh_token, repository)
    print(f"PFG reconnect verified against {len(products)} catalog products.")


def reconnect_gfs(playwright, repository, *, timeout_seconds=600):
    browser = _launch_browser(playwright)
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    session_payload = None

    print("Opening a private GFS login window.")
    print("Complete the provider prompts; the window closes after validation.")
    try:
        page.goto(GFS_PORTAL_URL, wait_until="domcontentloaded", timeout=45_000)
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                session_payload = _gfs_cookie_payload(context.cookies())
                break
            except VendorReconnectError:
                page.wait_for_timeout(500)
    finally:
        browser.close()

    if session_payload is None:
        raise VendorReconnectError(
            "GFS login completed without establishing an ordering session."
        )
    cookies = {
        source: session_payload[target]
        for source, target in GFS_COOKIE_NAMES.items()
        if session_payload.get(target)
    }
    guide = scrape_gfs.gfs_get("v6/lists/order-guide", cookies)
    material_count = _gfs_material_count(guide)
    if not material_count:
        raise VendorReconnectError(
            "GFS authenticated, but its order guide could not be read."
        )
    set_repository_secret("GFS_COOKIES", json.dumps(session_payload), repository)
    print(f"GFS reconnect verified against {material_count} order-guide materials.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vendor", choices=("pfg", "gfs"))
    parser.add_argument(
        "--repository",
        default=os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPOSITORY),
    )
    args = parser.parse_args(argv)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit(
            "Playwright is not installed. Run: python3 -m pip install -r requirements.txt"
        ) from None

    try:
        with sync_playwright() as playwright:
            if args.vendor == "pfg":
                reconnect_pfg(playwright, args.repository)
            else:
                reconnect_gfs(playwright, args.repository)
    except (SecretPromotionError, VendorReconnectError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()

