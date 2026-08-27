import unittest

from api import generate_order
from order_normalization import count_unit_for_item


def item(item_id, name, qty=1, count_unit="case"):
    return {
        "id": item_id,
        "name": name,
        "order_qty": qty,
        "category_id": 7,
        "par_level": qty,
        "count_unit": count_unit,
        "on_hand": 0,
        "party_need": 0,
    }


class OrderOverrideTests(unittest.TestCase):
    def test_case_override_controls_exact_vendor_quantity(self):
        flatbread = item(85, "Flatbread Dough", qty=2)
        prices = {
            85: {
                1: {"price": 59.95, "apn": "USF", "units_per_case": 18},
                2: {"price": 63.34, "apn": "PFG", "units_per_case": 28},
            }
        }

        generate_order.apply_order_overrides(
            [flatbread],
            prices,
            {"Flatbread Dough": {"quantity": 4, "mode": "cases", "vendorId": 2}},
        )

        self.assertEqual(flatbread["manual_case_qty"], 4)
        self.assertEqual(flatbread["forced_vendor_id"], 2)
        self.assertEqual(generate_order.filler_cap(flatbread), 0)
        assignment = generate_order.assign_cheapest([flatbread], prices, {1, 2})
        self.assertEqual(assignment, {85: 2})
        vendor_items, vendor_cases, _ = generate_order.calc_totals(
            assignment, {85: flatbread}, prices
        )
        self.assertEqual(vendor_cases, {2: 4})
        self.assertEqual(vendor_items[2][0]["cases"], 4)

    def test_zero_case_override_removes_item(self):
        mayo = item(24, "Mayo Packets", qty=1)
        prices = {24: {1: {"price": 20, "apn": "MAYO", "units_per_case": 1}}}

        generate_order.apply_order_overrides(
            [mayo], prices, {"Mayo Packets": {"quantity": 0, "mode": "cases"}}
        )

        self.assertEqual(mayo["order_qty"], 0)
        self.assertEqual(generate_order.assign_cheapest([mayo], prices, {1}), {})

    def test_count_unit_override_is_converted_to_a_vendor_case(self):
        salsa = item(36, "Fire Roasted Salsa", qty=0, count_unit="68-ounce container")
        prices = {
            36: {
                1: {"price": 47.78, "apn": "SALSA", "units_per_case": 4},
            }
        }

        generate_order.apply_order_overrides(
            [salsa], prices, {"Fire Roasted Salsa": {"quantity": 1, "mode": "count_unit"}}
        )
        assignment = generate_order.assign_cheapest([salsa], prices, {1})
        _, vendor_cases, _ = generate_order.calc_totals(
            assignment, {36: salsa}, prices
        )

        self.assertEqual(salsa["order_qty"], 1)
        self.assertEqual(vendor_cases, {1: 1})

    def test_minimum_case_override_raises_small_order_without_capping_large_order(self):
        pretzel = item(125, "24 Ounce Pretzel", qty=8, count_unit="each")
        prices = {
            125: {
                1: {
                    "price": 58.25,
                    "apn": "PRETZEL24",
                    "pack_size": "8/24 OZ",
                    "units_per_case": 2,
                }
            }
        }

        generate_order.apply_order_overrides(
            [pretzel],
            prices,
            {
                "24 Ounce Pretzel": {
                    "quantity": 4,
                    "mode": "minimum_cases",
                    "required_pack": "24 oz",
                }
            },
        )

        assignment = generate_order.assign_cheapest([pretzel], prices, {1})
        _, vendor_cases, _ = generate_order.calc_totals(
            assignment, {125: pretzel}, prices
        )
        self.assertEqual(vendor_cases, {1: 4})

        pretzel["order_qty"] = 12
        _, vendor_cases, _ = generate_order.calc_totals(
            assignment, {125: pretzel}, prices
        )
        self.assertEqual(vendor_cases, {1: 6})

    def test_required_pretzel_pack_removes_incompatible_quotes(self):
        pretzel = item(125, "24 Ounce Pretzel", qty=1, count_unit="each")
        prices = {
            125: {
                1: {
                    "price": 20,
                    "apn": "WRONG",
                    "pack_size": "8/16 OZ",
                    "units_per_case": 8,
                },
                2: {
                    "price": 50,
                    "apn": "RIGHT",
                    "pack_size": "9/24 OZ",
                    "units_per_case": 9,
                },
            }
        }

        generate_order.apply_order_overrides(
            [pretzel],
            prices,
            {
                "24 Ounce Pretzel": {
                    "quantity": 4,
                    "mode": "minimum_cases",
                    "required_pack": "24 oz",
                }
            },
        )

        self.assertEqual(set(prices[125]), {2})
        self.assertEqual(generate_order.assign_cheapest([pretzel], prices, {1, 2}), {125: 2})

    def test_august_25_specials_are_delivery_scoped_and_authoritative(self):
        manager = {"Pizza Cheese": {"quantity": 7, "mode": "cases"}}

        future = generate_order.order_overrides_for_delivery("2026-08-29", manager)
        special = generate_order.order_overrides_for_delivery("2026-08-25", manager)

        self.assertEqual(future, manager)
        self.assertEqual(special["Pizza Cheese"]["quantity"], 1)
        self.assertEqual(special["Sliced Red Tomatoes"]["quantity"], 2)
        self.assertEqual(special["Green Scrubbies"]["quantity"], 0)
        self.assertEqual(special["Crushed Red Pepper Packets"]["quantity"], 0)
        self.assertEqual(special["Tenders"], {"quantity": 5, "mode": "cases", "vendorId": 1})
        self.assertEqual(special["Fries"], {"quantity": 6, "mode": "cases", "vendorId": 2})
        self.assertEqual(special["24 Ounce Pretzel"]["mode"], "minimum_cases")
        self.assertEqual(special["24 Ounce Pretzel"]["required_pack"], "24 oz")

    def test_august_25_order_is_consolidated_to_us_foods_and_pfg(self):
        self.assertEqual(
            generate_order.broadliner_ids_for_delivery("2026-08-25"),
            {1, 2},
        )
        self.assertEqual(
            generate_order.broadliner_ids_for_delivery("2026-08-29"),
            set(generate_order.BROADLINER_IDS),
        )

    def test_optimizer_honors_an_explicit_broadliner_set(self):
        stocked = item(201, "Test Stock", qty=20)
        prices = {
            201: {
                1: {"price": 10, "apn": "USF", "units_per_case": 1},
                2: {"price": 9, "apn": "PFG", "units_per_case": 1},
                4: {"price": 1, "apn": "GFS", "units_per_case": 1},
            }
        }

        assignment, dropped, unassigned, notes, filler_cases = (
            generate_order.optimize_basket([stocked], prices, active_vendors={1, 2})
        )

        self.assertEqual(assignment, {201: 2})
        self.assertEqual(dropped, {3})
        self.assertEqual(unassigned, [])
        self.assertEqual(filler_cases, {})
        self.assertTrue(any("Broadliner consolidation" in note for note in notes))

    def test_unknown_item_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown manual order override item"):
            generate_order.apply_order_overrides(
                [item(1, "Known")], {}, {"Unknown": {"quantity": 1}}
            )

    def test_specialty_inventory_units_survive_generated_snapshot(self):
        self.assertEqual(
            count_unit_for_item({"name": "Use First Stickers", "category_id": 1}),
            "roll",
        )
        self.assertEqual(
            count_unit_for_item({"name": "Holy Gospel", "category_id": 2}),
            "5-pound bag",
        )


if __name__ == "__main__":
    unittest.main()
