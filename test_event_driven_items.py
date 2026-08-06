import pathlib
import unittest
from unittest.mock import patch

from api import generate_order
from order_normalization import cases_required, count_unit_for_item, units_per_case


class EventDrivenItemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index_source = pathlib.Path("index.html").read_text()

    def _ui_function(self, name, next_name):
        return self.index_source.split(
            f"function {name}", 1
        )[1].split(f"function {next_name}", 1)[0]

    def test_recent_event_driven_items_are_protected_from_ordering(self):
        for name in (
            "black beans",
            'tortilla, flour 6"',
            "variety dessert bars",
        ):
            with self.subTest(name=name):
                self.assertIn(name, generate_order.EVENT_DRIVEN_ITEM_NAMES)

    def test_event_driven_items_always_generate_zero_order_quantity(self):
        item_rows = [
            {
                "id": index,
                "name": name.title(),
                "category_id": 6,
                "pack_size": "1 CS",
                "par_level": 99,
                "preferred_vendor_id": 1,
            }
            for index, name in enumerate(
                sorted(generate_order.EVENT_DRIVEN_ITEM_NAMES), start=1
            )
        ]
        inflated_counts = {
            name: -100 for name in generate_order.EVENT_DRIVEN_ITEM_NAMES
        }

        with patch.object(
            generate_order, "sb_get_all", side_effect=[item_rows, []]
        ):
            canonical_items, _ = generate_order.load_data(inflated_counts)

        self.assertEqual(len(canonical_items), len(item_rows))
        for item in canonical_items:
            self.assertTrue(item["event_driven"])
            self.assertEqual(item["par_level"], 0)
            self.assertEqual(item["order_qty"], 0)

    def test_explicit_event_orders_are_kept_separate_from_standing_pars(self):
        item_rows = [
            {
                "id": 1,
                "name": "JTM Taco Meat",
                "category_id": 7,
                "pack_size": "4/5 LB",
                "par_level": None,
                "preferred_vendor_id": 1,
            },
            {
                "id": 2,
                "name": "Fajita Chicken",
                "category_id": 7,
                "pack_size": "2/5 LB",
                "par_level": None,
                "preferred_vendor_id": 2,
            },
            {
                "id": 3,
                "name": "Black Beans",
                "category_id": 4,
                "pack_size": "6/#10",
                "par_level": None,
                "preferred_vendor_id": 4,
            },
        ]
        planned = {
            "JTM Taco Meat": 21,
            "Fajita Chicken": 21,
            "Black Beans": 13,
        }

        with patch.object(
            generate_order, "sb_get_all", side_effect=[item_rows, []]
        ):
            canonical_items, _ = generate_order.load_data({}, "friday", planned)

        by_name = {item["name"]: item for item in canonical_items}
        for name, quantity in planned.items():
            self.assertTrue(by_name[name]["event_driven"])
            self.assertEqual(by_name[name]["par_level"], 0)
            self.assertEqual(by_name[name]["order_qty"], quantity)

    def test_party_proteins_convert_five_pound_bags_to_vendor_cases(self):
        cases = {
            "JTM Taco Meat": (21, 20, 4, 6),
            "Fajita Chicken": (21, 10, 2, 11),
        }
        for name, values in cases.items():
            with self.subTest(name=name):
                bags, pounds_per_case, bags_per_case, expected_cases = values
                item = {
                    "name": name,
                    "category_id": 7,
                    "order_qty": bags,
                }
                item["count_unit"] = count_unit_for_item(item)
                pricing = {
                    "unit_basis": "lb",
                    "unit_quantity": pounds_per_case,
                    "pack_size": "",
                    "unit_note": "",
                }
                pricing["units_per_case"] = units_per_case(item, pricing)

                self.assertEqual(item["count_unit"], "5-pound bag")
                self.assertEqual(pricing["units_per_case"], bags_per_case)
                self.assertEqual(cases_required(item, pricing), expected_cases)

    def test_event_driven_counts_are_rendered_and_persisted(self):
        render_card = self._ui_function("renderCard", "updateItem")
        collect_counts = self._ui_function(
            "collectCountRows", "saveSharedSnapshot"
        )
        load_snapshot = self._ui_function(
            "loadSharedSnapshot", "togglePanel"
        )
        generate_order = self._ui_function("generateOrder", "observeSections")

        self.assertIn('const countField = `<input type="number"', render_card)
        self.assertNotIn("Not needed", render_card)
        self.assertNotIn("if(item.eventDriven) continue;", collect_counts)
        self.assertNotIn("if(item.eventDriven) continue;", load_snapshot)
        self.assertNotIn("if(item.eventDriven) continue;", generate_order)
        self.assertIn("eventOrders", generate_order)
        self.assertIn("activeEventOrderQty(item)", render_card)

    def test_active_sheet_lists_current_party_quantities(self):
        self.assertIn(
            'name:"Black Beans",                buildTo:null,eventDriven:true, '
            'eventOrderQty:13',
            self.index_source,
        )
        self.assertIn(
            'name:"Fajita Chicken",             buildTo:null,eventDriven:true, '
            'eventOrderQty:21',
            self.index_source,
        )
        self.assertIn(
            'name:"JTM Taco Meat",              buildTo:null,eventDriven:true, '
            'eventOrderQty:21',
            self.index_source,
        )


if __name__ == "__main__":
    unittest.main()
