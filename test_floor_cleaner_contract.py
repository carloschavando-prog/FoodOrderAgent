import unittest

import weekly_order
from api import generate_order


class FloorCleanerContractTests(unittest.TestCase):
    def test_sanitizing_floor_cleaner_is_us_foods_only(self):
        items = [
            {
                "id": 140,
                "name": "Sanitizing Floor Cleaner",
                "order_qty": 2,
                "category_id": 8,
                "par_level": 2,
            }
        ]
        prices = {
            140: {
                1: {"price": 94.62, "apn": "8928261"},
                4: {"price": 55.28, "apn": "219750"},
            }
        }

        for module in (weekly_order, generate_order):
            self.assertEqual(
                module.assign_cheapest(items, prices, {1, 4}),
                {140: 1},
            )
            self.assertEqual(
                module.build_rescue_fillers(4, items, prices, {}, {}),
                {},
            )


if __name__ == "__main__":
    unittest.main()
