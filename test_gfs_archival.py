import pathlib
import unittest

import weekly_order
from api import generate_order, item_master, place_order_gfs


class GfsArchivalTests(unittest.TestCase):
    def test_gfs_is_not_an_active_order_or_item_master_vendor(self):
        self.assertEqual([1, 2, 3], generate_order.BROADLINER_IDS)
        self.assertEqual([1, 2, 3], weekly_order.BROADLINER_IDS)
        self.assertEqual([1, 2, 3], item_master.VENDOR_IDS)
        self.assertNotIn("GFS", item_master.build_html([], {}))
        self.assertEqual("Select active vendor", generate_order.preferred_vendor_name(4))
        self.assertEqual("Select active vendor", weekly_order.preferred_vendor_name(4))

    def test_item_master_inventory_feed_drops_archived_quotes(self):
        item = {
            "id": 1,
            "name": "Black Beans",
            "category_id": 4,
        }
        quote = {
            "apn": "557714",
            "price": 30.91,
            "pack_size": "6/#10 CAN",
            "unit_basis": "each",
            "unit_quantity": 6,
            "unit_price": 30.91 / 6,
            "pulled_at": "2026-08-26T12:00:00Z",
            "history": [],
        }

        feed = item_master.build_inventory_pricing([item], {1: {4: quote}})

        self.assertEqual({}, feed["items"][0]["quotes"])

    def test_gfs_automation_is_disabled_but_source_and_manifest_remain(self):
        workflow = pathlib.Path(".github/workflows/scrape_prices.yml").read_text()
        endpoint = pathlib.Path("api/place_order_gfs.py").read_text()
        handler = endpoint.split("def do_POST(self):", 1)[1]

        self.assertNotIn("  gfs:\n", workflow)
        self.assertFalse(place_order_gfs.GFS_ORDERING_ACTIVE)
        self.assertLess(
            handler.index("if not GFS_ORDERING_ACTIVE"),
            handler.index("cookies = load_gfs_cookies()"),
        )
        self.assertTrue(pathlib.Path("scrape_gfs.py").is_file())
        self.assertTrue(pathlib.Path("archive/gfs/live_snapshot_manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
