import unittest
from unittest.mock import patch

from api.item_master import (
    build_html,
    build_inventory_pricing,
    cheapest_comparable_quote,
    fmt_timestamp,
    load_data,
    price_history_summary,
)


class InventoryPricingTests(unittest.TestCase):
    @staticmethod
    def quote(price, quantity, basis="lb", verified="2026-08-12T12:00:00Z"):
        return {
            "price": price,
            "pack_size": f"{quantity} {basis}",
            "unit_basis": basis,
            "unit_quantity": quantity,
            "unit_price": None,
            "pulled_at": verified,
        }

    def test_selects_lowest_normalized_price_and_preserves_case_price(self):
        winner = cheapest_comparable_quote(
            {
                1: self.quote(60.00, 20),
                3: self.quote(55.00, 10),
                4: self.quote(52.00, 20),
            }
        )

        self.assertEqual(4, winner["vendor_id"])
        self.assertEqual(52.00, winner["price"])
        self.assertAlmostEqual(2.60, winner["unit_price"])
        self.assertEqual(3, winner["comparable_quotes"])

    def test_does_not_compare_incompatible_units(self):
        winner = cheapest_comparable_quote(
            {
                1: self.quote(30.00, 10, "lb"),
                3: self.quote(18.00, 12, "each"),
            }
        )

        self.assertIsNone(winner)

    def test_requires_two_verified_quotes(self):
        winner = cheapest_comparable_quote(
            {
                1: self.quote(30.00, 10),
                3: self.quote(28.00, 10, verified=None),
            }
        )

        self.assertIsNone(winner)

    def test_page_explains_the_verification_timestamp(self):
        page = build_html([], {})

        self.assertIn("Live Supabase history", page)
        self.assertIn("Price checked", page)
        self.assertIn("All 3 active vendors", page)
        self.assertNotIn(">GFS<", page)

    def test_price_history_counts_only_actual_price_changes(self):
        history = price_history_summary([
            {"price": 10, "apn": "A", "pulled_at": "2026-08-01T12:00:00Z"},
            {"price": 10, "apn": "A", "pulled_at": "2026-08-02T12:00:00Z"},
            {"price": 12, "apn": "A", "pulled_at": "2026-08-03T12:00:00Z"},
            {"price": 12, "apn": "B", "pulled_at": "2026-08-04T12:00:00Z"},
        ])

        self.assertEqual(4, history["observations"])
        self.assertEqual(1, history["price_changes"])
        self.assertEqual(3, len(history["events"]))

    def test_inventory_feed_converts_case_price_to_the_count_unit(self):
        items = [{
            "id": 19,
            "name": "Garlic Parmesan",
            "category_id": 4,
        }]
        prices = {
            19: {
                2: {
                    **self.quote(46.00, 256, "oz"),
                    "apn": "PFG-19",
                    "pack_size": "2/1 GAL",
                },
            },
        }

        feed = build_inventory_pricing(items, prices)
        quote = feed["items"][0]["quotes"]["2"]

        self.assertEqual("gallon", feed["items"][0]["count_unit"])
        self.assertEqual(2, quote["units_per_case"])
        self.assertEqual(23.00, quote["price_per_count_unit"])
        self.assertEqual("2026-08-12T12:00:00Z", quote["price_checked_at"])

    def test_inventory_feed_omits_blocked_and_unconvertible_quotes(self):
        items = [{
            "id": 19,
            "name": "Garlic Parmesan",
            "category_id": 4,
        }]
        blocked = {
            **self.quote(46.00, 256, "oz"),
            "apn": "BLOCKED",
            "blocks_ordering": True,
        }
        missing_conversion = {
            **self.quote(40.00, 1, "mystery"),
            "apn": "UNKNOWN",
        }

        feed = build_inventory_pricing(items, {19: {2: blocked, 3: missing_conversion}})

        self.assertEqual({}, feed["items"][0]["quotes"])

    def test_inventory_feed_understands_portal_roll_and_number_ten_can_packs(self):
        items = [
            {"id": 3, "name": "Labels", "category_id": 1},
            {"id": 29, "name": "Pizza Sauce", "category_id": 4},
        ]
        prices = {
            3: {
                1: {
                    **self.quote(24.29, 250, "label"),
                    "apn": "LABELS",
                    "pack_size": "1 RL",
                },
            },
            29: {
                2: {
                    **self.quote(42.99, 6, "can"),
                    "apn": "SAUCE",
                    "pack_size": "6/#10Can",
                },
            },
        }

        feed = build_inventory_pricing(items, prices)
        rows = {row["name"]: row for row in feed["items"]}

        self.assertEqual(
            24.29,
            rows["Labels"]["quotes"]["1"]["price_per_count_unit"],
        )
        self.assertAlmostEqual(
            42.99 / 6,
            rows["Pizza Sauce"]["quotes"]["2"]["price_per_count_unit"],
        )

    def test_postgres_variable_fraction_timestamp_is_human_readable(self):
        value = fmt_timestamp("2026-07-21T23:54:50.26699+00:00")

        self.assertEqual("Jul 21, 2026 7:54 PM EDT", value)

    def test_excludes_house_built_simple_syrup_from_item_master(self):
        item_rows = [
            {"id": 78, "name": "Ranch Dressing", "category_id": 6},
            {"id": 237, "name": "Simple Syrup", "category_id": 6},
        ]
        with patch(
            "api.item_master.sb_get_all",
            side_effect=[item_rows, [], [], []],
        ):
            items, prices = load_data()

        self.assertEqual(["Ranch Dressing"], [item["name"] for item in items])
        self.assertEqual("1344033", prices[78][3]["apn"])
        self.assertIsNone(prices[78][3]["price"])

    def test_cold_cups_sort_between_straws_and_styrofoam(self):
        item_rows = [
            {"id": 41, "name": "Styrofoam To-Go Containers", "category_id": 5},
            {"id": 44, "name": "Straws", "category_id": 5},
            {"id": 238, "name": "16 oz To-Go Cold Cups", "category_id": 5},
        ]
        with patch(
            "api.item_master.sb_get_all",
            side_effect=[item_rows, [], [], []],
        ):
            items, prices = load_data()

        self.assertEqual(
            ["Straws", "16 oz To-Go Cold Cups", "Styrofoam To-Go Containers"],
            [item["name"] for item in items],
        )
        self.assertEqual({}, prices)

    def test_vanilla_monin_sorts_immediately_after_dailys(self):
        item_rows = [
            {"id": 151, "name": "Chafing Fuel Can 6 Hour", "category_id": 9},
            {"id": 150, "name": "Daily's Sweet & Sour Mix", "category_id": 9},
            {"id": 240, "name": "Vanilla Monin", "category_id": 9},
        ]
        with patch(
            "api.item_master.sb_get_all",
            side_effect=[item_rows, [], [], []],
        ):
            items, prices = load_data()

        self.assertEqual(
            ["Chafing Fuel Can 6 Hour", "Daily's Sweet & Sour Mix", "Vanilla Monin"],
            [item["name"] for item in items],
        )
        self.assertEqual("8231367", prices[240][1]["apn"])
        self.assertIsNone(prices[240][1]["price"])


if __name__ == "__main__":
    unittest.main()
