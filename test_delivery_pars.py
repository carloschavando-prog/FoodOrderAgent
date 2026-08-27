import unittest

from delivery_pars import (
    BASE_PAR_LEVELS,
    DELIVERY_PARS,
    EVENT_DRIVEN_ITEM_NAMES,
    REMOVED_ITEM_NAMES,
    par_for_delivery,
)


class DeliveryParConfigurationTests(unittest.TestCase):
    def test_both_delivery_sets_cover_every_standing_item(self):
        expected_names = set(BASE_PAR_LEVELS)

        self.assertEqual(set(DELIVERY_PARS), {"tuesday", "friday"})
        self.assertEqual(set(DELIVERY_PARS["tuesday"]), expected_names)
        self.assertEqual(set(DELIVERY_PARS["friday"]), expected_names)
        self.assertEqual(len(expected_names), 102)

    def test_event_items_are_not_given_standing_pars(self):
        for cycle_pars in DELIVERY_PARS.values():
            self.assertTrue(EVENT_DRIVEN_ITEM_NAMES.isdisjoint(cycle_pars))

    def test_usage_calculated_cycle_differences_are_preserved(self):
        expected = {
            "burger patties": (3, 5),
            "chicken wings": (3, 7),
            "double lobe chicken breasts": (3, 4),
            "flatbread dough": (5, 6),
            "fries": (20, 26),
            "24 ounce pretzel": (7, 12),
            "ope sauce": (6, 9),
            "pizza cheese": (5, 10),
            "potato hamburger bun": (2, 3),
            "tenders": (8, 12),
            'tortilla, flour 12"': (5, 7),
            "yellow mustard": (2, 3),
        }

        actual = {
            name: (
                DELIVERY_PARS["tuesday"][name],
                DELIVERY_PARS["friday"][name],
            )
            for name in BASE_PAR_LEVELS
            if DELIVERY_PARS["tuesday"][name] != DELIVERY_PARS["friday"][name]
        }
        self.assertEqual(actual, expected)

    def test_removed_items_have_no_delivery_par(self):
        self.assertEqual(REMOVED_ITEM_NAMES, {"simple syrup"})
        self.assertIsNone(par_for_delivery("simple syrup", "tuesday"))
        self.assertIsNone(par_for_delivery("simple syrup", "friday"))

    def test_maintained_par_changes_apply_to_both_deliveries(self):
        expected = {
            "16 oz to-go cold cups": 2,
            "can liners": 3,
            "caesar dressing": 3,
            "celery sticks": 2,
            "cholula": 12,
            "garlic parmesan": 2,
            "l nitrile gloves": 12,
            "maraschino cherries": 2,
            "m nitrile gloves": 12,
            "mozzarella sticks": 6,
            "napkins xpressnap": 3,
            "parmesan cheese": 3,
            "pickles": 0.25,
            "pizza boxes": 1,
            "shortening": 8,
            "straws": 6,
            "vanilla monin": 4,
            "xl nitrile gloves": 12,
        }
        for item_name, par in expected.items():
            with self.subTest(item_name=item_name):
                self.assertEqual(par_for_delivery(item_name, "tuesday"), par)
                self.assertEqual(par_for_delivery(item_name, "friday"), par)

    def test_unknown_items_are_not_assigned_database_fallback_pars(self):
        self.assertIsNone(par_for_delivery("obsolete database item", "tuesday"))
        self.assertIsNone(par_for_delivery("obsolete database item", "friday"))

    def test_invalid_cycle_is_rejected(self):
        with self.assertRaises(ValueError):
            par_for_delivery("fries", "monday")


if __name__ == "__main__":
    unittest.main()
