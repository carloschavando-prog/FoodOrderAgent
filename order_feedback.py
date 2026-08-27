"""Pricing-feedback preparation, rendering, persistence, and safe delivery.

The food-order optimizer remains authoritative.  This module consumes its saved
decision metadata and never attempts to choose a different winning supplier.
"""

from __future__ import annotations

import collections
import datetime as dt
import hashlib
import html
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.utils import parseaddr

from order_normalization import cases_required, extended_cost


TEMPLATE_VERSION = "v1"
RESEND_ENDPOINT = "https://api.resend.com/emails"

SUPPLIERS = {
    1: {"company": "US Foods", "env": "US_FOODS", "slug": "us-foods"},
    2: {"company": "PFG", "env": "PFG", "slug": "pfg"},
    3: {"company": "Sysco", "env": "SYSCO", "slug": "sysco"},
    4: {"company": "GFS", "env": "GFS", "slug": "gfs"},
}

DECISION_SELECTED = "selected"
DECISION_LOST_ON_PRICE = "lost_on_price"
DECISION_NO_QUOTE = "missing_or_ineligible_quote"
DECISION_UNAVAILABLE = "unavailable_quote"
DECISION_NONCOMPARABLE = "different_specification_or_pack"
DECISION_MINIMUM = "minimum_order_requirement"
DECISION_SPECIFICATION = "specification_or_contract_requirement"
DECISION_TIE = "non_price_tie"
DECISION_MISSING_COMPARISON = "missing_or_unreliable_comparison_data"
DECISION_OTHER = "other_non_price_reason"


class FeedbackError(RuntimeError):
    """Base error for pricing feedback operations."""


class ConfigurationError(FeedbackError):
    """Raised when required server-side configuration is missing or invalid."""


class StorageError(FeedbackError):
    """Raised when durable feedback state cannot be read or written."""


class DeliveryError(FeedbackError):
    """Raised when Resend rejects or cannot complete a send."""


@dataclass(frozen=True)
class Representative:
    supplier_id: int
    company: str
    name: str
    email: str

    @property
    def first_name(self):
        return self.name.strip().split()[0]


@dataclass(frozen=True)
class FeedbackConfig:
    representatives: dict
    purchasing_name: str
    business_name: str
    contact_detail: str
    from_address: str = ""
    reply_to: str = ""
    resend_api_key: str = ""
    test_recipient: str = ""
    live_enabled: bool = False

    @classmethod
    def from_env(cls, require_representatives=True):
        errors = []
        representatives = {}
        for supplier_id, supplier in SUPPLIERS.items():
            prefix = f"ORDER_FEEDBACK_{supplier['env']}_REP"
            name = os.environ.get(f"{prefix}_NAME", "").strip()
            address = os.environ.get(f"{prefix}_EMAIL", "").strip()
            if require_representatives and not name:
                errors.append(f"{prefix}_NAME is required")
            if require_representatives and not _valid_email(address):
                errors.append(f"{prefix}_EMAIL must be a valid email address")
            if name and _valid_email(address):
                representatives[supplier_id] = Representative(
                    supplier_id=supplier_id,
                    company=supplier["company"],
                    name=name,
                    email=address,
                )

        purchasing_name = os.environ.get(
            "ORDER_FEEDBACK_PURCHASING_NAME", ""
        ).strip()
        business_name = os.environ.get("ORDER_FEEDBACK_BUSINESS_NAME", "").strip()
        contact_detail = os.environ.get("ORDER_FEEDBACK_CONTACT", "").strip()
        if require_representatives and not purchasing_name:
            errors.append("ORDER_FEEDBACK_PURCHASING_NAME is required")
        if require_representatives and not business_name:
            errors.append("ORDER_FEEDBACK_BUSINESS_NAME is required")

        live_raw = os.environ.get("ORDER_FEEDBACK_LIVE_ENABLED", "false")
        live_enabled = _parse_bool(live_raw, "ORDER_FEEDBACK_LIVE_ENABLED", errors)
        config = cls(
            representatives=representatives,
            purchasing_name=purchasing_name,
            business_name=business_name,
            contact_detail=contact_detail,
            from_address=os.environ.get("ORDER_FEEDBACK_FROM", "").strip(),
            reply_to=os.environ.get("ORDER_FEEDBACK_REPLY_TO", "").strip(),
            resend_api_key=os.environ.get("RESEND_API_KEY", "").strip(),
            test_recipient=os.environ.get(
                "ORDER_FEEDBACK_TEST_RECIPIENT", ""
            ).strip(),
            live_enabled=live_enabled,
        )
        if errors:
            raise ConfigurationError("; ".join(errors))
        return config

    def validate_delivery(self, action):
        errors = []
        if not self.resend_api_key.startswith("re_"):
            errors.append("RESEND_API_KEY is missing or invalid")
        if not _valid_mailbox(self.from_address):
            errors.append("ORDER_FEEDBACK_FROM must contain a valid sender address")
        if not _valid_email(self.reply_to):
            errors.append("ORDER_FEEDBACK_REPLY_TO must be a valid monitored address")
        if action == "test-send" and not _valid_email(self.test_recipient):
            errors.append(
                "ORDER_FEEDBACK_TEST_RECIPIENT must be a valid email address"
            )
        if action == "live-send":
            if not self.live_enabled:
                errors.append("ORDER_FEEDBACK_LIVE_ENABLED must be true")
            _, sender = parseaddr(self.from_address)
            if sender.lower().endswith("@resend.dev"):
                errors.append(
                    "ORDER_FEEDBACK_FROM must use your verified Resend domain for live sends"
                )
        if errors:
            raise ConfigurationError("; ".join(errors))


