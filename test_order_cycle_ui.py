import pathlib
import unittest


class OrderCycleUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = pathlib.Path("index.html").read_text()

    def test_friday_pars_activate_only_on_thursday(self):
        self.assertIn(
            "return date.getDay()===4 ? 'friday' : 'tuesday';",
            self.source,
        )
        self.assertNotIn(
            "day>=2 && day<=4 ? 'friday' : 'tuesday'",
            self.source,
        )

    def test_frozen_status_bar_names_the_delivery_cycle(self):
        self.assertIn('id="cycleStatus"', self.source)
        self.assertIn("Thursday Order · Friday Delivery", self.source)
        self.assertIn("Tuesday Delivery", self.source)

    def test_frontend_builds_complete_delivery_par_sets(self):
        self.assertIn("const DELIVERY_PARS=buildDeliveryPars();", self.source)
        self.assertIn(
            "return DELIVERY_PARS[currentTruckCycle()][normalizeName(item.name)];",
            self.source,
        )
        self.assertNotIn(
            "return cyclePar===undefined ? item.buildTo : cyclePar;",
            self.source,
        )

    def test_previously_missing_tuesday_pars_are_explicit(self):
        self.assertIn(
            'name:"Burger Patties",             buildTo:5,  '
            "tuesdayBuildTo:3, fridayBuildTo:5",
            self.source,
        )
        self.assertIn(
            'name:"Double Lobe Chicken Breasts",buildTo:4,  '
            "tuesdayBuildTo:3, fridayBuildTo:4",
            self.source,
        )
        self.assertIn(
            'name:"Tenders",                    buildTo:10, '
            "tuesdayBuildTo:6, fridayBuildTo:10",
            self.source,
        )
        self.assertIn(
            'name:\'Tortilla, Flour 12"\',        buildTo:7,  '
            "tuesdayBuildTo:5, fridayBuildTo:7",
            self.source,
        )
        self.assertIn(
            'name:"Pizza Cheese",               buildTo:10, '
            "tuesdayBuildTo:5, fridayBuildTo:10",
            self.source,
        )


if __name__ == "__main__":
    unittest.main()
