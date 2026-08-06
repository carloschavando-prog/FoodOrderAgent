"""Authoritative inventory PAR levels for each delivery cycle."""

EVENT_DRIVEN_ITEM_NAMES = frozenset({
    "assorted peppers",
    "baby carrots",
    "black beans",
    "broccoli",
    "cherry tomatoes",
    "cucumbers",
    "fajita chicken",
    "fire roasted salsa",
    "jtm taco meat",
    "mild cheddar cheese",
    "sour cream",
    "tater kegs",
    "tater tots",
    'tortilla, flour 6"',
    "variety dessert bars",
})

# Friday values are the latest maintained standing PARs. Items without a
# cycle-specific usage calculation intentionally use this value on both trucks.
BASE_PAR_LEVELS = {
    "thermal paper": 2.0,
    "use first stickers": 2.0,
    "labels": 4.0,
    "shopping bags": 1.0,
    "sandwich bags": 2.0,
    "holy gospel": 8.0,
    "holy cow": 8.0,
    "holy voodoo": 2.0,
    "blanco": 2.0,
    "coarse ground black pepper": 1.0,
    "taco seasoning mix": 1.0,
    "garlic powder": 1.0,
    "italian seasoning": 1.0,
    "hungarian style paprika": 1.0,
    "kosher salt": 1.0,
    'tortilla, flour 12"': 7.0,
    "garlic parmesan": 9.0,
    "yellow mustard": 3.0,
    "ketchup packets": 3.0,
    "mustard packets": 3.0,
    "mayo packets": 3.0,
    "ope sauce": 15.0,
    "blended oil": 5.0,
    "olive oil": 1.0,
    "buffalo sauce": 3.0,
    "pizza sauce": 6.0,
    "bulk sugar": 1.0,
    "bbq sauce": 4.0,
    "maraschino cherries": 3.0,
    "cholula": 1.0,
    "crushed red pepper packets": 2.0,
    "premium buttery pan & grill": 3.0,
    "shortening": 15.0,
    "croutons": 3.0,
    "styrofoam to-go containers": 4.0,
    "can liners": 6.0,
    "deli paper": 3.0,
    "straws": 2.0,
    "2 oz to-go cups": 2.0,
    "2 oz lids": 2.0,
    "foil sheets": 2.0,
    "cutlery kits": 1.0,
    "savaday": 2.0,
    "napkins xpressnap": 7.0,
    "t-shirt bags": 1.0,
    "plastic wrap": 1.0,
    "aluminum foil roll": 1.0,
    "pizza boxes": 4.0,
    "green onions": 2.0,
    "celery sticks": 3.0,
    "shredded lettuce": 6.0,
    "burger patties": 5.0,
    "double lobe chicken breasts": 4.0,
    "chicken wings": 7.0,
    "american slices 120 ct": 4.0,
    "pecorino romano blend": 1.0,
    "parmesan cheese": 1.0,
    "oranges": 2.0,
    "limes": 2.0,
    "sliced red tomatoes": 2.0,
    "sliced red onions": 2.0,
    "diced tomatoes": 2.0,
    "diced red onions": 2.0,
    "pizza cheese": 10.0,
    "caesar dressing": 3.0,
    "ranch dressing": 8.0,
    "simple syrup": 10.0,
    "pickles": 1.0,
    "bacon toppings": 2.0,
    "sliced bacon": 3.0,
    "potato hamburger bun": 6.0,
    "fries": 30.0,
    "flatbread dough": 10.0,
    "pepperoni": 4.0,
    "beer cheese dip": 12.0,
    "tenders": 10.0,
    "milwaukee pretzel": 20.0,
    "eco lyzer": 1.0,
    "delimer": 3.0,
    "oven cleaner": 3.0,
    "stainless steel polish": 3.0,
    "solid dish detergent": 2.0,
    "degreaser": 2.0,
    "pot & pan detergent": 2.0,
    "pre soak": 2.0,
    "heavy duty rinse additive": 2.0,
    "low temp sanitizer": 2.0,
    "sanitizing floor cleaner": 2.0,
    "quat sanitizer": 2.0,
    "dishmachine detergent": 2.0,
    "stainless steel scrubber": 1.0,
    "green scrubbies": 1.0,
    "m nitrile gloves": 1.0,
    "l nitrile gloves": 4.0,
    "xl nitrile gloves": 4.0,
    "fryer filters": 1.0,
    "daily's sweet & sour mix": 4.0,
    "chafing fuel can 6 hour": 2.0,
    "aluminum 1/2 pans": 4.0,
    "aluminum 1/3 pans": 4.0,
}

# Usage-based values use the peak of six completed service windows from the
# GoTab exports. Mega Pretzels use 4 ounces of yellow mustard each; the peak
# windows require 2 gallons for Tuesday delivery and 3 gallons for Friday.
# Wrap ingredients use 1 tortilla and
# 4 cooked ounces of chicken per wrap; pizza cheese uses 5 ounces per cheese
# or pepperoni pizza. Tortillas and cheese receive the established 25% build
# factor; chicken converts cooked portions to raw purchasing weight at 80%
# yield before rounding to 12-count tortilla packs or 5-pound bags.
TUESDAY_PAR_OVERRIDES = {
    "burger patties": 3.0,
    "chicken wings": 3.0,
    "double lobe chicken breasts": 3.0,
    "flatbread dough": 3.0,
    "fries": 5.0,
    "milwaukee pretzel": 6.0,
    "pizza cheese": 5.0,
    "potato hamburger bun": 2.0,
    "tenders": 6.0,
    'tortilla, flour 12"': 5.0,
    "yellow mustard": 2.0,
}

FRIDAY_PAR_OVERRIDES = {
    "burger patties": 5.0,
    "chicken wings": 7.0,
    "double lobe chicken breasts": 4.0,
    "flatbread dough": 10.0,
    "fries": 30.0,
    "milwaukee pretzel": 20.0,
    "pizza cheese": 10.0,
    "potato hamburger bun": 6.0,
    "tenders": 10.0,
    'tortilla, flour 12"': 7.0,
}

DELIVERY_PARS = {
    "tuesday": {**BASE_PAR_LEVELS, **TUESDAY_PAR_OVERRIDES},
    "friday": {**BASE_PAR_LEVELS, **FRIDAY_PAR_OVERRIDES},
}


def par_for_delivery(item_name, truck_cycle):
    """Return an item's configured PAR, or None when it is not on the sheet."""
    cycle = str(truck_cycle or "").lower()
    if cycle not in DELIVERY_PARS:
        raise ValueError(f"Unknown truck cycle: {truck_cycle}")
    return DELIVERY_PARS[cycle].get(str(item_name or "").lower().strip())
