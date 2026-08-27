import unittest
from unittest.mock import patch

import weekly_order
from api import generate_order


ITEM = {
    "id": 83,
    "all_ids": [83],
    "name": "Potato Hamburger Bun",
    "category_id": 7,
    "pack_size": "5/12",
    "par_level": 3.0,
    "on_hand": 0.0,
    "party_need": 0.0,
    "order_qty": 3.0,
    "event_driven": False,
    "count_unit": "case",
    "preferred_vid": 1,
}


class MissingVendorSkuTests(unittest.TestCase):
    def test_optimizer_ignores_cheaper_price_without_vendor_sku(self):
        prices = {
            ITEM["id"]: {
                2: {"price": 25.69, "apn": "", "units_per_case": 1.0},
                3: {"price": 30.26, "apn": "7303801", "units_per_case": 1.0},
            }
        }

        for module in (generate_order, weekly_order):
            with self.subTest(module=module.__name__):
                self.assertEqual(
                    module.assign_cheapest([ITEM], prices, {2, 3}),
                    {ITEM["id"]: 3},
                )

    def test_loader_excludes_price_rows_without_vendor_sku(self):
        item_rows = [{
            "id": 83,
            "name": "Potato Hamburger Bun",
            "category_id": 7,
            "pack_size": "5/12",
            "par_level": 3,
            "preferred_vendor_id": 1,
        }]
        pricing_rows = [
            {
                "item_id": 83,
                "vendor_id": 2,
                "apn": None,
                "price": 25.69,
                "price_list_id": 1,
                "pack_size": "5/12",
                "unit_basis": "each",
                "unit_quantity": 60,
                "unit_note": "",
                "vendor_item_name": "",
            },
            {
                "item_id": 83,
                "vendor_id": 3,
                "apn": "7303801",
                "price": 30.26,
                "price_list_id": 2,
                "pack_size": "5/12 EA",
                "unit_basis": "each",
                "unit_quantity": 60,
                "unit_note": "",
                "vendor_item_name": "Potato hamburger buns",
            },
        ]

        with patch.object(
            generate_order, "sb_get_all", side_effect=[item_rows, pricing_rows]
        ):
            items, prices = generate_order.load_data(
                {"potato hamburger bun": 0}, "friday"
            )

        self.assertEqual(items[0]["order_qty"], 3)
        self.assertNotIn(2, prices[83])
        self.assertEqual(prices[83][3]["apn"], "7303801")


if __name__ == "__main__":
    unittest.main()
