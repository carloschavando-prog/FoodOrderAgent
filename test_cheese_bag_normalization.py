import unittest

from order_normalization import cases_required, count_unit_for_item, units_per_case


class CheeseBagNormalizationTests(unittest.TestCase):
    def _item(self, name, order_qty=1):
        item = {
            "name": name,
            "category_id": 6,
            "order_qty": order_qty,
        }
        item["count_unit"] = count_unit_for_item(item)
        return item

    def test_cheese_inventory_counts_five_pound_bags(self):
        for name in (
            "Pecorino Romano Blend",
            "Parmesan Cheese",
            "Pizza Cheese",
        ):
            with self.subTest(name=name):
                self.assertEqual(
                    count_unit_for_item(self._item(name)),
                    "5-pound bag",
                )

    def test_twenty_pound_case_contains_four_bags(self):
        item = self._item("Pecorino Romano Blend", order_qty=5)
        pricing = {
            "pack_size": "4/5 LB",
            "unit_basis": "lb",
            "unit_quantity": 20,
        }

        self.assertEqual(units_per_case(item, pricing), 4)
        self.assertEqual(cases_required(item, pricing), 2)

    def test_ten_pound_case_contains_two_bags(self):
        item = self._item("Parmesan Cheese", order_qty=3)
        pricing = {
            "pack_size": "2/5 LB",
            "unit_basis": "lb",
            "unit_quantity": 10,
        }

        self.assertEqual(units_per_case(item, pricing), 2)
        self.assertEqual(cases_required(item, pricing), 2)

    def test_pizza_cheese_par_requires_two_six_bag_cases(self):
        item = self._item("Pizza Cheese", order_qty=10)
        pricing = {
            "pack_size": "6/5 LB",
            "unit_basis": "lb",
            "unit_quantity": 30,
        }

        self.assertEqual(units_per_case(item, pricing), 6)
        self.assertEqual(cases_required(item, pricing), 2)


if __name__ == "__main__":
    unittest.main()
