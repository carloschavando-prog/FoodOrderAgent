import pathlib
import unittest


class OrderReportOriginTests(unittest.TestCase):
    def test_report_uses_same_origin_form_post_instead_of_blob_url(self):
        source = pathlib.Path("index.html").read_text()

        self.assertIn("form.action='/api/generate_order';", source)
        self.assertIn("form.method='POST';", source)
        self.assertIn("input.name='payload';", source)
        self.assertNotIn("URL.createObjectURL", source)
        self.assertNotIn("new Blob([reportHtml]", source)

    def test_server_accepts_form_encoded_generation_payload(self):
        source = pathlib.Path("api/generate_order.py").read_text()

        self.assertIn('"application/x-www-form-urlencoded" in content_type', source)
        self.assertIn('fields.get("payload")', source)

    def test_report_resolves_each_order_endpoint_to_an_absolute_url(self):
        source = pathlib.Path("api/generate_order.py").read_text()

        self.assertIn(
            "return new URL(ORDER_ENDPOINTS[vid], document.baseURI).href;",
            source,
        )
        self.assertIn("fetch(getOrderEndpoint(vid),{", source)

    def test_report_stages_exact_lines_before_vendor_requests(self):
        source = pathlib.Path("api/generate_order.py").read_text()

        self.assertIn('"order_lines": _order_lines', source)
        self.assertIn("await stageGeneratedOrder();", source)
        self.assertLess(
            source.index("await stageGeneratedOrder();"),
            source.index("fetch(getOrderEndpoint(vid),{"),
        )
        self.assertIn("Check the Supabase server configuration and try again", source)

    def test_report_can_save_a_draft_without_submitting_to_vendors(self):
        source = pathlib.Path("api/generate_order.py").read_text()

        self.assertIn("Save Draft to Supabase", source)
        start = source.index("async function saveDraftOnly(){")
        end = source.index("async function finalizeCompletedOrder(){", start)
        save_draft_source = source[start:end]

        self.assertIn("await stageGeneratedOrder();", save_draft_source)
        self.assertIn("No vendor orders were submitted.", save_draft_source)
        self.assertNotIn("submitOrders(", save_draft_source)
        self.assertNotIn("getOrderEndpoint", save_draft_source)


if __name__ == "__main__":
    unittest.main()
