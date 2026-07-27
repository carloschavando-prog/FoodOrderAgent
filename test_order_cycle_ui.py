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


if __name__ == "__main__":
    unittest.main()
