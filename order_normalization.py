"""Inventory count-unit normalization for vendor case packs."""

import math
import re


DRY_STOCK_CATEGORY_ID = 4
DISPOSABLES_CATEGORY_ID = 5

DRY_STOCK_COUNT_UNITS = {
    "garlic parmesan": "gallon",
    "yellow mustard": "gallon",
    "ketchup packets": "case",
    "mustard packets": "case",
    "mayo packets": "case",
    "ope sauce": "gallon",
    "golden sauce": "gallon",
    "blended oil": "gallon",
    "olive oil": "10-liter box",
    "buffalo sauce": "gallon",
    "pizza sauce": "#10 can",
    "bulk sugar": "5-pound bag",
    "bbq sauce": "gallon",
    "maraschino cherries": "1/2-gallon jar",
    "cholula": "each",
    "crushed red pepper packets": "case",
    "premium buttery pan & grill": "gallon",
    "fire roasted salsa": "each",
    "black beans": "#10 can",
    "shortening": "case",
    "croutons": "bag",
}

DISPOSABLES_COUNT_UNITS = {
    "styrofoam to-go containers": "case",
    "can liners": "case",
    "deli paper": "box",
    "straws": "box",
    "2 oz to-go cups": "case",
    "2 oz lids": "case",
    "foil sheets": "box",
    "cutlery kits": "case",
    "savaday": "case",
    "save-a-day": "case",
    "napkins c fold": "case",
    "t-shirt bags": "case",
    "plastic wrap": "roll",
    "aluminum foil roll": "roll",
    "pizza boxes": "case",
}

OUNCES_PER_GALLON = 128.0
OUNCES_PER_LITER = 33.8140227
OUNCES_PER_POUND = 16.0


def count_unit_for_item(item):
    """Return the inventory counting unit for normalized categories."""
    name = item["name"].lower().strip()
    if item.get("category_id") == DRY_STOCK_CATEGORY_ID:
        return DRY_STOCK_COUNT_UNITS.get(name, "case")
    if item.get("category_id") == DISPOSABLES_CATEGORY_ID:
        return DISPOSABLES_COUNT_UNITS.get(name, "case")
    return "case"


def _positive_quantity(pricing):
    try:
        quantity = float(pricing.get("unit_quantity"))
    except (TypeError, ValueError):
        return None
    return quantity if quantity > 0 else None


def _basis(pricing):
    value = str(pricing.get("unit_basis") or "").lower().strip()
    aliases = {
        "ounces": "oz",
        "ounce": "oz",
        "fl oz": "oz",
        "fluid ounce": "oz",
        "fluid ounces": "oz",
        "pounds": "lb",
        "pound": "lb",
        "liters": "liter",
        "litres": "liter",
        "litre": "liter",
        "gallons": "gallon",
        "gal": "gallon",
        "units": "each",
        "unit": "each",
    }
    return aliases.get(value, value)


def _explicit_pack_count(pricing, marker):
    text = " ".join(
        str(pricing.get(field) or "")
        for field in ("pack_size", "unit_note")
    )
    match = re.search(rf"(\d+(?:\.\d+)?)\s*(?:/|x)\s*{marker}", text, re.I)
    return float(match.group(1)) if match else None


def _inner_pack_count(pricing):
    """Read the number of inner boxes from a case pack such as 12/500."""
    text = " ".join(
        str(pricing.get(field) or "")
        for field in ("pack_size", "unit_note")
    )
    match = re.search(r"(?:^|\s)(\d+(?:\.\d+)?)\s*/\s*\d+", text, re.I)
    return float(match.group(1)) if match else None


def _rolls_per_case(item, pricing):
    expected_feet = {
        "plastic wrap": 2000.0,
        "aluminum foil roll": 1000.0,
    }.get(item["name"].lower().strip())
    if expected_feet is None or _basis(pricing) != "ft":
        return None

    # Reject a clearly different roll length even when stale normalization
    # metadata claims the expected total footage.
    product_text = " ".join(
        str(pricing.get(field) or "")
        for field in ("pack_size", "vendor_item_name")
    )
    explicit_lengths = [
        float(value)
        for value in re.findall(r"(\d+(?:\.\d+)?)\s*(?:ft|feet|')(?!\w)", product_text, re.I)
    ]
    if explicit_lengths and expected_feet not in explicit_lengths:
        return None

    quantity = _positive_quantity(pricing)
    return quantity / expected_feet if quantity else None


def units_per_case(item, pricing):
    """
    Convert one vendor case into the item's inventory counting unit.

    Returning None makes an incompatible or ambiguous vendor listing ineligible;
    the order generator must never assume that one case equals one non-case unit.
    """
    count_unit = item.get("count_unit") or count_unit_for_item(item)
    if count_unit == "case":
        return 1.0

    if count_unit == "box":
        pack_count = _inner_pack_count(pricing)
        if pack_count:
            return pack_count

    quantity = _positive_quantity(pricing)
    basis = _basis(pricing)
    if quantity is None:
        return None

    if count_unit == "gallon":
        if basis == "oz":
            return quantity / OUNCES_PER_GALLON
        if basis == "gallon":
            return quantity
        if basis == "liter":
            return quantity / 3.785411784
        return None

    if count_unit == "10-liter box":
        if basis == "liter":
            return quantity / 10.0
        if basis == "oz":
            return quantity / (10.0 * OUNCES_PER_LITER)
        return None

    if count_unit == "5-pound bag":
        if basis == "lb":
            return quantity / 5.0
        if basis == "oz":
            return quantity / (5.0 * OUNCES_PER_POUND)
        return None

    if count_unit == "1/2-gallon jar":
        if basis == "oz":
            return quantity / (OUNCES_PER_GALLON / 2.0)
        if basis == "gallon":
            return quantity / 0.5
        return None

    if count_unit == "bag" and item["name"].lower().strip() == "croutons":
        if basis == "lb":
            return quantity / 2.5
        if basis == "oz":
            return quantity / (2.5 * OUNCES_PER_POUND)
        return None

    if count_unit == "each":
        if basis == "each":
            return quantity
        item_sizes_oz = {
            "cholula": 5.0,
            "fire roasted salsa": 68.0,
        }
        each_ounces = item_sizes_oz.get(item["name"].lower().strip())
        if each_ounces and basis == "oz":
            return quantity / each_ounces
        return None

    if count_unit == "#10 can":
        pack_count = _explicit_pack_count(pricing, r"#?\s*10\b")
        return pack_count

    if count_unit == "box":
        inner_sizes = {
            "deli paper": 500.0,
            "straws": 500.0,
            "foil sheets": 500.0,
        }
        inner_size = inner_sizes.get(item["name"].lower().strip())
        if inner_size and basis == "each":
            return quantity / inner_size
        return None

    if count_unit == "roll":
        return _rolls_per_case(item, pricing)

    return None


def cases_required(item, pricing):
    """Return whole vendor cases needed to cover the item's count-unit shortage."""
    conversion = pricing.get("units_per_case")
    if conversion is None:
        conversion = units_per_case(item, pricing)
    if not conversion or conversion <= 0:
        return None
    nearest_whole = round(conversion)
    if abs(conversion - nearest_whole) < 0.01:
        conversion = float(nearest_whole)
    shortage = float(item.get("order_qty") or 0)
    if shortage <= 0:
        return 0
    return max(1, math.ceil((shortage / conversion) - 1e-9))


def extended_cost(item, pricing):
    cases = cases_required(item, pricing)
    if cases is None:
        return math.inf
    return cases * float(pricing["price"])
