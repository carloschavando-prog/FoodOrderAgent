"""Party-demand retrieval, conversion, safety checks, and shared persistence.

All Event Kitchen names and purchasing conversions live in this module so the
browser and order generator consume one authoritative set of calculated totals.
"""

from __future__ import annotations

import datetime as dt
import http.cookiejar
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from zoneinfo import ZoneInfo


EASTERN = ZoneInfo("America/New_York")
BUFFER_FACTOR = 1.10
EVENT_KITCHEN_BASE_URL = os.environ.get(
    "EVENT_KITCHEN_BASE_URL", "https://eventhost-opal.vercel.app"
).rstrip("/")
EVENT_KITCHEN_PIN = os.environ.get("EVENT_KITCHEN_PIN", "").strip()
EVENT_KITCHEN_SESSION_COOKIE = os.environ.get(
    "EVENT_KITCHEN_SESSION_COOKIE", ""
).strip()
EVENT_PREP_BASE_URL = os.environ.get(
    "EVENT_PREP_BASE_URL", "https://preplist-theta.vercel.app"
).rstrip("/")
SOURCE_MAX_AGE_MINUTES = float(
    os.environ.get("PARTY_SOURCE_MAX_AGE_MINUTES", "60")
)
SOURCE_SYNC_MAX_ATTEMPTS = 3
SOURCE_SYNC_RETRY_BASE_SECONDS = 0.5
TRANSIENT_SOURCE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
EXCLUDED_EVENT_IDS = frozenset(filter(None, (
    value.strip()
    for value in os.environ.get("PARTY_EXCLUDED_EVENT_IDS", "").split(",")
)))

SB_URL = os.environ.get(
    "SUPABASE_URL", "https://gnkwdoohzspomvdshzge.supabase.co"
).rstrip("/")
SB_KEY = os.environ.get("SUPABASE_KEY", "")
SB_SKEY = os.environ.get("SUPABASE_SERVICE_KEY", SB_KEY)


class PartyDemandError(RuntimeError):
    """Base error for party-demand synchronization."""


class PartyDemandBlocked(PartyDemandError):
    """Raised when an order is unsafe without a recorded manager override."""

    def __init__(self, message, snapshot=None):
        super().__init__(message)
        self.snapshot = snapshot


def _log_party_event(event, **details):
    """Emit one compact, secret-free record for each refresh boundary."""
    print(json.dumps({
        "component": "party_demand",
        "event": event,
        **details,
    }, default=str, separators=(",", ":")), flush=True)


# Exact aliases are intentional. Unknown food is a blocking warning, never a
# fuzzy match. ``kind`` determines the buffered conversion into inventory units.
PARTY_ITEM_MAPPINGS = {
    "wings": {
        "aliases": ("wings", "chicken wings"),
        "inventory_item": "Chicken Wings",
        "raw_unit": "wings",
        "inventory_unit": "case",
        "kind": "ceil_divide",
        "divisor": 200.0,
        "note": "200 wings per case",
    },
    "chicken_tenders": {
        "aliases": ("chicken tenders", "tenders"),
        "inventory_item": "Tenders",
        "raw_unit": "tenders",
        "inventory_unit": "case",
        "kind": "ceil_divide",
        "divisor": 75.0,
        "note": "75 tenders per case",
    },
    "tater_kegs": {
        "aliases": (
            "tater kegs",
            "tater keg",
            "tater cakes",
            "tater cake",
            "tater kinks",
            "tater kink",
        ),
        "inventory_item": "Tater Kegs",
        "raw_unit": "pieces",
        "inventory_unit": "case",
        "kind": "ceil_divide",
        "divisor": 100.0,
        "note": "100 pieces per case",
    },
    "taco_beef": {
        "aliases": ("taco beef",),
        "inventory_item": "JTM Taco Meat",
        "raw_unit": "pounds",
        "inventory_unit": "5-pound bag",
        "kind": "ceil_divide",
        "divisor": 5.0,
        "note": "5 pounds per bag; 4 bags per vendor case",
    },
    "taco_chicken": {
        "aliases": ("taco chicken",),
        "inventory_item": "Fajita Chicken",
        "raw_unit": "pounds",
        "inventory_unit": "5-pound bag",
        "kind": "ceil_divide",
        "divisor": 5.0,
        "note": "5 pounds per bag; 2 bags per vendor case",
    },
    "taco_tortillas": {
        "aliases": ("taco tortillas",),
        "inventory_item": 'Tortilla, Flour 6"',
        "raw_unit": "packs",
        "inventory_unit": "pack",
        "kind": "ceil_each",
        "note": "Counted as packs; 12 packs per vendor case",
    },
    "taco_black_beans": {
        "aliases": ("taco black beans",),
        "inventory_item": "Black Beans",
        "raw_unit": "recipes",
        "inventory_unit": "#10 can",
        "kind": "ceil_each",
        "note": "One #10 can per recipe; 6 cans per vendor case",
    },
    "ranch": {
        "aliases": ("ranch", "ranch bowl", "ranch bowls", "ranch dressing"),
        "inventory_item": "Ranch Dressing",
        "raw_unit": "bowls",
        "inventory_unit": "gallon",
        "kind": "ranch_gallons",
        "note": "8 fluid ounces per bowl; 128 ounces per gallon",
    },
    "cold_side_set": {
        "aliases": (
            "cold side set",
            "cold side sets",
            "taco bar cold side set",
            "taco bar cold side sets",
        ),
        "raw_unit": "sets",
        "kind": "cold_side_set",
        "note": "One bowl of every cold-side ingredient per set",
        "ingredients": (
            ("Diced Red Onions", "5-pound bag", 6.0, "6 bowls per 5-pound bag"),
            ("Diced Tomatoes", "5-pound bag", 5.0, "5 bowls per 5-pound bag"),
            ("Mild Cheddar Cheese", "5-pound bag", 5.0, "5 bowls per 5-pound bag"),
            ("Shredded Lettuce", "2-pound bag", 8.0, "8 bowls per 2-pound bag"),
            ("Fire Roasted Salsa", "68-ounce container", 3.0, "3 bowls per 68-ounce container"),
            ("Sour Cream", "5-pound tub", 3.0, "3 bowls per 5-pound tub"),
        ),
    },
}


