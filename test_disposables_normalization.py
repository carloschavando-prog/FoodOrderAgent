import unittest

from order_normalization import cases_required, count_unit_for_item, units_per_case


def item(name, order_qty):
    row = {
        "id": 1,
        "name": name,
        "category_id": 5,
        "order_qty": order_qty,
        "par_level": order_qty,
    }
    row["count_unit"] = count_unit_for_item(row)
    return row


def price(quantity, basis="each", pack_size="", vendor_item_name="", unit_note=""):
    return {
        "price": 25,
        "unit_quantity": quantity,
        "unit_basis": basis,
        "pack_size": pack_size,
        "vendor_item_name": vendor_item_name,
        "unit_note": unit_note,
    }


class DisposablesNormalizationTests(unittest.TestCase):
    def test_count_units_match_inventory_rules(self):
        expected = {
            "Styrofoam To-Go Containers": "case",
            "Can Liners": "case",
            "Deli Paper": "box",
            "Straws": "box",
            "2 oz To-Go Cups": "case",
            "2 oz Lids": "case",
            "Foil Sheets": "box",
            "Cutlery Kits": "case",
            "Savaday": "case",
            "Napkins C Fold": "case",
            "T-Shirt Bags": "case",
            "Plastic Wrap": "roll",
            "Aluminum Foil Roll": "roll",
            "Pizza Boxes": "case",
        }
        for name, count_unit in expected.items():
            with self.subTest(name=name):
                self.assertEqual(count_unit_for_item(item(name, 1)), count_unit)

    def test_deli_paper_case_contains_twelve_boxes(self):
        row = item("Deli Paper", 3)
        pricing = price(6000, pack_size="12/500")
        pricing["units_per_case"] = units_per_case(row, pricing)

        self.assertEqual(pricing["units_per_case"], 12)
        self.assertEqual(cases_required(row, pricing), 1)

    def test_straw_box_count_uses_vendor_pack(self):
        row = item("Straws", 25)
        pricing = price(2000, pack_size="24/500")
        pricing["units_per_case"] = units_per_case(row, pricing)

        self.assertEqual(pricing["units_per_case"], 24)
        self.assertEqual(cases_required(row, pricing), 2)

    def test_foil_sheet_box_count_can_vary_by_vendor(self):
        row = item("Foil Sheets", 13)
        usf = price(2400, pack_size="12/200 EA")
        pfg = price(3000, pack_size="6/500 EA")

        self.assertEqual(units_per_case(row, usf), 12)
        self.assertEqual(units_per_case(row, pfg), 6)
        self.assertEqual(cases_required(row, usf), 2)
        self.assertEqual(cases_required(row, pfg), 3)

    def test_plastic_wrap_converts_feet_to_rolls(self):
        row = item("Plastic Wrap", 2)
        pricing = price(
            2000,
            basis="ft",
            pack_size="1 RL",
            vendor_item_name="Film Plastic 18x2000' Roll",
        )
        pricing["units_per_case"] = units_per_case(row, pricing)

        self.assertEqual(pricing["units_per_case"], 1)
        self.assertEqual(cases_required(row, pricing), 2)

    def test_short_aluminum_foil_roll_is_rejected(self):
        row = item("Aluminum Foil Roll", 1)
        pricing = price(
            1000,
            basis="ft",
            pack_size="1/500 Ft",
            vendor_item_name='Foil Aluminum Heavy Duty 18"X500\' Roll',
        )

        self.assertIsNone(units_per_case(row, pricing))


if __name__ == "__main__":
    unittest.main()
