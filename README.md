# FoodOrderAgent

Fully automated twice-weekly food price scraping for On Par Bar & Grill.
Pulls current prices from vendor portals → Supabase → basket optimizer.

**Schedule**: GitHub Actions cron Monday + Thursday 6 AM Eastern (11:00 UTC)

---

## Architecture

```
GitHub Actions (Mon/Thu)
  ├── scrape_usfoods.py  → US Foods panamax REST API
  ├── scrape_pfg.py      → PFG CustomerFirst Azure API
  ├── scrape_sysco.py    → Sysco Okta SAML2 + GraphQL
  └── basket_report.py   → Markdown summary → $GITHUB_STEP_SUMMARY
          ↓
     Supabase (pricing table)
          ↓
     index.html (basket optimizer — cheapest vendor per item)
```

Each scraper:
1. Authenticates (OAuth2 / MSAL / Okta SAML2)
2. Fetches vendor's current price list
3. Fuzzy-matches products to item master in Supabase
4. Upserts prices
5. (US Foods / PFG) Rotates its GitHub secret for the next run

### Live item master and price history

`/api/item_master` is the current item-master dashboard. Every scraper run adds
a `price_lists` import header and one `pricing` observation per matched item;
older observations are retained. Expand a supplier's **History** row in the
dashboard to see recorded checks, price-change events, product IDs, and dates.

The kitchen inventory sheet refreshes its valuation from
`/api/item_master?format=inventory` on load and from the **Sync Prices** button.
That feed includes only approved, orderable quotes and converts supplier case
prices into each item's inventory count unit (for example, case cost divided by
gallons or cans per case). The browser bypasses its cache on every refresh;
unresolved prices are shown as valuation gaps instead of falling back to stale
broadliner prices.

When **Generate Order** is clicked, the inventory sheet checks the latest shared
count first and then performs a no-cache Item Master refresh. A failed refresh
blocks generation. The server independently reloads the Item Master's approved,
non-blocked quote set before the basket optimizer chooses suppliers, so raw or
audit-blocked pricing rows cannot be selected for an order.

Current non-price exceptions are stored in `item_vendor_status`. This table is
the source of truth for product mismatches, identity reviews, special orders,
and not-found results, so these states can be corrected in Supabase without
editing or re-importing a CSV. The table is publicly readable for the existing
dashboard, but RLS and grants restrict client access to `SELECT`; writes require
the server-side `service_role`.

Apply the `ITEM MASTER AUDIT STATUS` section of `schema.sql` to a new Supabase
environment. It seeds the US Foods audit dated 2026-08-26 and uses item/vendor
name lookups rather than environment-specific generated IDs.

---

## Vendors

| # | Vendor | Status | vendor_id | Auth method |
|---|--------|--------|-----------|-------------|
| 1 | US Foods | ✅ Live | 1 | Azure B2C OAuth2 (JSON body) |
| 2 | PFG CustomerFirst | ✅ Live | 2 | MSAL B2C (form-encoded, `client_info=1`) |
| 3 | Sysco | ✅ Live | 3 | Okta SAML2 step-up + GraphQL (programmatic) |
| 4 | GFS Gordon Food Service | 🗄️ Temporarily archived | 4 | Okta SAML2 session cookies (`GFS_COOKIES` retained for restoration) |

GFS data and integration notes are preserved in
[`archive/gfs/README.md`](archive/gfs/README.md). Active orders and Item Master
formats currently use US Foods, PFG, and Sysco only.

---

## GitHub Secrets & Variables Required

| Key | Type | Description |
|-----|------|-------------|
| `SUPABASE_URL` | Secret | Supabase project URL |
| `SUPABASE_KEY` | Secret | Supabase publishable key |
| `GH_PAT` | Secret | GitHub PAT with repo secrets write permission |
| `USF_REFRESH_TOKEN` | Secret | US Foods refresh token (auto-rotated each run) |
| `USF_CONFIG` | Secret | US Foods static config JSON |
| `PFG_REFRESH_TOKEN` | Secret | PFG MSAL refresh token (auto-rotated each run) |
| `PFG_CONFIG` | Secret | PFG static config JSON |
| `GFS_COOKIES` | Archived secret | Retained only for a future GFS restoration |
| `SYSCO_EMAIL` | Secret | Sysco login email (`carlos@onparbar.com`) |
| `SYSCO_PASSWORD` | Secret | Sysco login password |
| `SYSCO_COOKIES` | Secret | Sysco session cookies JSON — fast path that bypasses Okta (refresh by running `intercept_sysco5.py`) |
| `PRICE_SEASON` | Variable | Season label for price_lists table (e.g. `Spring 2026`) |

