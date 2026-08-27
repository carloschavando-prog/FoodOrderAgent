import datetime as dt
import pathlib
import threading
import time
import unittest
import urllib.error
from unittest.mock import patch

from api import generate_order, inventory_snapshot
from order_normalization import cases_required, count_unit_for_item, units_per_case
from party_demand import (
    EASTERN,
    PartyDemandBlocked,
    PartyDemandError,
    EventKitchenClient,
    PrepListEventClient,
    _default_event_client,
    _prep_list_day_payload,
    build_party_snapshot,
    delivery_window,
    party_need_by_item,
    refresh_party_demand,
    require_safe_snapshot,
)


def checklist(event_id, name, local_date, rows=None, add_ons=None,
              status="Definite", category="appetizer", needs_review=False,
              warnings=None):
    return {
        "event": {
            "eventId": event_id,
            "name": name,
            "localDate": local_date,
            "status": status,
        },
        "needsReview": needs_review,
        "warnings": warnings or [],
        "sections": [{"category": category, "rows": rows or []}],
        "liveFoodAddOns": add_ons or [],
    }


def payload(*events):
    return {
        "events": list(events),
        "sourceMode": "live",
        "lastSyncedAt": "2026-08-06T16:00:00Z",
        "missingEnvironmentVariables": [],
    }


class CoverageWindowTests(unittest.TestCase):
    def test_tuesday_window_is_inclusive_tuesday_through_friday(self):
        window = delivery_window("tuesday", delivery_date="2026-08-11")
        self.assertEqual(
            window["dates"],
            ["2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14"],
        )

    def test_friday_window_is_inclusive_friday_through_tuesday(self):
        window = delivery_window("friday", delivery_date="2026-08-07")
        self.assertEqual(
            window["dates"],
            [
                "2026-08-07", "2026-08-08", "2026-08-09",
                "2026-08-10", "2026-08-11",
            ],
        )

    def test_delivery_date_uses_america_new_york_calendar_day(self):
        utc_time = dt.datetime(2026, 8, 7, 3, 30, tzinfo=dt.timezone.utc)
        self.assertEqual(utc_time.astimezone(EASTERN).date().isoformat(), "2026-08-06")
        window = delivery_window("friday", today=utc_time)
        self.assertEqual(window["delivery_date"], "2026-08-07")


