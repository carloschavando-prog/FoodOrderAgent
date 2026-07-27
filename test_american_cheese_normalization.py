import unittest

from order_normalization import cases_required, count_unit_for_item, units_per_case


class AmericanCheeseNormalizationTests(unittest.TestCase):
    def setUp(self):
        self.item = {
            "name": "American Slices 120 CT",
            "category_id": 6,
            "pack_size": "4/5 LB",
            "order_qty": 4,
        }
        self.item["count_unit"] = count_unit_for_item(self.item)

    def test_inventory_count_uses_five_pound_packs(self):
        self.assertEqual(self.item["count_unit"], "5-pound pack")

    def test_current_broadliner_case_contains_four_packs(self):
        vendor_packs = (
            ("4/5#", "Per slice; 4 packs x 120 slices"),
            ("4/5 LB", "Per slice; 4 packs x 120 slices"),
            ("", "Per slice; 4 packs x 120 slices"),
        )
        for pack_size, unit_note in vendor_packs:
            with self.subTest(pack_size=pack_size, unit_note=unit_note):
                pricing = {
                    "pack_size": pack_size,
                    "unit_basis": "each",
                    "unit_quantity": 480,
                    "unit_note": unit_note,
                }

                self.assertEqual(units_per_case(self.item, pricing), 4)
                self.assertEqual(cases_required(self.item, pricing), 1)

    def test_shortage_rounds_up_by_vendor_pack_count(self):
        self.item["order_qty"] = 5
        four_pack_case = {
            "pack_size": "4/5 LB",
            "unit_basis": "each",
            "unit_quantity": 480,
        }
        six_pack_case = {
            "pack_size": "6/5 LB",
            "unit_basis": "lb",
            "unit_quantity": 30,
        }

        self.assertEqual(cases_required(self.item, four_pack_case), 2)
        self.assertEqual(cases_required(self.item, six_pack_case), 1)


if __name__ == "__main__":
    unittest.main()
