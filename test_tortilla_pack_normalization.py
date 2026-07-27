import unittest

from order_normalization import cases_required, count_unit_for_item, units_per_case


class TortillaPackNormalizationTests(unittest.TestCase):
    def setUp(self):
        self.item = {
            "name": 'Tortilla, Flour 12"',
            "category_id": 3,
            "pack_size": "8/12",
            "order_qty": 7,
        }
        self.item["count_unit"] = count_unit_for_item(self.item)

    def test_inventory_counts_twelve_count_packs(self):
        self.assertEqual(self.item["count_unit"], "12-count pack")

    def test_ninety_six_count_case_contains_eight_packs(self):
        pricing = {"unit_basis": "each", "unit_quantity": 96}

        self.assertEqual(units_per_case(self.item, pricing), 8)
        self.assertEqual(cases_required(self.item, pricing), 1)

    def test_one_hundred_forty_four_count_case_contains_twelve_packs(self):
        pricing = {"unit_basis": "each", "unit_quantity": 144}

        self.assertEqual(units_per_case(self.item, pricing), 12)
        self.assertEqual(cases_required(self.item, pricing), 1)


if __name__ == "__main__":
    unittest.main()
