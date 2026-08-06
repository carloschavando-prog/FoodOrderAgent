import unittest

import weekly_order
from api import generate_order
from order_normalization import cases_required, count_unit_for_item, units_per_case


def item(name, order_qty, count_unit=None):
    row = {
        "id": 1,
        "name": name,
        "category_id": 4,
        "order_qty": order_qty,
        "par_level": order_qty,
    }
    row["count_unit"] = count_unit or count_unit_for_item(row)
    return row


def price(amount, quantity, basis="oz", pack_size="", unit_note=""):
    return {
        "price": amount,
        "apn": "123",
        "unit_quantity": quantity,
        "unit_basis": basis,
        "pack_size": pack_size,
        "unit_note": unit_note,
    }


class DryStockNormalizationTests(unittest.TestCase):
    def test_ope_sauce_shortage_converts_gallons_to_cases(self):
        row = item("OPE Sauce", 12)
        pricing = price(34.73, 256)
        pricing["units_per_case"] = units_per_case(row, pricing)

        self.assertEqual(pricing["units_per_case"], 2)
        self.assertEqual(cases_required(row, pricing), 6)

    def test_ranch_dressing_shortage_converts_gallons_to_cases(self):
        row = item("Ranch Dressing", 8)
        row["category_id"] = 6
        row["count_unit"] = count_unit_for_item(row)
        pricing = price(43.45, 512)
        pricing["units_per_case"] = units_per_case(row, pricing)

        self.assertEqual(row["count_unit"], "gallon")
        self.assertEqual(pricing["units_per_case"], 4)
        self.assertEqual(cases_required(row, pricing), 2)

    def test_simple_syrup_is_counted_in_gallons(self):
        row = item("Simple Syrup", 10)
        row["category_id"] = 6

        self.assertEqual(count_unit_for_item(row), "gallon")

    def test_vendor_selection_uses_extended_cost_for_each_pack(self):
        row = item("Garlic Parmesan", 5)
        two_gallon_case = price(30, 256)
        four_gallon_case = price(50, 512)
        for pricing in (two_gallon_case, four_gallon_case):
            pricing["units_per_case"] = units_per_case(row, pricing)
        prices = {1: {1: two_gallon_case, 2: four_gallon_case}}

        for module in (generate_order, weekly_order):
            assignment = module.assign_cheapest([row], prices, {1, 2})
            self.assertEqual(assignment, {1: 1})
            entries, cases, spend = module.calc_totals(
                assignment, {1: row}, prices
            )
            self.assertEqual(cases[1], 3)
            self.assertEqual(spend[1], 90)
            self.assertEqual(entries[1][0]["units_per_case"], 2)

    def test_case_counted_item_keeps_one_case_per_count(self):
        row = item("Shortening", 4)
        pricing = price(31.03, 35, "lb")

        self.assertEqual(units_per_case(row, pricing), 1)
        self.assertEqual(cases_required(row, pricing), 4)

    def test_number_ten_can_requires_explicit_pack_marker(self):
        pizza = item("Pizza Sauce", 6)
        pizza_price = price(42.88, 654, pack_size="6/#10")
        beans = item("Black Beans", 3)
        small_can_price = price(91.25, 372, pack_size="24/15.5 OZ")

        self.assertEqual(units_per_case(pizza, pizza_price), 6)
        self.assertIsNone(units_per_case(beans, small_can_price))

    def test_one_cherry_case_is_six_half_gallon_jars(self):
        row = item("Maraschino Cherries", 6)
        pricing = price(89.61, 384, pack_size="6/0.5 GAL")
        pricing["units_per_case"] = units_per_case(row, pricing)

        self.assertEqual(row["count_unit"], "1/2-gallon jar")
        self.assertEqual(pricing["units_per_case"], 6)
        self.assertEqual(cases_required(row, pricing), 1)

    def test_incompatible_weight_basis_is_not_used_for_gallons(self):
        row = item("Blended Oil", 5)
        weight_price = price(40, 35, "lb")

        self.assertIsNone(units_per_case(row, weight_price))

    def test_near_whole_metric_conversion_does_not_add_a_case(self):
        row = item("Olive Oil", 1)
        pricing = price(130.51, 338.14)
        pricing["units_per_case"] = units_per_case(row, pricing)

        self.assertAlmostEqual(pricing["units_per_case"], 1, places=5)
        self.assertEqual(cases_required(row, pricing), 1)


if __name__ == "__main__":
    unittest.main()
