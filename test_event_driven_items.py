import unittest
from unittest.mock import patch

from api import generate_order


class EventDrivenItemTests(unittest.TestCase):
    def test_variety_dessert_bars_are_event_driven(self):
        self.assertIn(
            "variety dessert bars",
            generate_order.EVENT_DRIVEN_ITEM_NAMES,
        )

    def test_event_driven_items_always_generate_zero_order_quantity(self):
        item_rows = [
            {
                "id": index,
                "name": name.title(),
                "category_id": 6,
                "pack_size": "1 CS",
                "par_level": 99,
                "preferred_vendor_id": 1,
            }
            for index, name in enumerate(
                sorted(generate_order.EVENT_DRIVEN_ITEM_NAMES), start=1
            )
        ]
        inflated_counts = {
            name: -100 for name in generate_order.EVENT_DRIVEN_ITEM_NAMES
        }

        with patch.object(
            generate_order, "sb_get_all", side_effect=[item_rows, []]
        ):
            canonical_items, _ = generate_order.load_data(inflated_counts)

        self.assertEqual(len(canonical_items), len(item_rows))
        for item in canonical_items:
            self.assertTrue(item["event_driven"])
            self.assertEqual(item["par_level"], 0)
            self.assertEqual(item["order_qty"], 0)


if __name__ == "__main__":
    unittest.main()
