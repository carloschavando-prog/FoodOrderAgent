import os
import json
import unittest
from unittest import mock

from order_feedback import (
    ConfigurationError,
    DECISION_LOST_ON_PRICE,
    DECISION_MINIMUM,
    DECISION_NO_QUOTE,
    DECISION_NONCOMPARABLE,
    DECISION_SPECIFICATION,
    DECISION_TIE,
    DeliveryError,
    FeedbackConfig,
    FeedbackError,
    FeedbackService,
    Representative,
    ResendClient,
    SUPPLIERS,
    SupabaseFeedbackStore,
    build_order_decisions,
    idempotency_key,
    qualify_supplier_items,
    render_feedback_email,
    sanitize_error,
    save_final_order,
    stage_order,
)


def decision(**overrides):
    row = {
        "canonical_product_key": "item-1",
        "item_id": 1,
        "item_name": "Pizza Cheese",
        "supplier_id": 1,
        "selected_supplier_id": 3,
        "decision_reason": DECISION_LOST_ON_PRICE,
        "ordered_quantity": 4,
        "allocated_elsewhere_cases": 2,
        "recipient_item_number": "ABC-1",
        "recipient_description": "Whole milk · 4/5 lb",
        "quote_eligible": True,
        "quote_available": True,
        "quote_comparable": True,
        "candidate_normalized_net_cost": 110,
        "selected_normalized_net_cost": 100,
    }
    row.update(overrides)
    return row


def config(live_enabled=False):
    representatives = {
        supplier_id: Representative(
            supplier_id,
            supplier["company"],
            f"Rep{supplier_id} Example",
            f"rep{supplier_id}@example.com",
        )
        for supplier_id, supplier in SUPPLIERS.items()
    }
    return FeedbackConfig(
        representatives=representatives,
        purchasing_name="Jordan Lee",
        business_name="On Par Bar & Grill",
        contact_detail="purchasing@example.com",
        from_address="On Par Purchasing <orders@onpar.example>",
        reply_to="purchasing@onpar.example",
        resend_api_key="re_test_key",
        test_recipient="safe-preview@example.com",
        live_enabled=live_enabled,
    )


class FakeStore:
    def __init__(self, decisions=None):
        self.orders = {
            "order-1": {
                "id": "order-1",
                "order_date": "2026-08-03",
                "status": "finalized",
            }
        }
        self.decisions = list(decisions or [])
        self.lines = []
        self.records = {}
        self.events = []

    def get_order(self, order_id):
        self.events.append(("get_order", order_id))
        return self.orders.get(order_id)

    def create_pending_order(self, order, submissions):
        self.events.append(("create_pending", order["order_id"]))
        row = {
            "id": order["order_id"],
            "order_date": order["order_date"],
            "status": "pending",
        }
        self.orders[row["id"]] = row
        return row

    def upsert_decisions(self, order_id, rows):
        self.events.append(("decisions", order_id))
        self.decisions = list(rows)

    def upsert_order_lines(self, order_id, rows):
        self.events.append(("lines", order_id))
        self.lines = list(rows)

    def get_order_lines(self, order_id):
        return list(self.lines)

    def finalize_order(self, order_id, submissions):
        self.events.append(("finalize", order_id))
        self.orders[order_id]["status"] = "finalized"
        return self.orders[order_id]

    def get_decisions(self, order_id):
        self.events.append(("get_decisions", order_id))
        return list(self.decisions)

    def get_send_record(self, order_id, supplier_id, template_version="v1"):
        return self.records.get((order_id, int(supplier_id), template_version))

    def upsert_send_record(self, record):
        key = (record["order_id"], int(record["supplier_id"]), record["template_version"])
        current = self.records.get(key, {})
        self.records[key] = {**current, **record}
        return self.records[key]

    def patch_send_record(
        self, order_id, supplier_id, changes, status_filter=None
    ):
        key = (order_id, int(supplier_id), "v1")
        current = self.records.get(key)
        if current is None:
            return None
        if status_filter and current.get("status") not in status_filter:
            return None
        current.update(changes)
        return dict(current)


