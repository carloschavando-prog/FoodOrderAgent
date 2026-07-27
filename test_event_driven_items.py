import pathlib
import unittest
from unittest.mock import patch

from api import generate_order


class EventDrivenItemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index_source = pathlib.Path("index.html").read_text()

    def _ui_function(self, name, next_name):
        return self.index_source.split(
            f"function {name}", 1
        )[1].split(f"function {next_name}", 1)[0]

    def test_recent_event_driven_items_are_protected_from_ordering(self):
        for name in (
            "black beans",
            'tortilla, flour 6"',
            "variety dessert bars",
        ):
            with self.subTest(name=name):
                self.assertIn(name, generate_order.EVENT_DRIVEN_ITEM_NAMES)

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

    def test_event_driven_counts_are_rendered_and_persisted(self):
        render_card = self._ui_function("renderCard", "updateItem")
        collect_counts = self._ui_function(
            "collectCountRows", "saveSharedSnapshot"
        )
        load_snapshot = self._ui_function(
            "loadSharedSnapshot", "togglePanel"
        )
        generate_order = self._ui_function("generateOrder", "observeSections")

        self.assertIn('const countField = `<input type="number"', render_card)
        self.assertNotIn("Not needed", render_card)
        self.assertNotIn("if(item.eventDriven) continue;", collect_counts)
        self.assertNotIn("if(item.eventDriven) continue;", load_snapshot)
        self.assertNotIn("if(item.eventDriven) continue;", generate_order)


if __name__ == "__main__":
    unittest.main()