class PartyCalculationTests(unittest.TestCase):
    def snapshot(self, rows, category="appetizer", add_ons=None):
        event = checklist(
            "evt-1", "Acceptance Party", "2026-08-07",
            rows=rows, add_ons=add_ons, category=category,
        )
        return build_party_snapshot(
            {"2026-08-07": payload(event)},
            "friday",
            delivery_date="2026-08-07",
        )

    def test_all_required_conversions(self):
        rows = [
            {"itemKey": "w", "foodName": "Wings", "quantity": 128, "unit": "wings"},
            {"itemKey": "t", "foodName": "Chicken Tenders", "quantity": 128, "unit": "tenders"},
            {"itemKey": "k", "foodName": "Tater Kinks", "quantity": 128, "unit": "pieces"},
            {"itemKey": "b", "foodName": "Beef", "quantity": 15, "unit": "pounds"},
            {"itemKey": "c", "foodName": "Chicken", "quantity": 15, "unit": "pounds"},
            {"itemKey": "f", "foodName": "Tortillas", "quantity": 7, "unit": "packs"},
            {"itemKey": "bb", "foodName": "Black Beans", "quantity": 3, "unit": "recipes"},
            {"itemKey": "cs", "foodName": "Cold Side Sets", "quantity": 3, "unit": "sets"},
        ]
        snapshot = self.snapshot(rows, category="taco")
        # Ranch is a structured live food add-on and must be included as food.
        ranch_snapshot = self.snapshot(
            [], category="sauces",
            add_ons=[{
                "itemKey": "ranch", "foodName": "Ranch",
                "quantity": 4, "unit": "bowls",
            }],
        )
        items = {item["inventory_item"]: item for item in snapshot["item_totals"]}
        ranch = {item["inventory_item"]: item for item in ranch_snapshot["item_totals"]}

        self.assertEqual(items["Chicken Wings"]["converted_quantity"], 1)
        self.assertEqual(items["Tenders"]["converted_quantity"], 2)
        self.assertEqual(items["Tater Kegs"]["converted_quantity"], 2)
        self.assertEqual(items["JTM Taco Meat"]["converted_quantity"], 4)
        self.assertEqual(items["Fajita Chicken"]["converted_quantity"], 4)
        self.assertEqual(items['Tortilla, Flour 6"']["converted_quantity"], 8)
        self.assertEqual(items["Black Beans"]["converted_quantity"], 4)
        self.assertEqual(items["Diced Red Onions"]["converted_quantity"], 1)
        self.assertEqual(items["Diced Tomatoes"]["converted_quantity"], 1)
        self.assertEqual(items["Mild Cheddar Cheese"]["converted_quantity"], 1)
        self.assertEqual(items["Shredded Lettuce"]["converted_quantity"], 1)
        self.assertEqual(items["Fire Roasted Salsa"]["converted_quantity"], 2)
        self.assertEqual(items["Sour Cream"]["converted_quantity"], 2)
        self.assertAlmostEqual(ranch["Ranch Dressing"]["converted_quantity"], 0.275)

    def test_aggregation_happens_before_buffer_and_rounding(self):
        first = checklist(
            "one", "First", "2026-08-07",
            rows=[{"itemKey": "w1", "foodName": "Wings", "quantity": 64}],
            category="wing",
        )
        second = checklist(
            "two", "Second", "2026-08-08",
            rows=[{"itemKey": "w2", "foodName": "Wings", "quantity": 64}],
            category="wing",
        )
        snapshot = build_party_snapshot(
            {
                "2026-08-07": payload(first),
                "2026-08-08": payload(second),
            },
            "friday",
            delivery_date="2026-08-07",
        )
        wings = snapshot["item_totals"][0]
        self.assertEqual(wings["raw_quantity"], 128)
        self.assertEqual(wings["buffered_quantity"], 140.8)
        self.assertEqual(wings["converted_quantity"], 1)
        self.assertEqual(len(wings["event_breakdown"]), 2)

    def test_definite_filter_event_deduplication_and_structured_add_ons(self):
        definite = checklist(
            "same", "Definite", "2026-08-07",
            rows=[{"itemKey": "w", "foodName": "Wings", "quantity": 50}],
            add_ons=[{"itemKey": "r", "foodName": "Ranch", "quantity": 2}],
            category="wing",
        )
        duplicate = checklist(
            "same", "Duplicate", "2026-08-08",
            rows=[{"itemKey": "w2", "foodName": "Wings", "quantity": 500}],
            category="wing",
        )
        tentative = checklist(
            "tentative", "Tentative", "2026-08-07",
            rows=[{"itemKey": "t", "foodName": "Wings", "quantity": 500}],
            status="Tentative", category="wing",
        )
        snapshot = build_party_snapshot(
            {
                "2026-08-07": payload(definite, tentative),
                "2026-08-08": payload(duplicate),
            },
            "friday",
            delivery_date="2026-08-07",
        )
        self.assertEqual(snapshot["source_event_ids"], ["same"])
        self.assertEqual(snapshot["party_count"], 1)
        self.assertEqual(len(snapshot["raw_requirements"]), 2)
        self.assertIn("structured_food_add_on", {
            row["origin"] for row in snapshot["raw_requirements"]
        })

    def test_lost_status_wins_over_conflicting_definite_status(self):
        lost = checklist(
            "lost", "Lost Party", "2026-08-07",
            rows=[{"itemKey": "w", "foodName": "Wings", "quantity": 500}],
            status="Definite", category="wing",
        )
        lost["event"]["bookingStatus"] = "Lost"
        active = checklist(
            "active", "Active Party", "2026-08-07",
            rows=[{"itemKey": "w2", "foodName": "Wings", "quantity": 50}],
            category="wing",
        )
        snapshot = build_party_snapshot(
            {"2026-08-07": payload(lost, active)},
            "friday",
            delivery_date="2026-08-07",
        )

        self.assertEqual(snapshot["source_event_ids"], ["active"])
        self.assertEqual(snapshot["raw_requirements"][0]["event_name"], "Active Party")

    def test_operator_excluded_event_id_is_ignored(self):
        lost_upstream = checklist(
            "62003797", "WPAFB", "2026-08-07",
            rows=[{"itemKey": "w", "foodName": "Wings", "quantity": 500}],
            status="Definite", category="wing",
        )
        active = checklist(
            "active", "Active Party", "2026-08-07",
            rows=[{"itemKey": "w2", "foodName": "Wings", "quantity": 50}],
            category="wing",
        )

        with patch("party_demand.EXCLUDED_EVENT_IDS", {"62003797"}):
            snapshot = build_party_snapshot(
                {"2026-08-07": payload(lost_upstream, active)},
                "friday",
                delivery_date="2026-08-07",
            )

        self.assertEqual(snapshot["source_event_ids"], ["active"])
        self.assertEqual(snapshot["party_count"], 1)

    def test_needs_review_and_unmapped_food_block_generation(self):
        event = checklist(
            "review", "Review Me", "2026-08-07",
            rows=[{"itemKey": "m", "foodName": "Mystery Croquettes", "quantity": 5}],
            needs_review=True,
            warnings=[{"message": "Needs Review: recipe is incomplete"}],
        )
        snapshot = build_party_snapshot(
            {"2026-08-07": payload(event)}, "friday", "2026-08-07"
        )
        self.assertFalse(snapshot["can_generate"])
        self.assertTrue(any("Needs Review" in warning for warning in snapshot["blocking_warnings"]))
        self.assertTrue(any("Unmapped" in warning for warning in snapshot["blocking_warnings"]))
        with self.assertRaises(PartyDemandBlocked):
            require_safe_snapshot(snapshot)

    def test_no_party_window_is_safe_and_empty(self):
        snapshot = build_party_snapshot(
            {"2026-08-07": payload()}, "friday", "2026-08-07"
        )
        self.assertTrue(snapshot["can_generate"])
        self.assertEqual(snapshot["party_count"], 0)
        self.assertEqual(snapshot["item_totals"], [])

    def test_failed_refresh_returns_last_snapshot_as_stale_and_blocking(self):
        previous = self.snapshot([
            {"itemKey": "w", "foodName": "Wings", "quantity": 50}
        ], category="wing")
        previous["id"] = 17

        class BrokenClient:
            def fetch_day(self, local_date):
                raise RuntimeError("source unavailable")

        with patch("party_demand.load_party_snapshot", return_value=previous):
            stale = refresh_party_demand(
                "friday", "2026-08-07", client=BrokenClient(), persist=False
            )
        self.assertTrue(stale["stale"])
        self.assertFalse(stale["can_generate"])
        self.assertEqual(stale["item_totals"], previous["item_totals"])
        self.assertTrue(any("source unavailable" in item for item in stale["blocking_warnings"]))

    def test_refresh_syncs_each_source_day_before_loading_it(self):
        calls = []

        class SyncingClient:
            def sync_day(client_self, local_date):
                calls.append(("sync", local_date))

            def fetch_day(client_self, local_date):
                calls.append(("fetch", local_date))
                fresh = payload()
                fresh["lastSyncedAt"] = dt.datetime.now(dt.timezone.utc).isoformat()
                return fresh

        snapshot = refresh_party_demand(
            "tuesday", "2026-08-11", client=SyncingClient(), persist=False
        )

        self.assertFalse(snapshot["stale"])
        self.assertEqual(len(calls), 8)
        for local_date in (
            "2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14"
        ):
            self.assertEqual(calls.count(("sync", local_date)), 1)
            self.assertEqual(calls.count(("fetch", local_date)), 1)
            self.assertLess(
                calls.index(("sync", local_date)),
                calls.index(("fetch", local_date)),
            )
        first_fetch = min(
            calls.index(("fetch", local_date))
            for local_date in (
                "2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14"
            )
        )
        self.assertTrue(all(
            calls.index(("sync", local_date)) < first_fetch
            for local_date in (
                "2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14"
            )
        ))

    def test_refresh_never_overlaps_mutating_source_syncs(self):
        lock = threading.Lock()
        active_syncs = 0
        max_active_syncs = 0

        class ThrottleSensitiveClient:
            def sync_day(client_self, local_date):
                nonlocal active_syncs, max_active_syncs
                with lock:
                    active_syncs += 1
                    max_active_syncs = max(max_active_syncs, active_syncs)
                time.sleep(0.005)
                with lock:
                    active_syncs -= 1

            def fetch_day(client_self, local_date):
                fresh = payload()
                fresh["lastSyncedAt"] = dt.datetime.now(dt.timezone.utc).isoformat()
                return fresh

        snapshot = refresh_party_demand(
            "friday", "2026-08-07",
            client=ThrottleSensitiveClient(), persist=False,
        )

        self.assertTrue(snapshot["can_generate"])
        self.assertEqual(max_active_syncs, 1)

    def test_event_kitchen_sync_retries_a_transient_502(self):
        class JsonResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"ok": true}'

        client = EventKitchenClient(session_cookie="session=test")
        transient = urllib.error.HTTPError(
            client.base_url, 502, "Bad Gateway", {}, None
        )
        with patch.object(
            client.opener, "open", side_effect=[transient, JsonResponse()]
        ) as opened, patch("party_demand.time.sleep") as slept:
            result = client.sync_day("2026-08-29")

        self.assertEqual(result, {"ok": True})
        self.assertEqual(opened.call_count, 2)
        slept.assert_called_once_with(0.5)

    def test_event_kitchen_sync_does_not_retry_an_auth_failure(self):
        client = EventKitchenClient(session_cookie="session=test")
        denied = urllib.error.HTTPError(
            client.base_url, 401, "Unauthorized", {}, None
        )
        with patch.object(client.opener, "open", side_effect=denied) as opened, \
                patch("party_demand.time.sleep") as slept:
            with self.assertRaisesRegex(PartyDemandError, "HTTP Error 401"):
                client.sync_day("2026-08-29")

        self.assertEqual(opened.call_count, 1)
        slept.assert_not_called()

    def test_refresh_blocks_an_old_source_timestamp(self):
        class StaleClient:
            def fetch_day(client_self, local_date):
                stale = payload()
                stale["lastSyncedAt"] = "2026-08-01T12:00:00Z"
                return stale

        snapshot = refresh_party_demand(
            "tuesday", "2026-08-11", client=StaleClient(), persist=False
        )

        self.assertTrue(snapshot["stale"])
        self.assertEqual(snapshot["source_status"], "warning")
        self.assertFalse(snapshot["can_generate"])
        self.assertTrue(any(
            "did not return a current Tripleseat sync" in warning
            for warning in snapshot["blocking_warnings"]
        ))

    def test_preplist_fallback_preserves_events_food_and_review_warnings(self):
        translated = _prep_list_day_payload({
            "date": "2026-08-15",
            "sourceMode": "live",
            "lastSyncedAt": "2026-08-10T20:41:52Z",
            "events": [{
                "id": "60126828",
                "name": "ADF",
                "needsReview": True,
                "warningCount": 2,
                "items": [{
                    "key": "taco-beef",
                    "section": "Taco Bar",
                    "category": "taco",
                    "foodName": "Beef",
                    "quantity": 20,
                    "unit": "pounds",
                    "description": "Seasoned beef",
                }],
            }],
        }, "2026-08-15")
        snapshot = build_party_snapshot(
            {"2026-08-15": translated},
            "friday",
            delivery_date="2026-08-14",
        )

        self.assertEqual(snapshot["source_event_ids"], ["60126828"])
        self.assertEqual(snapshot["party_count"], 1)
        self.assertEqual(snapshot["raw_requirements"][0]["food_name"], "Beef")
        self.assertEqual(snapshot["item_totals"][0]["inventory_item"], "JTM Taco Meat")
        self.assertTrue(any(
            "2 source warnings" in warning
            for warning in snapshot["blocking_warnings"]
        ))

    def test_default_client_uses_preplist_only_without_direct_credentials(self):
        with patch("party_demand.EVENT_KITCHEN_PIN", ""), patch(
            "party_demand.EVENT_KITCHEN_SESSION_COOKIE", ""
        ):
            self.assertIsInstance(_default_event_client(), PrepListEventClient)


