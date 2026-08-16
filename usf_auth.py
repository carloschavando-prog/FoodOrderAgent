"""US Foods legacy B2C refresh-token helpers.

This module contains only authentication exchanges. It never prints or stores
token values. New credential recovery uses the provider's interactive browser
policy in :mod:`browser_auth` because this B2C policy does not support ROPC.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


B2C_TENANT = "usfoodsb2cprod.onmicrosoft.com"
B2C_POLICY = "b2c_1a_signin_sellersandcustomers"
B2C_CLIENT_ID = "bb101b81-7868-40b5-85d9-dbc155ba41d9"
B2C_TOKEN_URL = (
    "https://usfoodsb2cprod.b2clogin.com/"
    f"{B2C_TENANT}/{B2C_POLICY}/oauth2/v2.0/token"
)
B2C_SCOPE = f"openid offline_access {B2C_CLIENT_ID}"


class USFAuthError(RuntimeError):
    """A sanitized US Foods authentication failure."""


def _post_token(payload, *, opener=urllib.request.urlopen):
    request = urllib.request.Request(
        B2C_TOKEN_URL,
        data=urllib.parse.urlencode(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with opener(request, timeout=20) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            error_code = json.loads(exc.read()).get("error", "oauth_error")
        except Exception:
            error_code = "oauth_error"
        raise USFAuthError(
            f"US Foods B2C refresh failed (HTTP {exc.code}, {error_code})."
        ) from None
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise USFAuthError(
            f"US Foods B2C endpoint was unavailable ({type(exc).__name__})."
        ) from None

    access_token = result.get("access_token", "")
    refresh_token = result.get("refresh_token", "")
    if not access_token or not refresh_token:
        raise USFAuthError(
            "US Foods B2C refresh did not return both access and "
            "refresh tokens. MFA or an interactive login may be required."
        )
    return result


def refresh_grant(refresh_token, *, opener=urllib.request.urlopen):
    """Refresh a legacy B2C token chain."""
    if not refresh_token:
        raise USFAuthError("USF_REFRESH_TOKEN is not configured.")
    return _post_token(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": B2C_CLIENT_ID,
            "scope": B2C_SCOPE,
        },
        opener=opener,
    )


def apply_b2c_result(config, result):
    """Update config in memory and return a bearer header without logging it."""
    token_type = result.get("token_type", "Bearer")
    access_token = result["access_token"]
    config["refresh_provider"] = "b2c"
    config["refresh_token"] = result["refresh_token"]
    return f"{token_type} {access_token}"