---

## Local Development

### Run scrapers locally:
```bash
# US Foods / PFG — reads tokens from ~/.FoodOrderAgent/
python3 scrape_usfoods.py
python3 scrape_pfg.py

# GFS archive utility — manual use only; not part of the scheduled workflow
python3 scrape_gfs.py

# Sysco — reads from env vars (no session file needed)
SYSCO_PASSWORD='...' python3 scrape_sysco.py

# Basket report
python3 basket_report.py
```

### Refresh GFS cookies (when `GFS_COOKIES` secret expires, ~30 days):
```bash
python3 intercept_gfs2.py   # opens Chrome, logs in via Okta SAML — fully automated
python3 - <<'EOF'
import json, os
s = json.load(open(os.path.expanduser('~/.FoodOrderAgent/gfs_session.json')))
cks = {c['name']: c['value'] for c in s['cookies']}
print(json.dumps({'gor': cks.get('GOR','us-central1'), 'gclb': cks.get('GCLB',''),
    'xsrf': cks.get('XSRF-TOKEN',''), 'session': cks.get('__Secure-GORDONORDERING2','')}))
EOF
| gh secret set GFS_COOKIES -R carloschavando-prog/FoodOrderAgent
```

### Update season (when a new menu season starts):
```bash
gh variable set PRICE_SEASON --body "Fall 2026" -R carloschavando-prog/FoodOrderAgent
```

### Directory structure:
```
~/.FoodOrderAgent/
  gfs_session.json          # Playwright browser state (GFS Okta SAML)
  pfg_session.json          # Playwright browser state (PFG)
  pfg_api_config.json       # PFG tokens + config
  usf_api_config.json       # US Foods tokens + config
  api_captures/             # Raw API response captures (exploration)
```

---

## Sysco API Notes

- **Auth** (2 paths — fast path preferred):
  - **Fast path** (`SYSCO_COOKIES` set): loads `MSS_STATEFUL` + `TAPID` + `vid` + `JSESSIONID` from secret, calls `auth/validate` directly — no Okta needed. Refresh by running `intercept_sysco5.py`.
  - **Okta IDX fallback** (`SYSCO_COOKIES` absent): full 6-step Okta flow. ⚠️ Sysco migrated to Okta Identity Engine (June 2026) — stateToken now starts with `02.id.` (IDX interactionHandle); scraper uses `/idp/idx/introspect` → `/idp/idx/identify` → `/idp/idx/challenge/answer` instead of old `/api/v1/authn`.
  - Step 5-6 (both paths): `POST auth.shop.sysco.com/api/v1/auth/sso/assert` → sets `MSS_STATEFUL` cookie → `GET auth/validate` → `{gatewayCredentials: JWT}`
- **GraphQL**: `POST gateway-api.shop.sysco.com/graphql`
  - Required headers: `Authorization: Bearer <gatewayCredentials>` + `syy-authorization` (base64 account context) + `syy-requested-by` (csrf_token from JWT)
  - `GetListItemsV2` → Order Guide items (listId `66a83a1e-8c6f-4e83-820e-f485012da85f`, listType `MY_LIST`)
  - `Prices` → `priceInfoV2.case.netPrice` per product
- **Cookie refresh** (when `SYSCO_COOKIES` expires): run `intercept_sysco5.py` → push new `SYSCO_COOKIES` secret
- **Items**: 97 products in Order Guide (as of June 2026)

### Refresh Sysco cookies:
```bash
python3 intercept_sysco5.py  # opens Chrome, logs in — saves ~/.FoodOrderAgent/sysco_session.json
python3 - <<'EOF' | gh secret set SYSCO_COOKIES -R carloschavando-prog/FoodOrderAgent
import json, os
s = json.load(open(os.path.expanduser('~/.FoodOrderAgent/sysco_session.json')))
cks = {c['name']: c['value'] for c in s.get('cookies', [])}
keep = ['MSS_STATEFUL', 'TAPID', 'vid', 'JSESSIONID']
print(json.dumps({k: cks[k] for k in keep if k in cks}))
EOF
```

