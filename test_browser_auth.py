import base64
import json
import unittest
from unittest import mock

import browser_auth


class BrowserAuthParsingTests(unittest.TestCase):
    def test_identifies_secondary_id_before_password(self):
        self.assertEqual(
            browser_auth._classify_usf_credential_step(
                "Enter your secondary ID to continue",
                has_visible_password=True,
            ),
            "secondary-id",
        )

    def test_identifies_visible_password_modal(self):
        self.assertEqual(
            browser_auth._classify_usf_credential_step(
                "Enter your password below to continue"
            ),
            "password",
        )

    def test_safe_field_state_never_reads_values(self):
        field = mock.Mock()
        field.get_attribute.side_effect = lambda name: {
            "id": "signInName-facade",
            "aria-label": "User ID",
            "placeholder": "User ID",
            "name": "User ID",
            "type": "text",
        }.get(name)
        page = mock.Mock()
        page.locator.return_value.all.return_value = [field]

        self.assertEqual(browser_auth._safe_usf_field_state(page), "user-id")

    def test_builds_usf_refresh_candidate_without_credentials(self):
        bearer, candidate = browser_auth._usf_candidate(
            {
                "authContext": {"customer": "configured"},
                "scopes": "ordering",
                "platform": "DESKTOP",
            },
            {"consumer-id": "ecom"},
            {
                "tokenType": "Bearer",
                "accessToken": "access-value",
                "refreshToken": "refresh-value",
            },
        )

        self.assertEqual(bearer, "Bearer access-value")
        self.assertEqual(candidate["refresh_token"], "refresh-value")
        self.assertEqual(candidate["auth_context"], {"customer": "configured"})

    def test_rejects_incomplete_usf_token_response(self):
        with self.assertRaises(browser_auth.BrowserAuthError):
            browser_auth._usf_candidate({}, {}, {"accessToken": "access-value"})

    def test_parses_sysco_customer_context(self):
        payload = base64.urlsafe_b64encode(
            json.dumps({"csrf_token": "csrf", "vid": "visitor"}).encode()
        ).decode().rstrip("=")
        result = browser_auth._sysco_result(
            {
                "role": "CUSTOMER",
                "gatewayCredentials": f"header.{payload}.signature",
                "shopAccountId": "account",
            }
        )

        self.assertEqual(result, ("Bearer header.%s.signature" % payload, "account", "csrf", "visitor"))


if __name__ == "__main__":
    unittest.main()
