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
            "return DELIVERY_PARS[activeTruckCycle()][normalizeName(item.name)];",
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
            'name:"Tenders",                    buildTo:12, '
            "tuesdayBuildTo:8, fridayBuildTo:12",
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
        self.assertIn(
            'name:"Flatbread Dough",            buildTo:6,  '
            "tuesdayBuildTo:5, fridayBuildTo:6",
            self.source,
        )
        self.assertIn(
            'name:"24 Ounce Pretzel",           buildTo:12, '
            "tuesdayBuildTo:7, fridayBuildTo:12",
            self.source,
        )
        self.assertIn(
            'name:"Potato Hamburger Bun",       buildTo:3,  '
            "tuesdayBuildTo:2, fridayBuildTo:3",
            self.source,
        )

    def test_latest_maintained_pars_and_glove_units_are_embedded(self):
        expected_lines = (
            'name:"16 oz To-Go Cold Cups",      buildTo:2,',
            'name:"Maraschino Cherries",        buildTo:2,',
            'name:"Shortening",                 buildTo:8,',
            'name:"Can Liners",                 buildTo:3,',
            'name:"Napkins C Fold",             buildTo:3,',
        )
        for line in expected_lines:
            with self.subTest(line=line):
                self.assertIn(line, self.source)

        self.assertRegex(
            self.source,
            r'name:"Maraschino Cherries"[^\n]+countUnit:"case"',
        )

        for glove in ("M Nitrile Gloves", "L Nitrile Gloves", "XL Nitrile Gloves"):
            with self.subTest(glove=glove):
                self.assertRegex(
                    self.source,
                    rf'name:"{glove}"[^\n]+countUnit:"box"',
                )

        self.assertIn("kitchen_order_count_units_v3", self.source)
        for item_id in (41, 146, 147, 148):
            self.assertIn(f"delete state[{item_id}];", self.source)

    def test_requested_par_updates_and_removals_are_embedded(self):
        expected_lines = (
            'name:"Garlic Parmesan",            buildTo:2,',
            'name:"Cholula",                    buildTo:12,',
            'name:"Straws",                     buildTo:6,',
            'name:"Pizza Boxes",                buildTo:1,',
            'name:"Celery Sticks",              buildTo:2,',
            'name:"Parmesan Cheese",            buildTo:3,',
            'name:"Caesar Dressing",            buildTo:3,',
            'name:"Pickles",                    buildTo:0.25,',
            'name:"M Nitrile Gloves",           buildTo:12,',
            'name:"L Nitrile Gloves",           buildTo:12,',
            'name:"XL Nitrile Gloves",          buildTo:12,',
        )
        for line in expected_lines:
            with self.subTest(line=line):
                self.assertIn(line, self.source)

        self.assertIn("const FRY_PAR_MULTIPLIER=2;", self.source)
        self.assertIn(
            "tuesdayBuildTo:10*FRY_PAR_MULTIPLIER, "
            "fridayBuildTo:13*FRY_PAR_MULTIPLIER",
            self.source,
        )
        self.assertNotIn('name:"Simple Syrup"', self.source)
        self.assertRegex(
            self.source,
            r'name:"Caesar Dressing"[^\n]+countUnit:"gallon"',
        )

    def test_mozzarella_sticks_is_last_freezer_item(self):
        freezer = self.source.index('id:"freezer"')
        chemicals = self.source.index('id:"chemicals"')
        freezer_source = self.source[freezer:chemicals]

        self.assertIn('name:"Mozzarella Sticks"', freezer_source)
        self.assertGreater(
            freezer_source.index('name:"Mozzarella Sticks"'),
            freezer_source.index('name:"Variety Dessert Bars"'),
        )
        self.assertRegex(
            freezer_source,
            r'name:"Mozzarella Sticks"[^\n]+buildTo:6[^\n]+vendor:"US FOODS"',
        )

    def test_vanilla_monin_follows_dailys_in_beverage_dock(self):
        beverage = self.source.index('id:"beverage"')
        beverage_source = self.source[beverage:self.source.index('];', beverage)]
        daily = beverage_source.index('name:"Daily\'s Sweet & Sour Mix"')
        vanilla = beverage_source.index('name:"Vanilla Monin"')
        chafing = beverage_source.index('name:"Chafing Fuel Can 6 Hour"')

        self.assertLess(daily, vanilla)
        self.assertLess(vanilla, chafing)
        self.assertRegex(
            beverage_source,
            r'name:"Vanilla Monin"[^\n]+buildTo:4[^\n]+vendor:"US FOODS"[^\n]+countUnit:"bottle"',
        )

    def test_cold_cups_are_between_straws_and_styrofoam(self):
        straws = self.source.index('name:"Straws"')
        cold_cups = self.source.index('name:"16 oz To-Go Cold Cups"')
        styrofoam = self.source.index('name:"Styrofoam To-Go Containers"')

        self.assertLess(straws, cold_cups)
        self.assertLess(cold_cups, styrofoam)
        self.assertRegex(
            self.source,
            r'name:"16 oz To-Go Cold Cups"[^\n]+buildTo:2[^\n]+countUnit:"case"',
        )


    def test_inventory_pricing_button_opens_the_live_item_master(self):
        self.assertIn('id="inventoryPricingBtn"', self.source)
        self.assertIn('href="/api/item_master"', self.source)
        self.assertIn('target="_blank"', self.source)

    def test_reset_clears_manual_order_overrides(self):
        self.assertIn("setManualOrderOverrides('');", self.source)
        self.assertIn(
            "const manualOverrides=document.getElementById('manualOrderOverridesText');",
            self.source,
        )
        self.assertIn("if(manualOverrides) manualOverrides.value='';", self.source)

    def test_shared_snapshot_persists_manual_order_overrides(self):
        self.assertIn("order_overrides:orderOverrides,", self.source)
        self.assertIn("function formatManualOrderOverrides(overrides)", self.source)
        self.assertIn(
            "data.snapshot && data.snapshot.order_overrides",
            self.source,
        )

    def test_august_25_manual_override_preset_is_loaded_with_pretzel_floor(self):
        self.assertIn("deliveryDate:'2026-08-25'", self.source)
        self.assertIn("'Pizza Cheese':{quantity:1,mode:'cases'}", self.source)
        self.assertIn("'Sliced Red Tomatoes':{quantity:2,mode:'cases'}", self.source)
        self.assertIn("'Green Scrubbies':{quantity:0,mode:'cases'}", self.source)
        self.assertIn("'Crushed Red Pepper Packets':{quantity:0,mode:'cases'}", self.source)
        self.assertIn("'Tenders':{quantity:5,mode:'cases',vendorId:1}", self.source)
        self.assertIn("'Fries':{quantity:6,mode:'cases',vendorId:2}", self.source)
        self.assertIn(
            "'24 Ounce Pretzel':{quantity:4,mode:'minimum_cases',required_pack:'24 oz'}",
            self.source,
        )
        self.assertIn("ensureScheduledOrderOverrides();", self.source)
        self.assertIn("minimum 4 cases (24 oz only)", self.source)


if __name__ == "__main__":
    unittest.main()