class InventoryAndOrderIntegrationTests(unittest.TestCase):
    def test_acceptance_demand_reaches_expected_vendor_case_quantities(self):
        rows = [
            {"itemKey": "w", "foodName": "Wings", "quantity": 128},
            {"itemKey": "t", "foodName": "Chicken Tenders", "quantity": 128},
            {"itemKey": "k", "foodName": "Tater Cakes", "quantity": 128},
            {"itemKey": "b", "foodName": "Beef", "quantity": 15},
            {"itemKey": "c", "foodName": "Chicken", "quantity": 15},
            {"itemKey": "f", "foodName": "Tortillas", "quantity": 7},
            {"itemKey": "bb", "foodName": "Black Beans", "quantity": 3},
            {"itemKey": "cs", "foodName": "Cold Side Sets", "quantity": 3},
        ]
        event = checklist(
            "flow", "Flow Test", "2026-08-07", rows=rows, category="taco",
            add_ons=[{"itemKey": "r", "foodName": "Ranch", "quantity": 4}],
        )
        snapshot = build_party_snapshot(
            {"2026-08-07": payload(event)}, "friday", "2026-08-07"
        )
        definitions = (
            (1, "Chicken Wings", 6, "40 LB", 1, "case", 1, 1),
            (2, "Tenders", 7, "2/5 LB", 2, "case", 1, 2),
            (3, "Tater Kegs", 7, "10 LB", 1, "case", 1, 2),
            (4, "JTM Taco Meat", 7, "4/5 LB", 1, "lb", 20, 1),
            (5, "Fajita Chicken", 7, "2/5 LB", 2, "lb", 10, 2),
            (6, 'Tortilla, Flour 6"', 3, "12/24", 1, "each", 288, 1),
            (7, "Black Beans", 4, "6/#10 CAN", 1, "each", 6, 1),
            (8, "Diced Red Onions", 6, "1/5 LB", 3, "lb", 5, 1),
            (9, "Diced Tomatoes", 6, "4/5 LB", 1, "lb", 20, 1),
            (10, "Mild Cheddar Cheese", 6, "4/5 LB", 1, "lb", 20, 1),
            (11, "Shredded Lettuce", 6, "6/2 LB", 2, "lb", 12, 1),
            (12, "Fire Roasted Salsa", 4, "4/68 OZ", 1, "oz", 272, 1),
            (13, "Sour Cream", 6, "4/5 LB", 1, "lb", 20, 1),
            (14, "Ranch Dressing", 6, "4/1 GAL", 1, "gallon", 4, 1),
        )
        item_rows = [{
            "id": item_id,
            "name": name,
            "category_id": category_id,
            "pack_size": pack_size,
            "par_level": None,
            "preferred_vendor_id": vendor_id,
        } for item_id, name, category_id, pack_size, vendor_id, _, _, _ in definitions]
        pricing_rows = [{
            "item_id": item_id,
            "vendor_id": vendor_id,
            "apn": str(item_id),
            "price": 10,
            "price_list_id": 1,
            "pack_size": pack_size,
            "unit_basis": basis,
            "unit_quantity": quantity,
            "unit_note": "",
            "vendor_item_name": name,
        } for item_id, name, _, pack_size, vendor_id, basis, quantity, _ in definitions]
        standing_on_hand = {
            "chicken wings": 7,
            "tenders": 12,
            "diced red onions": 2,
            "diced tomatoes": 2,
            "shredded lettuce": 6,
            "ranch dressing": 8,
            "tater kegs": 0,
            "jtm taco meat": 0,
            "fajita chicken": 0,
            'tortilla, flour 6"': 0,
            "black beans": 0,
            "mild cheddar cheese": 0,
            "fire roasted salsa": 0,
            "sour cream": 0,
        }
        with patch.object(
            generate_order, "sb_get_all", side_effect=[item_rows, pricing_rows]
        ):
            items, prices = generate_order.load_data(
                standing_on_hand, "friday", party_need_by_item(snapshot)
            )
        by_name = {item["name"]: item for item in items}
        for item_id, name, _, _, vendor_id, _, _, expected_cases in definitions:
            with self.subTest(name=name):
                self.assertEqual(
                    cases_required(by_name[name], prices[item_id][vendor_id]),
                    expected_cases,
                )

    def test_regular_and_party_only_shortages_subtract_on_hand_once(self):
        rows = [
            {"id": 82, "name": "Chicken Wings", "category_id": 6,
             "pack_size": "40 LB", "par_level": 7, "preferred_vendor_id": 1},
            {"id": 121, "name": "JTM Taco Meat", "category_id": 7,
             "pack_size": "4/5 LB", "par_level": None, "preferred_vendor_id": 1},
        ]
        with patch.object(generate_order, "sb_get_all", side_effect=[rows, []]):
            items, _ = generate_order.load_data(
                {"chicken wings": 9, "jtm taco meat": 3},
                "friday",
                {"chicken wings": 3, "jtm taco meat": 4},
            )
        by_name = {item["name"]: item for item in items}
        self.assertEqual(by_name["Chicken Wings"]["order_qty"], 1)
        self.assertEqual(by_name["JTM Taco Meat"]["order_qty"], 1)

    def test_existing_non_party_behavior_is_preserved(self):
        rows = [{
            "id": 82, "name": "Chicken Wings", "category_id": 6,
            "pack_size": "40 LB", "par_level": 7, "preferred_vendor_id": 1,
        }]
        with patch.object(generate_order, "sb_get_all", side_effect=[rows, []]):
            items, _ = generate_order.load_data({"chicken wings": 5}, "friday")
        self.assertEqual(items[0]["order_qty"], 2)

    def test_new_count_units_convert_through_existing_case_rounding(self):
        cases = (
            ("Diced Red Onions", 4, "lb", 10, 2),
            ("Shredded Lettuce", 1, "lb", 12, 1),
            ("Sour Cream", 2, "lb", 20, 1),
            ("Fire Roasted Salsa", 2, "oz", 272, 1),
            ('Tortilla, Flour 6"', 8, "each", 288, 1),
        )
        for name, need, basis, quantity, expected_cases in cases:
            with self.subTest(name=name):
                item = {"name": name, "category_id": 6, "order_qty": need}
                if name == 'Tortilla, Flour 6"':
                    item["category_id"] = 3
                if name == "Fire Roasted Salsa":
                    item["category_id"] = 4
                item["count_unit"] = count_unit_for_item(item)
                pricing = {
                    "unit_basis": basis,
                    "unit_quantity": quantity,
                    "pack_size": "12/24" if "Tortilla" in name else "",
                    "unit_note": "",
                }
                pricing["units_per_case"] = units_per_case(item, pricing)
                self.assertEqual(cases_required(item, pricing), expected_cases)

    def test_shared_inventory_snapshot_loads_linked_party_snapshot(self):
        inventory = {"id": 4, "party_demand_snapshot_id": 17}
        line = {"id": 1, "snapshot_id": 4, "item_name": "chicken wings", "on_hand_qty": 2}
        party = {"id": 17, "item_totals": []}
        with patch.object(
            inventory_snapshot, "_sb_request", side_effect=[[inventory], [line]]
        ), patch.object(
            inventory_snapshot, "load_party_snapshot", return_value=party
        ):
            header, lines, linked = inventory_snapshot._latest_snapshot()
        self.assertEqual(header["id"], 4)
        self.assertEqual(lines, [line])
        self.assertEqual(linked, party)

    def test_shared_inventory_save_records_party_snapshot_id(self):
        calls = []

        def fake_request(path, **kwargs):
            calls.append((path, kwargs))
            if path == "inventory_snapshots":
                return [{"id": 5}]
            return None

        with patch.object(inventory_snapshot, "_load_item_lookup", return_value={}), \
                patch.object(inventory_snapshot, "_sb_request", side_effect=fake_request):
            inventory_snapshot._save_snapshot({
                "party_demand_snapshot_id": 17,
                "items": [{"name": "Chicken Wings", "on_hand": 2, "unit": "case"}],
            })
        header_payload = calls[0][1]["payload"]
        self.assertEqual(header_payload["party_demand_snapshot_id"], 17)

    def test_shared_inventory_save_records_order_overrides(self):
        calls = []

        def fake_request(path, **kwargs):
            calls.append((path, kwargs))
            if path == "inventory_snapshots":
                return [{"id": 5}]
            return None

        overrides = {
            "Chicken Wings": {"quantity": 3, "mode": "cases"},
            "Shortening": {"quantity": 1, "mode": "cases", "vendorId": 1},
        }
        with patch.object(inventory_snapshot, "_load_item_lookup", return_value={}), \
                patch.object(inventory_snapshot, "_sb_request", side_effect=fake_request):
            inventory_snapshot._save_snapshot({
                "order_overrides": overrides,
                "items": [{"name": "Chicken Wings", "on_hand": 2, "unit": "case"}],
            })
        self.assertEqual(calls[0][1]["payload"]["order_overrides"], overrides)

    def test_shared_inventory_rejects_fractional_case_override(self):
        with self.assertRaisesRegex(ValueError, "whole cases"):
            inventory_snapshot._normalize_order_overrides({
                "Chicken Wings": {"quantity": 1.5, "mode": "cases"},
            })

    def test_ui_has_no_temporary_event_quantities(self):
        source = pathlib.Path("index.html").read_text()
        self.assertNotIn("eventOrderQty", source)
        self.assertNotIn("eventOrderCycle", source)
        self.assertNotIn("eventOrderThrough", source)
        self.assertIn("Party Need", source)
        self.assertIn("Final Order", source)

    def test_generated_and_print_reports_include_party_audit(self):
        party = {
            "id": 17,
            "delivery_date": "2026-08-07",
            "coverage_start": "2026-08-07",
            "coverage_end": "2026-08-11",
            "event_audit": [{
                "event_id": "evt-1",
                "event_name": "Audit Party",
                "event_date": "2026-08-08",
            }],
            "item_totals": [{
                "inventory_item": "Chicken Wings",
                "raw_quantity": 128,
                "raw_unit": "wings",
                "buffered_quantity": 140.8,
                "buffered_unit": "wings",
                "converted_quantity": 1,
                "inventory_unit": "case",
                "conversion_note": "200 wings per case",
            }],
        }
        report = generate_order.build_html(
            {}, set(), [], [], [], {}, [], 0,
            filler_cases={}, inventory_snapshot_id=9, party_snapshot=party,
        )
        self.assertIn("Party Demand", report)
        self.assertIn("Audit Party", report)
        self.assertIn("140.8 wings", report)
        source = pathlib.Path("index.html").read_text()
        self.assertIn(".party-demand{box-shadow:none", source)


if __name__ == "__main__":
    unittest.main()
