import pathlib
import unittest


class OrderReportOriginTests(unittest.TestCase):
    def test_blob_report_gets_generator_origin_as_base_url(self):
        source = pathlib.Path("index.html").read_text()

        self.assertIn("const reportBase = new URL('/', resp.url).href;", source)
        self.assertIn('`<head><base href="${reportBase}">`', source)
        self.assertIn(
            "new Blob([reportHtml], {type:'text/html;charset=utf-8'})",
            source,
        )

    def test_report_resolves_each_order_endpoint_to_an_absolute_url(self):
        source = pathlib.Path("api/generate_order.py").read_text()

        self.assertIn(
            "return new URL(ORDER_ENDPOINTS[vid], document.baseURI).href;",
            source,
        )
        self.assertIn("fetch(getOrderEndpoint(vid),{", source)


if __name__ == "__main__":
    unittest.main()
