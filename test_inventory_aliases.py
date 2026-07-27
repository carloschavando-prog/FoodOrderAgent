import unittest
from unittest.mock import patch

from api import generate_order


class InventoryAliasTests(unittest.TestCase):
    def test_c_fold_count_applies_to_xpressnap_item(self):
        item_rows = [
            {
                "id": 50,
                "name": "Napkins Xpressnap",
                "category_id": 5,
                "pack_size": "24 Packs",
                "par_level": 7,
                "preferred_vendor_id": 5,
            }
        ]

        with patch.object(
            generate_order, "sb_get_all", side_effect=[item_rows, []]
        ):
            items, _ = generate_order.load_data({"napkins c fold": 10})

        self.assertEqual(items[0]["par_level"], 7)
        self.assertEqual(items[0]["order_qty"], 0)


if __name__ == "__main__":
    unittest.main()
