"""Deterministic tests for the US Foods credential OAuth helper."""

import io
import json
import unittest
import urllib.error
import urllib.parse

import usf_auth


class _Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


class USFoodsPasswordGrantTests(unittest.TestCase):
    def test_password_grant_uses_supplied_credentials_without_logging(self):
        captured = {}

        def opener(request, timeout):
            captured["timeout"] = timeout
            captured["body"] = urllib.parse.parse_qs(request.data.decode())
            return _Response(
                {
                    "token_type": "Bearer",
                    "access_token": "access-value",
                    "refresh_token": "refresh-value",
                }
            )

        result = usf_auth.password_grant(
            "account-name",
            "password-value",
            opener=opener,
        )

        self.assertEqual(result["refresh_token"], "refresh-value")
        self.assertEqual(captured["body"]["username"], ["account-name"])
        self.assertEqual(captured["body"]["password"], ["password-value"])
        self.assertEqual(captured["timeout"], 20)

    def test_password_grant_requires_both_secrets(self):
        with self.assertRaisesRegex(usf_auth.USFAuthError, "USF_EMAIL"):
            usf_auth.password_grant("", "")

    def test_http_error_is_sanitized(self):
        def opener(_request, timeout):
            self.assertEqual(timeout, 20)
            raise urllib.error.HTTPError(
                usf_auth.B2C_TOKEN_URL,
                400,
                "Bad Request",
                {},
                io.BytesIO(b'{"error":"invalid_grant","detail":"sensitive"}'),
            )

        with self.assertRaises(usf_auth.USFAuthError) as caught:
            usf_auth.password_grant("account-name", "password-value", opener=opener)

        message = str(caught.exception)
        self.assertIn("invalid_grant", message)
        self.assertNotIn("sensitive", message)
        self.assertNotIn("password-value", message)

    def test_apply_result_marks_b2c_provider(self):
        config = {"consumer_id": "ecom"}
        bearer = usf_auth.apply_b2c_result(
            config,
            {
                "token_type": "Bearer",
                "access_token": "access-value",
                "refresh_token": "refresh-value",
            },
        )

        self.assertEqual(bearer, "Bearer access-value")
        self.assertEqual(config["refresh_provider"], "b2c")
        self.assertEqual(config["refresh_token"], "refresh-value")


if __name__ == "__main__":
    unittest.main()