def _parse_bool(value, name, errors):
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    errors.append(f"{name} must be true or false")
    return False


def _valid_email(value):
    _, address = parseaddr(str(value or ""))
    return bool(
        address == value
        and re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", address or "")
    )


def _valid_mailbox(value):
    _, address = parseaddr(str(value or ""))
    return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", address or ""))


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def idempotency_key(order_id, supplier_id, template_version=TEMPLATE_VERSION):
    return f"pricing-feedback/{order_id}/{supplier_id}/{template_version}"


def _finite_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _recipient_description(item, quote):
    parts = []
    for value in (
        item.get("brand"),
        item.get("specification"),
        item.get("size"),
        item.get("pack_size"),
        quote.get("vendor_item_name"),
        quote.get("pack_size"),
        quote.get("unit_note"),
    ):
        text = str(value or "").strip()
        if text and text.lower() not in {part.lower() for part in parts}:
            parts.append(text)
    return " · ".join(parts) or "Comparable quoted pack"


def _normalized_pack_text(value):
    return re.sub(r"[^a-z0-9.#]+", "", str(value or "").lower())


def _directly_comparable(item, selected_quote, candidate_quote):
    """Prove pack comparability without introducing a new price algorithm."""
    if (item.get("count_unit") or "case") != "case":
        # Both quotes already converted into this same inventory count unit by
        # order_normalization.units_per_case.
        return True

    selected_basis = _normalized_pack_text(selected_quote.get("unit_basis"))
    candidate_basis = _normalized_pack_text(candidate_quote.get("unit_basis"))
    selected_quantity = _finite_number(selected_quote.get("unit_quantity"))
    candidate_quantity = _finite_number(candidate_quote.get("unit_quantity"))
    if (
        selected_basis
        and candidate_basis
        and selected_quantity is not None
        and candidate_quantity is not None
    ):
        return (
            selected_basis == candidate_basis
            and selected_quantity == candidate_quantity
        )

    selected_pack = _normalized_pack_text(selected_quote.get("pack_size"))
    candidate_pack = _normalized_pack_text(candidate_quote.get("pack_size"))
    return bool(selected_pack and candidate_pack and selected_pack == candidate_pack)


