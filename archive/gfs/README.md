# GFS temporary archive

Archived on August 27, 2026. GFS is vendor ID `4`.

This is a reversible operational archive, not a data deletion. The live
Supabase records and the GFS integration source files remain in place. Active
orders, Item Master output, manual order overrides, automatic order placement,
and scheduled price scraping exclude GFS.

## Preserved live data

The FoodOrderAgent Supabase project contained the following GFS records at the
time of archival:

- 1 vendor record
- 26 price lists
- 423 pricing observations
- 8 preferred-item links (four duplicated item names)
- 24 historical order lines
- 120 supplier decision rows
- 92 selected-supplier decision rows
- 1 vendor authentication row (credentials remain only in Supabase)

The preferred-item links were:

| Item | Item IDs |
| --- | --- |
| Use First Stickers | 3, 120 |
| Black Beans | 37, 154 |
| Diced Tomatoes | 74, 191 |
| Diced Red Onions | 75, 192 |

Historical finalized and pending order records were deliberately left
unchanged so reporting and audit history remain accurate.

## Preserved integration files

The scraper, authentication helpers, probes, intercept scripts, ordering
implementation, documentation, tests, and prior output artifacts remain in the
repository. In particular, `scrape_gfs.py` and `api/place_order_gfs.py` were not
deleted.

## Restore checklist

When GFS becomes active again:

1. Add vendor ID `4` back to `BROADLINER_IDS` in `api/generate_order.py` and
   `weekly_order.py`.
2. Add vendor ID `4` back to `VENDOR_IDS` and `VENDOR_NAMES` in
   `api/item_master.py` and `generate_item_master.py`, then restore the GFS
   summary and table column.
3. Set `GFS_ORDERING_ACTIVE = True` in `api/place_order_gfs.py` and restore the
   GFS order endpoint entry in the generated-order JavaScript.
4. Restore the GFS job and report dependency in
   `.github/workflows/scrape_prices.yml`.
5. Re-enable GFS in manual order overrides in `index.html` and
   `api/inventory_snapshot.py`.
6. Refresh the saved GFS session/cookies, run the scraper, and verify a preview
   order before enabling live submission.

No database restore should be necessary because the GFS rows were retained.