## Archived GFS Gordon Food Service API Notes

These notes are intentionally retained for restoration. GFS is excluded from
active ordering, Item Master output, and scheduled scraping.

- **Base**: `https://order.gfs.com/us-central1/api`
- **Auth**: Okta SAML2 session cookies — `GOR`, `GCLB`, `XSRF-TOKEN`, `__Secure-GORDONORDERING2`
- **Order guide**: `GET /v6/lists/order-guide` → `{guideCategories: [{categoryName, materialNumbers}]}`
- **Material info**: `POST /v1/materials/info` → plain JSON array body; response `{materialInfos: [{materialNumber, brand.en, description.en}]}`
- **Prices**: `POST /v5/prices` → `{"materialNumbers": [...]}` ; response `{materialPrices: [{materialNumber, unitPrices: [{salesUom, price}]}]}`
- **⚠️ Required on all mutating calls**: `X-Requested-With: XMLHttpRequest` — without it GFS returns HTTP 218 (silent error) instead of 200
- **Session refresh**: cookies expire ~30 days — run `intercept_gfs2.py` locally then `gh secret set GFS_COOKIES`
- **Items**: 143 materials, ~138 with prices (June 2026)

### GFS Order Placement (confirmed working 2026-05-27):
```
POST v8/cart                        {}
  → {id: cartId, status, fulfillmentType, materials, ...}

GET  v3/delivery-schedules
  → {deliverySchedules: [{routeDate, customerArrivalDate, cutoffDateTime, routeId}]}
    cutoff is 9 PM UTC the day before delivery

PUT  v7/cart/{cartId}               {userLastUpdatedTimestamp:"...Z", fulfillmentType:"TRUCK",
                                     truckFulfillment:{routeDate, customerArrivalDate},
                                     materials:[{materialNumber, lines:[{uom:"CS",quantity:N}],
                                                 restored:false, originTrackingId:null}]}

POST v6/cart/{cartId}/submit        {splitOrders:[]}
  → {cartOrderIds:["..."]}   (new empty cart created after submit)

POST v1/orders/cancel               {orderId:"1050723762", groupNumber:"01"}   (if needed)
```
- Order detail URL: `https://order.gfs.com/orders/{orderNumber}/details/stock/{groupNumber}`
- Orders list API: `GET /v7/orders`
- Order detail API: `POST /v6/order-details` body: `{orderNumber, orderType:"STOCK", groupNumber}`

## US Foods API Notes

- **Base**: `https://panamax-api.ama.usfoods.com`
- **Token refresh**: `POST auth-api/v1/oauth/token` — JSON body `grantType: "refreshToken"`
- **Required headers**: `consumer-id: ecom`, `correlation-id: ecomr4-{uuid}`, `transaction-id: {ms}`, `Origin: https://order.usfoods.com`, `usflang: en`

## PFG CustomerFirst API Notes

- **Base**: `https://apps-zz-cusfst-mw-p-eus01.azurewebsites.net/api`
- **Token refresh**: MSAL B2C — `POST pfgcustomerfirst.b2clogin.com/.../token` (form-encoded, `client_info=1`)
- **Pricing flow**: `CreateOrderEntryHeader` → `SearchProductList` → `GetOrderEntryCustomerProductPrice` → `DeleteOrderEntryHeader`
- **Critical**: price request field is `CustomerProductPriceRequests`; requires `BusinessUnitKey`, `OperationCompanyNumber`, `DeliveryDate`, `IgnoreRetry`

---

## Sales-representative pricing feedback

After every vendor represented in the generated order has returned a successful
submission, the report calls `/api/finalize_order`. The endpoint first saves the
final order and its per-item, per-supplier decision metadata, marks the order
`finalized`, and only then renders pricing-feedback previews. Finalization never
calls Resend. A failed feedback step does not change or roll back vendor orders.

The exact inclusion rule is deliberately conservative. A supplier line is
included only when the saved order says all of the following are true:

