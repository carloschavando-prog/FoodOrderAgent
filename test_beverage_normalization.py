import unittest

from order_normalization import count_unit_for_item, units_per_case


class BeverageNormalizationTests(unittest.TestCase):
    def setUp(self):
        self.vanilla = {
            "name": "Vanilla Monin",
            "category_id": 9,
        }

    def test_vanilla_monin_is_counted_by_bottle(self):
        self.assertEqual(count_unit_for_item(self.vanilla), "bottle")

    def test_bottle_case_pack_uses_the_inner_pack_count(self):
        pricing = {
            "pack_size": "4/1 L",
            "unit_basis": "liter",
            "unit_quantity": 4,
        }

        self.assertEqual(units_per_case(self.vanilla, pricing), 4)

    def test_ambiguous_bottle_pack_is_not_assumed(self):
        pricing = {
            "pack_size": "1 L",
            "unit_basis": "liter",
            "unit_quantity": 1,
        }

        self.assertIsNone(units_per_case(self.vanilla, pricing))


if __name__ == "__main__":
    unittest.main()
