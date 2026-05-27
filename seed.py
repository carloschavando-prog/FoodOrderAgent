"""
Seed items + current pricing into Supabase from the Orders CSV data.
Run: python3 seed.py
"""
import json, urllib.request, urllib.error, urllib.parse

URL  = "https://gnkwdoohzspomvdshzge.supabase.co/rest/v1"
KEY  = "sb_publishable_BZ9rpzEITSHCo2BVGHA1iA_7nsCVnMc"
HDRS = {
    "apikey": KEY,
    "Authorization": f"Bearer {KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

def post(path, payload):
    req = urllib.request.Request(f"{URL}/{path}", method="POST",
          data=json.dumps(payload).encode(), headers=HDRS)
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        # ignore duplicate key errors
        if "duplicate" in body.lower() or "23505" in body:
            return None
        print(f"  ERROR {path}: {body[:120]}")
        return None

# ── Vendor ID map ──────────────────────────────────────────
V = {"US FOODS":1,"PFG":2,"SYSCO":3,"GFS":4,"I SUPPLY":5,
     "ISUPPLY":5,"MARKETS DEPOT":6,"MEAT CHURCH":7}

# ── Items: (name, category_id, pack_size, par_level, preferred_vendor_id)
ITEMS = [
  # Paper Goods (cat 1)
  ("Thermal Paper",              1, "",           2,  1),
  ("Use First Stickers",         1, "",           2,  4),
  ("Labels",                     1, "",           2,  1),
  ("Shopping Bags",              1, "",           1,  1),
  ("Tamperproof Bags",           1, "",           2,  1),
  ("Portion Bags",               1, "",           2,  5),
  # Spice Shelf (cat 2)
  ("Holy Gospel",                2, "5 LB",       8,  7),
  ("Holy Cow",                   2, "5 LB",       6,  7),
  ("Holy Voodoo",                2, "5 LB",       6,  7),
  ("Blanco",                     2, "5 LB",       6,  7),
  ("Coarse Ground Black Pepper", 2, "5 LB",       1,  1),
  ("Taco Seasoning Mix",         2, "21 OZ",      1,  1),
  ("Garlic Powder",              2, "21 OZ",      1,  1),
  ("Italian Seasoning",          2, "6 OZ",       1,  1),
  ("Hungarian Style Paprika",    2, "18 OZ",      1,  1),
  ("Kosher Salt",                2, "12/3 LB",    1,  1),
  # Tortilla Shelf (cat 3)
  ('Tortilla, Flour 12"',        3, "8/12",       5,  1),
  ('Tortilla, Flour 6"',         3, "12/24",      6,  1),
  # Dry Stock (cat 4)
  ("Garlic Parmesan",            4, "2/1 GAL",    9,  2),
  ("Yellow Mustard",             4, "4/1 GAL",    4,  2),
  ("Ketchup Packets",            4, "1000/9G",    3,  2),
  ("Mustard Packets",            4, "500/7G",     3,  2),
  ("Mayo Packets",               4, "500/9G",     3,  2),
  ("Golden Sauce",               4, "2/1 GAL",   15,  3),
  ("Blended Oil",                4, "4/1 GAL",    5,  1),
  ("Olive Oil",                  4, "10 L",       1,  2),
  ("Buffalo Sauce",              4, "4/1 GAL",    3,  2),
  ("Pizza Sauce",                4, "6/#10",      6,  2),
  ("Bulk Sugar",                 4, "8/5 LB",     1,  1),
  ("BBQ Sauce",                  4, "4/1 GAL",    4,  2),
  ("Maraschino Cherries",        4, "6/0.5 GAL",  3,  1),
  ("Cholula",                    4, "24/5 OZ",    1,  1),
  ("Crushed Red Pepper Packets", 4, "200/1G",     2,  2),
  ("Premium Buttery Pan & Grill",4, "6/1 GAL",    3,  1),
  ("Fire Roasted Salsa",         4, "4/68 OZ",    8,  1),
  ("Black Beans",                4, "24/15.5oz",  3,  2),
  ("Shortening",                 4, "35 LB",     28,  1),
  ("Croutons",                   4, "4/2.5 LB",   3,  1),
  # Disposables (cat 5)
  ("To-Go Black Base Clear Top", 5, "",           2,  5),
  ("Styrofoam To-Go Containers", 5, "200",        4,  5),
  ("Can Liners",                 5, "5 Rolls",    6,  5),
  ("Deli Paper",                 5, "12/500",     3,  1),
  ("Straws",                     5, "4/500",      2,  5),
  ("2 oz To-Go Cups",            5, "2500",       2,  5),
  ("2 oz Lids",                  5, "2500",       2,  5),
  ("Foil Sheets",                5, "6/500",      2,  5),
  ("Cutlery Kits",               5, "250",        1,  5),
  ("Savaday",                    5, "2/250",      2,  1),
  ("Napkins C Fold",             5, "24 Packs",   7,  5),
  ("T-Shirt Bags",               5, "500",        1,  5),
  ("Plastic Wrap",               5, "2000'",      1,  5),
  ("Aluminum Foil Roll",         5, "18x1000",    1,  1),
  ("Pizza Boxes",                5, "50 CT",      4,  2),
  # Walk-In Cooler (cat 6)
  ("Baby Carrots",               6, "4/5 LB",     1,  1),
  ("Broccoli",                   6, "20 LB",      3,  2),
  ("Green Onions",               6, "4/2 LB",     2,  1),
  ("Cucumbers",                  6, "12 EA",      1,  2),
  ("Celery Sticks",              6, "2/5 LB",     3,  2),
  ("Shredded Lettuce",           6, "6/2 LB",     6,  2),
  ("Burger Patties",             6, "48/4 OZ",   11,  2),
  ("Double Lobe Chicken Breasts",6, "4/5 LB",     8,  1),
  ("Chicken Wings",              6, "40 LB",     15,  1),
  ("Assorted Peppers",           6, "5 LB",       1,  1),
  ("Cherry Tomatoes",            6, "10 LB",      1,  1),
  ("American Slices 120 CT",     6, "4/5#",       4,  2),
  ("Pecorino Romano Blend",      6, "4/5 LB",     1,  2),
  ("Parmesan Cheese",            6, "2/5 LB",     1,  1),
  ("Mild Cheddar Cheese",        6, "4/5 LB",     3,  1),
  ("Oranges",                    6, "88 CT",      2,  1),
  ("Limes",                      6, "48 EA",      2,  2),
  ("Sliced Red Tomatoes",        6, "2/5 LB",     2,  1),
  ("Sliced Red Onions",          6, "2/5 LB",     2,  1),
  ("Diced Tomatoes",             6, "2/5 LB",     2,  4),
  ("Diced Red Onions",           6, "2/5 LB",     2,  4),
  ("Pizza Cheese",               6, "6/5 LB",     3,  2),
  ("Caesar Dressing",            6, "4/1 GAL",    3,  1),
  ("Ranch Dressing",             6, "4/1 GAL",    8,  1),
  ("Sour Cream",                 6, "4/5 LB",     2,  1),
  ("Pickles",                    6, "5 GAL",      1,  2),
  ("Bacon Toppings",             6, "2/5 LB",     2,  1),
  ("Sliced Bacon",               6, "15 LB",      3,  2),
  # Freezer (cat 7)
  ("Potato Hamburger Bun",       7, "5/12",       6,  1),
  ("Fries",                      7, "6/5 LB",    40,  2),
  ("Flatbread Dough",            7, "28/1",      10,  2),
  ("Pepperoni",                  7, "10 LB",      4,  2),
  ("Fajita Chicken",             7, "2/5 LB",     6,  2),
  ("Beer Cheese Dip",            7, "4/5 LB",    12,  1),
  ("JTM Taco Meat",              7, "4/5 LB",     8,  1),
  ("Tenders",                    7, "2/5 LB",    21,  2),
  ("Boneless Wings",             7, "2/5 LB",    12,  1),
  ("Milwaukee Pretzel",          7, "8/24 OZ",   20,  1),
  ("David's Cookies",            7, "80/4.5 OZ",  4,  1),
  ("Pretzel Bites",              7, "500/.4 OZ",  5,  2),
  ("Tater Kegs",                 7, "10 LB",     12,  1),
  # Chemical Room (cat 8)
  ("Eco Lyzer",                  8, "4/1 GAL",    1,  5),
  ("Delimer",                    8, "4/1 GAL",    3,  1),
  ("Oven Cleaner",               8, "6/20 oz",    3,  1),
  ("Stainless Steel Polish",     8, "6/16 OZ",    3,  2),
  ("Solid Dish Detergent",       8, "2/9 LB",     2,  1),
  ("Degreaser",                  8, "6/32 oz",    2,  1),
  ("Pot & Pan Detergent",        8, "1 EA",       2,  1),
  ("Pre Soak",                   8, "1 EA",       2,  1),
  ("Heavy Duty Rinse Additive",  8, "1 EA",       2,  1),
  ("Low Temp Sanitizer",         8, "1 EA",       2,  1),
  ("Sanitizing Floor Cleaner",   8, "1 EA",       2,  1),
  ("Quat Sanitizer",             8, "1 EA",       2,  1),
  ("Dishmachine Detergent",      8, "1 EA",       2,  1),
  ("Stainless Steel Scrubber",   8, "6/12",       1,  1),
  ("Green Scrubbies",            8, "20 EA",      1,  1),
  ("M Nitrile Gloves",           8, "10/100",     1,  5),
  ("L Nitrile Gloves",           8, "10/100",     4,  5),
  ("XL Nitrile Gloves",          8, "10/100",     4,  5),
  ("Fryer Filters",              8, "",           1,  5),
  # Beverage Dock (cat 9)
  ("Daily's Sweet & Sour Mix",   9, "4/1 GAL",    4,  1),
  ("Chafing Fuel Can 6 Hour",    9, "24 CT",      2,  1),
  ("Aluminum 1/2 Pans",          9, "",           4,  1),
  ("Aluminum 1/3 Pans",          9, "",           4,  1),
]

# ── Pricing from Orders.csv (US Foods + PFG known prices) ─
# (item_name, vendor_id, apn, price, pack_size)
PRICES = [
  ("Coarse Ground Black Pepper", 1, "760843",  95.40, "5 LB"),
  ("Coarse Ground Black Pepper", 2, None,      63.31, "5 LB"),
  ("Taco Seasoning Mix",         1, "324251",  16.42, "21 OZ"),
  ("Taco Seasoning Mix",         2, None,      10.69, "21 OZ"),
  ("Garlic Powder",              1, "2501161", 14.70, "21 OZ"),
  ("Garlic Powder",              2, None,      11.34, "21 OZ"),
  ("Italian Seasoning",          1, "760314",  48.00, "6 OZ"),
  ("Italian Seasoning",          2, None,      15.80, "6 OZ"),
  ("Hungarian Style Paprika",    1, "760405",  20.70, "18 OZ"),
  ("Hungarian Style Paprika",    2, None,       6.30, "18 OZ"),
  ("Kosher Salt",                1, "4999470", 38.57, "12/3 LB"),
  ("Kosher Salt",                2, None,      93.24, "12/3 LB"),
  ('Tortilla, Flour 12"',        1, "8337680", 34.27, "8/12"),
  ('Tortilla, Flour 12"',        2, None,      24.04, "8/12"),
  ('Tortilla, Flour 6"',         1, "59147",   33.04, "12/24"),
  ('Tortilla, Flour 6"',         2, None,      24.39, "12/24"),
  ("Garlic Parmesan",            1, None,      51.08, "2/1 GAL"),
  ("Garlic Parmesan",            2, "D0796",   46.15, "2/1 GAL"),
  ("Yellow Mustard",             1, None,      32.92, "4/1 GAL"),
  ("Yellow Mustard",             2, "DE304",   18.20, "4/1 GAL"),
  ("Ketchup Packets",            1, None,      25.29, "1000/9G"),
  ("Ketchup Packets",            2, "DV214",   24.69, "1000/9G"),
  ("Mustard Packets",            1, None,      18.10, "500/7G"),
  ("Mustard Packets",            2, "CB386",   15.00, "500/7G"),
  ("Mayo Packets",               1, None,      20.61, "500/9G"),
  ("Mayo Packets",               2, "CV532",   18.00, "500/9G"),
  ("Golden Sauce",               3, "442059",  40.99, "2/1 GAL"),
  ("Blended Oil",                1, "990416", 100.28, "4/1 GAL"),
  ("Blended Oil",                2, None,      29.89, "4/1 GAL"),
  ("Olive Oil",                  1, None,     109.03, "10 L"),
  ("Olive Oil",                  2, "B1740",  130.51, "10 L"),
  ("Buffalo Sauce",              1, None,      69.84, "4/1 GAL"),
  ("Buffalo Sauce",              2, "CV242",   71.25, "4/1 GAL"),
  ("Pizza Sauce",                1, None,      48.25, "6/#10"),
  ("Pizza Sauce",                2, "HD182",   42.88, "6/#10"),
  ("Bulk Sugar",                 1, "6395610", 47.83, "8/5 LB"),
  ("BBQ Sauce",                  1, None,      50.00, "4/1 GAL"),
  ("BBQ Sauce",                  2, "VE590",   58.39, "4/1 GAL"),
  ("Maraschino Cherries",        1, "9523465",114.65, "6/0.5 GAL"),
  ("Cholula",                    1, "1285548", 80.77, "24/5 OZ"),
  ("Crushed Red Pepper Packets", 1, None,      35.00, "200/1G"),
  ("Crushed Red Pepper Packets", 2, "FA304",   20.00, "200/1G"),
  ("Premium Buttery Pan & Grill",1, "6560809", 71.76, "6/1 GAL"),
  ("Premium Buttery Pan & Grill",2, None,      70.14, "6/1 GAL"),
  ("Fire Roasted Salsa",         1, "5576771",102.40, "4/68 OZ"),
  ("Fire Roasted Salsa",         2, None,      52.65, "4/68 OZ"),
  ("Black Beans",                2, "KM806",   91.25, "24/15.5oz"),
  ("Shortening",                 1, "1328699", 30.38, "35 LB"),
  ("Shortening",                 2, None,      62.76, "35 LB"),
  ("Croutons",                   1, "3631189", 33.28, "4/2.5 LB"),
  ("Croutons",                   2, None,      30.91, "4/2.5 LB"),
  ("Deli Paper",                 1, "778662",  79.99, "12/500"),
  ("Deli Paper",                 2, None,      71.21, "12/500"),
  ("Savaday",                    1, "4010922",105.69, "2/250"),
  ("Baby Carrots",               1, "6342026", 27.89, "4/5 LB"),
  ("Broccoli",                   1, None,       2.73, "20 LB"),
  ("Broccoli",                   2, "732478",   3.04, "20 LB"),
  ("Green Onions",               1, "1326438", 24.50, "4/2 LB"),
  ("Cucumbers",                  1, None,      21.01, "12 EA"),
  ("Cucumbers",                  2, "FT516",   16.99, "12 EA"),
  ("Celery Sticks",              1, None,      52.46, "2/5 LB"),
  ("Celery Sticks",              2, "FB980",    1.85, "2/5 LB"),
  ("Shredded Lettuce",           1, None,      60.55, "6/2 LB"),
  ("Shredded Lettuce",           2, "GC916",   33.50, "6/2 LB"),
  ("Burger Patties",             1, None,      46.55, "48/4 OZ"),
  ("Burger Patties",             2, "GL384",   50.71, "48/4 OZ"),
  ("Double Lobe Chicken Breasts",1, "2725661", 77.60, "4/5 LB"),
  ("Double Lobe Chicken Breasts",2, None,      65.41, "4/5 LB"),
  ("Chicken Wings",              1, "1121825", 58.15, "40 LB"),
  ("Chicken Wings",              2, None,      58.66, "40 LB"),
  ("Assorted Peppers",           1, "8316622", 27.33, "5 LB"),
  ("Cherry Tomatoes",            1, "4731774",  2.44, "10 LB"),
  ("Cherry Tomatoes",            2, None,       2.72, "10 LB"),
  ("American Slices 120 CT",     1, None,      53.91, "4/5#"),
  ("American Slices 120 CT",     2, "DCV106",  53.58, "4/5#"),
  ("Pecorino Romano Blend",      1, None,     136.10, "4/5 LB"),
  ("Pecorino Romano Blend",      2, "DA708",  110.05, "4/5 LB"),
  ("Mild Cheddar Cheese",        1, "1332642", 72.72, "4/5 LB"),
  ("Mild Cheddar Cheese",        2, None,      56.68, "4/5 LB"),
  ("Oranges",                    1, "HB846",   50.48, "88 CT"),
  ("Oranges",                    2, None,      55.06, "88 CT"),
  ("Limes",                      1, None,      20.37, "48 EA"),
  ("Limes",                      2, "LG444",   15.10, "48 EA"),
  ("Pizza Cheese",               1, None,      48.43, "6/5 LB"),
  ("Pizza Cheese",               2, "NE872",   87.27, "6/5 LB"),
  ("Caesar Dressing",            1, "9328634", 66.20, "4/1 GAL"),
  ("Caesar Dressing",            2, None,      79.89, "4/1 GAL"),
  ("Ranch Dressing",             1, "5635602", 43.73, "4/1 GAL"),
  ("Ranch Dressing",             2, None,      36.11, "4/1 GAL"),
  ("Sour Cream",                 1, "2739175", 30.47, "4/5 LB"),
  ("Sour Cream",                 2, None,      33.39, "4/5 LB"),
  ("Pickles",                    1, None,      38.70, "5 GAL"),
  ("Pickles",                    2, "D9540",   38.12, "5 GAL"),
  ("Bacon Toppings",             1, "4350070", 81.68, "2/5 LB"),
  ("Bacon Toppings",             2, None,      89.94, "2/5 LB"),
  ("Sliced Bacon",               1, None,      89.15, "15 LB"),
  ("Sliced Bacon",               2, "JC478",   67.85, "15 LB"),
  ("Potato Hamburger Bun",       1, "1011788", 29.25, "5/12"),
  ("Potato Hamburger Bun",       2, None,      25.69, "5/12"),
  ("Fries",                      1, None,      46.83, "6/5 LB"),
  ("Fries",                      2, "88802",   42.02, "6/5 LB"),
  ("Flatbread Dough",            2, "JV526",   63.34, "28/1"),
  ("Pepperoni",                  2, "NJ032",   56.66, "10 LB"),
  ("Fajita Chicken",             1, None,      58.00, "2/5 LB"),
  ("Fajita Chicken",             2, "HC322",   61.81, "2/5 LB"),
  ("Beer Cheese Dip",            1, "4503294", 69.30, "4/5 LB"),
  ("Beer Cheese Dip",            2, None,      67.30, "4/5 LB"),
  ("JTM Taco Meat",              1, "2305027", 74.22, "4/5 LB"),
  ("JTM Taco Meat",              2, None,      58.58, "4/5 LB"),
  ("Tenders",                    1, None,      45.30, "2/5 LB"),
  ("Tenders",                    2, "D7526",   31.88, "2/5 LB"),
  ("Boneless Wings",             1, None,      44.00, "2/5 LB"),
  ("Milwaukee Pretzel",          1, "5405123", 56.24, "8/24 OZ"),
  ("David's Cookies",            1, "8515199", 85.22, "80/4.5 OZ"),
  ("David's Cookies",            2, None,      81.41, "80/4.5 OZ"),
  ("Pretzel Bites",              1, None,      37.85, "500/.4 OZ"),
  ("Pretzel Bites",              2, "JP736",   47.31, "500/.4 OZ"),
  ("Tater Kegs",                 1, "543471",  36.33, "10 LB"),
  ("Tater Kegs",                 2, None,      38.77, "10 LB"),
  ("Delimer",                    1, "4367561",117.10, "4/1 GAL"),
  ("Oven Cleaner",               1, "7912660", 63.59, "6/20 oz"),
  ("Solid Dish Detergent",       1, "4000885", None,  "2/9 LB"),
  ("Degreaser",                  1, "7912694", 57.82, "6/32 oz"),
  ("Pot & Pan Detergent",        1, "1086044", None,  "1 EA"),
  ("Pre Soak",                   1, "3986002", None,  "1 EA"),
  ("Heavy Duty Rinse Additive",  1, "4959856", None,  "1 EA"),
  ("Low Temp Sanitizer",         1, "3742737", None,  "1 EA"),
  ("Sanitizing Floor Cleaner",   1, None,      89.01, "1 EA"),
  ("Quat Sanitizer",             1, "1483772", None,  "1 EA"),
  ("Dishmachine Detergent",      1, "1554679", 27.21, "1 EA"),
  ("Stainless Steel Scrubber",   1, "2950335",  0.57, "6/12"),
  ("Green Scrubbies",            1, "2949105",  9.55, "20 EA"),
  ("L Nitrile Gloves",           5, None,      29.95, "10/100"),
  ("XL Nitrile Gloves",          5, None,      59.95, "10/100"),
  ("Daily's Sweet & Sour Mix",   1, "4004420", 38.36, "4/1 GAL"),
  ("Chafing Fuel Can 6 Hour",    1, "2912038", 93.50, "24 CT"),
  ("Aluminum 1/2 Pans",         1,  None,      91.89, ""),
  ("Holy Gospel",                7, None,      39.00, "5 LB"),
  ("Holy Cow",                   7, None,      39.00, "5 LB"),
  ("Holy Voodoo",                7, None,      39.00, "5 LB"),
  ("Blanco",                     7, None,      39.00, "5 LB"),
]

def main():
    # 1. Delete the test item inserted earlier and re-insert clean
    req = urllib.request.Request(
        f"{URL}/items?id=eq.1", method="DELETE",
        headers={**HDRS, "Prefer": "return=minimal"})
    try: urllib.request.urlopen(req)
    except: pass

    # 2. Seed items
    print("Seeding items...")
    item_ids = {}
    for name, cat, pack, par, vendor in ITEMS:
        row = {"name": name, "category_id": cat, "pack_size": pack,
               "par_level": par, "preferred_vendor_id": vendor}
        result = post("items", row)
        if result and isinstance(result, list) and result:
            item_ids[name] = result[0]["id"]
            print(f"  ✓ {name} (id={result[0]['id']})")
        elif result is None:
            pass

    # re-fetch all item ids
    req3 = urllib.request.Request(f"{URL}/items?select=id,name", headers=HDRS)
    with urllib.request.urlopen(req3) as r:
        for row in json.loads(r.read()):
            item_ids[row["name"]] = row["id"]

    # 3. Create price list record for current known prices
    print("\nCreating price list record...")
    pl = post("price_lists", {"vendor_id": 1, "season": "Current (from order guide)",
                               "notes": "Seeded from Orders.csv baseline data"})
    pl_id = pl[0]["id"] if pl and isinstance(pl, list) else None
    print(f"  Price list id: {pl_id}")

    # 4. Seed pricing
    print("\nSeeding pricing...")
    ok = 0
    skip = 0
    for name, vendor_id, apn, price, pack in PRICES:
        if price is None:
            skip += 1
            continue
        item_id = item_ids.get(name)
        if not item_id:
            print(f"  ? Item not found: {name}")
            continue
        row = {"item_id": item_id, "vendor_id": vendor_id,
               "price_list_id": pl_id, "apn": apn,
               "price": price, "pack_size": pack}
        result = post("pricing", row)
        if result is not None:
            ok += 1
        else:
            skip += 1

    print(f"\n✅ Done. {ok} prices seeded, {skip} skipped.")
    print(f"   Items in DB: {len(item_ids)}")

if __name__ == "__main__":
    main()
