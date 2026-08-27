import pathlib
import unittest
from unittest.mock import patch

from api import generate_order


class GenerateOrderPriceSourceTests(unittest.TestCase):
    def setUp(self):
        self.order_item = {
            "id": 19,
            "name": "Garlic Parmesan",
            "category_id": 4,
            "count_unit": "gallon",
            "order_qty": 2,
        }
        self.master_item = {
            "id": 19,
            "name": "Garlic Parmesan",
            "category_id": 4,
        }

    @staticmethod
    def quote(vendor_item, price, **overrides):
        row = {
            "apn": vendor_item,
            "price": price,
            "pack_size": "2/1 GAL",
            "unit_basis": "gallon",
            "unit_quantity": 2,
            "unit_note": "",
            "vendor_item_name": "Garlic parmesan sauce",
            "pulled_at": "2026-08-27T12:00:00Z",
        }
        row.update(overrides)
        return row

    def test_optimizer_prices_are_built_from_approved_item_master_quotes(self):
        prices = generate_order.build_order_prices_from_item_master(
            [self.order_item],
            [self.master_item],
            {
                19: {
                    1: self.quote("USF-19", 50.00),
                    2: self.quote(
                        "PFG-19",
                        40.00,
                        blocks_ordering=True,
                        availability="identity_review",
                    ),
                }
            },
        )

        self.assertEqual({1}, set(prices[19]))
        self.assertEqual("USF-19", prices[19][1]["apn"])
        self.assertEqual(2, prices[19][1]["units_per_case"])

    def test_live_loader_uses_item_master_data(self):
        master_prices = {19: {1: self.quote("USF-19", 50.00)}}
        with patch(
            "api.item_master.load_data",
            return_value=([self.master_item], master_prices),
        ) as item_master_loader:
            prices = generate_order.load_order_prices_from_item_master(
                [self.order_item]
            )

        item_master_loader.assert_called_once_with()
        self.assertEqual(50.00, prices[19][1]["price"])

    def test_generate_handler_refreshes_item_master_before_optimization(self):
        source = pathlib.Path("api/generate_order.py").read_text()
        handler = source.split("def do_POST(self):", 1)[1]

        item_master_refresh = handler.index(
            "load_order_prices_from_item_master(canonical_items)"
        )
        optimization = handler.index("optimize_basket(")

        self.assertLess(item_master_refresh, optimization)
        self.assertIn("load_prices=False", handler[:item_master_refresh])


if __name__ == "__main__":
    unittest.main()
