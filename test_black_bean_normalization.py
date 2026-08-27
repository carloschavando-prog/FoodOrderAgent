import pathlib
import unittest

from order_normalization import cases_required, count_unit_for_item, units_per_case


class BlackBeanNormalizationTests(unittest.TestCase):
    def test_thirteen_number_ten_cans_require_three_gfs_cases(self):
        item = {
            "name": "Black Beans",
            "category_id": 4,
            "order_qty": 13,
        }
        item["count_unit"] = count_unit_for_item(item)
        pricing = {
            "unit_basis": "each",
            "unit_quantity": 6,
            "pack_size": "6/#10 CAN",
            "unit_note": "GFS item 557714",
        }
        pricing["units_per_case"] = units_per_case(item, pricing)

        self.assertEqual(item["count_unit"], "#10 can")
        self.assertEqual(pricing["units_per_case"], 6)
        self.assertEqual(cases_required(item, pricing), 3)

    def test_active_sheet_hides_but_preserves_the_archived_gfs_mapping(self):
        source = pathlib.Path("index.html").read_text()

        # The original mapping stays in source as archive data.
        self.assertIn(
            'name:"Black Beans",                buildTo:null,eventDriven:true, '
            'vendor:"GFS"',
            source,
        )
        self.assertNotIn("eventOrderQty", source)
        # Active rendering and pricing route it through current suppliers.
        self.assertIn("ARCHIVED_INVENTORY_VENDOR_NAMES = new Set(['gfs'])", source)
        self.assertIn("${inventoryVendorLabel(item)}", source)
        self.assertNotIn("'sysco only':3,'gfs':4", source)


if __name__ == "__main__":
    unittest.main()