class FakeResend:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def send(self, message, key):
        self.calls.append((message, key))
        if self.error:
            raise self.error
        return "email_123"


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class FeedbackFilteringTests(unittest.TestCase):
    def test_only_lost_on_price_is_included(self):
        rows = [
            decision(),
            decision(canonical_product_key="minimum", decision_reason=DECISION_MINIMUM),
            decision(canonical_product_key="spec", decision_reason=DECISION_SPECIFICATION),
            decision(canonical_product_key="no-quote", decision_reason=DECISION_NO_QUOTE),
        ]
        items, omissions = qualify_supplier_items(rows, 1)
        self.assertEqual(["item-1"], [row["canonical_product_key"] for row in items])
        self.assertEqual(1, omissions[DECISION_MINIMUM])
        self.assertEqual(1, omissions[DECISION_SPECIFICATION])
        self.assertEqual(1, omissions[DECISION_NO_QUOTE])

    def test_unavailable_ineligible_and_noncomparable_quotes_are_excluded(self):
        rows = [
            decision(canonical_product_key="unavailable", quote_available=False),
            decision(canonical_product_key="ineligible", quote_eligible=False),
            decision(canonical_product_key="noncomp", quote_comparable=False),
        ]
        items, omissions = qualify_supplier_items(rows, 1)
        self.assertEqual([], items)
        self.assertEqual(
            {
                "quote_ineligible": 1,
                "quote_noncomparable": 1,
                "quote_unavailable": 1,
            },
            omissions,
        )

    def test_strict_price_tie_is_excluded_even_if_reason_is_wrong(self):
        items, omissions = qualify_supplier_items(
            [decision(candidate_normalized_net_cost=100)], 1
        )
        self.assertEqual([], items)
        self.assertEqual({DECISION_TIE: 1}, omissions)

    def test_missing_comparison_data_is_excluded(self):
        items, omissions = qualify_supplier_items(
            [decision(candidate_normalized_net_cost=None)], 1
        )
        self.assertEqual([], items)
        self.assertEqual(1, sum(omissions.values()))

    def test_duplicate_canonical_lines_are_aggregated(self):
        rows = [decision(allocated_elsewhere_cases=2), decision(allocated_elsewhere_cases=3)]
        items, _ = qualify_supplier_items(rows, 1)
        self.assertEqual(1, len(items))
        self.assertEqual(5, items[0]["cases_ordered"])

    def test_split_order_reports_only_quantity_allocated_away(self):
        rows = [decision(ordered_quantity=10, allocated_elsewhere_cases=4)]
        items, _ = qualify_supplier_items(rows, 1)
        self.assertEqual(4, items[0]["cases_ordered"])

    def test_nonpositive_order_and_zero_away_quantity_are_excluded(self):
        rows = [
            decision(canonical_product_key="zero-order", ordered_quantity=0),
            decision(canonical_product_key="zero-away", allocated_elsewhere_cases=0),
        ]
        items, omissions = qualify_supplier_items(rows, 1)
        self.assertEqual([], items)
        self.assertEqual(2, sum(omissions.values()))

    def test_supplier_with_no_qualifying_items_is_skipped(self):
        store = FakeStore([decision(decision_reason=DECISION_MINIMUM)])
        result = FeedbackService(store, config()).preview_supplier("order-1", 1)
        self.assertEqual("skipped", result["status"])
        self.assertEqual(0, result["item_total"])


