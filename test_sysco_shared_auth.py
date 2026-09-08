import contextlib
import io
import os
import unittest
from unittest.mock import Mock, patch

import scrape_sysco
from api import place_order_sysco as sysco
from api.vendor_auth_bridge import ALLOWED_VENDOR_IDS


class SharedSyscoAuthTests(unittest.TestCase):
    def test_saved_connection_is_verified_for_expected_account_before_commit(self):
        lease = Mock(credentials={"cookies": "saved-session"})
        client = Mock()
        client.claim.return_value = lease
        result = ("access", "expected-account", "csrf", "visitor")
        with patch("api.vendor_auth.VendorAuthClient.from_env", return_value=client), patch.object(
            sysco, "_validate_with_cookies", return_value=result
        ) as validate:
            self.assertEqual(result, sysco.authenticate_shared_sysco())
        client.claim.assert_called_once_with(3)
        validate.assert_called_once_with("saved-session", require_account=True)
        lease.commit.assert_called_once_with(lease.credentials, verified=True)
        lease.fail.assert_not_called()

    def test_expired_or_wrong_account_session_is_not_committed(self):
        lease = Mock(credentials={"cookies": "expired-session"})
        client = Mock()
        client.claim.return_value = lease
        with patch("api.vendor_auth.VendorAuthClient.from_env", return_value=client), patch.object(
            sysco, "_validate_with_cookies", side_effect=RuntimeError("account mismatch")
        ):
            with self.assertRaises(RuntimeError):
                sysco.authenticate_shared_sysco()
        lease.commit.assert_not_called()
        lease.fail.assert_called_once()

    def test_api_does_not_fall_back_to_stale_environment_cookies(self):
        with patch.dict(os.environ, {"SYSCO_SHARED_AUTH": "true", "SYSCO_COOKIES": "stale"}), patch.object(
            sysco, "authenticate_shared_sysco", side_effect=RuntimeError("reconnect required")
        ), patch.object(sysco, "_validate_with_cookies") as stale:
            with self.assertRaisesRegex(RuntimeError, "reconnect required"):
                sysco.get_bearer_token("unused", "unused")
        stale.assert_not_called()

    def test_scraper_uses_the_same_saved_sign_in(self):
        expected = ("access", "account", "csrf", "visitor")
        with patch.dict(os.environ, {"SYSCO_SHARED_AUTH": "true"}), patch.object(
            sysco, "authenticate_shared_sysco", return_value=expected
        ) as shared:
            self.assertEqual(expected, scrape_sysco.get_bearer_token("unused", "unused"))
        shared.assert_called_once_with()

    def test_shared_scraper_does_not_require_legacy_login_secrets(self):
        class AuthenticationReached(Exception):
            pass
        with patch.dict(os.environ, {"SYSCO_SHARED_AUTH": "true"}, clear=True), patch.object(
            scrape_sysco, "get_bearer_token", side_effect=AuthenticationReached
        ) as authenticate, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(AuthenticationReached):
                scrape_sysco.main()
        authenticate.assert_called_once()

    def test_bridge_is_limited_to_the_three_supported_shared_connections(self):
        self.assertEqual({1, 2, 3}, ALLOWED_VENDOR_IDS)


if __name__ == "__main__":
    unittest.main()
