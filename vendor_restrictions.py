"""Business rules that limit which broadliners may supply specific items."""


EXCLUDED_VENDORS_BY_ITEM = {
    "aluminum 1/3 pans": frozenset({2, 3, 4}),  # Contracted to US Foods.
    # Sysco is vendor-blocked; PFG FL098 is blocked separately so a future
    # corrected PFG SKU can be approved without changing the vendor rule.
    "bulk sugar": frozenset({3}),
    "dishmachine detergent": frozenset({2, 3, 4}),  # Contracted to US Foods.
    "low temp sanitizer": frozenset({2, 3, 4}),  # Contracted to US Foods.
    "mozzarella sticks": frozenset({2, 3, 4}),  # User-confirmed US Foods only.
    "pot & pan detergent": frozenset({2, 3, 4}),  # Contracted to US Foods.
    "pre soak": frozenset({2, 3, 4}),  # Contracted to US Foods.
    "quat sanitizer": frozenset({2, 3, 4}),  # Contracted to US Foods.
    "sanitizing floor cleaner": frozenset({2, 3, 4}),  # Contracted to US Foods.
    "solid dish detergent": frozenset({2, 3, 4}),  # Contracted to US Foods.
    "sliced red onions": frozenset({4}),  # Do not order sliced onions from GFS.
}

# Vendor SKUs that are known to be the wrong product for the named item.
# Unlike a vendor-wide restriction, a corrected SKU from the same vendor remains allowed.
EXCLUDED_VENDOR_APNS_BY_ITEM = {
    # PFG CustomerFirst full-catalog audit, 2026-08-26.
    # These products remain visible in the item master with an audit status, but
    # must not flow into generated orders until the identity/availability issue
    # is resolved and a subsequent audit removes the block.
    "bulk sugar": frozenset({(2, "FL098")}),  # Removed: 50 lb bag is not the required pack.
    # Sysco Spring 2026 audit, 2026-08-26.  These stale/incorrect SKUs remain
    # blocked even if a future scrape happens to rank them after the verified
    # replacement.
    "chafing fuel can 4 hour": frozenset({
        (2, "FC002"),
        (3, "7092795"),  # Six-hour fuel, not the requested four-hour fuel.
    }),
    "chicken wings": frozenset({
        (3, "8439794"),  # Boneless breaded 2/5 lb, not raw bone-in 4/10 lb.
    }),
    "coarse ground black pepper": frozenset({
        (3, "5229273"),  # EA-only in Sysco Shop; automated ordering submits CS.
    }),
    "fire roasted salsa": frozenset({
        (3, "7775069"),  # Chunky mild salsa, not fire-roasted diced salsa.
    }),
    "garlic powder": frozenset({
        (3, "9806449"),  # EA-only in Sysco Shop; automated ordering submits CS.
    }),
    "hungarian style paprika": frozenset({
        (3, "5229224"),  # EA-only in Sysco Shop; automated ordering submits CS.
    }),
    "oranges": frozenset({(2, "HB846")}),  # Ordering disabled by PFG.
    # Pending representative approval; keep the historical SKU out of ordering.
    "pecorino romano blend": frozenset({(1, "3588381")}),
    "ranch dressing": frozenset({(3, "4428298")}),  # Dry ranch mix, not prepared dressing.
    "sliced red tomatoes": frozenset({(2, "VL638")}),  # Removed: whole tomato, not sliced.
    "use first stickers": frozenset({(2, "N7184")}),  # Ordering disabled by PFG.
    "variety dessert bars": frozenset({
        (3, "4290474"),  # Exact product is currently out of stock.
    }),
    "yellow mustard": frozenset({
        (3, "1608850"),  # Mustard packets, not prepared mustard in 4/1 gallon.
    }),
}


def vendor_allowed_for_item(item_name, vendor_id, apn=None):
    """Return whether a vendor is an approved source for the named item."""
    item_key = str(item_name or "").lower().strip()
    if vendor_id in EXCLUDED_VENDORS_BY_ITEM.get(item_key, frozenset()):
        return False
    vendor_apn = str(apn or "").upper().strip()
    if vendor_apn and (vendor_id, vendor_apn) in EXCLUDED_VENDOR_APNS_BY_ITEM.get(
        item_key, frozenset()
    ):
        return False
    return True