def build_order_decisions(
    canonical_items,
    best_prices,
    assignment,
    dropped_supplier_ids,
    included_product_ids=None,
    required_vendor_by_item=None,
):
    """Preserve why each supplier did or did not receive each ordered item.

    The comparison calls the exact ``extended_cost`` function used by the
    optimizer, so unlike-price case packs are never compared directly.
    """
    required_vendor_by_item = required_vendor_by_item or {}
    included = set(
        assignment.keys() if included_product_ids is None else included_product_ids
    )
    dropped = set(dropped_supplier_ids or [])
    decisions = []

    for item in canonical_items:
        product_id = item.get("id")
        if product_id not in assignment or product_id not in included:
            continue
        selected_supplier_id = int(assignment[product_id])
        selected_quote = best_prices.get(product_id, {}).get(selected_supplier_id)
        if not selected_quote:
            continue
        selected_cases = cases_required(item, selected_quote)
        selected_cost = _finite_number(extended_cost(item, selected_quote))
        if selected_cases is None or selected_cost is None:
            continue

        item_key = str(product_id)
        required_supplier = required_vendor_by_item.get(
            str(item.get("name") or "").lower().strip()
        )
        quotes = best_prices.get(product_id, {})
        for supplier_id in sorted(SUPPLIERS):
            quote = quotes.get(supplier_id)
            candidate_cost = (
                _finite_number(extended_cost(item, quote)) if quote else None
            )
            quote_is_eligible = bool(
                quote
                and quote.get("units_per_case") is not None
                and candidate_cost is not None
                and quote.get("eligible", True) is not False
            )
            quote_is_available = bool(
                quote_is_eligible and quote.get("available", True) is not False
            )
            quote_is_comparable = bool(
                quote_is_available
                and _directly_comparable(item, selected_quote, quote)
            )

            if supplier_id == selected_supplier_id:
                reason = DECISION_SELECTED
            elif required_supplier is not None:
                reason = DECISION_SPECIFICATION
            elif not quote_is_eligible:
                reason = DECISION_NO_QUOTE
            elif not quote_is_available:
                reason = DECISION_UNAVAILABLE
            elif not quote_is_comparable:
                reason = DECISION_NONCOMPARABLE
            elif supplier_id in dropped:
                reason = DECISION_MINIMUM
            elif candidate_cost > selected_cost:
                reason = DECISION_LOST_ON_PRICE
            elif candidate_cost == selected_cost:
                reason = DECISION_TIE
            else:
                # This should be unreachable for an unrestricted active quote,
                # but retaining an explicit non-price reason is safer than
                # inferring that a lower quote lost on price.
                reason = DECISION_OTHER

            allocated_elsewhere = (
                int(selected_cases) if supplier_id != selected_supplier_id else 0
            )
            decision = {
                "canonical_product_key": item_key,
                "item_id": product_id,
                "item_name": str(item.get("name") or "").strip(),
                "supplier_id": supplier_id,
                "selected_supplier_id": selected_supplier_id,
                "decision_reason": reason,
                "ordered_quantity": float(item.get("order_qty") or 0),
                "ordered_quantity_unit": item.get("count_unit") or "case",
                "allocated_elsewhere_cases": allocated_elsewhere,
                "recipient_item_number": (quote or {}).get("apn") or "",
                "recipient_description": _recipient_description(item, quote or {}),
                "quote_eligible": quote_is_eligible,
                "quote_available": quote_is_available,
                "quote_comparable": quote_is_comparable,
                "candidate_normalized_net_cost": candidate_cost,
                "selected_normalized_net_cost": selected_cost,
                "normalization_method": "existing_extended_cost",
            }
            eligible, omission = evaluate_feedback_decision(decision)
            decision["feedback_eligible"] = eligible
            decision["feedback_omission_reason"] = omission
            decisions.append(decision)
    return decisions


def evaluate_feedback_decision(decision):
    """Return ``(eligible, omission_reason)`` for one supplier decision."""
    if _finite_number(decision.get("ordered_quantity")) is None:
        return False, DECISION_MISSING_COMPARISON
    if float(decision.get("ordered_quantity") or 0) <= 0:
        return False, "not_ordered"
    if decision.get("decision_reason") != DECISION_LOST_ON_PRICE:
        return False, decision.get("decision_reason") or DECISION_OTHER
    if not decision.get("quote_eligible"):
        return False, "quote_ineligible"
    if not decision.get("quote_available"):
        return False, "quote_unavailable"
    if not decision.get("quote_comparable"):
        return False, "quote_noncomparable"
    if float(decision.get("allocated_elsewhere_cases") or 0) <= 0:
        return False, "no_quantity_allocated_elsewhere"
    candidate = _finite_number(decision.get("candidate_normalized_net_cost"))
    selected = _finite_number(decision.get("selected_normalized_net_cost"))
    if candidate is None or selected is None:
        return False, DECISION_MISSING_COMPARISON
    if candidate <= selected:
        return False, DECISION_TIE if candidate == selected else DECISION_OTHER
    return True, ""


