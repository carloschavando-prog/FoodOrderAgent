import unittest
from unittest import mock

from api import vendor_preflight


class VendorPreflightTests(unittest.TestCase):
    def test_all_selected_vendors_must_be_ready(self):
        with mock.patch.object(
            vendor_preflight,
            "check_vendor",
            side_effect=lambda vendor_id: {
                "vendorId": vendor_id,
                "vendor": vendor_preflight.VENDOR_NAMES[vendor_id],
                "ready": vendor_id != 2,
                "error": None if vendor_id != 2 else "expired",
            },
        ):
            results = vendor_preflight.check_vendors([1, 2, 3])

        self.assertEqual([1, 2, 3], [row["vendorId"] for row in results])
        self.assertFalse(all(row["ready"] for row in results))

    def test_pfg_preflight_authenticates_without_creating_an_order(self):
        with mock.patch.object(
            vendor_preflight.pfg,
            "authenticate_pfg",
            return_value=("Bearer token", {}),
        ) as authenticate, mock.patch.object(
            vendor_preflight.pfg, "get_or_create_order_header"
        ) as create:
            result = vendor_preflight.check_vendor(2)

        self.assertTrue(result["ready"])
        authenticate.assert_called_once_with()
        create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