1. The canonical item had a positive required quantity.
2. That supplier had an eligible, available, directly comparable quote with a
   reliable pack conversion.
3. A positive number of the selected supplier's cases was ordered elsewhere.
4. The supplier's normalized net cost was strictly greater than the selected
   normalized net cost for the same shortage.
5. The saved `decision_reason` is exactly `lost_on_price`.

The normalized cost is the existing optimizer's `extended_cost`: whole cases
required after `units_per_case` conversion multiplied by the stored net case
price. This is the same calculation used to pick the order winner; the feedback
feature does not compare raw prices across packs and does not choose a new
winner. A minimum-order drop, contract/specification rule, absent or incompatible
quote, unavailable/noncomparable offer, strict tie, or unprovable comparison is
saved with its non-price reason and omitted. Duplicate canonical lines are
grouped, and only cases allocated away because of price are totaled.

Current source data exposes one net `pricing.price` value and reliable pack
metadata for eligible rows; it does not expose separate fee, discount, or rebate
components or a universal availability reason. Those adjustments must already be
reflected upstream in `pricing.price`, exactly as they are for order selection.
When a current pricing row or trustworthy conversion is absent, the feature
conservatively records `missing_or_ineligible_quote` rather than trying to infer
whether the portal had no quote, unavailable stock, or a noncomparable pack. The
current optimizer awards one base supplier per canonical product; the feedback
filter also supports future split decision lines by aggregating only their
`allocated_elsewhere_cases`.

### Database setup

Apply the `FINAL FOOD ORDERS + PRICING FEEDBACK` section of `schema.sql` in the
Supabase SQL Editor before deploying the endpoints. It creates:

- `food_orders`: pre-submission staging, finalization status, and vendor
  confirmations.
- `food_order_lines`: the exact item, supplier item number, pack, base/filler
  case count, and extended amount submitted to each vendor.
- `food_order_decisions`: authoritative machine-readable selection reasons and
  normalized comparison evidence.
- `order_feedback_sends`: preview, skip, failure, retry, and delivery ledger.

The tables have RLS enabled, revoke `anon` and `authenticated` access, and grant
access only to server-side `service_role`. The APIs require
`SUPABASE_SERVICE_KEY`; never expose that value to `index.html` or any other
client code. Current Supabase projects require explicit Data API grants for new
tables, which are included in `schema.sql`.

The Place Orders workflow calls `/api/stage_order` before contacting a vendor.
If the order header, exact item lines, and decision evidence cannot be saved,
vendor submission is stopped with a visible error. Successful vendor responses
then finalize that same staged order. This preserves access to ordered items
even if later feedback preparation fails.

## Event Kitchen party demand

The inventory sheet reads definite-event food requirements through the
authenticated Event Kitchen JSON endpoint (`/api/kitchen/day?date=YYYY-MM-DD`).
The four-digit Event Host PIN is exchanged for a session entirely inside the
FoodOrder Vercel function; neither the PIN nor the session cookie is returned to
the browser.

Required server environment:

- `EVENT_KITCHEN_PIN` — the current four-digit Event Host PIN. When it is not
  configured, FoodOrder uses PrepList's authenticated server-side event-prep
  endpoint as a compatibility source.
- `EVENT_KITCHEN_BASE_URL` — optional; defaults to
  `https://eventhost-opal.vercel.app`.
- `EVENT_PREP_BASE_URL` — optional PrepList compatibility-source base URL;
  defaults to `https://preplist-theta.vercel.app`.
- `SUPABASE_SERVICE_KEY` — required for server-only party snapshot and override
  persistence.

Apply the `PARTY DEMAND SNAPSHOTS` section of `schema.sql` before deployment. It
creates `party_demand_snapshots` and `party_demand_overrides`, links each
`inventory_snapshots` row to its applicable party snapshot, enables RLS, revokes
client access, and grants access only to `service_role`.

If Event Kitchen cannot be refreshed, the UI displays the last saved snapshot
as stale and order generation is blocked. Unmapped food and Event Kitchen
`Needs Review` events also block by default. The emergency manager override
requires a reason and is recorded in `party_demand_overrides` before the order
report is generated. A refresh first invokes Event Kitchen's authenticated
Tripleseat sync for each date in the delivery window, then reloads the window
without cache. Mutating sync requests are serialized and transient 429/5xx or
network failures receive bounded exponential-backoff retries; the read-only day
loads remain parallel. Source timestamps older than `PARTY_SOURCE_MAX_AGE_MINUTES`
(60 minutes by default) are treated as stale and cannot be presented as a
successful refresh.