def qualify_supplier_items(decisions, supplier_id):
    """Filter and aggregate eligible lines for a single supplier."""
    grouped = {}
    omissions = collections.Counter()
    for decision in decisions:
        if int(decision.get("supplier_id") or 0) != int(supplier_id):
            continue
        eligible, omission = evaluate_feedback_decision(decision)
        if not eligible:
            omissions[omission] += 1
            continue
        key = str(
            decision.get("canonical_product_key")
            or decision.get("item_id")
            or decision.get("item_name")
        )
        cases = float(decision.get("allocated_elsewhere_cases") or 0)
        if key not in grouped:
            grouped[key] = {
                "canonical_product_key": key,
                "item_name": str(decision.get("item_name") or "").strip(),
                "description": str(
                    decision.get("recipient_description") or "Comparable quoted pack"
                ).strip(),
                "recipient_item_number": str(
                    decision.get("recipient_item_number") or ""
                ).strip(),
                "cases_ordered": 0.0,
            }
        grouped[key]["cases_ordered"] += cases

    items = sorted(grouped.values(), key=lambda row: row["item_name"].lower())
    for item in items:
        item["cases_ordered"] = _display_number(item["cases_ordered"])
    return items, dict(sorted(omissions.items()))


def _display_number(value):
    number = float(value)
    rounded = round(number)
    return int(rounded) if abs(number - rounded) < 1e-9 else round(number, 2)


def _human_order_date(value):
    text = str(value or "").strip()
    try:
        parsed = dt.date.fromisoformat(text[:10])
        return f"{parsed.strftime('%B')} {parsed.day}, {parsed.year}"
    except ValueError:
        return text


def render_feedback_email(
    representative,
    order_date,
    items,
    purchasing_name,
    business_name,
    contact_detail="",
):
    if not items:
        raise FeedbackError("A supplier email cannot be rendered without qualifying items")
    display_date = _human_order_date(order_date)
    subject = f"Pricing feedback for our {display_date} order"
    item_count = len(items)
    case_count = _display_number(sum(float(i["cases_ordered"]) for i in items))

    rows = []
    text_rows = []
    for item in items:
        description = item["description"]
        if item.get("recipient_item_number"):
            description += f" · Your item #: {item['recipient_item_number']}"
        rows.append(
            "<tr>"
            f"<td style=\"padding:12px;border-top:1px solid #e5e7eb;"
            f"vertical-align:top;font-weight:600\">{html.escape(item['item_name'])}</td>"
            f"<td style=\"padding:12px;border-top:1px solid #e5e7eb;"
            f"vertical-align:top;color:#4b5563\">{html.escape(description)}</td>"
            f"<td style=\"padding:12px;border-top:1px solid #e5e7eb;"
            f"vertical-align:top;text-align:right;white-space:nowrap\">"
            f"{html.escape(str(item['cases_ordered']))}</td></tr>"
        )
        text_rows.append(
            f"- {item['item_name']} | {description} | {item['cases_ordered']} cases"
        )

    signature_lines = [purchasing_name, business_name]
    if contact_detail:
        signature_lines.append(contact_detail)
    signature_html = "<br>".join(html.escape(line) for line in signature_lines if line)
    signature_text = "\n".join(line for line in signature_lines if line)

    email_html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(subject)}</title></head>
