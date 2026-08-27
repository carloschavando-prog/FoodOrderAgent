import json
import io
import unittest
import urllib.error
from unittest import mock

from api import place_order_gfs as gfs
from api import place_order_pfg as pfg
from api import place_order_sysco as sysco
from api import place_order_usfoods as usfoods


class UsFoodsOrderSubmissionTests(unittest.TestCase):
    def test_shared_auth_commits_rotated_token_before_returning_bearer(self):
        lease = mock.Mock()
        lease.credentials = {"refresh_token": "old", "auth_context": {}}
        store = mock.Mock()
        store.claim.return_value = lease

        def rotate(config, *, persist):
            self.assertFalse(persist)
            config["refresh_token"] = "new"
            return "Bearer current"

        with mock.patch.object(
            usfoods.VendorAuthClient, "from_env", return_value=store
        ), mock.patch.object(usfoods, "refresh_bearer", side_effect=rotate):
            bearer, credentials = usfoods.authenticate_usfoods()

        self.assertEqual("Bearer current", bearer)
        self.assertEqual("new", credentials["refresh_token"])
        lease.commit.assert_called_once_with(credentials, verified=True)

    def test_supabase_token_remains_primary_when_env_bootstrap_exists(self):
        rows = [{"credentials": {
            "refresh_token": "stale-token",
            "auth_context": {"customerNumber": 31586241},
        }}]
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(rows).encode()

        with mock.patch.dict(
            usfoods.os.environ,
            {"USF_REFRESH_TOKEN": "fresh-token"},
            clear=False,
        ), mock.patch.object(
            usfoods, "SB_SKEY", "service-role-key"
        ), mock.patch.object(usfoods.urllib.request, "urlopen", return_value=response):
            credentials = usfoods.load_usf_credentials()

        self.assertEqual("stale-token", credentials["refresh_token"])
        self.assertEqual(31586241, credentials["auth_context"]["customerNumber"])

    def test_invalid_supabase_token_retries_once_with_env_bootstrap(self):
        credentials = {
            "refresh_token": "stale-token",
            "auth_context": {"customerNumber": 31586241},
        }

        with mock.patch.dict(
            usfoods.os.environ,
            {"USF_REFRESH_TOKEN": "fresh-token"},
            clear=False,
        ), mock.patch.object(
            usfoods,
            "refresh_bearer",
            side_effect=[RuntimeError("Invalid Refresh Token"), "Bearer current"],
        ) as refresh:
            bearer, refreshed_credentials = usfoods.refresh_bearer_with_fallback(
                credentials
            )

        self.assertEqual("Bearer current", bearer)
        self.assertEqual("stale-token", credentials["refresh_token"])
        self.assertEqual("fresh-token", refreshed_credentials["refresh_token"])
        self.assertEqual(2, refresh.call_count)

    def test_order_uses_create_update_and_submit_sequence(self):
        items = [
            {"productNumber": 1085770, "qty": 3},
            {"productNumber": 2961092, "qty": 2},
        ]
        responses = [
            [{"orderId": "draft-1", "orderStatus": "IN_PROGRESS"}],
            [{"orderId": "draft-1", "orderStatus": "IN_PROGRESS"}],
            [{
                "orderId": "submitted-1",
                "tandemOrderNumber": 98765,
                "requestedDeliveryDate": "2026-08-04T00:00:00.000Z",
            }],
        ]

        with mock.patch.object(
            usfoods,
            "get_delivery_date",
            return_value="2026-08-04T00:00:00.000Z",
        ), mock.patch.object(usfoods, "usf_call", side_effect=responses) as call:
            result = usfoods.place_order("Bearer token", {"auth_context": {}}, items)

        self.assertEqual("submitted-1", result["orderId"])
        self.assertEqual(98765, result["tandemOrderNumber"])
        self.assertEqual("2026-08-04", result["deliveryDate"])

        self.assertEqual(
            ["PUT", "PUT", "POST"],
            [entry.args[0] for entry in call.call_args_list],
        )
        self.assertEqual(
            [
                "order-domain-api/v1/orders",
                "order-domain-api/v1/orders",
                "order-submission-domain-api/v1/submitIpOrder",
            ],
            [entry.args[1] for entry in call.call_args_list],
        )
        created_payload = call.call_args_list[0].args[3]
        updated_payload = call.call_args_list[1].args[3]
        self.assertEqual([], created_payload["orderItems"])
        self.assertEqual(5, updated_payload["totalUnits"])
        self.assertEqual(0, updated_payload["orderItems"][0]["eachesOrdered"])

    def test_http_error_includes_vendor_stage_and_response_body(self):
        error = urllib.error.HTTPError(
            "https://example.test",
            400,
            "Bad Request",
            {},
            io.BytesIO(b'{"message":"invalid order status"}'),
        )
        message = usfoods._http_error_message("order submission", error)
        self.assertIn("US Foods order submission failed (HTTP 400)", message)
        self.assertIn("invalid order status", message)