If an upstream Event Kitchen payload continues emitting an event after an
operator confirms it is non-definite in Tripleseat, add its event ID to the
comma-separated `PARTY_EXCLUDED_EVENT_IDS` production variable. This explicit
override removes the event from demand without disabling refreshes for the rest
of the delivery window.

### Configuration

Copy the relevant names from `.env.example` into local/Vercel server-side
environment variables. Required preview configuration is:

- `ORDER_FEEDBACK_{SYSCO,GFS,US_FOODS,PFG}_REP_NAME`
- `ORDER_FEEDBACK_{SYSCO,GFS,US_FOODS,PFG}_REP_EMAIL`
- `ORDER_FEEDBACK_PURCHASING_NAME`
- `ORDER_FEEDBACK_BUSINESS_NAME`
- optional `ORDER_FEEDBACK_CONTACT` for the monitored email or phone in the
  signature

Delivery additionally requires `RESEND_API_KEY`, `ORDER_FEEDBACK_FROM`, and
`ORDER_FEEDBACK_REPLY_TO`. Verify the `ORDER_FEEDBACK_FROM` domain in Resend and
use an actively monitored reply-to mailbox. `ORDER_FEEDBACK_TEST_RECIPIENT` is
required for test delivery. `ORDER_FEEDBACK_LIVE_ENABLED` defaults to `false`.

Representative names and addresses are validated before preview or delivery.
API keys, prices, competitor details, and raw Resend errors are never written to
templates or logs.

For local CLI use, export the variables into the current shell first; the CLI
does not automatically parse `.env`. Vercel injects configured environment
variables into the serverless functions at runtime.

### Preview and operate

Explicitly render the synthetic four-supplier fixture without Supabase or
Resend:

```bash
python3 order_feedback_cli.py preview-fixture
```

Render a saved finalized order using configured representatives:

```bash
python3 order_feedback_cli.py preview --order-id ORDER_UUID
```

The normal `preview` command requires `--order-id`; it never silently falls back
to fixture data. Both commands write responsive HTML, plain text, and an
`index.json` summary to
`outputs/order_feedback_previews/`. The summary shows intended recipient,
subject, qualifying item count, affected case count, and skipped results.

Display the exact ordered item lines from the newest durable order:

```bash
python3 order_feedback_cli.py show-order --latest
```

Use `--order-id ORDER_UUID` for an older order or add `--json` for structured
output. This command reads `food_order_lines`; it never reconstructs or
substitutes fixture data.

To make an explicit test delivery, every rendered message is routed only to
`ORDER_FEEDBACK_TEST_RECIPIENT` and receives a `[TEST]` subject prefix:

```bash
python3 order_feedback_cli.py send-test --order-id ORDER_UUID --supplier all
```

The first representative send requires both a deployed environment with
`ORDER_FEEDBACK_LIVE_ENABLED=true` and this explicit command/action:

```bash
python3 order_feedback_cli.py send-live --order-id ORDER_UUID --supplier all --confirm-live
```

Do not run that command until previews have been approved. The equivalent API
actions are `preview`, `test-send`, and `live-send` on `/api/order_feedback`;
there is no implicit send action.

### Retries, duplicate prevention, and shutdown

Each live message uses
`pricing-feedback/{orderId}/{supplierId}/v1` as its Resend idempotency key.
Resend protects the immediate retry window, while `order_feedback_sends` retains
successful sends indefinitely. A `sent` record is never claimed again. Failed
records retain a sanitized error and can be retried with the same explicit
`send-live` command; attempt counts and timestamps are updated independently of
the completed food order. Suppliers with no qualifying rows are recorded as
`skipped` and are never sent.

To disable delivery immediately, set `ORDER_FEEDBACK_LIVE_ENABLED=false` and
redeploy. For an emergency hard stop, also revoke the Resend API key. Preview
preparation can continue safely while live delivery is disabled.
