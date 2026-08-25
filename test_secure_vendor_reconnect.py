import json
import subprocess
import unittest
from unittest import mock

import bootstrap_vendor_auth
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


if __name__ == "__main__":
    unittest.main()
