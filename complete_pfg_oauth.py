"""Complete a PFG OAuth authorization-code reconnect without browser storage.

The browser-facing step writes a one-time authorization code and PKCE verifier
to a permission-restricted temporary file. This helper removes that file as
soon as it is read, exchanges the code, validates the configured product list,
and promotes only the renewable token through GitHub Secrets stdin.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import urllib.error
import urllib.parse
import urllib.request

import scrape_pfg
from github_secrets import SecretPromotionError, set_repository_secret


DEFAULT_REPOSITORY = "carloschavando-prog/FoodOrderAgent"
PFG_CLIENT_ID = "c68e7fae-80a1-42db-bd89-3fb37d1224a2"
PFG_REDIRECT_URI = "https://www.customerfirstsolutions.com"
PFG_TOKEN_URL = (
    "https://pfgcustomerfirst.b2clogin.com/"
    "pfgcustomerfirst.onmicrosoft.com/"
    "B2C_1A_signup_signin/oauth2/v2.0/token"
)
PFG_SCOPE = (
    "https://pfgcustomerfirst.onmicrosoft.com/api/customer-first-site-api "
    "openid profile offline_access"
)
PFG_CUSTOMER_ID = "ccbddeae-bc43-4287-a4e0-8d5bee2b913c"
PFG_LIST_ID = "13e8ce85-8f4e-4cfe-a6dd-cac49a88dc60"


class PFGOAuthError(RuntimeError):
    """A sanitized PFG authorization-code failure."""


def exchange_authorization_code(code, verifier, *, opener=urllib.request.urlopen):
    if not code or not verifier:
        raise PFGOAuthError("PFG authorization context is incomplete.")
    request = urllib.request.Request(
        PFG_TOKEN_URL,
        data=urllib.parse.urlencode(
            {
                "grant_type": "authorization_code",
                "client_id": PFG_CLIENT_ID,
                "code": code,
                "redirect_uri": PFG_REDIRECT_URI,
                "code_verifier": verifier,
                "scope": PFG_SCOPE,
                "client_info": "1",
            }
        ).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with opener(request, timeout=20) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise PFGOAuthError(
            f"PFG authorization-code exchange was rejected (HTTP {exc.code})."
        ) from None
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise PFGOAuthError(
            f"PFG authorization-code exchange failed ({type(exc).__name__})."
        ) from None

    access = payload.get("access_token") or payload.get("id_token")
    refresh = payload.get("refresh_token")
    if not access or not refresh:
        raise PFGOAuthError(
            "PFG authorization did not return catalog and renewable credentials."
        )
    return f"Bearer {access}", refresh


def complete_reconnect(context_path, repository):
    path = pathlib.Path(context_path)
    try:
        context = json.loads(path.read_text())
    except (OSError, ValueError, json.JSONDecodeError):
        raise PFGOAuthError("PFG authorization context could not be read.") from None
    finally:
        path.unlink(missing_ok=True)

    bearer, refresh_token = exchange_authorization_code(
        context.get("code", ""), context.get("verifier", "")
    )
    products = scrape_pfg.get_products(
        bearer,
        context.get("customer_id", PFG_CUSTOMER_ID),
        context.get("list_id", PFG_LIST_ID),
    )
    if not products:
        raise PFGOAuthError(
            "PFG authorized, but the configured product list could not be read."
        )
    set_repository_secret("PFG_REFRESH_TOKEN", refresh_token, repository)
    print(f"PFG reconnect verified against {len(products)} catalog products.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("context_path")
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    args = parser.parse_args(argv)
    try:
        complete_reconnect(args.context_path, args.repository)
    except (PFGOAuthError, SecretPromotionError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()

