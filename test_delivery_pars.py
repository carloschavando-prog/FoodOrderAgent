import unittest

from delivery_pars import (
    BASE_PAR_LEVELS,
    DELIVERY_PARS,
    EVENT_DRIVEN_ITEM_NAMES,
    par_for_delivery,
)


class DeliveryParConfigurationTests(unittest.TestCase):
    def test_both_delivery_sets_cover_every_standing_item(self):
        expected_names = set(BASE_PAR_LEVELS)

        self.assertEqual(set(DELIVERY_PARS), {"tuesday", "friday"})
        self.assertEqual(set(DELIVERY_PARS["tuesday"]), expected_names)
        self.assertEqual(set(DELIVERY_PARS["friday"]), expected_names)
        self.assertEqual(len(expected_names), 100)

    def test_event_items_are_not_given_standing_pars(self):
        for cycle_pars in DELIVERY_PARS.values():
            self.assertTrue(EVENT_DRIVEN_ITEM_NAMES.isdisjoint(cycle_pars))

    def test_usage_calculated_cycle_differences_are_preserved(self):
        expected = {
            "burger patties": (3, 5),
            "chicken wings": (3, 7),
            "double lobe chicken breasts": (3, 4),
            "flatbread dough": (3, 10),
            "fries": (5, 30),
            "milwaukee pretzel": (6, 20),
            "ope sauce": (6, 9),
            "pizza cheese": (5, 10),
            "potato hamburger bun": (2, 6),
            "tenders": (6, 10),
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

    def test_simple_syrup_is_a_fixed_ten_gallon_build_to(self):
        self.assertEqual(par_for_delivery("simple syrup", "tuesday"), 10)
        self.assertEqual(par_for_delivery("simple syrup", "friday"), 10)

    def test_unknown_items_are_not_assigned_database_fallback_pars(self):
        self.assertIsNone(par_for_delivery("obsolete database item", "tuesday"))
        self.assertIsNone(par_for_delivery("obsolete database item", "friday"))

    def test_invalid_cycle_is_rejected(self):
        with self.assertRaises(ValueError):
            par_for_delivery("fries", "monday")


if __name__ == "__main__":
    unittest.main()
