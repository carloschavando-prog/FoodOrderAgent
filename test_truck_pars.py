import unittest
from unittest.mock import patch

from api import generate_order


class TruckParTests(unittest.TestCase):
    def setUp(self):
        self.item_rows = [
            {
                "id": 82,
                "name": "Chicken Wings",
                "category_id": 6,
                "pack_size": "40 LB",
                "par_level": 7,
                "preferred_vendor_id": 1,
            },
            {
                "id": 114,
                "name": "Potato Hamburger Bun",
                "category_id": 7,
                "pack_size": "5/12",
                "par_level": 6,
                "preferred_vendor_id": 1,
            },
            {
                "id": 115,
                "name": "Fries",
                "category_id": 7,
                "pack_size": "6/5 LB",
                "par_level": 30,
                "preferred_vendor_id": 2,
            },
            {
                "id": 116,
                "name": "Flatbread Dough",
                "category_id": 7,
                "pack_size": "28/1",
                "par_level": 10,
                "preferred_vendor_id": 2,
            },
            {
                "id": 125,
                "name": "Milwaukee Pretzel",
                "category_id": 7,
                "pack_size": "8/24 OZ",
                "par_level": 20,
                "preferred_vendor_id": 1,
            },
            {
                "id": 17,
                "name": 'Tortilla, Flour 12"',
                "category_id": 3,
                "pack_size": "8/12",
                "par_level": 5,
                "preferred_vendor_id": 1,
            },
            {
                "id": 18,
                "name": 'Tortilla, Flour 6"',
                "category_id": 3,
                "pack_size": "12/24",
                "par_level": 6,
                "preferred_vendor_id": 1,
            },
            {
                "id": 19,
                "name": "Double Lobe Chicken Breasts",
                "category_id": 6,
                "pack_size": "4/5 LB",
                "par_level": 8,
                "preferred_vendor_id": 1,
            },
            {
                "id": 79,
                "name": "Burger Patties",
                "category_id": 6,
                "pack_size": "48/4 OZ",
                "par_level": 5,
                "preferred_vendor_id": 2,
            },
            {
                "id": 122,
                "name": "Tenders",
                "category_id": 7,
                "pack_size": "2/5 LB",
                "par_level": 10,
                "preferred_vendor_id": 2,
            },
            {
                "id": 104,
                "name": "Pizza Cheese",
                "category_id": 6,
                "pack_size": "6/5 LB",
                "par_level": 10,
                "preferred_vendor_id": 2,
            },
        ]

    def load_items(self, truck_cycle):
        counts = {row["name"].lower(): 0 for row in self.item_rows}
        with patch.object(
            generate_order, "sb_get_all", side_effect=[self.item_rows, []]
        ):
            items, _ = generate_order.load_data(counts, truck_cycle)
        return {item["name"].lower(): item for item in items}

    def test_tuesday_truck_uses_three_case_wing_par(self):
        wings = self.load_items("tuesday")["chicken wings"]
        self.assertEqual(wings["par_level"], 3)
        self.assertEqual(wings["order_qty"], 3)

    def test_friday_truck_preserves_seven_case_wing_par(self):
        wings = self.load_items("friday")["chicken wings"]
        self.assertEqual(wings["par_level"], 7)
        self.assertEqual(wings["order_qty"], 7)

    def test_tuesday_truck_uses_calculated_freezer_pars(self):
        items = self.load_items("tuesday")
        expected = {
            "burger patties": 3,
            "double lobe chicken breasts": 3,
            "potato hamburger bun": 2,
            "fries": 5,
            "flatbread dough": 3,
            "milwaukee pretzel": 6,
            "pizza cheese": 5,
            "tenders": 6,
        }
        for name, par in expected.items():
            self.assertEqual(items[name]["par_level"], par)
            self.assertEqual(items[name]["order_qty"], par)

    def test_friday_truck_preserves_existing_freezer_pars(self):
        items = self.load_items("friday")
        expected = {
            "burger patties": 5,
            "double lobe chicken breasts": 4,
            "potato hamburger bun": 6,
            "fries": 30,
            "flatbread dough": 10,
            "milwaukee pretzel": 20,
            "pizza cheese": 10,
            "tenders": 10,
        }
        for name, par in expected.items():
            self.assertEqual(items[name]["par_level"], par)
            self.assertEqual(items[name]["order_qty"], par)

    def test_tuesday_truck_keeps_six_inch_tortillas_event_driven(self):
        items = self.load_items("tuesday")

        self.assertEqual(items['tortilla, flour 12"']["par_level"], 5)
        self.assertTrue(items['tortilla, flour 6"']["event_driven"])
        self.assertEqual(items['tortilla, flour 6"']["par_level"], 0)
        self.assertEqual(items['tortilla, flour 6"']["order_qty"], 0)

    def test_tuesday_truck_uses_three_bag_double_lobe_par(self):
        chicken = self.load_items("tuesday")["double lobe chicken breasts"]

        self.assertEqual(chicken["par_level"], 3)
        self.assertEqual(chicken["order_qty"], 3)


if __name__ == "__main__":
    unittest.main()