class SyscoOrderSubmissionTests(unittest.TestCase):
    def test_idx_password_flow_uses_state_token_and_current_remediations(self):
        identify = {
            "name": "identify",
            "href": "https://secure.sysco.com/idp/idx/identify",
            "value": [{"name": "stateHandle", "value": "state-1"}],
        }
        select = {
            "name": "select-authenticator-authenticate",
            "href": "https://secure.sysco.com/idp/idx/challenge",
            "value": [
                {
                    "name": "authenticator",
                    "options": [{
                        "label": "Okta Password",
                        "value": {"form": {"value": [
                            {"name": "id", "value": "password-id"},
                            {"name": "methodType", "value": "password"},
                        ]}},
                    }],
                },
                {"name": "stateHandle", "value": "state-2"},
            ],
        }
        challenge = {
            "name": "challenge-authenticator",
            "href": "https://secure.sysco.com/idp/idx/challenge/answer",
            "value": [{"name": "stateHandle", "value": "state-3"}],
        }
        responses = [
            {"remediation": {"value": [identify]}},
            {"remediation": {"value": [select]}},
            {"remediation": {"value": [challenge]}},
            {"success": {"href": "https://secure.sysco.com/login/success"}},
        ]

        with mock.patch.object(
            sysco, "_open_json", return_value=responses[0]
        ) as introspect, mock.patch.object(
            sysco, "_idx_post", side_effect=responses[1:]
        ) as post, mock.patch.object(
            sysco, "_open_okta_success", return_value="<form>SAML</form>"
        ):
            result = sysco._complete_idx_password(
                mock.Mock(), "02.id.current", "user@example.com", "secret"
            )

        self.assertEqual("<form>SAML</form>", result)
        introspect_request = introspect.call_args.args[1]
        self.assertEqual(
            {"stateToken": "02.id.current"},
            json.loads(introspect_request.data),
        )
        self.assertEqual(
            {"identifier": "user@example.com"}, post.call_args_list[0].args[2]
        )
        self.assertEqual(
            {"authenticator": {"id": "password-id", "methodType": "password"}},
            post.call_args_list[1].args[2],
        )
        self.assertEqual(
            {"credentials": {"passcode": "secret"}},
            post.call_args_list[2].args[2],
        )

    def test_create_order_input_matches_current_graphql_contract(self):
        result = sysco._create_order_input(
            [{"productId": "0534567", "qty": 3}],
            "2026-08-04T00:00:00.000Z",
        )

        self.assertEqual("WEB", result["orderSource"])
        self.assertEqual("Food Order", result["name"])
        self.assertEqual("GROUND", result["shippingCondition"])
        self.assertEqual("cs", result["lineItems"][0]["soldAs"])
        self.assertEqual("N", result["lineItems"][0]["pricingType"])
        self.assertEqual("0534567", result["lineItems"][0]["productId"])
        self.assertEqual(3, result["lineItems"][0]["qty"])

    def test_submit_order_input_uses_created_draft_identifiers(self):
        created = {
            "id": "draft-2",
            "deliveryDate": "2026-08-04T00:00:00.000Z",
            "shippingCondition": "GROUND",
            "sequenceId": 4,
            "caseEachLineItems": [{
                "caseItem": {
                    "id": "line-1",
                    "lineNumber": 1,
                    "productId": "0534567",
                    "siteId": "019",
                    "sellerId": "USBL",
                    "qty": 3,
                    "soldAs": "case",
                    "pricingType": "N",
                },
                "eachItem": None,
            }],
        }

        result = sysco._submit_order_input(created)

        self.assertEqual("draft-2", result["id"])
        self.assertEqual("Food Order", result["name"])
        self.assertEqual("GROUND", result["shippingCondition"])
        self.assertEqual(4, result["sequenceId"])
        self.assertNotIn("id", result["lineItems"][0])
        self.assertEqual("cs", result["lineItems"][0]["soldAs"])
        self.assertIsNone(result["lineItems"][0]["deliveryDate"])
        self.assertIn("submissionTime", result)

    def test_order_calls_current_create_and_submit_mutations(self):
        created = {
            "id": "draft-3",
            "uomOrderNumber": "123456",
            "deliveryDate": "2026-08-04T00:00:00.000Z",
            "shippingCondition": "0",
            "caseEachLineItems": [{
                "caseItem": {
                    "id": "line-1",
                    "lineNumber": 1,
                    "productId": "0534567",
                    "siteId": "019",
                    "sellerId": "USBL",
                    "qty": 3,
                    "soldAs": "case",
                    "pricingType": "N",
                },
                "eachItem": None,
            }],
        }
        responses = [
            {"data": {"createOrderV2": created}},
            {"data": {"submitOrderV2": {"orders": [{
                "name": "123456",
                "deliveryDate": "2026-08-04T00:00:00.000Z",
            }]}}},
        ]

        with mock.patch.object(
            sysco,
            "get_bearer_token",
            return_value=("Bearer token", sysco.SHOP_ACCOUNT_ID, "csrf", "vid"),
        ), mock.patch.object(
            sysco,
            "get_delivery_date",
            return_value="2026-08-04T00:00:00.000Z",
        ), mock.patch.object(sysco, "gql", side_effect=responses) as gql_call:
            result = sysco.place_sysco_order(
                [{"productId": "0534567", "qty": 3}]
            )

        self.assertEqual("draft-3", result["orderId"])
        self.assertEqual("123456", result["orderNumber"])
        self.assertEqual(
            ["CreateOrder", "SubmitOrder"],
            [entry.args[1] for entry in gql_call.call_args_list],
        )
        create_variables = gql_call.call_args_list[0].args[3]
        submit_variables = gql_call.call_args_list[1].args[3]
        self.assertIn("idempotencyToken", create_variables)
        self.assertEqual("draft-3", submit_variables["order"]["id"])
        self.assertNotIn("punchoutSessionContext", submit_variables)

    def test_resume_submits_only_one_exact_matching_open_draft(self):
        draft = {
            "id": "draft-existing",
            "name": "Food Order",
            "status": "ACTIVE",
            "deliveryDate": 1785801600000,
            "shippingCondition": "GROUND",
            "sequenceId": 2,
            "isLatest": True,
            "isPriceSynced": True,
            "caseEachLineItems": [{
                "caseItem": {
                    "id": "line-existing",
                    "lineNumber": 1,
                    "productId": "4049195",
                    "siteId": "019",
                    "sellerId": "USBL",
                    "qty": 1,
                    "soldAs": "case",
                    "pricingType": "N",
                },
                "eachItem": None,
            }],
        }
        responses = [
            {"data": {"getUserConfig": {
                "data": {"value": "draft-existing"},
            }}},
            {"data": {"getOrderHeadersV2": {"orders": [{
                "id": "draft-existing",
                "status": "ACTIVE",
                "totalLineItems": 1,
                "sequenceId": 3,
            }]}}},
            {"data": {"getOrderV2": draft}},
            {"data": {"updateOrderV2": {
                "sequenceId": 4,
                "lineItems": [],
            }}},
            {"data": {"submitOrderV2": {"orders": [{
                "name": "confirmation-1",
                "deliveryDate": 1785801600000,
            }]}}},
        ]

        with mock.patch.object(
            sysco,
            "get_bearer_token",
            return_value=("Bearer token", sysco.SHOP_ACCOUNT_ID, "csrf", "vid"),
        ), mock.patch.object(sysco, "gql", side_effect=responses) as gql_call:
            result = sysco.resume_sysco_order([
                {"productId": "4049195", "qty": 1}
            ])

        self.assertEqual("draft-existing", result["orderId"])
        operations = [entry.args[1] for entry in gql_call.call_args_list]
        self.assertEqual(
            [
                "GetUserConfig", "GetOrderHeadersV2", "GetOrderV2",
                "UpdateOrder", "SubmitOrder",
            ],
            operations,
        )
        self.assertNotIn("CreateOrder", operations)
        submit_order = gql_call.call_args_list[4].args[3]["order"]
        self.assertEqual("GROUND", submit_order["shippingCondition"])
        self.assertEqual(5, submit_order["sequenceId"])


