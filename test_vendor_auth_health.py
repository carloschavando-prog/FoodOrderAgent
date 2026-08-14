import os
import unittest
from unittest import mock

import scrape_usfoods
import usf_auth
import vendor_auth_health


class USFoodsRecoveryTests(unittest.TestCase):
    @mock.patch.dict(
        os.environ,
        {"USF_EMAIL": "account-name", "USF_PASSWORD": "password-value"},
        clear=False,
    )
    @mock.patch("scrape_usfoods.save_config")
    @mock.patch("scrape_usfoods.get_list_items", return_value=[101, 202])
    @mock.patch(
        "scrape_usfoods.password_grant",
        return_value={
            "token_type": "Bearer",
            "access_token": "access-value",
            "refresh_token": "replacement-refresh",
        },
    )
    @mock.patch(
        "scrape_usfoods.refresh_token",
        side_effect=usf_auth.USFAuthError("expired"),
    )
    def test_password_recovery_is_validated_before_promotion(
        self,
        _refresh,
        password_grant,
        get_list_items,
        save_config,
    ):
        config = {"fall_2025_list_id": 123, "refresh_token": "old-refresh"}

        bearer = scrape_usfoods.authenticate(config)

        self.assertEqual(bearer, "Bearer access-value")
        self.assertEqual(config["refresh_provider"], "b2c")
        self.assertEqual(config["refresh_token"], "replacement-refresh")
        password_grant.assert_called_once_with("account-name", "password-value")
        get_list_items.assert_called_once_with("Bearer access-value", 123)
        save_config.assert_called_once_with(config, persist_static=True)

    @mock.patch.dict(
        os.environ,
        {"USF_EMAIL": "account-name", "USF_PASSWORD": "password-value"},
        clear=False,
    )
    @mock.patch("scrape_usfoods.save_config")
    @mock.patch("scrape_usfoods.get_list_items", return_value=[])
    @mock.patch(
        "scrape_usfoods.password_grant",
        return_value={
            "token_type": "Bearer",
            "access_token": "access-value",
            "refresh_token": "replacement-refresh",
        },
    )
    @mock.patch(
        "scrape_usfoods.refresh_token",
        side_effect=usf_auth.USFAuthError("expired"),
    )
    def test_failed_catalog_validation_preserves_existing_secrets(
        self,
        _refresh,
        _password_grant,
        _get_list_items,
        save_config,
    ):
        config = {"fall_2025_list_id": 123, "refresh_token": "old-refresh"}

        with self.assertRaises(usf_auth.USFAuthError):
            scrape_usfoods.authenticate(config)

        self.assertEqual(config["refresh_token"], "old-refresh")
        self.assertNotIn("refresh_provider", config)
        save_config.assert_not_called()


class VendorHealthCheckTests(unittest.TestCase):
    @mock.patch.dict(
        os.environ,
        {"SYSCO_EMAIL": "account-name", "SYSCO_PASSWORD": "password-value"},
        clear=False,
    )
    @mock.patch("vendor_auth_health.scrape_sysco.fetch_order_guide", return_value=[{}])
    @mock.patch(
        "vendor_auth_health.scrape_sysco.get_bearer_token",
        return_value=("Bearer access", "account", "csrf", "vid"),
    )
    def test_sysco_check_forces_password_path(self, get_token, fetch_order_guide):
        vendor_auth_health.check_sysco()

        get_token.assert_called_once_with(
            "account-name",
            "password-value",
            allow_cookies=False,
        )
        fetch_order_guide.assert_called_once()


if __name__ == "__main__":
    unittest.main()