class DecisionMetadataTests(unittest.TestCase):
    def _item(self):
        return {
            "id": 10,
            "name": "Pizza Cheese",
            "pack_size": "5 lb bag",
            "count_unit": "5-pound bag",
            "order_qty": 8,
        }

    def test_pack_normalized_extended_cost_is_reused(self):
        # Candidate raw case price is lower, but it needs four cases rather than
        # two to cover the same eight 5-pound bags.
        prices = {
            10: {
                1: {"price": 80, "units_per_case": 4, "apn": "WIN"},
                2: {"price": 50, "units_per_case": 2, "apn": "OWN"},
            }
        }
        rows = build_order_decisions([self._item()], prices, {10: 1}, set())
        pfg = next(row for row in rows if row["supplier_id"] == 2)
        self.assertEqual(DECISION_LOST_ON_PRICE, pfg["decision_reason"])
        self.assertEqual(200, pfg["candidate_normalized_net_cost"])
        self.assertEqual(160, pfg["selected_normalized_net_cost"])

    def test_equal_normalized_cost_is_a_nonprice_tie(self):
        prices = {
            10: {
                1: {"price": 80, "units_per_case": 4},
                2: {"price": 40, "units_per_case": 2},
            }
        }
        rows = build_order_decisions([self._item()], prices, {10: 1}, set())
        pfg = next(row for row in rows if row["supplier_id"] == 2)
        self.assertEqual(DECISION_TIE, pfg["decision_reason"])
        self.assertFalse(pfg["feedback_eligible"])

    def test_dropped_supplier_retains_minimum_reason(self):
        prices = {
            10: {
                1: {"price": 80, "units_per_case": 4},
                2: {"price": 90, "units_per_case": 4},
            }
        }
        rows = build_order_decisions([self._item()], prices, {10: 1}, {2})
        pfg = next(row for row in rows if row["supplier_id"] == 2)
        self.assertEqual(DECISION_MINIMUM, pfg["decision_reason"])

    def test_missing_quote_is_not_mislabeled_as_minimum_loss(self):
        prices = {10: {1: {"price": 80, "units_per_case": 4}}}
        rows = build_order_decisions([self._item()], prices, {10: 1}, {2})
        pfg = next(row for row in rows if row["supplier_id"] == 2)
        self.assertEqual(DECISION_NO_QUOTE, pfg["decision_reason"])

    def test_raw_case_prices_with_different_packs_are_not_compared(self):
        item = {
            "id": 20,
            "name": "Can Liners",
            "pack_size": "case",
            "count_unit": "case",
            "order_qty": 2,
        }
        prices = {
            20: {
                1: {"price": 50, "units_per_case": 1, "pack_size": "100/CS"},
                2: {"price": 60, "units_per_case": 1, "pack_size": "200/CS"},
            }
        }
        rows = build_order_decisions([item], prices, {20: 1}, set())
        pfg = next(row for row in rows if row["supplier_id"] == 2)
        self.assertEqual(DECISION_NONCOMPARABLE, pfg["decision_reason"])
        self.assertFalse(pfg["feedback_eligible"])

    def test_matching_case_pack_can_be_identified_as_lost_on_price(self):
        item = {
            "id": 20,
            "name": "Can Liners",
            "pack_size": "case",
            "count_unit": "case",
            "order_qty": 2,
        }
        prices = {
            20: {
                1: {"price": 50, "units_per_case": 1, "pack_size": "100 / CS"},
                2: {"price": 60, "units_per_case": 1, "pack_size": "100/CS"},
            }
        }
        rows = build_order_decisions([item], prices, {20: 1}, set())
        pfg = next(row for row in rows if row["supplier_id"] == 2)
        self.assertEqual(DECISION_LOST_ON_PRICE, pfg["decision_reason"])
        self.assertTrue(pfg["feedback_eligible"])

    def test_contract_item_never_becomes_lost_on_price(self):
        prices = {
            10: {
                1: {"price": 80, "units_per_case": 4},
                2: {"price": 90, "units_per_case": 4},
            }
        }
        rows = build_order_decisions(
            [self._item()],
            prices,
            {10: 1},
            set(),
            required_vendor_by_item={"pizza cheese": 1},
        )
        pfg = next(row for row in rows if row["supplier_id"] == 2)
        self.assertEqual(DECISION_SPECIFICATION, pfg["decision_reason"])