class PfgOrderSubmissionTests(unittest.TestCase):
    def test_shared_auth_commits_rotated_token_before_returning_bearer(self):
        lease = mock.Mock()
        lease.credentials = {"refresh_token": "old"}
        store = mock.Mock()
        store.claim.return_value = lease

        def rotate(config, *, persist):
            self.assertFalse(persist)
            config["refresh_token"] = "new"
            return "Bearer current"

        with mock.patch.object(
            pfg.VendorAuthClient, "from_env", return_value=store
        ), mock.patch.object(pfg, "refresh_bearer", side_effect=rotate):
            bearer, credentials = pfg.authenticate_pfg()

        self.assertEqual("Bearer current", bearer)
        lease.commit.assert_called_once_with(credentials, verified=True)

    def test_create_header_uses_current_customer_payload(self):
        responses = [
            {"IsSuccess": True, "ResultObject": {}},
            {
                "IsSuccess": True,
                "ResultObject": {
                    "OrderEntryHeaderId": "header-1",
                    "DeliveryDate": "2026-08-07T00:00:00",
                },
            },
        ]
        with mock.patch.object(pfg, "pfg_call", side_effect=responses) as call:
            result = pfg.get_or_create_order_header("Bearer token", "customer-1")

        self.assertEqual(("header-1", "2026-08-07T00:00:00"), result)
        self.assertEqual(
            {"CustomerId": "customer-1", "PurchaseOrderNumber": ""},
            call.call_args_list[1].args[3],
        )

    def test_item_update_uses_current_product_contract(self):
        resolved = [{
            "item": {"apn": "12345", "qty": 3, "uomType": "CS"},
            "product": {
                "BusinessUnitKey": 3,
                "BusinessUnitERPKey": "019",
                "ProductKey": "product-key",
                "ProductNumber": "12345",
                "ProductDescription": "Test product",
                "ProductBrand": "Brand",
                "ProductIsCatchWeight": False,
                "ProductAverageWeight": 0,
                "ShipLaterMaxEstimatedDays": 0,
                "CutoffDateTime": None,
            },
            "uom": {
                "UnitOfMeasure": "CS",
                "Price": 42.5,
                "PackSize": "4/1 GAL",
                "CanOrderUom": True,
                "UOMOrderQuantityAlertThresholdMin": 0,
                "UOMOrderQuantityAlertThresholdMax": 10,
            },
        }]
        with mock.patch.object(
            pfg,
            "pfg_call",
            return_value={"IsSuccess": True, "ResultObject": {}},
        ) as call:
            pfg.add_order_items(
                "Bearer token", "header-1", "customer-1", resolved
            )

        self.assertEqual(
            "OrderEntryDetail/V1/UpdateOrderEntryDetail", call.call_args.args[1]
        )
        payload = call.call_args.args[3]
        self.assertEqual("product-key", payload["ProductKey"])
        self.assertEqual("CS", payload["UnitOfMeasureType"])
        self.assertEqual(3, payload["Quantity"])
        self.assertEqual("4/1 GAL", payload["ProductPackSize"])

    def test_submit_uses_query_parameters(self):
        with mock.patch.object(
            pfg,
            "pfg_call",
            return_value={
                "IsSuccess": True,
                "ResultObject": {"ConfirmationOrderNumber": "confirmation-1"},
            },
        ) as call:
            result = pfg.submit_order("Bearer token", "header-1")

        self.assertEqual("confirmation-1", result)
        self.assertIsNone(call.call_args.kwargs.get("payload"))
        self.assertEqual(
            {
                "OrderEntryHeaderId": "header-1",
                "TimeZone": "America/New_York",
            },
            call.call_args.kwargs["params"],
        )


class GfsOrderSubmissionTests(unittest.TestCase):
    def test_archived_gfs_ordering_is_blocked_before_any_request(self):
        with self.assertRaisesRegex(RuntimeError, "temporarily archived"):
            gfs.place_gfs_order({}, [{"materialNumber": "282537", "qty": 1}])

    def test_session_validation_rejects_empty_schedule_response(self):
        with mock.patch.object(gfs, "gfs_get", return_value={}):
            with self.assertRaisesRegex(RuntimeError, "session expired or invalid"):
                gfs.validate_session({})


if __name__ == "__main__":
    unittest.main()