def _norm(value):
    text = str(value or "").lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9#]+", " ", text)
    return " ".join(text.split())


_ALIASES = {
    _norm(alias): key
    for key, config in PARTY_ITEM_MAPPINGS.items()
    for alias in config["aliases"]
}


def eastern_now():
    return dt.datetime.now(EASTERN)


def _date(value):
    if isinstance(value, dt.datetime):
        return value.astimezone(EASTERN).date() if value.tzinfo else value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value))


def delivery_window(cycle, today=None, delivery_date=None):
    """Return inclusive Event Kitchen dates for the selected truck cycle."""
    cycle = str(cycle or "").lower().strip()
    if cycle not in {"tuesday", "friday"}:
        raise ValueError(f"Unknown delivery cycle: {cycle}")
    if delivery_date is None:
        base = _date(today or eastern_now())
        target_weekday = 1 if cycle == "tuesday" else 4
        delivery = base + dt.timedelta(days=(target_weekday - base.weekday()) % 7)
    else:
        delivery = _date(delivery_date)
    coverage_end = delivery + dt.timedelta(days=3 if cycle == "tuesday" else 4)
    return {
        "delivery_cycle": cycle,
        "delivery_date": delivery.isoformat(),
        "coverage_start": delivery.isoformat(),
        "coverage_end": coverage_end.isoformat(),
        "dates": [
            (delivery + dt.timedelta(days=offset)).isoformat()
            for offset in range((coverage_end - delivery).days + 1)
        ],
    }