class RenderingTests(unittest.TestCase):
    def _rendered(self, supplier_id=1):
        representative = config().representatives[supplier_id]
        items, _ = qualify_supplier_items([decision(supplier_id=supplier_id)], supplier_id)
        return render_feedback_email(
            representative,
            "2026-08-03",
            items,
            "Jordan Lee",
            "On Par Bar & Grill",
            "purchasing@example.com",
        )

    def test_html_and_plain_text_render_required_copy_and_totals(self):
        rendered = self._rendered()
        self.assertIn("Pricing feedback for our August 3, 2026 order", rendered["subject"])
        self.assertIn("Description / Pack", rendered["html"])
        self.assertIn("Summary: 1 item representing 2 cases", rendered["html"])
        self.assertIn("Item | Description / Pack | Cases Ordered", rendered["text"])
        self.assertIn("No immediate action is required", rendered["text"])

    def test_competitor_fields_prices_and_names_do_not_leak(self):
        contaminated = decision(
            competitor_name="Sysco",
            competitor_item_number="SECRET-9",
            winning_price="$99.99",
            savings="25%",
        )
        items, _ = qualify_supplier_items([contaminated], 1)
        rendered = render_feedback_email(
            config().representatives[1],
            "2026-08-03",
            items,
            "Jordan Lee",
            "On Par Bar & Grill",
        )
        content = rendered["html"] + rendered["text"]
        for forbidden in ("Sysco", "SECRET-9", "$99.99", "25%", "winning price"):
            self.assertNotIn(forbidden, content)

    def test_another_supplier_name_in_description_is_blocked(self):
        items, _ = qualify_supplier_items(
            [decision(recipient_description="Sysco secret pack")], 1
        )
        with self.assertRaises(FeedbackError):
            render_feedback_email(
                config().representatives[1],
                "2026-08-03",
                items,
                "Jordan Lee",
                "On Par Bar & Grill",
            )


