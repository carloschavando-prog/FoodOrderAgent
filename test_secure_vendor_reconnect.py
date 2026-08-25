import json
import subprocess
import unittest
from unittest import mock

import bootstrap_vendor_auth
import complete_pfg_oauth
import github_secrets


class GitHubSecretTests(unittest.TestCase):
    def test_secret_is_sent_only_through_stdin(self):
        runner = mock.Mock(
            return_value=subprocess.CompletedProcess([], 0, "", "")
        )

        github_secrets.set_repository_secret(
            "PFG_REFRESH_TOKEN",
            "sensitive-value",
            "owner/repository",
            runner=runner,
        )

        args, kwargs = runner.call_args
        self.assertNotIn("sensitive-value", args[0])
        self.assertEqual(kwargs["input"], "sensitive-value")
        self.assertTrue(kwargs["capture_output"])

    def test_empty_secret_is_rejected_without_invoking_gh(self):
        runner = mock.Mock()

        with self.assertRaises(github_secrets.SecretPromotionError):
            github_secrets.set_repository_secret(
                "GFS_COOKIES", "", "owner/repository", runner=runner
            )

        runner.assert_not_called()


class ReconnectParsingTests(unittest.TestCase):
    def test_pfg_requires_access_and_refresh_credentials(self):
        self.assertEqual(
            bootstrap_vendor_auth._pfg_token_candidate(
                {"access_token": "access", "refresh_token": "refresh"}
            ),
            ("Bearer access", "refresh"),
        )
        with self.assertRaises(bootstrap_vendor_auth.VendorReconnectError):
            bootstrap_vendor_auth._pfg_token_candidate(
                {"access_token": "access"}
            )

    def test_gfs_selects_only_required_ordering_cookies(self):
        payload = bootstrap_vendor_auth._gfs_cookie_payload(
            [
                {"name": "GOR", "value": "region"},
                {"name": "XSRF-TOKEN", "value": "xsrf"},
                {"name": "__Secure-GORDONORDERING2", "value": "session"},
                {"name": "unrelated", "value": "discard-me"},
            ]
        )

        self.assertEqual(
            payload,
            {"gor": "region", "gclb": "", "xsrf": "xsrf", "session": "session"},
        )
        self.assertNotIn("unrelated", json.dumps(payload))

    def test_gfs_material_count_deduplicates_the_guide(self):
        count = bootstrap_vendor_auth._gfs_material_count(
            {
                "guideCategories": [
                    {"materialNumbers": ["1", "2"]},
                    {"materialNumbers": ["2", "3"]},
                ]
            }
        )

        self.assertEqual(count, 3)


class PFGAuthorizationCodeTests(unittest.TestCase):
    def test_exchange_uses_pkce_and_returns_renewable_token(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(
            {"access_token": "access", "refresh_token": "refresh"}
        ).encode()
        opener = mock.Mock(return_value=response)

        bearer, refresh = complete_pfg_oauth.exchange_authorization_code(
            "one-time-code", "pkce-verifier", opener=opener
        )

        self.assertEqual((bearer, refresh), ("Bearer access", "refresh"))
        request = opener.call_args.args[0]
        body = request.data.decode()
        self.assertIn("grant_type=authorization_code", body)
        self.assertIn("code_verifier=pkce-verifier", body)

    @mock.patch("complete_pfg_oauth.set_repository_secret")
    @mock.patch("complete_pfg_oauth.scrape_pfg.get_products", return_value=[])
    @mock.patch(
        "complete_pfg_oauth.exchange_authorization_code",
        return_value=("Bearer access", "refresh"),
    )
    def test_failed_catalog_validation_does_not_promote_token(
        self, _exchange, _products, promote
    ):
        with mock.patch("pathlib.Path.read_text", return_value=json.dumps(
            {"code": "code", "verifier": "verifier"}
        )), mock.patch("pathlib.Path.unlink") as unlink:
            with self.assertRaises(complete_pfg_oauth.PFGOAuthError):
                complete_pfg_oauth.complete_reconnect(
                    "/tmp/context", "owner/repository"
                )

        unlink.assert_called_once_with(missing_ok=True)
        promote.assert_not_called()


if __name__ == "__main__":
    unittest.main()
