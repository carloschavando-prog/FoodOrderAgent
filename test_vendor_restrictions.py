import unittest
from unittest.mock import patch

import weekly_order
from api import generate_order, item_master
from scrape_gfs import match_item as match_gfs_item
from vendor_restrictions import vendor_allowed_for_item


def item(item_id, name, qty=1):
    return {
        "id": item_id,
        "name": name,
        "order_qty": qty,
        "category_id": 8,
        "par_level": qty,
    }


class VendorRestrictionTests(unittest.TestCase):
    def test_bulk_sugar_blocks_removed_pfg_sku_and_sysco(self):
        self.assertFalse(vendor_allowed_for_item("Bulk Sugar", 2, "FL098"))
        self.assertTrue(vendor_allowed_for_item("Bulk Sugar", 2, "CORRECTED"))
        self.assertFalse(vendor_allowed_for_item("Bulk Sugar", 3))
        for vendor_id in (1, 4):
            self.assertTrue(vendor_allowed_for_item("Bulk Sugar", vendor_id))

    def test_sliced_red_tomatoes_block_removed_pfg_sku_only(self):
        self.assertFalse(vendor_allowed_for_item("Sliced Red Tomatoes", 2, "VL638"))
        self.assertTrue(vendor_allowed_for_item("Sliced Red Tomatoes", 2, "CORRECTED"))
        for vendor_id in (1, 3, 4):
            self.assertTrue(vendor_allowed_for_item("Sliced Red Tomatoes", vendor_id))

    def test_ranch_mix_is_excluded_but_corrected_sysco_ranch_is_allowed(self):
        self.assertFalse(vendor_allowed_for_item("Ranch Dressing", 3, "4428298"))
        self.assertTrue(vendor_allowed_for_item("Ranch Dressing", 3, "1344033"))

    def test_sysco_audit_blocks_wrong_skus_and_allows_replacements(self):
        corrected = {
            "Chafing Fuel Can 4 Hour": ("7092795", "4783678"),
            "Chicken Wings": ("8439794", "9556481"),
            "Fire Roasted Salsa": ("7775069", "7143211"),
            "Ranch Dressing": ("4428298", "1344033"),
            "Yellow Mustard": ("1608850", "4006797"),
        }
        for item_name, (wrong_apn, correct_apn) in corrected.items():
            with self.subTest(item_name=item_name):
                self.assertFalse(vendor_allowed_for_item(item_name, 3, wrong_apn))
                self.assertTrue(vendor_allowed_for_item(item_name, 3, correct_apn))

    def test_sysco_ea_only_spices_and_out_of_stock_bars_are_blocked(self):
        blocked = {
            "Coarse Ground Black Pepper": "5229273",
            "Garlic Powder": "9806449",
            "Hungarian Style Paprika": "5229224",
            "Variety Dessert Bars": "4290474",
        }
        for item_name, apn in blocked.items():
            with self.subTest(item_name=item_name):
                self.assertFalse(vendor_allowed_for_item(item_name, 3, apn))

    def test_user_approved_us_foods_matches_are_orderable(self):
        self.assertTrue(
            vendor_allowed_for_item("Chafing Fuel Can 4 Hour", 1, "2912061")
        )
        self.assertTrue(vendor_allowed_for_item("Limes", 1, "4667994"))
        self.assertFalse(
            vendor_allowed_for_item("Pecorino Romano Blend", 1, "3588381")
        )

    def test_pfg_audit_blocks_disabled_and_mismatched_skus(self):
        blocked = {
            "Bulk Sugar": "FL098",
            "Chafing Fuel Can 4 Hour": "FC002",
            "Oranges": "HB846",
            "Sliced Red Tomatoes": "VL638",
            "Use First Stickers": "N7184",
        }
        for item_name, apn in blocked.items():
            with self.subTest(item_name=item_name, apn=apn):
                self.assertFalse(vendor_allowed_for_item(item_name, 2, apn))
                self.assertTrue(vendor_allowed_for_item(item_name, 2, "CORRECTED"))

    def test_user_approved_pfg_substitutes_are_orderable(self):
        approved = {
            "Double Lobe Chicken Breasts": "JP006",
            "Fire Roasted Salsa": "E6508",
            "Flatbread Dough": "JV526",
            "Parmesan Cheese": "DA702",
        }
        for item_name, apn in approved.items():
            with self.subTest(item_name=item_name, apn=apn):
                self.assertTrue(vendor_allowed_for_item(item_name, 2, apn))

    def test_pfg_verified_potato_bun_is_orderable(self):
        self.assertTrue(vendor_allowed_for_item("Potato Hamburger Bun", 2, "TTN38"))

    def test_item_master_replaces_stale_ranch_mix_with_verified_ranch_sku(self):
        def fake_sb_get_all(path, page_size=1000):
            if path.startswith("items?"):
                return [{"id": 78, "name": "Ranch Dressing", "category_id": 6}]
            if path.startswith("price_lists?"):
                return [{"id": 1, "pulled_at": "2026-08-13T09:58:40Z"}]
            if path.startswith("pricing?"):
                return [{
                    "item_id": 78,
                    "vendor_id": 3,
                    "apn": "4428298",
                    "price": 41.79,
                    "price_list_id": 1,
                    "pulled_at": None,
                    "pack_size": None,
                    "unit_basis": "oz",
                    "unit_quantity": 512,
                    "unit_price": 0.0816,
                    "unit_note": None,
                    "vendor_item_name": "Dressing Mix Ranch",
                }]
            if path.startswith("item_vendor_status?"):
                return []
            raise AssertionError(f"Unexpected Supabase path: {path}")

        with patch.object(item_master, "sb_get_all", side_effect=fake_sb_get_all):
            canonical_items, prices = item_master.load_data()

        self.assertEqual([row["name"] for row in canonical_items], ["Ranch Dressing"])
        self.assertEqual(prices[78][3]["apn"], "1344033")
        self.assertIsNone(prices[78][3]["price"])

    def test_us_foods_only_chemicals_exclude_other_broadliners(self):
        for item_name in (
            "Aluminum 1/3 Pans",
            "Dishmachine Detergent",
            "Low Temp Sanitizer",
            "Pot & Pan Detergent",
            "Pre Soak",
            "Quat Sanitizer",
            "Sanitizing Floor Cleaner",
            "Solid Dish Detergent",
        ):
            self.assertTrue(vendor_allowed_for_item(item_name, 1))
            for vendor_id in (2, 3, 4):
                self.assertFalse(vendor_allowed_for_item(item_name, vendor_id))

    def test_mozzarella_sticks_is_us_foods_only(self):
        self.assertTrue(vendor_allowed_for_item("Mozzarella Sticks", 1, "7332687"))
        for vendor_id in (2, 3, 4):
            self.assertFalse(vendor_allowed_for_item("Mozzarella Sticks", vendor_id))

    def test_item_master_shows_dishmachine_detergent_only_for_us_foods(self):
        def fake_sb_get_all(path, page_size=1000):
            if path.startswith("items?"):
                return [{"id": 108, "name": "Dishmachine Detergent", "category_id": 8}]
            if path.startswith("price_lists?"):
                return [{"id": 1, "pulled_at": "2026-08-25T12:00:00Z"}]
            if path.startswith("pricing?"):
                return [
                    {
                        "item_id": 108,
                        "vendor_id": vendor_id,
                        "apn": apn,
                        "price": price,
                        "price_list_id": 1,
                        "pulled_at": None,
                        "pack_size": "1 EA",
                        "unit_basis": "each",
                        "unit_quantity": 1,
                        "unit_price": price,
                        "unit_note": None,
                        "vendor_item_name": "Dishmachine Detergent",
                    }
                    for vendor_id, apn, price in (
                        (1, "1554679", 101.60),
                        (2, "DV986", 142.55),
                        (3, "7670118", 71.88),
                        (4, "666858", 134.50),
                    )
                ]
            if path.startswith("item_vendor_status?"):
                return []
            raise AssertionError(f"Unexpected Supabase path: {path}")

        with patch.object(item_master, "sb_get_all", side_effect=fake_sb_get_all):
            canonical_items, prices = item_master.load_data()

        self.assertEqual([row["name"] for row in canonical_items], ["Dishmachine Detergent"])
        self.assertEqual(set(prices[108]), {1})
        self.assertEqual(prices[108][1]["apn"], "1554679")

    def test_item_master_shows_verified_sku_when_price_is_pending(self):
        def fake_sb_get_all(path, page_size=1000):
            if path.startswith("items?"):
                return [{"id": 118, "name": "Aluminum 1/3 Pans", "category_id": 9}]
            if path.startswith("price_lists?") or path.startswith("pricing?"):
                return []
            if path.startswith("item_vendor_status?"):
                return []
            raise AssertionError(f"Unexpected Supabase path: {path}")

        with patch.object(item_master, "sb_get_all", side_effect=fake_sb_get_all):
            canonical_items, prices = item_master.load_data()

        self.assertEqual([row["name"] for row in canonical_items], ["Aluminum 1/3 Pans"])
        self.assertEqual(set(prices[118]), {1})
        self.assertEqual(prices[118][1]["apn"], "7737075")
        self.assertIsNone(prices[118][1]["price"])

    def test_item_master_shows_new_us_foods_skus_while_prices_are_pending(self):
        def fake_sb_get_all(path, page_size=1000):
            if path.startswith("items?"):
                return [
                    {"id": 239, "name": "Mozzarella Sticks", "category_id": 7},
                    {"id": 240, "name": "Vanilla Monin", "category_id": 9},
                ]
            if path.startswith("price_lists?") or path.startswith("pricing?"):
                return []
            if path.startswith("item_vendor_status?"):
                return []
            raise AssertionError(f"Unexpected Supabase path: {path}")

        with patch.object(item_master, "sb_get_all", side_effect=fake_sb_get_all):
            canonical_items, prices = item_master.load_data()

        self.assertEqual(
            [row["name"] for row in canonical_items],
            ["Mozzarella Sticks", "Vanilla Monin"],
        )
        self.assertEqual(prices[239][1]["apn"], "7332687")
        self.assertEqual(prices[240][1]["apn"], "8231367")
        self.assertIsNone(prices[239][1]["price"])
        self.assertIsNone(prices[240][1]["price"])

    def test_item_master_replaces_removed_us_foods_sku_with_pending_approval(self):
        def fake_sb_get_all(path, page_size=1000):
            if path.startswith("items?"):
                return [{
                    "id": 67,
                    "name": "Pecorino Romano Blend",
                    "category_id": 6,
                }]
            if path.startswith("price_lists?"):
                return [{"id": 1, "pulled_at": "2026-05-27T00:42:15Z"}]
            if path.startswith("pricing?"):
                return [{
                    "item_id": 67,
                    "vendor_id": 1,
                    "apn": "3588381",
                    "price": 136.10,
                    "price_list_id": 1,
                    "pulled_at": None,
                    "pack_size": "4/5 LB",
                    "unit_basis": "lb",
                    "unit_quantity": 20,
                    "unit_price": 6.805,
                    "unit_note": None,
                    "vendor_item_name": "Cheese, Romano Pecorino Grated Bag Ref",
                }]
            if path.startswith("item_vendor_status?"):
                return [{
                    "item_id": 67,
                    "vendor_id": 1,
                    "apn": None,
                    "status": "pending_approval",
                    "note": "Pending approval while we wait for our US Foods representative to get back to us.",
                    "vendor_item_name": None,
                    "pack_size": "4/5 LB",
                    "price_available": False,
                    "blocks_ordering": True,
                    "verified_on": "2026-08-26",
                    "source": "us_foods_audit_2026_08_26",
                }]
            raise AssertionError(f"Unexpected Supabase path: {path}")

        with patch.object(item_master, "sb_get_all", side_effect=fake_sb_get_all):
            canonical_items, prices = item_master.load_data()

        self.assertEqual([row["name"] for row in canonical_items], ["Pecorino Romano Blend"])
        self.assertEqual(prices[67][1]["apn"], "")
        self.assertIsNone(prices[67][1]["price"])
        self.assertEqual(prices[67][1]["availability"], "pending_approval")
        page = item_master.build_html(item_master.assign_op_ids(canonical_items), prices)
        self.assertIn("Pending approval", page)
        self.assertIn("wait for our US Foods representative", page)
        self.assertIn("1 checks · 0 price changes", page)

    def test_item_master_accepts_corrected_chafing_fuel_and_lime_matches(self):
        def fake_sb_get_all(path, page_size=1000):
            if path.startswith("items?"):
                return [
                    {"id": 71, "name": "Limes", "category_id": 6},
                    {"id": 116, "name": "Chafing Fuel Can 4 Hour", "category_id": 9},
                ]
            if path.startswith("price_lists?"):
                return [{"id": 173, "pulled_at": "2026-08-25T16:57:43Z"}]
            if path.startswith("pricing?"):
                return [
                    {
                        "item_id": 71,
                        "vendor_id": 1,
                        "apn": "4667994",
                        "price": 18.41,
                        "price_list_id": 173,
                        "pulled_at": None,
                        "pack_size": "48 EA",
                        "unit_basis": "each",
                        "unit_quantity": 48,
                        "unit_price": 0.3835,
                        "unit_note": None,
                        "vendor_item_name": "Lime, #1 Grade 48 Count Fresh",
                    },
                    {
                        "item_id": 116,
                        "vendor_id": 1,
                        "apn": "2912061",
                        "price": 98.17,
                        "price_list_id": 173,
                        "pulled_at": None,
                        "pack_size": "24 EA",
                        "unit_basis": "each",
                        "unit_quantity": 24,
                        "unit_price": 4.0904,
                        "unit_note": None,
                        "vendor_item_name": "Fuel, Chafing Can 4 Hour Wick",
                    },
                ]
            if path.startswith("item_vendor_status?"):
                return []
            raise AssertionError(f"Unexpected Supabase path: {path}")

        with patch.object(item_master, "sb_get_all", side_effect=fake_sb_get_all):
            canonical_items, prices = item_master.load_data()

        self.assertEqual(18.41, prices[71][1]["price"])
        self.assertEqual(98.17, prices[116][1]["price"])
        page = item_master.build_html(item_master.assign_op_ids(canonical_items), prices)
        self.assertNotIn("Identity review needed", page)
        self.assertNotIn("Product mismatch", page)

    def test_contracted_chemicals_are_assigned_to_us_foods(self):
        items = [
            item(-1, "Low Temp Sanitizer"),
            item(0, "Dishmachine Detergent"),
            item(1, "Pot & Pan Detergent"),
            item(2, "Pre Soak"),
            item(3, "Heavy Duty Rinse Additive"),
            item(4, "Quat Sanitizer"),
            item(5, "Sanitizing Floor Cleaner"),
            item(6, "Solid Dish Detergent"),
            item(7, "Aluminum 1/3 Pans"),
        ]
        prices = {
            row["id"]: {
                1: {"price": 100.0, "apn": "USF"},
                4: {"price": 50.0, "apn": "GFS"},
            }
            for row in items
        }

        for module in (weekly_order, generate_order):
            assignment = module.assign_cheapest(items, prices, {1, 4})
            self.assertEqual(
                assignment,
                {-1: 1, 0: 1, 1: 1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 1, 7: 1},
            )

    def test_oven_cleaner_remains_open_to_cheapest_vendor(self):
        oven_cleaner = [item(4, "Oven Cleaner")]
        prices = {
            4: {
                1: {"price": 68.17, "apn": "USF"},
                4: {"price": 52.22, "apn": "GFS"},
            }
        }

        for module in (weekly_order, generate_order):
            assignment = module.assign_cheapest(oven_cleaner, prices, {1, 4})
            self.assertEqual(assignment, {4: 4})

    def test_contracted_chemicals_cannot_fill_another_vendor_minimum(self):
        contracted = item(5, "Pot & Pan Detergent", qty=0)
        prices = {
            5: {
                1: {"price": 86.50, "apn": "USF"},
                4: {"price": 49.40, "apn": "GFS"},
            }
        }

        for module in (weekly_order, generate_order):
            plan = module.build_rescue_fillers(
                4, [contracted], prices, {}, {}
            )
            self.assertEqual(plan, {})

    def test_sliced_red_onions_are_never_assigned_to_gfs(self):
        onions = [item(73, "Sliced Red Onions")]
        prices = {
            73: {
                1: {"price": 32.35, "apn": "6425730"},
                4: {"price": 13.89, "apn": "313157"},
            }
        }

        for module in (weekly_order, generate_order):
            assignment = module.assign_cheapest(onions, prices, {1, 4})
            self.assertEqual(assignment, {73: 1})

    def test_sliced_red_onions_cannot_be_forced_to_gfs(self):
        onions = item(73, "Sliced Red Onions")
        prices = {73: {4: {"price": 13.89, "apn": "313157"}}}

        with self.assertRaisesRegex(ValueError, "temporarily archived"):
            generate_order.apply_order_overrides(
                [onions],
                prices,
                {"Sliced Red Onions": {"quantity": 1, "vendorId": 4}},
            )

    def test_gfs_scraper_does_not_restore_sliced_red_onion_mapping(self):
        item_map = {
            "by_apn": {"313157": 73},
            "by_name": {"sliced red onions": 73},
            "item_name_by_id": {73: "Sliced Red Onions"},
        }

        self.assertIsNone(
            match_gfs_item("Onions, Red, Sliced", "313157", item_map)
        )


if __name__ == "__main__":
    unittest.main()