class EventKitchenClient:
    """Authenticated reader for Event Kitchen's JSON API."""

    def __init__(self, base_url=None, pin=None, session_cookie=None, timeout=20):
        self.base_url = (base_url or EVENT_KITCHEN_BASE_URL).rstrip("/")
        self.pin = (pin if pin is not None else EVENT_KITCHEN_PIN).strip()
        self.session_cookie = (
            session_cookie
            if session_cookie is not None
            else EVENT_KITCHEN_SESSION_COOKIE
        ).strip()
        self.timeout = timeout
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar)
        )
        self._authenticated = False

    def authenticate(self):
        if self._authenticated:
            return
        if self.session_cookie:
            self.opener.addheaders = [("Cookie", self.session_cookie)]
            self._authenticated = True
            return
        if not self.pin:
            raise PartyDemandError(
                "EVENT_KITCHEN_PIN is not configured on the FoodOrder server."
            )
        request = urllib.request.Request(
            f"{self.base_url}/api/admin-session",
            data=json.dumps({"pin": self.pin}).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "On-Par-FoodOrder/party-demand-v1",
            },
            method="POST",
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                response.read()
        except Exception as exc:
            raise PartyDemandError(
                f"Event Kitchen authentication failed: {exc}"
            ) from exc
        self._authenticated = True

    def fetch_day(self, local_date):
        self.authenticate()
        query = urllib.parse.urlencode({
            "date": str(local_date),
            "_refresh": int(eastern_now().timestamp() * 1000),
        })
        url = f"{self.base_url}/api/kitchen/day?{query}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "User-Agent": "On-Par-FoodOrder/party-demand-v1",
            },
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise PartyDemandError(
                f"Unable to load Event Kitchen for {local_date}: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise PartyDemandError(
                f"Event Kitchen returned an invalid response for {local_date}."
            )
        return payload

    def sync_day(self, local_date):
        """Force Event Kitchen to synchronize the selected date from Tripleseat."""
        self.authenticate()
        payload = None
        for attempt in range(1, SOURCE_SYNC_MAX_ATTEMPTS + 1):
            request = urllib.request.Request(
                f"{self.base_url}/api/kitchen/sync",
                data=json.dumps({"date": str(local_date)}).encode("utf-8"),
                headers={
                    "Accept": "application/json",
                    "Cache-Control": "no-cache",
                    "Content-Type": "application/json",
                    "Pragma": "no-cache",
                    "User-Agent": "On-Par-FoodOrder/party-demand-v1",
                },
                method="POST",
            )
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except Exception as exc:
                status_code = (
                    exc.code if isinstance(exc, urllib.error.HTTPError) else None
                )
                retryable = (
                    status_code in TRANSIENT_SOURCE_STATUS_CODES
                    or (
                        not isinstance(exc, urllib.error.HTTPError)
                        and isinstance(exc, (urllib.error.URLError, TimeoutError))
                    )
                )
                if retryable and attempt < SOURCE_SYNC_MAX_ATTEMPTS:
                    retry_after = (
                        exc.headers.get("Retry-After")
                        if isinstance(exc, urllib.error.HTTPError) and exc.headers
                        else None
                    )
                    try:
                        delay = float(retry_after)
                    except (TypeError, ValueError):
                        delay = SOURCE_SYNC_RETRY_BASE_SECONDS * (2 ** (attempt - 1))
                    _log_party_event(
                        "source_sync_retry",
                        local_date=str(local_date),
                        attempt=attempt,
                        next_attempt=attempt + 1,
                        status_code=status_code,
                        retry_delay_seconds=delay,
                    )
                    time.sleep(delay)
                    continue
                raise PartyDemandError(
                    f"Unable to sync Event Kitchen from Tripleseat for "
                    f"{local_date}: {exc}"
                ) from exc
        if not isinstance(payload, dict):
            raise PartyDemandError(
                f"Event Kitchen returned an invalid sync response for "
                f"{local_date}."
            )
        sync_error = payload.get("error") or payload.get("syncError")
        if sync_error:
            raise PartyDemandError(str(sync_error))
        return payload


class PrepListEventClient:
    """Read Event Kitchen data through PrepList's server-side integration.

    This compatibility source is used only when FoodOrder has neither a direct
    Event Kitchen PIN nor a server session cookie. PrepList authenticates to
    Event Kitchen on its server and returns food-only event-prep records.
    """

    def __init__(self, base_url=None, timeout=20):
        self.base_url = (base_url or EVENT_PREP_BASE_URL).rstrip("/")
        self.timeout = timeout

    def fetch_day(self, local_date):
        url = (
            f"{self.base_url}/api/event-prep?"
            f"date={urllib.parse.quote(str(local_date))}"
        )
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "Cache-Control": "no-cache",
                "User-Agent": "On-Par-FoodOrder/party-demand-v1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise PartyDemandError(
                f"Unable to load Event Kitchen through PrepList for "
                f"{local_date}: {exc}"
            ) from exc
        if not isinstance(payload, dict) or payload.get("error"):
            raise PartyDemandError(
                str((payload or {}).get("error") or (
                    f"PrepList returned an invalid response for {local_date}."
                ))
            )
        return _prep_list_day_payload(payload, local_date)


