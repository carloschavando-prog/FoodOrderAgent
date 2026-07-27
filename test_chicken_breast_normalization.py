import unittest

from order_normalization import cases_required, count_unit_for_item, units_per_case


class ChickenBreastNormalizationTests(unittest.TestCase):
    def setUp(self):
        self.item = {
            "name": "Double Lobe Chicken Breasts",
            "category_id": 6,
            "pack_size": "4/5 LB",
            "order_qty": 3,
        }
        self.item["count_unit"] = count_unit_for_item(self.item)

    def test_inventory_count_uses_twenty_pound_case(self):
        self.assertEqual(self.item["count_unit"], "20-pound case")

    def test_sysco_forty_pound_case_covers_two_inventory_cases(self):
        pricing = {"unit_basis": "lb", "unit_quantity": 40}

        self.assertEqual(units_per_case(self.item, pricing), 2)
        self.assertEqual(cases_required(self.item, pricing), 2)

    def test_twenty_pound_vendor_case_is_one_inventory_case(self):
        pricing = {"unit_basis": "lb", "unit_quantity": 20}

        self.assertEqual(units_per_case(self.item, pricing), 1)
        self.assertEqual(cases_required(self.item, pricing), 3)


if __name__ == "__main__":
    unittest.main()
