import pathlib
import unittest


class InventorySyncGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = pathlib.Path("index.html").read_text()

    def _function(self, name, next_name):
        return self.source.split(f"function {name}", 1)[1].split(
            f"function {next_name}", 1
        )[0]

    def test_page_load_always_applies_the_latest_shared_snapshot(self):
        load_snapshot = self._function(
            "loadSharedSnapshot", "togglePanel"
        )

        self.assertIn("const data=await fetchSharedSnapshot();", load_snapshot)
        self.assertIn("applySharedSnapshot(data);", load_snapshot)
        self.assertNotIn("hasLocalCounts", load_snapshot)

    def test_shared_reads_bypass_browser_cache(self):
        fetch_snapshot = self._function(
            "fetchSharedSnapshot", "applySharedSnapshot"
        )

        self.assertIn("{cache:'no-store'}", fetch_snapshot)

    def test_order_button_stays_disabled_until_shared_sync_finishes(self):
        update_stats = self._function("updateStats", "setSaveStatus")

        self.assertIn("if(!sharedSyncReady)", update_stats)
        self.assertIn("Checking the latest shared inventory", update_stats)

    def test_generate_order_checks_for_a_newer_snapshot_first(self):
        verify_latest = self._function(
            "verifyLatestSharedInventory", "syncAfterGeneratedOrder"
        )
        generate_order = self._function("generateOrder", "observeSections")

        self.assertIn("latestId!==sharedSnapshotId", verify_latest)
        self.assertIn("applySharedSnapshot(data);", verify_latest)
        self.assertIn("return false;", verify_latest)
        self.assertIn(
            "await verifyLatestSharedInventory()", generate_order
        )
        self.assertLess(
            generate_order.index("await verifyLatestSharedInventory()"),
            generate_order.index("form.submit()"),
        )

    def test_failed_verification_blocks_order_generation(self):
        verify_latest = self._function(
            "verifyLatestSharedInventory", "syncAfterGeneratedOrder"
        )

        self.assertIn("sharedSyncReady=false;", verify_latest)
        self.assertIn("Order blocked:", verify_latest)
        self.assertGreaterEqual(verify_latest.count("return false;"), 2)


if __name__ == "__main__":
    unittest.main()