def _prep_list_day_payload(payload, requested_date):
    """Translate PrepList's food-only event records into the kitchen contract."""
    local_date = str(payload.get("date") or requested_date)
    events = []
    for event in payload.get("events") or []:
        if not isinstance(event, dict):
            continue
        grouped_rows = {}
        for item in event.get("items") or []:
            if not isinstance(item, dict):
                continue
            section = str(item.get("section") or "Event prep")
            category = str(item.get("category") or section)
            grouped_rows.setdefault((section, category), []).append({
                "itemKey": item.get("key"),
                "foodName": item.get("foodName"),
                "quantity": item.get("quantity"),
                "unit": item.get("unit"),
                "description": item.get("description"),
            })
        warning_count = int(_number(event.get("warningCount")) or 0)
        warnings = []
        if warning_count:
            warnings.append({
                "message": (
                    f"Event Kitchen reported {warning_count} source "
                    f"warning{'s' if warning_count != 1 else ''}; review the "
                    "party in Event Kitchen."
                )
            })
        events.append({
            "event": {
                "eventId": event.get("id"),
                "name": event.get("name"),
                "localDate": local_date,
                "status": "Definite",
            },
            "needsReview": bool(event.get("needsReview")),
            "warnings": warnings,
            "sections": [
                {"label": section, "category": category, "rows": rows}
                for (section, category), rows in grouped_rows.items()
            ],
            "liveFoodAddOns": [],
        })
    return {
        "events": events,
        "sourceMode": str(payload.get("sourceMode") or payload.get("source") or "live"),
        "lastSyncedAt": payload.get("lastSyncedAt") or payload.get("fetchedAt"),
        "missingEnvironmentVariables": [],
    }


def _default_event_client():
    if EVENT_KITCHEN_PIN or EVENT_KITCHEN_SESSION_COOKIE:
        return EventKitchenClient()
    return PrepListEventClient()


def _number(value):
    if isinstance(value, bool) or value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
        if not match:
            return None
        number = float(match.group(0))
    return number if math.isfinite(number) else None


def _event_statuses(checklist):
    event = checklist.get("event") or {}
    candidates = (
        event.get("status"),
        event.get("eventStatus"),
        event.get("bookingStatus"),
        checklist.get("status"),
    )
    return [str(value) for value in candidates if value not in (None, "")]


def _event_status(checklist):
    statuses = _event_statuses(checklist)
    return statuses[0] if statuses else ""


def _is_definite(checklist):
    statuses = [_norm(value) for value in _event_statuses(checklist)]
    # The /api/kitchen/day contract is already definite-only. When a status is
    # supplied, independently enforce it so tentative/cancelled fixtures cannot
    # leak into demand. A terminal value in any supplied status field wins over
    # a conflicting or stale "Definite" value in another field.
    if not statuses:
        return True
    terminal = {"lost", "cancelled", "canceled", "dead", "declined"}
    for status in statuses:
        tokens = set(status.split())
        if tokens & terminal or ({"not", "definite"} <= tokens):
            return False
    return any("definite" in status.split() for status in statuses)


def _event_identity(checklist, fallback_date):
    event = checklist.get("event") or {}
    event_id = event.get("eventId", event.get("id"))
    name = event.get("name") or event.get("eventName") or "Unnamed event"
    local_date = event.get("localDate") or fallback_date
    return str(event_id or ""), str(name), str(local_date)


def _requirement(row, audit, origin, category, index):
    name = row.get("foodName") or row.get("name") or row.get("itemName")
    quantity = _number(row.get("quantity"))
    if not name or quantity is None or quantity <= 0:
        return None
    return {
        "event_id": audit["event_id"],
        "event_name": audit["event_name"],
        "event_date": audit["event_date"],
        "origin": origin,
        "requirement_id": str(
            row.get("itemKey")
            or row.get("id")
            or f"{origin}-{_norm(category)}-{index}"
        ),
        "category": str(category or ""),
        "food_name": str(name).strip(),
        "quantity": quantity,
        "unit": str(row.get("unit") or "").strip(),
        "description": str(row.get("description") or "").strip(),
    }


def _mapping_key(requirement):
    name = _norm(requirement.get("food_name"))
    category = _norm(requirement.get("category"))
    if category == "taco" or "taco" in category:
        taco_aliases = {
            "beef": "taco_beef",
            "taco beef": "taco_beef",
            "chicken": "taco_chicken",
            "taco chicken": "taco_chicken",
            "tortilla": "taco_tortillas",
            "tortillas": "taco_tortillas",
            "flour tortillas": "taco_tortillas",
            "black bean": "taco_black_beans",
            "black beans": "taco_black_beans",
            "cold side set": "cold_side_set",
            "cold side sets": "cold_side_set",
        }
        if name in taco_aliases:
            return taco_aliases[name]
    return _ALIASES.get(name)


