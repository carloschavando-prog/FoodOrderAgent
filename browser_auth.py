"""Headless browser fallbacks for vendor credential authentication.

The provider login pages are interactive identity-provider applications, not
stable password-grant APIs. These helpers keep credentials in memory, never
take screenshots, and return only the tokens/configuration needed by the
existing read-only catalog clients.
"""

from __future__ import annotations

import base64
import json
import re
import time


SYSCO_LOGIN_URL = "https://shop.sysco.com/auth/login"
SYSCO_VALIDATE_URL = "https://auth.shop.sysco.com/api/v1/auth/validate"
USF_ORDER_LIST_URL = "https://order.usfoods.com/desktop/lists"
USF_TOKEN_PATH = "panamax-api.ama.usfoods.com/auth-api/v1/oauth/token"
USF_AUTHORIZE_URL = (
    "https://usfoodsb2cprod.b2clogin.com/"
    "usfoodsb2cprod.onmicrosoft.com/oauth2/v2.0/authorize"
    "?p=b2c_1a_signin_sellersandcustomers"
    "&client_id=bb101b81-7868-40b5-85d9-dbc155ba41d9"
    "&response_type=id_token"
    "&redirect_uri=https%3A%2F%2Fwww.usfoods.com%2Fusfdce%2Flogin%2F"
    "validation%2Fcallback%2Fj_security_check"
    "&scope=openid%20offline_access%20bb101b81-7868-40b5-85d9-dbc155ba41d9"
    "&response_mode=query&state=%252F"
)


class BrowserAuthError(RuntimeError):
    """A sanitized interactive authentication failure."""


def _playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise BrowserAuthError(
            "Browser authentication support is not installed."
        ) from None
    return sync_playwright


def _launch_context(playwright):
    browser = playwright.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled"],
    )
    context = browser.new_context(
        viewport={"width": 1440, "height": 900},
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
    )
    return browser, context


def _jwt_context(credentials):
    try:
        payload = credentials.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError, json.JSONDecodeError):
        decoded = {}
    return decoded.get("csrf_token", ""), decoded.get("vid", "")


def _sysco_result(payload):
    if payload.get("role") != "CUSTOMER":
        raise BrowserAuthError(
            "Sysco browser login did not establish a customer session."
        )
    credentials = payload.get("gatewayCredentials", "")
    if not credentials:
        raise BrowserAuthError(
            "Sysco browser login returned no catalog credentials."
        )
    csrf_token, vid = _jwt_context(credentials)
    return (
        f"Bearer {credentials}",
        payload.get("shopAccountId", ""),
        csrf_token,
        vid,
    )


def sysco_password_login(email, password):
    """Authenticate to Sysco in a fresh browser and return API context."""
    if not email or not password:
        raise BrowserAuthError(
            "SYSCO_EMAIL and SYSCO_PASSWORD must both be configured."
        )

    sync_playwright = _playwright()
    with sync_playwright() as playwright:
        browser, context = _launch_context(playwright)
        stage = "open-login"
        try:
            page = context.new_page()
            page.goto(SYSCO_LOGIN_URL, wait_until="domcontentloaded", timeout=45_000)

            stage = "submit-username"
            username = page.get_by_role(
                "textbox", name=re.compile(r"email or username", re.I)
            ).first
            username.wait_for(state="visible", timeout=15_000)
            username.fill(email)
            page.get_by_role("button", name="Next", exact=True).click()

            stage = "wait-for-password"
            password_box = page.locator(
                "input[type='password'], input[name='credentials.passcode']"
            ).first
            password_box.wait_for(state="visible", timeout=30_000)
            stage = "submit-password"
            password_box.fill(password)
            password_box.press("Enter")

            stage = "validate-customer-session"
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                response = context.request.get(
                    SYSCO_VALIDATE_URL,
                    headers={
                        "Accept": "application/json",
                        "Origin": "https://shop.sysco.com",
                        "Referer": "https://shop.sysco.com/",
                    },
                    timeout=10_000,
                )
                if response.ok:
                    try:
                        return _sysco_result(response.json())
                    except (BrowserAuthError, ValueError, json.JSONDecodeError):
                        pass

                if password_box.is_visible():
                    alerts = page.locator(
                        "[role='alert'], .o-form-error-container, .okta-form-infobox-error"
                    )
                    if alerts.count() and alerts.first.is_visible():
                        raise BrowserAuthError(
                            "Sysco rejected the configured email/password."
                        )

                one_time_code = page.locator(
                    "input[autocomplete='one-time-code'], "
                    "input[name*='verification'], input[name*='otp']"
                )
                if one_time_code.count() and one_time_code.first.is_visible():
                    raise BrowserAuthError(
                        "Sysco accepted the password but requires an interactive MFA code."
                    )
                page.wait_for_timeout(1_000)

            raise BrowserAuthError(
                "Sysco browser login did not complete before the timeout."
            )
        except BrowserAuthError:
            raise
        except Exception as exc:
            raise BrowserAuthError(
                "Sysco browser authentication failed during "
                f"{stage} ({type(exc).__name__})."
            ) from None
        finally:
            browser.close()


