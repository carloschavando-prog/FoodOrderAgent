import pathlib
import unittest


class InventoryPriceSyncUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = pathlib.Path("index.html").read_text()

    def _function(self, name, next_name):
        return self.source.split(f"function {name}", 1)[1].split(
            f"function {next_name}", 1
        )[0]

    def test_inventory_has_an_explicit_item_master_sync_button(self):
        self.assertIn('id="syncPricesBtn"', self.source)
        self.assertIn('onclick="syncInventoryPrices({force:true})"', self.source)
        self.assertIn('id="inventoryValue"', self.source)

    def test_sync_fetches_fresh_item_master_inventory_prices(self):
        sync = self._function("syncInventoryPrices", "setDeliveryCycle")

        self.assertIn("ITEM_MASTER_PRICE_URL", sync)
        self.assertIn("{cache:'no-store'}", sync)
        self.assertIn("applyInventoryPricing(data)", sync)

    def test_sync_uses_the_configured_vendor_and_count_unit_price(self):
        apply_prices = self._function(
            "applyInventoryPricing", "syncInventoryPrices"
        )

        self.assertIn("inventoryVendorId(item)", apply_prices)
        self.assertIn("quote.price_per_count_unit", apply_prices)
        self.assertIn("syncedUnit!==expectedUnit", apply_prices)
        self.assertIn("delete item.inventoryUnitPrice", apply_prices)

    def test_missing_vendor_quote_uses_latest_approved_alternate(self):
        apply_prices = self._function(
            "applyInventoryPricing", "syncInventoryPrices"
        )
        latest_quote = self._function(
            "latestApprovedQuote", "applyInventoryPricing"
        )

        self.assertIn("exactQuote ||", apply_prices)
        self.assertIn("latestApprovedQuote(row.quotes)", apply_prices)
        self.assertIn("normalizeName(item.vendor)!=='sysco only'", apply_prices)
        self.assertIn("Date.parse", latest_quote)

    def test_broadliners_never_fall_back_to_hard_coded_prices(self):
        unit_price = self._function(
            "inventoryUnitPrice", "inventoryValuation"
        )

        self.assertIn(
            "if(inventoryVendorId(item) || isArchivedInventoryVendor(item)) return null",
            unit_price,
        )

    def test_missing_external_price_is_not_treated_as_zero(self):
        static_price = self._function(
            "staticInventoryUnitPrice", "inventoryUnitPrice"
        )

        self.assertIn("item.price===null", static_price)
        self.assertIn("return null", static_price)

    def test_page_load_syncs_counts_and_prices_together(self):
        initialize = self._function("initializeSheet", "observeSections")

        self.assertIn("Promise.all", initialize)
        self.assertIn("loadSharedSnapshot()", initialize)
        self.assertIn("syncInventoryPrices()", initialize)

    def test_generate_order_refreshes_item_master_after_inventory_check(self):
        generate = self._function("generateOrder", "initializeSheet")

        inventory_check = generate.index("verifyLatestSharedInventory()")
        price_refresh = generate.index(
            "syncInventoryPrices({beforeOrder:true})"
        )
        order_submit = generate.index("form.submit()")

        self.assertLess(inventory_check, price_refresh)
        self.assertLess(price_refresh, order_submit)
        self.assertIn(
            "Order blocked: current Item Master prices could not be loaded.",
            generate,
        )


if __name__ == "__main__":
    unittest.main()
