import os
import unittest
from unittest import mock

import scrape_usfoods
import usf_auth
import vendor_auth_health


class USFoodsRecoveryTests(unittest.TestCase):
    @mock.patch.dict(
        os.environ,
        {
            "USF_EMAIL": "account-name",
            "USF_SECONDARY_ID": "secondary-value",
            "USF_PASSWORD": "password-value",
        },
        clear=False,
    )
    @mock.patch("scrape_usfoods.save_config")
    @mock.patch("scrape_usfoods.get_list_items", return_value=[101, 202])
    @mock.patch(
        "browser_auth.usf_password_login",
        return_value=(
            "Bearer access-value",
            {
                "refresh_token": "replacement-refresh",
                "auth_context": {"customer": "configured"},
                "scopes": "ordering",
                "platform": "DESKTOP",
                "consumer_id": "ecom",
            },
        ),
    )
    @mock.patch(
        "scrape_usfoods.refresh_token",
        side_effect=usf_auth.USFAuthError("expired"),
    )
    def test_password_recovery_is_validated_before_promotion(
        self,
        _refresh,
        browser_login,
        get_list_items,
        save_config,
    ):
        config = {"fall_2025_list_id": 123, "refresh_token": "old-refresh"}

        bearer = scrape_usfoods.authenticate(config)

        self.assertEqual(bearer, "Bearer access-value")
        self.assertNotIn("refresh_provider", config)
        self.assertEqual(config["refresh_token"], "replacement-refresh")
        browser_login.assert_called_once_with(
            "account-name", "password-value", "secondary-value"
        )
        get_list_items.assert_called_once_with("Bearer access-value", 123)
        save_config.assert_called_once_with(config, persist_static=True)

    @mock.patch.dict(
        os.environ,
        {
            "USF_EMAIL": "account-name",
            "USF_SECONDARY_ID": "",
            "USF_PASSWORD": "password-value",
        },
        clear=False,
    )
    @mock.patch("scrape_usfoods.save_config")
    @mock.patch("scrape_usfoods.get_list_items", return_value=[])
    @mock.patch(
        "browser_auth.usf_password_login",
        return_value=(
            "Bearer access-value",
            {
                "refresh_token": "replacement-refresh",
                "auth_context": {"customer": "configured"},
                "scopes": "ordering",
            },
        ),
    )
    @mock.patch(
        "scrape_usfoods.refresh_token",
        side_effect=usf_auth.USFAuthError("expired"),
    )
    def test_failed_catalog_validation_preserves_existing_secrets(
        self,
        _refresh,
        _browser_login,
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

    @mock.patch("vendor_auth_health.scrape_pfg.save_config")
    @mock.patch("vendor_auth_health.scrape_pfg.get_products", return_value=[{}])
    @mock.patch(
        "vendor_auth_health.scrape_pfg.refresh_token",
        return_value="Bearer access",
    )
    @mock.patch(
        "vendor_auth_health.scrape_pfg.load_config",
        return_value={
            "customer_id": "customer",
            "fall_list_id": "list",
            "refresh_token": "refresh",
        },
    )
    def test_pfg_promotes_refresh_only_after_catalog_validation(
        self,
        load_config,
        refresh_token,
        get_products,
        save_config,
    ):
        vendor_auth_health.check_pfg()

        refresh_token.assert_called_once_with(load_config.return_value, persist=False)
        get_products.assert_called_once_with("Bearer access", "customer", "list")
        save_config.assert_called_once_with(load_config.return_value)

    @mock.patch("vendor_auth_health.scrape_gfs.gfs_get")
    @mock.patch(
        "vendor_auth_health.scrape_gfs.load_cookies",
        return_value={"session": "configured"},
    )
    def test_gfs_health_reads_only_the_order_guide(self, load_cookies, gfs_get):
        gfs_get.return_value = {
            "guideCategories": [{"materialNumbers": ["101", "102"]}]
        }

        vendor_auth_health.check_gfs()

        gfs_get.assert_called_once_with(
            "v6/lists/order-guide", load_cookies.return_value
        )


if __name__ == "__main__":
    unittest.main()