<body style="margin:0;background:#f4f5f7;color:#1f2937;font-family:Arial,Helvetica,sans-serif;line-height:1.55">
<div style="display:none;max-height:0;overflow:hidden;color:transparent">Constructive pricing feedback for our {html.escape(display_date)} order.</div>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f4f5f7"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:680px;background:#ffffff;border:1px solid #e5e7eb;border-radius:8px;overflow:hidden">
<tr><td style="padding:28px 30px 12px"><p style="margin:0 0 16px">Hi {html.escape(representative.first_name)},</p>
<p style="margin:0 0 16px">Thank you for your continued support and partnership.</p>
<p style="margin:0 0 20px">I wanted to share a brief pricing update from our {html.escape(display_date)} order. The items below were not placed with {html.escape(representative.company)} because another comparable option offered a lower normalized price. I am sharing this as constructive feedback so you can review these items and identify any pricing programs or adjustments that may make them more competitive for future orders.</p></td></tr>
<tr><td style="padding:0 18px 8px"><div style="overflow-x:auto"><table role="table" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;font-size:14px">
<thead><tr style="background:#f3f4f6"><th align="left" style="padding:10px 12px">Item</th><th align="left" style="padding:10px 12px">Description / Pack</th><th align="right" style="padding:10px 12px;white-space:nowrap">Cases Ordered</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div></td></tr>
<tr><td style="padding:12px 30px 8px"><p style="margin:0;font-weight:600">Summary: {item_count} item{'s' if item_count != 1 else ''} representing {case_count} case{'s' if case_count != 1 else ''}</p></td></tr>
<tr><td style="padding:12px 30px 30px"><p style="margin:0 0 18px">No immediate action is required, but we would be glad to consider updated pricing, contract opportunities, or comparable alternatives on a future order.</p>
<p style="margin:0">Thank you,<br><br>{signature_html}</p></td></tr>
</table></td></tr></table></body></html>"""

    email_text = (
        f"Hi {representative.first_name},\n\n"
        "Thank you for your continued support and partnership.\n\n"
        f"I wanted to share a brief pricing update from our {display_date} order. "
        f"The items below were not placed with {representative.company} because "
        "another comparable option offered a lower normalized price. I am sharing "
        "this as constructive feedback so you can review these items and identify "
        "any pricing programs or adjustments that may make them more competitive "
        "for future orders.\n\n"
        "Item | Description / Pack | Cases Ordered\n"
        + "\n".join(text_rows)
        + f"\n\nSummary: {item_count} item{'s' if item_count != 1 else ''} "
        f"representing {case_count} case{'s' if case_count != 1 else ''}\n\n"
        "No immediate action is required, but we would be glad to consider updated "
        "pricing, contract opportunities, or comparable alternatives on a future "
        f"order.\n\nThank you,\n\n{signature_text}"
    )
    _assert_private_render(representative.company, subject, email_html, email_text)
    return {
        "subject": subject,
        "html": email_html,
        "text": email_text,
        "item_count": item_count,
        "case_count": case_count,
    }


def _assert_private_render(recipient_company, *parts):
    rendered = "\n".join(parts).lower()
    for supplier in SUPPLIERS.values():
        company = supplier["company"]
        if company.lower() != recipient_company.lower() and company.lower() in rendered:
            raise FeedbackError(
                f"Privacy safeguard blocked another supplier name in {recipient_company} email"
            )
    forbidden = (
        "winning price",
        "competitor price",
        "savings amount",
        "you lost",
        "too expensive",
        "missed sales",
    )
    for phrase in forbidden:
        if phrase in rendered:
            raise FeedbackError(f"Tone/privacy safeguard blocked phrase: {phrase}")


def sanitize_error(error):
    """Return a short, credential-free error suitable for persistent storage."""
    message = str(error or "delivery failed")
    message = re.sub(r"re_[A-Za-z0-9_-]+", "[redacted-api-key]", message)
    message = re.sub(
        r"(?i)authorization\s*:\s*bearer\s+\S+",
        "Authorization: [redacted]",
        message,
    )
    message = re.sub(r"[\r\n\t]+", " ", message)
    return message[:500]


class ResendClient:
    def __init__(self, api_key, endpoint=RESEND_ENDPOINT, opener=None):
        self.api_key = api_key
        self.endpoint = endpoint
        self.opener = opener or urllib.request.urlopen

    def send(self, message, key):
        payload = json.dumps(message).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Idempotency-Key": key,
            },
        )
        try:
            with self.opener(request, timeout=20) as response:
                data = json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            try:
                body = json.loads(error.read() or b"{}")
                detail = body.get("message") or body.get("name") or f"HTTP {error.code}"
            except Exception:
                detail = f"HTTP {error.code}"
            raise DeliveryError(f"Resend rejected the request: {detail}") from error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise DeliveryError(f"Resend request failed: {sanitize_error(error)}") from error
        email_id = data.get("id")
        if not email_id:
            raise DeliveryError("Resend response did not contain an email ID")
        return str(email_id)


class SupabaseFeedbackStore:
    """Server-only PostgREST access for final orders and feedback send records."""

    def __init__(self, url, secret_key, opener=None):
        if not url or not secret_key:
            raise ConfigurationError(
                "SUPABASE_URL and SUPABASE_SERVICE_KEY are required for order feedback"
            )
        self.base_url = url.rstrip("/") + "/rest/v1"
        self.secret_key = secret_key
        self.opener = opener or urllib.request.urlopen

    @classmethod
    def from_env(cls):
        return cls(
            os.environ.get("SUPABASE_URL", "").strip(),
            os.environ.get("SUPABASE_SERVICE_KEY", "").strip(),
        )

    def _request(self, method, path, payload=None, prefer=""):
        headers = {
            "apikey": self.secret_key,
            "Accept": "application/json",
        }
        # Opaque sb_secret keys are authenticated by the API gateway through
        # ``apikey``. Legacy service-role keys are JWTs and also belong in the
        # Authorization header so PostgREST sees the service_role claim.
        if not self.secret_key.startswith("sb_secret_"):
            headers["Authorization"] = f"Bearer {self.secret_key}"
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")
        if prefer:
            headers["Prefer"] = prefer
        request = urllib.request.Request(
            f"{self.base_url}/{path}", data=data, headers=headers, method=method
        )
        try:
            with self.opener(request, timeout=20) as response:
                raw = response.read()
                return json.loads(raw) if raw else []
        except urllib.error.HTTPError as error:
            try:
                body = json.loads(error.read() or b"{}")
                detail = body.get("message") or body.get("hint") or f"HTTP {error.code}"
            except Exception:
                detail = f"HTTP {error.code}"
            raise StorageError(f"Supabase order-feedback write failed: {detail}") from error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise StorageError(
                f"Supabase order-feedback request failed: {sanitize_error(error)}"
            ) from error

    @staticmethod
    def _eq(value):
        return urllib.parse.quote(str(value), safe="")

    def get_order(self, order_id):
        rows = self._request(
            "GET",
            f"food_orders?id=eq.{self._eq(order_id)}&select=*",
        )
        return rows[0] if rows else None

    def get_latest_order(self):
        rows = self._request(
            "GET",
            "food_orders?select=*&order=created_at.desc&limit=1",
        )
        return rows[0] if rows else None

    def create_pending_order(self, order, submissions=None):
        row = {
            "id": order["order_id"],
            "order_date": order["order_date"],
            "status": "pending",
            "inventory_snapshot_id": order.get("inventory_snapshot_id"),
            "expected_supplier_ids": order.get("expected_supplier_ids") or [],
            "vendor_confirmations": submissions or {},
            "item_total": int(order.get("item_total") or 0),
            "case_total": float(order.get("case_total") or 0),
        }
        rows = self._request(
            "POST", "food_orders", row, prefer="return=representation"
        )
        return rows[0]

    def upsert_order_lines(self, order_id, order_lines):
        rows = []
        for line in order_lines:
            rows.append(
                {
                    **line,
                    "order_id": order_id,
                    "item_id": line.get("item_id"),
                }
            )
        if rows:
            self._request(
                "POST",
                "food_order_lines?on_conflict=order_id,supplier_id,canonical_product_key",
                rows,
                prefer="resolution=merge-duplicates,return=minimal",
            )

    def get_order_lines(self, order_id):
        return self._request(
            "GET",
            f"food_order_lines?order_id=eq.{self._eq(order_id)}&select=*"
            "&order=supplier_id.asc,item_name.asc",
        )

    def upsert_decisions(self, order_id, decisions):
        rows = []
        for decision in decisions:
            rows.append(
                {
                    **decision,
                    "order_id": order_id,
                    "item_id": decision.get("item_id"),
                }
            )
        if rows:
            self._request(
                "POST",
                "food_order_decisions?on_conflict=order_id,canonical_product_key,supplier_id",
                rows,
                prefer="resolution=merge-duplicates,return=minimal",
            )

    def finalize_order(self, order_id, submissions):
        rows = self._request(
            "PATCH",
            f"food_orders?id=eq.{self._eq(order_id)}&status=eq.pending",
            {
                "status": "finalized",
                "vendor_confirmations": submissions,
                "finalized_at": utc_now(),
            },
            prefer="return=representation",
        )
        if rows:
            return rows[0]
        existing = self.get_order(order_id)
        if existing and existing.get("status") == "finalized":
            return existing
        raise StorageError("Final order could not be marked finalized")

    def get_decisions(self, order_id):
        return self._request(
            "GET",
            f"food_order_decisions?order_id=eq.{self._eq(order_id)}&select=*"
            "&order=supplier_id.asc,item_name.asc",
        )

    def get_send_record(self, order_id, supplier_id, template_version=TEMPLATE_VERSION):
        rows = self._request(
            "GET",
            f"order_feedback_sends?order_id=eq.{self._eq(order_id)}"
            f"&supplier_id=eq.{int(supplier_id)}"
            f"&template_version=eq.{self._eq(template_version)}&select=*",
        )
        return rows[0] if rows else None

    def upsert_send_record(self, record):
        rows = self._request(
            "POST",
            "order_feedback_sends?on_conflict=order_id,supplier_id,template_version",
            record,
            prefer="resolution=merge-duplicates,return=representation",
        )
        return rows[0] if rows else record

    def patch_send_record(self, order_id, supplier_id, changes, status_filter=None):
        path = (
            f"order_feedback_sends?order_id=eq.{self._eq(order_id)}"
            f"&supplier_id=eq.{int(supplier_id)}"
            f"&template_version=eq.{self._eq(TEMPLATE_VERSION)}"
        )
        if status_filter:
            statuses = ",".join(status_filter)
            path += f"&status=in.({statuses})"
        rows = self._request(
            "PATCH", path, changes, prefer="return=representation"
        )
        return rows[0] if rows else None


def stage_order(store, order):
    """Persist the exact generated order before any vendor side effect."""
    existing = store.get_order(order["order_id"])
    if existing and existing.get("status") == "finalized":
        return existing
    if not existing:
        existing = store.create_pending_order(order, {})
    store.upsert_order_lines(order["order_id"], order.get("order_lines") or [])
    store.upsert_decisions(order["order_id"], order.get("decisions") or [])
    return existing


def save_final_order(store, order, submissions):
    """Verify confirmations, then mark an already-staged order finalized."""
    expected = {int(value) for value in order.get("expected_supplier_ids") or []}
    confirmed = {
        int(key)
        for key, value in (submissions or {}).items()
        if isinstance(value, dict) and value.get("success")
    }
    missing = sorted(expected - confirmed)
    if missing:
        raise FeedbackError(
            "Order is not final: missing successful submissions for supplier IDs "
            + ", ".join(map(str, missing))
        )
    existing = store.get_order(order["order_id"])
    if existing and existing.get("status") == "finalized":
        return existing
    stage_order(store, order)
    return store.finalize_order(order["order_id"], submissions)


class FeedbackService:
    def __init__(self, store, config, resend_client=None):
        self.store = store
        self.config = config
        self.resend_client = resend_client

    def prepare_order(self, order_id):
        order = self.store.get_order(order_id)
        if not order or order.get("status") != "finalized":
            raise FeedbackError("Feedback can only be prepared for a saved finalized order")
        decisions = self.store.get_decisions(order_id)
        results = []
        for supplier_id in sorted(SUPPLIERS):
            results.append(
                self._prepare_supplier(order, decisions, supplier_id, persist=True)
            )
        return results

    def preview_supplier(self, order_id, supplier_id):
        order = self.store.get_order(order_id)
        if not order or order.get("status") != "finalized":
            raise FeedbackError("Feedback can only be previewed for a saved finalized order")
        return self._prepare_supplier(
            order, self.store.get_decisions(order_id), int(supplier_id), persist=True
        )

    def _prepare_supplier(self, order, decisions, supplier_id, persist):
        representative = self.config.representatives.get(int(supplier_id))
        if not representative:
            raise ConfigurationError(
                f"Representative configuration is missing for supplier ID {supplier_id}"
            )
        items, omissions = qualify_supplier_items(decisions, supplier_id)
        key = idempotency_key(order["id"], supplier_id)
        existing = self.store.get_send_record(order["id"], supplier_id) if persist else None
        base = {
            "order_id": order["id"],
            "supplier_id": int(supplier_id),
            "template_version": TEMPLATE_VERSION,
            "idempotency_key": key,
            "intended_recipient": representative.email,
            "item_total": len(items),
            "case_total": sum(float(item["cases_ordered"]) for item in items),
            "omission_summary": omissions,
        }
        if not items:
            result = {
                **base,
                "status": "skipped",
                "subject": "",
                "html": "",
                "text": "",
            }
            if persist and not (existing and existing.get("status") == "sent"):
                record = dict(result)
                record.pop("html", None)
                record.pop("text", None)
                self.store.upsert_send_record(record)
            return result

        rendered = render_feedback_email(
            representative,
            order["order_date"],
            items,
            self.config.purchasing_name,
            self.config.business_name,
            self.config.contact_detail,
        )
        result = {
            **base,
            **rendered,
            "status": "dry-run",
            "preview_html": rendered["html"],
            "preview_text": rendered["text"],
        }
        if persist and not (existing and existing.get("status") == "sent"):
            record = dict(result)
            record.pop("html", None)
            record.pop("text", None)
            record.pop("item_count", None)
            record.pop("case_count", None)
            if existing and existing.get("status") in {"failed", "pending"}:
                record["status"] = existing["status"]
            self.store.upsert_send_record(record)
        if existing and existing.get("status") == "sent":
            result["status"] = "sent"
            result["resend_email_id"] = existing.get("resend_email_id")
        return result

    def send(self, order_id, supplier_id, action):
        if action not in {"test-send", "live-send"}:
            raise FeedbackError("Action must be test-send or live-send")
        self.config.validate_delivery(action)
        preview = self.preview_supplier(order_id, supplier_id)
        if preview["status"] == "skipped":
            return preview
        existing = self.store.get_send_record(order_id, supplier_id)
        if action == "live-send" and existing and existing.get("status") == "sent":
            return {**preview, "status": "sent", "duplicate_prevented": True}

        attempts = int((existing or {}).get("attempt_count") or 0) + 1
        attempted_at = utc_now()
        if action == "live-send":
            claimed = self.store.patch_send_record(
                order_id,
                supplier_id,
                {
                    "status": "pending",
                    "attempt_count": attempts,
                    "attempted_at": attempted_at,
                    "sanitized_error": None,
                },
                status_filter=("dry-run", "failed"),
            )
            if not claimed:
                current = self.store.get_send_record(order_id, supplier_id)
                if current and current.get("status") == "sent":
                    return {**preview, "status": "sent", "duplicate_prevented": True}
                raise FeedbackError("A live send is already pending for this supplier")
            recipient = self.config.representatives[int(supplier_id)].email
            subject = preview["subject"]
            key = preview["idempotency_key"]
        else:
            recipient = self.config.test_recipient
            subject = f"[TEST] {preview['subject']}"
            digest = hashlib.sha256(recipient.lower().encode()).hexdigest()[:12]
            key = f"{preview['idempotency_key']}/test/{digest}"
            self.store.patch_send_record(
                order_id,
                supplier_id,
                {"attempt_count": attempts, "attempted_at": attempted_at},
            )

        client = self.resend_client or ResendClient(self.config.resend_api_key)
        message = {
            "from": self.config.from_address,
            "to": [recipient],
            "reply_to": self.config.reply_to,
            "subject": subject,
            "html": preview["html"],
            "text": preview["text"],
            "tags": [
                {"name": "category", "value": "pricing-feedback"},
                {"name": "supplier", "value": SUPPLIERS[int(supplier_id)]["slug"]},
            ],
        }
        try:
            email_id = client.send(message, key)
        except Exception as error:
            failure_status = (
                "sent"
                if action == "test-send"
                and existing
                and existing.get("status") == "sent"
                else "failed"
            )
            self.store.patch_send_record(
                order_id,
                supplier_id,
                {
                    "status": failure_status,
                    "attempt_count": attempts,
                    "attempted_at": attempted_at,
                    "sanitized_error": sanitize_error(error),
                },
            )
            raise

        changes = {
            "attempt_count": attempts,
            "attempted_at": attempted_at,
            "sanitized_error": None,
        }
        if action == "live-send":
            changes.update(
                {
                    "status": "sent",
                    "resend_email_id": email_id,
                    "sent_at": utc_now(),
                }
            )
        else:
            changes.update(
                {
                    "status": (
                        "sent"
                        if existing and existing.get("status") == "sent"
                        else "dry-run"
                    ),
                    "last_test_resend_email_id": email_id,
                }
            )
        self.store.patch_send_record(order_id, supplier_id, changes)
        return {
            **preview,
            "status": "sent" if action == "live-send" else "test-sent",
            "delivered_to": recipient,
            "resend_email_id": email_id,
        }
