import unittest

from api import generate_order
from order_normalization import pricing_matches_item_requirements
from scrape_gfs import match_item as match_gfs_item
from scrape_sysco import match_item as match_sysco_item
from scrape_usfoods import match_item


ITEM = {
    "id": 41,
    "name": "Styrofoam To-Go Containers",
    "category_id": 5,
    "order_qty": 2,
    "count_unit": "case",
}


class BlackFoamContainerTests(unittest.TestCase):
    def test_generator_accepts_black_container(self):
        pricing = {
            "vendor_item_name": "Container Foam Hinged Lid Black",
            "unit_note": "",
        }
        self.assertTrue(pricing_matches_item_requirements(ITEM, pricing))

    def test_generator_rejects_white_or_unspecified_container(self):
        self.assertFalse(pricing_matches_item_requirements(
            ITEM,
            {"vendor_item_name": "Container Foam Hinged Lid White"},
        ))
        self.assertFalse(pricing_matches_item_requirements(
            ITEM,
            {"vendor_item_name": "Styrofoam To-Go Containers"},
        ))

    def test_usfoods_scraper_does_not_fuzzy_match_white_container(self):
        item_map = {
            "by_apn": {"1136414": 41},
            "by_name": {"styrofoam to-go containers": 41},
        }
        white = "Container, Foam 9x9 1 Cmpt White Hinged Lid"
        black = "Container, Foam 9x9 1 Cmpt Black Hinged Lid"

        self.assertIsNone(match_item(white, "7804644", item_map))
        self.assertEqual(match_item(black, "1136414", item_map), 41)

    def test_sysco_scraper_does_not_fuzzy_match_white_container(self):
        item_map = {
            "by_apn": {"7302704": 41},
            "by_name": {"styrofoam to-go containers": 41},
        }
        white = "Container Foam Hinged White 1 Compartment"
        black = "Container Foam Hinged Black 1 Compartment"

        self.assertIsNone(match_sysco_item(white, "7551334", item_map))
        self.assertEqual(match_sysco_item(black, "7302704", item_map), 41)

    def test_gfs_scraper_does_not_fuzzy_match_white_container(self):
        item_map = {
            "by_apn": {"887130": 41},
            "by_name": {"styrofoam to-go containers": 41},
        }
        white = "Containers, Foam, White, Hinged, 1-Compartment"
        black = "Containers, Foam, Black, Hinged, 1-Compartment"

        self.assertIsNone(match_gfs_item(white, "831640", item_map))
        self.assertEqual(match_gfs_item(black, "887130", item_map), 41)

    def test_live_apn_can_be_assigned_by_generator(self):
        pricing = {
            41: {
                1: {
                    "price": 22.72,
                    "apn": "1136414",
                    "units_per_case": 1,
                }
            }
        }
        self.assertEqual(
            generate_order.assign_cheapest([ITEM], pricing, {1}),
            {41: 1},
        )


if __name__ == "__main__":
    unittest.main()