def _usf_candidate(request_payload, request_headers, response_payload):
    access_token = response_payload.get("accessToken", "")
    refresh_token = response_payload.get("refreshToken", "")
    token_type = response_payload.get("tokenType", "Bearer")
    if not access_token or not refresh_token:
        raise BrowserAuthError(
            "US Foods browser login returned an incomplete token response."
        )
    candidate = {
        "refresh_token": refresh_token,
        "auth_context": request_payload.get("authContext", {}),
        "scopes": request_payload.get("scopes", ""),
        "platform": request_payload.get("platform", "DESKTOP"),
        "consumer_id": request_headers.get("consumer-id", "ecom"),
    }
    if not candidate["auth_context"] or not candidate["scopes"]:
        raise BrowserAuthError(
            "US Foods browser login did not expose the refresh configuration."
        )
    return f"{token_type} {access_token}", candidate


def _classify_usf_credential_step(visible_text, has_visible_password=False):
    normalized = " ".join(visible_text.lower().split())
    if re.search(r"secondary\s+(user\s+)?id|secondary\s+identifier", normalized):
        return "secondary-id"
    if "enter your password" in normalized or has_visible_password:
        return "password"
    return ""


def _visible_usf_field(page, step):
    if step == "secondary-id":
        selectors = (
            "input[aria-label*='secondary' i]:visible, "
            "input[placeholder*='secondary' i]:visible, "
            "#modal-input:visible, input[name*='secondary' i]:visible"
        )
    else:
        selectors = (
            "#modal-input:visible, #passwordInput:visible, "
            "input[type='password']:visible"
        )
    field = page.locator(selectors).first
    field.wait_for(state="visible", timeout=5_000)
    return field


def _click_visible_usf_button(page, names):
    pattern = re.compile(
        rf"^({'|'.join(re.escape(name) for name in names)})$",
        re.I,
    )
    for button in page.get_by_role("button", name=pattern).all():
        if button.is_visible():
            button.click()
            return
    raise BrowserAuthError(
        "US Foods displayed a credential step without a Continue button."
    )


def usf_password_login(user_id_value, password, secondary_id=""):
    """Log in through US Foods B2C and capture a fresh Panamax token chain."""
    if not user_id_value or not password:
        raise BrowserAuthError("USF_EMAIL and USF_PASSWORD must both be configured.")

    sync_playwright = _playwright()
    with sync_playwright() as playwright:
        browser, context = _launch_context(playwright)
        capture = {}
        stage = "open-user-id-page"
        try:
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

            stage = "submit-user-id"
            user_id = page.locator(
                "#signInName, input[placeholder='User ID'], "
                "input[name*='user'], input[id*='user']"
            ).first
            user_id.wait_for(state="visible", timeout=15_000)
            user_id.fill(user_id_value)
            page.get_by_role(
                "button", name=re.compile(r"^log in$", re.I)
            ).first.click()

            stage = "identify-next-credential-step"
            deadline = time.monotonic() + 35
            secondary_submitted = False
            password_box = None
            while time.monotonic() < deadline:
                visible_text = page.locator("body").inner_text(timeout=5_000)
                visible_passwords = page.locator(
                    "#modal-input:visible, #passwordInput:visible, "
                    "input[type='password']:visible"
                )
                step = _classify_usf_credential_step(
                    visible_text,
                    has_visible_password=visible_passwords.count() > 0,
                )
                if step == "secondary-id":
                    stage = "submit-secondary-id"
                    if secondary_submitted:
                        raise BrowserAuthError(
                            "US Foods rejected the configured secondary ID."
                        )
                    if not secondary_id:
                        raise BrowserAuthError(
                            "USF_SECONDARY_ID is required by this US Foods account."
                        )
                    secondary_box = _visible_usf_field(page, step)
                    secondary_box.fill(secondary_id)
                    _click_visible_usf_button(page, ("Continue", "Log in"))
                    secondary_submitted = True
                    page.wait_for_timeout(1_500)
                    continue
                if step == "password":
                    stage = "submit-password"
                    password_box = _visible_usf_field(page, step)
                    password_box.fill(password)
                    _click_visible_usf_button(page, ("Continue", "Log in"))
                    break
                page.wait_for_timeout(500)
            else:
                raise BrowserAuthError(
                    "US Foods did not display the next credential step."
                )

            try:
                stage = "complete-provider-login"
                page.wait_for_url(
                    lambda url: "b2clogin.com" not in url,
                    timeout=45_000,
                )
            except Exception:
                if password_box is not None and password_box.is_visible():
                    raise BrowserAuthError(
                        "US Foods rejected the configured credentials."
                    ) from None
                raise BrowserAuthError(
                    "US Foods requires an additional interactive sign-in step."
                ) from None

            stage = "open-ordering-list"
            page.goto(
                USF_ORDER_LIST_URL,
                wait_until="domcontentloaded",
                timeout=45_000,
            )
            deadline = time.monotonic() + 45
            stage = "capture-renewable-token"
            while time.monotonic() < deadline and "result" not in capture:
                page.wait_for_timeout(1_000)
            if "result" not in capture:
                raise BrowserAuthError(
                    "US Foods login succeeded, but no renewable ordering token was issued."
                )
            return capture["result"]
        except BrowserAuthError:
            raise
        except Exception as exc:
            raise BrowserAuthError(
                "US Foods browser authentication failed during "
                f"{stage} ({type(exc).__name__})."
            ) from None
        finally:
            browser.close()
