import unittest
from unittest.mock import patch

from api import generate_order


class TruckParTests(unittest.TestCase):
    def setUp(self):
        self.wing_rows = [
            {
                "id": 82,
                "name": "Chicken Wings",
                "category_id": 6,
                "pack_size": "40 LB",
                "par_level": 7,
                "preferred_vendor_id": 1,
            }
        ]

    def load_wings(self, truck_cycle):
        with patch.object(
            generate_order, "sb_get_all", side_effect=[self.wing_rows, []]
        ):
            items, _ = generate_order.load_data(
                {"chicken wings": 0}, truck_cycle
            )
        return items[0]

    def test_tuesday_truck_uses_three_case_wing_par(self):
        wings = self.load_wings("tuesday")
        self.assertEqual(wings["par_level"], 3)
        self.assertEqual(wings["order_qty"], 3)

    def test_friday_truck_preserves_seven_case_wing_par(self):
        wings = self.load_wings("friday")
        self.assertEqual(wings["par_level"], 7)
        self.assertEqual(wings["order_qty"], 7)


if __name__ == "__main__":
    unittest.main()