def _converted_item(mapping_key, raw_quantity, event_breakdown):
    config = PARTY_ITEM_MAPPINGS[mapping_key]
    buffered = raw_quantity * BUFFER_FACTOR
    common = {
        "mapping_key": mapping_key,
        "party_item": mapping_key.replace("_", " ").title(),
        "raw_quantity": round(raw_quantity, 6),
        "raw_unit": config["raw_unit"],
        "buffered_quantity": round(buffered, 6),
        "buffered_unit": config["raw_unit"],
        "event_breakdown": event_breakdown,
    }
    if config["kind"] == "cold_side_set":
        return [
            {
                **common,
                "party_item": "Taco Bar Cold Side Sets",
                "inventory_item": inventory_item,
                "converted_quantity": int(math.ceil(buffered / bowls_per_unit)),
                "inventory_unit": inventory_unit,
                "conversion_note": f"{config['note']}; {note}",
            }
            for inventory_item, inventory_unit, bowls_per_unit, note
            in config["ingredients"]
        ]
    if config["kind"] == "ceil_divide":
        converted = int(math.ceil(buffered / config["divisor"]))
    elif config["kind"] == "ceil_each":
        converted = int(math.ceil(buffered))
    elif config["kind"] == "ranch_gallons":
        converted = round(buffered * 8.0 / 128.0, 6)
    else:
        raise ValueError(f"Unknown party conversion: {config['kind']}")
    return [{
        **common,
        "inventory_item": config["inventory_item"],
        "converted_quantity": converted,
        "inventory_unit": config["inventory_unit"],
        "conversion_note": config["note"],
    }]