class SafetyAndReliabilityTests(unittest.TestCase):
    def test_resend_request_includes_documented_idempotency_header(self):
        captured = []

        def opener(request, timeout):
            captured.append(request)
            return FakeResponse({"id": "email_123"})

        client = ResendClient("re_test", opener=opener)
        email_id = client.send(
            {
                "from": "orders@example.com",
                "to": ["rep@example.com"],
                "subject": "Subject",
                "html": "<p>Hello</p>",
                "text": "Hello",
            },
            "pricing-feedback/order/1/v1",
        )
        self.assertEqual("email_123", email_id)
        self.assertEqual(
            "pricing-feedback/order/1/v1",
            captured[0].get_header("Idempotency-key"),
        )
        self.assertEqual("Bearer re_test", captured[0].get_header("Authorization"))

    def test_opaque_supabase_secret_uses_apikey_without_invalid_bearer(self):
        captured = []

        def opener(request, timeout):
            captured.append(request)
            return FakeResponse([])

        store = SupabaseFeedbackStore(
            "https://project.supabase.co", "sb_secret_example", opener=opener
        )
        store.get_order("order-1")
        self.assertEqual("sb_secret_example", captured[0].get_header("Apikey"))
        self.assertIsNone(captured[0].get_header("Authorization"))

    def test_legacy_service_role_key_is_sent_as_bearer(self):
        captured = []

        def opener(request, timeout):
            captured.append(request)
            return FakeResponse([])

        store = SupabaseFeedbackStore(
            "https://project.supabase.co", "eyJlegacy", opener=opener
        )
        store.get_order("order-1")
        self.assertEqual("Bearer eyJlegacy", captured[0].get_header("Authorization"))

    def test_dry_run_preparation_does_not_contact_resend(self):
        store = FakeStore([decision()])
        resend = FakeResend()
        result = FeedbackService(store, config(), resend).preview_supplier("order-1", 1)
        self.assertEqual("dry-run", result["status"])
        self.assertEqual([], resend.calls)

    def test_test_send_routes_only_to_test_recipient_and_marks_subject(self):
        store = FakeStore([decision()])
        resend = FakeResend()
        result = FeedbackService(store, config(), resend).send(
            "order-1", 1, "test-send"
        )
        message, key = resend.calls[0]
        self.assertEqual(["safe-preview@example.com"], message["to"])
        self.assertTrue(message["subject"].startswith("[TEST] "))
        self.assertIn("/test/", key)
        self.assertEqual("test-sent", result["status"])
        self.assertEqual("dry-run", store.get_send_record("order-1", 1)["status"])

    def test_live_send_requires_environment_gate(self):
        store = FakeStore([decision()])
        resend = FakeResend()
        with self.assertRaises(ConfigurationError):
            FeedbackService(store, config(live_enabled=False), resend).send(
                "order-1", 1, "live-send"
            )
        self.assertEqual([], resend.calls)

    def test_live_send_uses_representative_and_deterministic_key(self):
        store = FakeStore([decision()])
        resend = FakeResend()
        result = FeedbackService(store, config(live_enabled=True), resend).send(
            "order-1", 1, "live-send"
        )
        message, key = resend.calls[0]
        self.assertEqual(["rep1@example.com"], message["to"])
        self.assertEqual(idempotency_key("order-1", 1), key)
        self.assertEqual("sent", result["status"])

    def test_persistent_sent_record_prevents_duplicate_after_resend_window(self):
        store = FakeStore([decision()])
        resend = FakeResend()
        service = FeedbackService(store, config(live_enabled=True), resend)
        service.send("order-1", 1, "live-send")
        second = service.send("order-1", 1, "live-send")
        self.assertEqual(1, len(resend.calls))
        self.assertTrue(second["duplicate_prevented"])

    def test_test_send_after_live_send_does_not_clear_sent_ledger(self):
        store = FakeStore([decision()])
        service = FeedbackService(store, config(live_enabled=True), FakeResend())
        service.send("order-1", 1, "live-send")
        service.send("order-1", 1, "test-send")
        self.assertEqual("sent", store.get_send_record("order-1", 1)["status"])

    def test_failed_send_is_recorded_safely_and_can_be_retried(self):
        store = FakeStore([decision()])
        failure = DeliveryError("bad key re_SUPERSECRET\nAuthorization: Bearer abc")
        with self.assertRaises(DeliveryError):
            FeedbackService(
                store, config(live_enabled=True), FakeResend(failure)
            ).send("order-1", 1, "live-send")
        record = store.get_send_record("order-1", 1)
        self.assertEqual("failed", record["status"])
        self.assertNotIn("SUPERSECRET", record["sanitized_error"])
        self.assertNotIn("Bearer abc", record["sanitized_error"])

        resend = FakeResend()
        retried = FeedbackService(store, config(live_enabled=True), resend).send(
            "order-1", 1, "live-send"
        )
        self.assertEqual("sent", retried["status"])
        self.assertEqual(2, store.get_send_record("order-1", 1)["attempt_count"])

    def test_missing_configuration_has_clear_errors(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(
                ConfigurationError, "ORDER_FEEDBACK_US_FOODS_REP_NAME"
            ):
                FeedbackConfig.from_env()

    def test_error_sanitizer_removes_credentials(self):
        clean = sanitize_error("re_abc123 Authorization: Bearer token\nfailed")
        self.assertNotIn("re_abc123", clean)
        self.assertNotIn("Bearer token", clean)

    def test_final_order_is_saved_before_it_can_be_prepared(self):
        store = FakeStore()
        order = {
            "order_id": "new-order",
            "order_date": "2026-08-03",
            "expected_supplier_ids": [1],
            "order_lines": [
                {
                    "canonical_product_key": "item-1",
                    "supplier_id": 3,
                    "item_name": "Pizza Cheese",
                    "cases_ordered": 2,
                }
            ],
            "decisions": [decision()],
        }
        saved = save_final_order(store, order, {"1": {"success": True}})
        self.assertEqual("finalized", saved["status"])
        self.assertLess(
            store.events.index(("lines", "new-order")),
            store.events.index(("finalize", "new-order")),
        )
        self.assertLess(
            store.events.index(("decisions", "new-order")),
            store.events.index(("finalize", "new-order")),
        )

    def test_generated_order_is_staged_with_lines_before_submission(self):
        store = FakeStore()
        order = {
            "order_id": "new-order",
            "order_date": "2026-08-03",
            "expected_supplier_ids": [1, 3],
            "item_total": 1,
            "case_total": 2,
            "order_lines": [
                {
                    "canonical_product_key": "item-1",
                    "supplier_id": 3,
                    "item_name": "Pizza Cheese",
                    "cases_ordered": 2,
                }
            ],
            "decisions": [decision()],
        }
        staged = stage_order(store, order)
        self.assertEqual("pending", staged["status"])
        self.assertEqual(order["order_lines"], store.lines)
        self.assertNotIn(("finalize", "new-order"), store.events)

    def test_incomplete_vendor_submissions_never_save_or_prepare(self):
        store = FakeStore()
        order = {
            "order_id": "new-order",
            "order_date": "2026-08-03",
            "expected_supplier_ids": [1, 2],
            "decisions": [decision()],
        }
        with self.assertRaisesRegex(FeedbackError, "missing successful submissions"):
            save_final_order(store, order, {"1": {"success": True}})
        self.assertNotIn(("create_pending", "new-order"), store.events)


if __name__ == "__main__":
    unittest.main()
