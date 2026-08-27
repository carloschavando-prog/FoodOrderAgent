"""
POST /api/place_order_sysco
============================
Places a Sysco order through the same GraphQL create/submit flow used by
shop.sysco.com.

Body JSON:
  {"items": [{"productId": "0534567", "qty": 3}, ...]}

Auth:
  Uses SYSCO_COOKIES when available, otherwise signs in with
  SYSCO_EMAIL + SYSCO_PASSWORD through Sysco's Okta SAML state-token flow.
"""

import base64
import html.parser
import http.cookiejar
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler


GQL_URL   = "https://gateway-api.shop.sysco.com/graphql"
AUTH_BASE = "https://auth.shop.sysco.com"
OKTA_BASE = "https://secure.sysco.com"

SELLER_ID        = "USBL"
SITE_ID          = "019"
SELLER_ACCOUNT_ID = "700932"
SHOP_ACCOUNT_ID  = "usbl-019-700932"
ORDER_NAME       = "Food Order"
GROUND_SHIPPING_CONDITION = "GROUND"

EMAIL    = os.getenv("SYSCO_EMAIL", "carlos@onparbar.com")
PASSWORD = os.getenv("SYSCO_PASSWORD", "")

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


class _FormParser(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields = {}
        self.action = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and attrs.get("method", "").upper() == "POST":
            self.action = attrs.get("action", "")
        if tag == "input" and attrs.get("type", "").lower() == "hidden":
            name = attrs.get("name", "")
            if name:
                self.fields[name] = attrs.get("value", "")


def _extract_state_token(html_text):
    for pattern in [
        r'"stateToken"\s*:\s*"([^"]{10,})"',
        r"'stateToken'\s*:\s*'([^']{10,})'",
        r'stateToken[\s:="\']+([0-9A-Za-z_\-]{20,})',
    ]:
        match = re.search(pattern, html_text)
        if match:
            token = match.group(1)
            token = re.sub(
                r'\\x([0-9A-Fa-f]{2})',
                lambda value: chr(int(value.group(1), 16)),
                token,
            )
            token = re.sub(
                r'\\u([0-9A-Fa-f]{4})',
                lambda value: chr(int(value.group(1), 16)),
                token,
            )
            return token
    return None


def _decode_gateway_credentials(validate_response):
    if validate_response.get("role") != "CUSTOMER":
        raise RuntimeError(
            "Sysco authentication did not return a CUSTOMER session "
            f"(role={validate_response.get('role')!r})"
        )

    credentials = validate_response.get("gatewayCredentials", "")
    if not credentials:
        raise RuntimeError("Sysco authentication returned no gateway credentials")

    try:
        payload = credentials.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        jwt_payload = json.loads(base64.urlsafe_b64decode(payload))
        csrf_token = jwt_payload.get("csrf_token", "")
        visitor_id = jwt_payload.get("vid", "")
    except Exception:
        csrf_token = ""
        visitor_id = ""

    return (
        f"Bearer {credentials}",
        validate_response.get("shopAccountId", SHOP_ACCOUNT_ID),
        csrf_token,
        visitor_id,
    )


def _validate_with_cookies(cookie_string):
    request = urllib.request.Request(
        f"{AUTH_BASE}/api/v1/auth/validate",
        headers={
            "Cookie": cookie_string,
            "User-Agent": _UA,
            "Accept": "application/json",
            "Origin": "https://shop.sysco.com",
            "Referer": "https://shop.sysco.com/",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            validate_response = json.loads(response.read())
    except urllib.error.HTTPError as ex:
        body = ex.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(
            f"Sysco cookie validation failed (HTTP {ex.code}): {body or ex.reason}"
        ) from ex
    return _decode_gateway_credentials(validate_response)


def _open_json(opener, request, stage, timeout=20):
    try:
        with opener.open(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as ex:
        body = ex.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(
            f"Sysco {stage} failed (HTTP {ex.code}): {body or ex.reason}"
        ) from ex


def _idx_error(response, fallback):
    messages = []
    for message in (response.get("messages") or {}).get("value", []):
        text = message.get("message")
        if text:
            messages.append(text)
    return "; ".join(messages[:3]) or fallback


def _idx_remediations(response):
    return (response.get("remediation") or {}).get("value") or []


def _idx_form_value(remediation, name):
    for field in remediation.get("value") or []:
        if field.get("name") == name:
            return field.get("value")
    return None


def _idx_password_authenticator(remediation):
    for field in remediation.get("value") or []:
        if field.get("name") != "authenticator":
            continue
        for option in field.get("options") or []:
            form = ((option.get("value") or {}).get("form") or {}).get("value") or []
            values = {entry.get("name"): entry.get("value") for entry in form}
            label = str(option.get("label", "")).lower()
            if values.get("methodType") == "password" or "password" in label:
                return {
                    key: value
                    for key, value in values.items()
                    if value is not None
                }
    return None


def _idx_post(opener, remediation, payload, stage):
    href = remediation.get("href")
    if not href:
        raise RuntimeError(f"Sysco {stage} returned no remediation URL")
    state_handle = _idx_form_value(remediation, "stateHandle")
    if state_handle:
        payload = {**payload, "stateHandle": state_handle}
    request = urllib.request.Request(
        href,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": remediation.get("accepts") or "application/ion+json",
            "Accept": "application/ion+json; okta-version=1.0.0",
            "User-Agent": _UA,
            "Origin": OKTA_BASE,
            "Referer": f"{OKTA_BASE}/",
        },
        method=remediation.get("method", "POST"),
    )
    return _open_json(opener, request, stage)


def _idx_success_href(response):
    for key in ("success", "successWithInteractionCode"):
        value = response.get(key) or {}
        if value.get("href"):
            return value["href"]
        nested = value.get("value")
        if isinstance(nested, dict) and nested.get("href"):
            return nested["href"]
    return ""


def _open_okta_success(opener, href):
    try:
        with opener.open(urllib.request.Request(
            href,
            headers={
                "User-Agent": _UA,
                "Accept": "text/html,application/xhtml+xml,*/*",
                "Referer": f"{OKTA_BASE}/",
            },
        ), timeout=20) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as ex:
        body = ex.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(
            f"Sysco Okta completion failed (HTTP {ex.code}): {body or ex.reason}"
        ) from ex


def _complete_idx_password(opener, state_token, email, password):
    """Complete Okta Identity Engine's server-directed password flow."""
    response = _open_json(
        opener,
        urllib.request.Request(
            f"{OKTA_BASE}/idp/idx/introspect",
            data=json.dumps({"stateToken": state_token}).encode(),
            headers={
                "Content-Type": "application/ion+json; okta-version=1.0.0",
                "Accept": "application/ion+json; okta-version=1.0.0",
                "User-Agent": _UA,
                "Origin": OKTA_BASE,
                "Referer": f"{OKTA_BASE}/",
            },
            method="POST",
        ),
        "Okta IDX introspection",
    )

    identified = False
    password_selected = False
    password_challenged = False
    visited = []
    for _ in range(8):
        success_href = _idx_success_href(response)
        if success_href:
            return _open_okta_success(opener, success_href)

        remediations = _idx_remediations(response)
        by_name = {entry.get("name"): entry for entry in remediations}
        visited.append(",".join(sorted(str(name) for name in by_name if name)))

        if "challenge-authenticator" in by_name:
            if password_challenged:
                raise RuntimeError(
                    "Sysco Okta password verification was not accepted: "
                    + _idx_error(response, "authentication failed")
                )
            response = _idx_post(
                opener,
                by_name["challenge-authenticator"],
                {"credentials": {"passcode": password}},
                "Okta password verification",
            )
            password_challenged = True
            continue

        # Okta often keeps "identify" available after it has accepted the
        # username. Prefer the new authenticator step so we do not resubmit the
        # identifier until the transaction expires.
        if "select-authenticator-authenticate" in by_name:
            if password_selected:
                raise RuntimeError(
                    "Sysco Okta did not accept the password authenticator"
                )
            remediation = by_name["select-authenticator-authenticate"]
            authenticator = _idx_password_authenticator(remediation)
            if not authenticator:
                raise RuntimeError(
                    "Sysco sign-on requires interactive verification; "
                    "the service account needs password authentication enabled"
                )
            response = _idx_post(
                opener,
                remediation,
                {"authenticator": authenticator},
                "Okta password selection",
            )
            password_selected = True
            continue

        if "identify" in by_name and not identified:
            remediation = by_name["identify"]
            payload = {"identifier": email}
            if any(
                field.get("name") == "credentials"
                for field in remediation.get("value") or []
            ):
                payload["credentials"] = {"passcode": password}
            response = _idx_post(
                opener, remediation, payload, "Okta identity verification"
            )
            identified = True
            continue

        if "skip" in by_name:
            response = _idx_post(
                opener, by_name["skip"], {}, "Okta optional enrollment skip"
            )
            continue

        redirect = by_name.get("redirect-idp")
        if redirect and redirect.get("href"):
            return _open_okta_success(opener, redirect["href"])

        names = ", ".join(sorted(str(name) for name in by_name if name))
        raise RuntimeError(
            "Sysco Okta sign-on could not continue: "
            + _idx_error(response, names or "no supported next step")
        )

    raise RuntimeError(
        "Sysco Okta sign-on exceeded the expected number of steps "
        f"({'; '.join(visited)})"
    )


def get_bearer_token(email, password):
    """Authenticate with a current session cookie or Okta SAML and return API context."""
    cookies_raw = os.getenv("SYSCO_COOKIES", "").strip()
    if cookies_raw:
        try:
            if cookies_raw.startswith("{"):
                cookies = json.loads(cookies_raw)
                cookie_string = "; ".join(
                    f"{key}={value}" for key, value in cookies.items() if value
                )
            else:
                cookie_string = cookies_raw
            return _validate_with_cookies(cookie_string)
        except Exception:
            if not password:
                raise
            # A stored browser session can expire. Continue with Okta credentials.

    if not password:
        raise RuntimeError(
            "SYSCO_PASSWORD is not set and SYSCO_COOKIES did not provide a valid session"
        )

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    sso_request = urllib.request.Request(
        f"{AUTH_BASE}/api/v1/auth/sso",
        data=json.dumps({"email": email}).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": _UA,
            "Origin": "https://shop.sysco.com",
            "Referer": "https://shop.sysco.com/",
        },
    )
    sso_response = _open_json(opener, sso_request, "SSO discovery")
    redirect_to = (sso_response.get("data") or {}).get("redirectTo", "")
    if not redirect_to:
        raise RuntimeError("Sysco SSO discovery returned no Okta redirect")

    try:
        with opener.open(urllib.request.Request(
            redirect_to,
            headers={
                "User-Agent": _UA,
                "Accept": "text/html,application/xhtml+xml,*/*",
                "Referer": "https://shop.sysco.com/",
            },
        ), timeout=20) as response:
            okta_html = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as ex:
        body = ex.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(
            f"Sysco Okta sign-in page failed (HTTP {ex.code}): {body or ex.reason}"
        ) from ex

    state_token = _extract_state_token(okta_html)
    if not state_token:
        raise RuntimeError("Could not extract the current Okta state token")

    # Sysco now serves an Identity Engine state handle (often beginning
    # ``02.id.``).  It must go to IDX introspection as ``stateToken``; the
    # retired Classic /api/v1/authn endpoint rejects it as an invalid token.
    step_html = _complete_idx_password(
        opener, state_token, email, password
    )

    parser = _FormParser()
    parser.feed(step_html)
    saml_response = parser.fields.get("SAMLResponse", "")
    relay_state = parser.fields.get("RelayState", "")
    form_action = parser.action or f"{AUTH_BASE}/api/v1/auth/sso/assert"
    if not saml_response:
        raise RuntimeError("Okta sign-in returned no SAML assertion")

    assertion_request = urllib.request.Request(
        form_action,
        data=urllib.parse.urlencode({
            "SAMLResponse": saml_response,
            "RelayState": relay_state,
        }).encode(),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": _UA,
            "Accept": "text/html,application/xhtml+xml,*/*",
            "Origin": OKTA_BASE,
            "Referer": f"{OKTA_BASE}/",
        },
    )
    try:
        with opener.open(assertion_request, timeout=20) as response:
            response.read()
    except urllib.error.HTTPError as ex:
        if ex.code != 302:
            body = ex.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(
                f"Sysco SAML assertion failed (HTTP {ex.code}): {body or ex.reason}"
            ) from ex

    validate_request = urllib.request.Request(
        f"{AUTH_BASE}/api/v1/auth/validate",
        headers={
            "User-Agent": _UA,
            "Accept": "application/json",
            "Origin": "https://shop.sysco.com",
            "Referer": "https://shop.sysco.com/",
        },
    )
    validate_response = _open_json(opener, validate_request, "session validation")
    return _decode_gateway_credentials(validate_response)


def _build_syy_auth(shop_account_id, seller_id=SELLER_ID, site_id=SITE_ID):
    payload = {
        "data": {
            "shopAccountId": shop_account_id,
            "sellers": {
                seller_id: {
                    "siteId": site_id,
                    "sellerAccountId": SELLER_ACCOUNT_ID,
                }
            },
            "shopUserType": "multi_buyer",
            "country": "US",
        },
        "_hash": "bc038006687544baa90fb5021c9432ee",
    }
    return base64.b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode().rstrip("=")


def gql(
    bearer,
    operation_name,
    query,
    variables,
    ctx=None,
    request_type="write",
    workflow="Ordering",
):
    ctx = ctx or {}
    syy_auth = _build_syy_auth(ctx.get("shop_account_id", SHOP_ACCOUNT_ID))
    request = urllib.request.Request(
        GQL_URL,
        data=json.dumps({
            "operationName": operation_name,
            "variables": variables,
            "query": query,
        }).encode(),
        headers={
            "Authorization": bearer,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "apollographql-client-name": "SYSCO_SHOP_WEB",
            "apollographql-client-version": "1",
            "Origin": "https://shop.sysco.com",
            "Referer": "https://shop.sysco.com/",
            "User-Agent": _UA,
            "syy-authorization": syy_auth,
            "syy-experience": "exp-usbl",
            "syy-pricing-version": "2",
            "syy-request-tier": "priority",
            "syy-request-type": request_type,
            "syy-site": SITE_ID,
            "syy-source": "web",
            "syy-visitor-id": ctx.get("vid", ""),
            "syy-requested-by": ctx.get("csrf_token", ""),
            "syy-correlation-id": str(uuid.uuid4()),
            "syy-workflow-context": workflow,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as ex:
        body = ex.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(
            f"Sysco {operation_name} failed (HTTP {ex.code}): {body or ex.reason}"
        ) from ex

    errors = result.get("errors") or []
    if errors:
        messages = []
        for error in errors[:3]:
            message = error.get("message", "GraphQL request failed")
            extensions = error.get("extensions")
            if extensions:
                detail = json.dumps(extensions, separators=(",", ":"))[:1000]
                message = f"{message} ({detail})"
            messages.append(message)
        raise RuntimeError(f"Sysco {operation_name} failed: {'; '.join(messages)}")
    return result


_GQL_DELIVERY = """
query GetShopAccountsDeliveryDaysInfo(
  $shopAccountIds: [String!]!
  $internal: Boolean
  $shippingCondition: Int
  $shouldQuerySalesforce: Boolean
  $includeDeliveryHours: Boolean
) {
  getShopAccountsV2(
    shopAccountIds: $shopAccountIds
    internal: $internal
    shippingCondition: $shippingCondition
    shouldQuerySalesforce: $shouldQuerySalesforce
    includeDeliveryHours: $includeDeliveryHours
  ) {
    sellerAccounts {
      deliveryPreference {
        firstDeliveryDate
        firstSelectableDate
        defaultDeliveryDateUtc
        selectableDeliveryDatesUtc
      }
    }
    errors { code message additionalInfo }
  }
}
"""

_GQL_CREATE = """
mutation CreateOrder($order: OrderInputV2!, $idempotencyToken: String) {
  createOrderV2(order: $order, idempotencyToken: $idempotencyToken) {
    invoiceSeparate
    totalLineItems
    id
    uomOrderNumber
    name
    poNumber
    deliveryInstructions
    deliveryAddress
    status
    shippingCondition
    orderSource
    originatedOrderSource
    sequenceId
    deliveryDate
    caseEachLineItems {
      caseItem {
        id price qty quantityAllocated isAllocated totalPrice pricingType
        totalPriceAllocations soldAs sellerId siteId productId lineNumber
      }
      eachItem {
        id price qty quantityAllocated isAllocated totalPrice pricingType
        totalPriceAllocations soldAs sellerId siteId productId lineNumber
      }
    }
  }
}
"""

_GQL_SUBMIT = """
mutation SubmitOrder(
  $order: OrderInputV2!
  $punchoutSessionContext: PunchOutSessionContextInput
) {
  submitOrderV2(
    order: $order
    punchoutSessionContext: $punchoutSessionContext
  ) {
    orders {
      secondaryStatus
      deliveryDate
      sellerGroupId
      leadTime
      name
      totalPrice
      totalLineItems
      totalCases
      totalSplits
      totalQuantity
      seller { name id group provider }
    }
  }
}
"""

_GQL_UPDATE = """
mutation UpdateOrder(
  $order: OrderInputV2!
  $isPatching: Boolean
  $punchoutSessionContext: PunchOutSessionContextInput
) {
  updateOrderV2(
    order: $order
    isPatching: $isPatching
    punchoutSessionContext: $punchoutSessionContext
  ) {
    sequenceId
    lineItems { id productId qty soldAs deliveryDate }
  }
}
"""

_GQL_ORDER_HEADERS = """
query GetOrderHeadersV2($headerFilter: HeaderFilterV2!) {
  getOrderHeadersV2(params: $headerFilter) {
    shopAccountId
    orders {
      id
      name
      status
      createdDate
      modifiedDate
      deliveryDate
      totalLineItems
      sequenceId
      shippingCondition
    }
  }
}
"""

_GQL_USER_CONFIG = """
query GetUserConfig(
  $namespace: Namespace!
  $preference: String!
  $shopAccountId: String!
) {
  getUserConfig(
    namespace: $namespace
    preference: $preference
    shopAccountId: $shopAccountId
  ) {
    data
  }
}
"""

_GQL_ORDER = """
query GetOrderV2($orderId: String!, $forceLatestPrice: Boolean) {
  getOrderV2(
    id: $orderId
    forceLatestPrice: $forceLatestPrice
    includeSubs: false
  ) {
    id
    erpReferenceNumber
    name
    poNumber
    deliveryInstructions
    status
    modifiedDate
    deliveryDate
    orderSource
    originatedOrderSource
    invoiceSeparate
    shippingCondition
    sequenceId
    isLatest
    isPriceSynced
    deliveryType
    caseEachLineItems {
      caseItem {
        id lineNumber sellerId siteId productId soldAs qty pricingType
        deliveryDate
      }
      eachItem {
        id lineNumber sellerId siteId productId soldAs qty pricingType
        deliveryDate
      }
    }
  }
}
"""


def get_delivery_date(bearer, ctx):
    response = gql(
        bearer,
        "GetShopAccountsDeliveryDaysInfo",
        _GQL_DELIVERY,
        {
            "shopAccountIds": [ctx.get("shop_account_id", SHOP_ACCOUNT_ID)],
            "internal": False,
            "shippingCondition": 0,
            "shouldQuerySalesforce": False,
            "includeDeliveryHours": False,
        },
        ctx=ctx,
        request_type="read",
        workflow="AccountManagement",
    )
    accounts = (response.get("data") or {}).get("getShopAccountsV2") or []
    if not accounts:
        raise RuntimeError("Sysco returned no shop account delivery information")
    account_errors = accounts[0].get("errors") or []
    if account_errors:
        raise RuntimeError(
            "Sysco delivery-date lookup failed: "
            + "; ".join(error.get("message", "unknown error") for error in account_errors)
        )
    sellers = accounts[0].get("sellerAccounts") or []
    preference = (sellers[0].get("deliveryPreference") or {}) if sellers else {}
    dates = preference.get("selectableDeliveryDatesUtc") or []
    delivery_date = (
        preference.get("defaultDeliveryDateUtc")
        or preference.get("firstSelectableDate")
        or preference.get("firstDeliveryDate")
        or (dates[0] if dates else "")
    )
    if not delivery_date:
        raise RuntimeError("Sysco returned no available delivery date")
    return delivery_date


def _create_order_input(items, delivery_date):
    line_items = []
    for line_number, item in enumerate(items, start=1):
        product_id = str(item.get("productId", "")).strip()
        try:
            quantity = int(item.get("qty", 0))
        except (TypeError, ValueError) as ex:
            raise ValueError(f"Invalid Sysco quantity for product {product_id or '?'}") from ex
        if not product_id or quantity <= 0:
            raise ValueError("Each Sysco item needs a productId and a positive qty")
        line_items.append({
            "lineNumber": line_number,
            "deliveryDate": None,
            "productId": product_id,
            "siteId": SITE_ID,
            "sellerId": SELLER_ID,
            "qty": quantity,
            "soldAs": "cs",
            "pricingType": "N",
        })

    return {
        "deliveryInstructions": "",
        "poNumber": "",
        "deliveryDate": delivery_date,
        "name": ORDER_NAME,
        "orderSource": "WEB",
        "shippingCondition": GROUND_SHIPPING_CONDITION,
        "invoiceSeparate": False,
        "lineItems": line_items,
    }


def _normalize_sold_as(value):
    sold_as = str(value or "cs").strip().lower()
    if sold_as in {"case", "cases", "cs"}:
        return "cs"
    if sold_as in {"each", "split", "splits", "ea"}:
        return "ea"
    return sold_as


def _submit_order_input(created_order):
    line_items = []
    for pair in created_order.get("caseEachLineItems") or []:
        for key in ("caseItem", "eachItem"):
            item = pair.get(key)
            if not item:
                continue
            line_item = {
                field: item.get(field)
                for field in (
                    "lineNumber", "productId", "siteId", "sellerId",
                    "qty", "pricingType", "deliveryType",
                )
                if item.get(field) is not None
            }
            line_item["soldAs"] = _normalize_sold_as(item.get("soldAs"))
            line_item["deliveryDate"] = None
            line_items.append(line_item)

    if not line_items:
        raise RuntimeError("Sysco created an order without any line items")

    order = {
        "id": created_order.get("id"),
        "deliveryInstructions": created_order.get("deliveryInstructions") or "",
        "poNumber": created_order.get("poNumber") or "",
        "deliveryDate": created_order.get("deliveryDate"),
        "name": created_order.get("name") or ORDER_NAME,
        "orderSource": "WEB",
        # SubmitOrder uses the ShippingConditionInput enum, not the read-model code.
        "shippingCondition": GROUND_SHIPPING_CONDITION,
        "invoiceSeparate": bool(created_order.get("invoiceSeparate")),
        "lineItems": line_items,
        "submissionTime": int(time.time() * 1000),
    }
    if created_order.get("sequenceId") is not None:
        order["sequenceId"] = created_order["sequenceId"]
    if created_order.get("originatedOrderSource"):
        order["originatedOrderSource"] = created_order["originatedOrderSource"]
    return order


def _item_fingerprint(items):
    fingerprint = []
    for item in items:
        product_id = str(item.get("productId", "")).strip()
        try:
            quantity = int(item.get("qty", 0))
        except (TypeError, ValueError):
            return None
        sold_as = _normalize_sold_as(item.get("soldAs"))
        if product_id and quantity > 0:
            fingerprint.append((product_id, quantity, sold_as))
    return sorted(fingerprint)


def _draft_items(order):
    items = []
    for pair in order.get("caseEachLineItems") or []:
        for key in ("caseItem", "eachItem"):
            item = pair.get(key)
            if item:
                items.append(item)
    return items


def _active_order_id(config_response):
    config = (config_response.get("data") or {}).get("getUserConfig") or {}
    value = config.get("data") if isinstance(config, dict) else None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return value.strip()
    if isinstance(value, dict):
        return str(value.get("value") or value.get("orderId") or "").strip()
    return ""


def _submit_created_order(
    bearer,
    ctx,
    created,
    fallback_delivery_date=None,
):
    submit_order = _submit_order_input(created)
    if submit_order.get("sequenceId") is not None:
        submit_order["sequenceId"] = int(submit_order["sequenceId"]) + 1
    submit_response = gql(
        bearer,
        "SubmitOrder",
        _GQL_SUBMIT,
        {"order": submit_order},
        ctx=ctx,
    )
    submitted_orders = (
        ((submit_response.get("data") or {}).get("submitOrderV2") or {})
        .get("orders") or []
    )
    if not submitted_orders:
        raise RuntimeError("Sysco submitOrderV2 returned no submitted order")

    submitted = submitted_orders[0]
    return {
        "orderId": created["id"],
        "orderNumber": (
            created.get("uomOrderNumber")
            or created.get("erpReferenceNumber")
            or submitted.get("name")
            or ""
        ),
        "deliveryDate": (
            submitted.get("deliveryDate")
            or created.get("deliveryDate")
            or fallback_delivery_date
        ),
    }


def _update_open_order(bearer, ctx, order):
    """Advance an open cart to the current version before submission."""
    update_order = _submit_order_input(order)
    update_order.pop("submissionTime", None)
    if order.get("isPriceSynced") is False:
        update_order["sequenceId"] = int(order.get("sequenceId") or 0) + 1
    response = gql(
        bearer,
        "UpdateOrder",
        _GQL_UPDATE,
        {
            "order": update_order,
            "isPatching": False,
        },
        ctx=ctx,
    )
    updated = ((response.get("data") or {}).get("updateOrderV2") or {})
    if updated.get("sequenceId") is None:
        raise RuntimeError("Sysco updateOrderV2 returned no cart version")
    order["sequenceId"] = updated["sequenceId"]
    return order


def _get_open_order(bearer, ctx, order_id, force_latest_price=False):
    response = gql(
        bearer,
        "GetOrderV2",
        _GQL_ORDER,
        {
            "orderId": order_id,
            "forceLatestPrice": force_latest_price,
        },
        ctx=ctx,
        request_type="read",
    )
    return (response.get("data") or {}).get("getOrderV2") or {}


def _sync_open_order_prices(bearer, ctx, order):
    if order.get("isPriceSynced") is True:
        return order
    forced = _get_open_order(bearer, ctx, order["id"], force_latest_price=True)
    if forced.get("isLatest") is True and forced.get("isPriceSynced") is True:
        return forced
    refreshed = forced
    for _ in range(10):
        time.sleep(1)
        refreshed = _get_open_order(
            bearer,
            ctx,
            order["id"],
            force_latest_price=False,
        )
        if refreshed.get("isLatest") is True and refreshed.get("isPriceSynced") is True:
            return refreshed
    return refreshed


def resume_sysco_order(items):
    """Submit one exact matching open draft without creating another order."""
    requested_fingerprint = _item_fingerprint(items)
    if not requested_fingerprint or len(requested_fingerprint) != len(items):
        raise ValueError("Each Sysco item needs a productId and a positive qty")

    bearer, shop_account, csrf_token, visitor_id = get_bearer_token(EMAIL, PASSWORD)
    ctx = {
        "csrf_token": csrf_token,
        "vid": visitor_id,
        "shop_account_id": shop_account,
    }
    config_response = gql(
        bearer,
        "GetUserConfig",
        _GQL_USER_CONFIG,
        {
            "namespace": "MSS",
            "preference": "ACTIVE_ORDER_ID",
            "shopAccountId": shop_account,
        },
        ctx=ctx,
        request_type="read",
    )
    active_order_id = _active_order_id(config_response)

    headers_response = gql(
        bearer,
        "GetOrderHeadersV2",
        _GQL_ORDER_HEADERS,
        {
            "headerFilter": {
                "customer": {
                    "shopAccountId": shop_account,
                    "ordersCount": 50,
                    "pricingV2Enabled": True,
                    "timeZone": "America/New_York",
                },
                "filterGroups": (
                    "DELIVERY_DATE|SUBMITTED_DATE,EXCEPTIONS,"
                    "SHIPPING_TYPE,FILTER_STATUS"
                ),
                "filterStatus": ["OPEN"],
            }
        },
        ctx=ctx,
        request_type="read",
    )
    account_groups = (
        (headers_response.get("data") or {}).get("getOrderHeadersV2") or []
    )
    if isinstance(account_groups, dict):
        account_groups = [account_groups]
    headers = [
        order
        for group in account_groups
        for order in (group.get("orders") or [])
        if (
            order.get("id")
            and str(order.get("status", "")).upper() in {"ACTIVE", "OPEN"}
        )
    ]
    headers_by_id = {order["id"]: order for order in headers}

    candidate_ids = []
    if active_order_id:
        candidate_ids.append(active_order_id)
    candidate_ids.extend(
        order["id"]
        for order in sorted(
            headers,
            key=lambda order: str(
                order.get("modifiedDate") or order.get("createdDate") or ""
            ),
            reverse=True,
        )
        if order.get("totalLineItems") in (None, len(items))
        and order["id"] not in candidate_ids
    )

    matching = []
    for order_id in candidate_ids:
        order = _get_open_order(bearer, ctx, order_id)
        header_sequence = (headers_by_id.get(order_id) or {}).get("sequenceId")
        order_sequence = order.get("sequenceId")
        if (
            header_sequence is not None
            and (order_sequence is None or int(header_sequence) > int(order_sequence))
        ):
            order["sequenceId"] = header_sequence
        if (
            str(order.get("status", "")).upper() in {"ACTIVE", "OPEN"}
            and _item_fingerprint(_draft_items(order)) == requested_fingerprint
        ):
            matching.append(order)

    if not matching:
        raise RuntimeError(
            "No exact matching active/open Sysco draft was found; nothing submitted "
            f"(active cart present={bool(active_order_id)}, "
            f"open candidates={len(headers)})"
        )
    if len(matching) > 1:
        raise RuntimeError(
            f"Found {len(matching)} exact matching open Sysco drafts; nothing submitted"
        )
    current = _sync_open_order_prices(bearer, ctx, matching[0])
    if _item_fingerprint(_draft_items(current)) != requested_fingerprint:
        raise RuntimeError("Sysco cart changed while pricing synchronized; nothing submitted")
    current = _update_open_order(bearer, ctx, current)
    return _submit_created_order(bearer, ctx, current)


def place_sysco_order(items):
    bearer, shop_account, csrf_token, visitor_id = get_bearer_token(EMAIL, PASSWORD)
    ctx = {
        "csrf_token": csrf_token,
        "vid": visitor_id,
        "shop_account_id": shop_account,
    }
    delivery_date = get_delivery_date(bearer, ctx)

    create_response = gql(
        bearer,
        "CreateOrder",
        _GQL_CREATE,
        {
            "order": _create_order_input(items, delivery_date),
            "idempotencyToken": str(uuid.uuid4()),
        },
        ctx=ctx,
    )
    created = (create_response.get("data") or {}).get("createOrderV2") or {}
    if not created.get("id"):
        raise RuntimeError("Sysco createOrderV2 returned no order ID")

    return _submit_created_order(bearer, ctx, created, delivery_date)


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) if length else b"{}")
        items = body.get("items", [])

        try:
            if not items:
                raise ValueError("No items in request body")
            if body.get("resumeOnly") is True:
                result = resume_sysco_order(items)
            else:
                result = place_sysco_order(items)
            payload = json.dumps({
                "success": True,
                "vendor": "Sysco",
                "orderId": result["orderId"],
                "orderNumber": result["orderNumber"],
                "deliveryDate": result["deliveryDate"],
                "totalItems": len(items),
                "error": None,
            }).encode()
        except Exception as ex:
            import traceback
            payload = json.dumps({
                "success": False,
                "vendor": "Sysco",
                "error": str(ex),
                "trace": traceback.format_exc()[-500:],
            }).encode()

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self._cors()
        self.end_headers()
        self.wfile.write(payload)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, fmt, *args):
        pass