def build_party_snapshot(day_payloads, cycle, delivery_date=None, synced_at=None):
    """Build a deterministic, replacement snapshot from Event Kitchen days."""
    window = delivery_window(cycle, delivery_date=delivery_date)
    allowed_dates = set(window["dates"])
    events = []
    raw_requirements = []
    warnings = []
    blocking = []
    seen_events = set()
    source_modes = set()
    last_synced_values = []

    payload_iter = (
        day_payloads.items()
        if isinstance(day_payloads, dict)
        else zip(window["dates"], day_payloads)
    )
    for requested_date, payload in payload_iter:
        if not isinstance(payload, dict):
            continue
        source_mode = str(payload.get("sourceMode") or "").strip()
        if source_mode:
            source_modes.add(source_mode)
        if payload.get("lastSyncedAt"):
            last_synced_values.append(str(payload["lastSyncedAt"]))
        source_problems = []
        if payload.get("syncError"):
            source_problems.append(str(payload["syncError"]))
        missing_vars = payload.get("missingEnvironmentVariables") or []
        if missing_vars:
            source_problems.append(
                "Event Kitchen is missing: " + ", ".join(map(str, missing_vars))
            )
        if source_mode.lower() == "mock":
            source_problems.append("Event Kitchen is returning mock data, not live events.")
        for problem in source_problems:
            if problem not in warnings:
                warnings.append(problem)
                blocking.append(problem)

        for checklist in payload.get("events") or []:
            if not isinstance(checklist, dict):
                continue
            event_id, event_name, event_date = _event_identity(
                checklist, requested_date
            )
            # This is an explicit operator safety override for source systems
            # that continue emitting an event after Tripleseat marks it Lost.
            if event_id in EXCLUDED_EVENT_IDS or not _is_definite(checklist):
                continue
            if not event_id or event_date not in allowed_dates or event_id in seen_events:
                continue
            seen_events.add(event_id)
            audit = {
                "event_id": event_id,
                "event_name": event_name,
                "event_date": event_date,
            }
            events.append(audit)

            event_warnings = [
                str(item.get("message") or item)
                for item in (checklist.get("warnings") or [])
                if item
            ]
            if checklist.get("needsReview"):
                message = (
                    f"{event_name} ({event_date}) is marked Needs Review"
                    + (f": {event_warnings[0]}" if event_warnings else ".")
                )
                warnings.append(message)
                blocking.append(message)

            seen_requirements = set()
            for section in checklist.get("sections") or []:
                if not isinstance(section, dict):
                    continue
                category = section.get("category") or section.get("label") or ""
                for index, row in enumerate(section.get("rows") or []):
                    if not isinstance(row, dict):
                        continue
                    requirement = _requirement(
                        row, audit, "food_requirement", category, index
                    )
                    if not requirement:
                        continue
                    dedupe_key = (requirement["origin"], requirement["requirement_id"])
                    if dedupe_key not in seen_requirements:
                        seen_requirements.add(dedupe_key)
                        raw_requirements.append(requirement)
            for index, row in enumerate(checklist.get("liveFoodAddOns") or []):
                if not isinstance(row, dict):
                    continue
                requirement = _requirement(
                    row, audit, "structured_food_add_on", row.get("category"), index
                )
                if not requirement:
                    continue
                dedupe_key = (requirement["origin"], requirement["requirement_id"])
                if dedupe_key not in seen_requirements:
                    seen_requirements.add(dedupe_key)
                    raw_requirements.append(requirement)

    grouped = defaultdict(lambda: {"quantity": 0.0, "requirements": []})
    for requirement in raw_requirements:
        key = _mapping_key(requirement)
        if not key:
            message = (
                f"Unmapped food requirement for {requirement['event_name']} "
                f"({requirement['event_date']}): {requirement['food_name']} "
                f"{requirement['quantity']:g} {requirement['unit']}"
            ).strip()
            warnings.append(message)
            blocking.append(message)
            continue
        grouped[key]["quantity"] += requirement["quantity"]
        grouped[key]["requirements"].append(requirement)

    aggregated_raw = []
    item_totals = []
    for key in sorted(grouped):
        group = grouped[key]
        config = PARTY_ITEM_MAPPINGS[key]
        by_event = defaultdict(float)
        event_info = {}
        for requirement in group["requirements"]:
            event_id = requirement["event_id"]
            by_event[event_id] += requirement["quantity"]
            event_info[event_id] = {
                "event_id": event_id,
                "event_name": requirement["event_name"],
                "event_date": requirement["event_date"],
            }
        breakdown = [
            {
                **event_info[event_id],
                "raw_quantity": round(quantity, 6),
                "raw_unit": config["raw_unit"],
            }
            for event_id, quantity in sorted(
                by_event.items(),
                key=lambda pair: (
                    event_info[pair[0]]["event_date"],
                    event_info[pair[0]]["event_name"],
                    pair[0],
                ),
            )
        ]
        aggregated_raw.append({
            "mapping_key": key,
            "party_item": key.replace("_", " ").title(),
            "raw_quantity": round(group["quantity"], 6),
            "raw_unit": config["raw_unit"],
            "source_requirement_count": len(group["requirements"]),
        })
        item_totals.extend(
            _converted_item(key, group["quantity"], breakdown)
        )

    item_totals.sort(key=lambda item: item["inventory_item"].lower())
    events.sort(key=lambda event: (
        event["event_date"], event["event_name"].lower(), event["event_id"]
    ))
    warnings = list(dict.fromkeys(warnings))
    blocking = list(dict.fromkeys(blocking))
    successful_sync = synced_at or (
        max(last_synced_values) if last_synced_values else eastern_now().isoformat()
    )
    source_mode = ", ".join(sorted(source_modes)) or "live"
    return {
        **{key: value for key, value in window.items() if key != "dates"},
        "source_event_ids": [event["event_id"] for event in events],
        "event_audit": events,
        "raw_requirements": raw_requirements,
        "aggregated_raw": aggregated_raw,
        "item_totals": item_totals,
        "last_successful_sync": successful_sync,
        "source_status": "ok" if not blocking else "warning",
        "source_mode": source_mode,
        "source_warnings": warnings,
        "blocking_warnings": blocking,
        "stale": False,
        "party_count": len(events),
        "can_generate": not blocking,
    }


def party_need_by_item(snapshot):
    totals = {}
    for item in (snapshot or {}).get("item_totals") or []:
        name = str(item.get("inventory_item") or "").lower().strip()
        quantity = _number(item.get("converted_quantity"))
        if name and quantity is not None and quantity > 0:
            totals[name] = totals.get(name, 0.0) + quantity
    return totals


def _sb_headers(prefer=None):
    if not SB_SKEY:
        raise PartyDemandError("SUPABASE_SERVICE_KEY is not configured.")
    headers = {
        "apikey": SB_SKEY,
        "Authorization": f"Bearer {SB_SKEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _sb_request(path, method="GET", payload=None, prefer=None, timeout=20):
    request = urllib.request.Request(
        f"{SB_URL}/rest/v1/{path}",
        data=(json.dumps(payload).encode("utf-8") if payload is not None else None),
        headers=_sb_headers(prefer),
        method=method,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else None


def save_party_snapshot(snapshot):
    columns = (
        "delivery_cycle", "delivery_date", "coverage_start", "coverage_end",
        "source_event_ids", "event_audit", "raw_requirements", "aggregated_raw",
        "item_totals", "last_successful_sync", "source_status", "source_mode",
        "source_warnings", "blocking_warnings", "stale",
    )
    payload = {column: snapshot.get(column) for column in columns}
    saved = _sb_request(
        "party_demand_snapshots",
        method="POST",
        payload=payload,
        prefer="return=representation",
    )
    if not saved:
        raise PartyDemandError("Party snapshot was not returned after saving.")
    return _decorate_snapshot(saved[0])


def _decorate_snapshot(snapshot):
    if not snapshot:
        return None
    result = dict(snapshot)
    result["party_count"] = len(result.get("event_audit") or [])
    result["can_generate"] = (
        not result.get("stale") and not (result.get("blocking_warnings") or [])
    )
    return result


def load_party_snapshot(snapshot_id=None, cycle=None, delivery_date=None):
    filters = ["select=*"]
    if snapshot_id is not None:
        filters.append(f"id=eq.{int(snapshot_id)}")
    if cycle:
        filters.append(f"delivery_cycle=eq.{urllib.parse.quote(str(cycle))}")
    if delivery_date:
        filters.append(f"delivery_date=eq.{urllib.parse.quote(str(delivery_date))}")
    filters.extend(("order=created_at.desc,id.desc", "limit=1"))
    rows = _sb_request("party_demand_snapshots?" + "&".join(filters))
    return _decorate_snapshot(rows[0]) if rows else None


def _stale_snapshot(previous, window, error_message):
    base = dict(previous or {})
    warnings = list(base.get("source_warnings") or [])
    blocking = list(base.get("blocking_warnings") or [])
    warnings.append(error_message)
    blocking.append(error_message)
    base.update({
        **{key: value for key, value in window.items() if key != "dates"},
        "source_status": "unconfigured" if "not configured" in error_message else "error",
        "source_warnings": list(dict.fromkeys(warnings)),
        "blocking_warnings": list(dict.fromkeys(blocking)),
        "stale": True,
        "can_generate": False,
    })
    for key in (
        "source_event_ids", "event_audit", "raw_requirements", "aggregated_raw",
        "item_totals",
    ):
        base.setdefault(key, [])
    return base


def _parsed_source_time(value):
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(EASTERN)


def _enforce_source_freshness(snapshot, payloads, refresh_started):
    """Block a refresh when any dated source payload predates the freshness SLA."""
    source_times = [
        (str(local_date), _parsed_source_time(payload.get("lastSyncedAt")))
        for local_date, payload in payloads.items()
        if isinstance(payload, dict) and payload.get("lastSyncedAt")
    ]
    stale_times = [
        (local_date, source_time)
        for local_date, source_time in source_times
        if (refresh_started - source_time).total_seconds()
        > SOURCE_MAX_AGE_MINUTES * 60
    ]
    if not stale_times:
        return snapshot
    oldest_date, oldest_time = min(stale_times, key=lambda item: item[1])
    message = (
        "Event Kitchen did not return a current Tripleseat sync. "
        f"The source for {oldest_date} was last synchronized at "
        f"{oldest_time.isoformat()}."
    )
    # ``stale`` is represented by the dedicated boolean column. Keep the
    # status within the database's existing constrained values so the safety
    # snapshot can still be persisted and audited.
    snapshot["source_status"] = "warning"
    snapshot["source_warnings"] = list(dict.fromkeys(
        list(snapshot.get("source_warnings") or []) + [message]
    ))
    snapshot["blocking_warnings"] = list(dict.fromkeys(
        list(snapshot.get("blocking_warnings") or []) + [message]
    ))
    snapshot["stale"] = True
    snapshot["can_generate"] = False
    return snapshot


def refresh_party_demand(cycle, delivery_date=None, client=None, persist=True):
    """Replace the selected window's totals with a fresh source calculation."""
    window = delivery_window(cycle, delivery_date=delivery_date)
    source = client or _default_event_client()
    refresh_started = eastern_now()
    refresh_id = int(refresh_started.timestamp() * 1000)
    sync_day = getattr(source, "sync_day", None)
    _log_party_event(
        "refresh_started",
        refresh_id=refresh_id,
        cycle=window["delivery_cycle"],
        delivery_date=window["delivery_date"],
        source=type(source).__name__,
        source_sync_supported=callable(sync_day),
    )
    try:
        authenticate = getattr(source, "authenticate", None)
        if callable(sync_day) and callable(authenticate):
            authenticate()

        # Tripleseat throttles mutating synchronization requests. Running one
        # POST per coverage date concurrently caused intermittent upstream 502s
        # and discarded otherwise healthy windows. Serialize only those writes;
        # the cache-backed day reads below remain parallel for response time.
        if callable(sync_day):
            for local_date in window["dates"]:
                _log_party_event(
                    "source_sync_started",
                    refresh_id=refresh_id,
                    local_date=local_date,
                )
                sync_day(local_date)
                _log_party_event(
                    "source_sync_completed",
                    refresh_id=refresh_id,
                    local_date=local_date,
                )

        def load_source_day(local_date):
            payload = source.fetch_day(local_date)
            _log_party_event(
                "source_day_loaded",
                refresh_id=refresh_id,
                local_date=local_date,
                event_count=len(payload.get("events") or []),
                source_last_synced_at=payload.get("lastSyncedAt"),
            )
            return payload

        unordered_payloads = {}
        with ThreadPoolExecutor(max_workers=min(5, len(window["dates"]))) as executor:
            futures = {
                executor.submit(load_source_day, local_date): local_date
                for local_date in window["dates"]
            }
            for future in as_completed(futures):
                unordered_payloads[futures[future]] = future.result()
        payloads = {
            local_date: unordered_payloads[local_date]
            for local_date in window["dates"]
        }
        snapshot = build_party_snapshot(
            payloads, window["delivery_cycle"], window["delivery_date"]
        )
        snapshot = _enforce_source_freshness(
            snapshot, payloads, refresh_started
        )
        _log_party_event(
            "refresh_built",
            refresh_id=refresh_id,
            party_count=snapshot.get("party_count", 0),
            source_status=snapshot.get("source_status"),
            blocking_warning_count=len(snapshot.get("blocking_warnings") or []),
        )
    except Exception as exc:
        _log_party_event(
            "refresh_failed",
            refresh_id=refresh_id,
            error=str(exc),
        )
        try:
            previous = load_party_snapshot(
                cycle=cycle, delivery_date=window["delivery_date"]
            )
        except Exception:
            previous = None
        snapshot = _stale_snapshot(previous, window, str(exc))
    if not persist:
        return snapshot
    try:
        saved = save_party_snapshot(snapshot)
        _log_party_event(
            "refresh_saved",
            refresh_id=refresh_id,
            snapshot_id=saved.get("id"),
            source_status=saved.get("source_status"),
        )
        return saved
    except Exception as exc:
        _log_party_event(
            "refresh_save_failed",
            refresh_id=refresh_id,
            error=str(exc),
        )
        message = f"Party demand could not be saved centrally: {exc}"
        snapshot["source_warnings"] = list(dict.fromkeys(
            list(snapshot.get("source_warnings") or []) + [message]
        ))
        snapshot["blocking_warnings"] = list(dict.fromkeys(
            list(snapshot.get("blocking_warnings") or []) + [message]
        ))
        snapshot["can_generate"] = False
        snapshot["id"] = None
        return snapshot


def record_manager_override(snapshot_id, reason, inventory_snapshot_id=None):
    reason = str(reason or "").strip()
    if not reason:
        raise PartyDemandBlocked("A manager override reason is required.")
    payload = {
        "party_demand_snapshot_id": int(snapshot_id),
        "inventory_snapshot_id": (
            int(inventory_snapshot_id) if inventory_snapshot_id is not None else None
        ),
        "reason": reason,
    }
    rows = _sb_request(
        "party_demand_overrides",
        method="POST",
        payload=payload,
        prefer="return=representation",
    )
    return rows[0] if rows else None


def require_safe_snapshot(snapshot, override_reason=None):
    if snapshot and snapshot.get("can_generate"):
        return False
    warnings = (snapshot or {}).get("blocking_warnings") or [
        "No party-demand snapshot is available."
    ]
    if not str(override_reason or "").strip():
        raise PartyDemandBlocked("Order blocked: " + " ".join(warnings), snapshot)
    if not snapshot or snapshot.get("id") is None:
        raise PartyDemandBlocked(
            "Order blocked: a manager override cannot be audited until the "
            "party snapshot is saved centrally.",
            snapshot,
        )
    return True
